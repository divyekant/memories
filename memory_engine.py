"""
Memories Engine
Local semantic search with hybrid BM25+vector retrieval, markdown-aware
chunking, automatic backups, and concurrency safety.
"""

import json
import logging
import os
import re
import shutil
import threading
import gc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from entity_locks import EntityLockManager
from qdrant_config import QdrantSettings
from qdrant_store import QdrantStore
from rank_bm25 import BM25Okapi

logger = logging.getLogger("memories")

# Cloud sync (optional dependency)
try:
    from cloud_sync import CloudSync

    CLOUD_SYNC_AVAILABLE = True
except ImportError:
    CLOUD_SYNC_AVAILABLE = False
    logger.info("Cloud sync not available (cloud_sync.py not found or boto3 not installed)")


class MemoryEngine:
    """Memories engine with hybrid search and backup support."""

    def __init__(
        self,
        data_dir: str = "/data",
        model_name: str = None,
        max_backups: int = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.backup_dir = self.data_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)

        # Legacy FAISS path is kept only for backup compatibility and migration.
        self.index_path = self.data_dir / "index.faiss"
        self.metadata_path = self.data_dir / "metadata.json"
        self.config_path = self.data_dir / "config.json"
        self.migration_dir = self.data_dir / "migrations"
        self.faiss_migration_marker = self.migration_dir / "faiss_to_qdrant.done"

        self._model_name = model_name or os.getenv("MODEL_NAME", "all-MiniLM-L6-v2")
        self._max_backups = max_backups or int(os.getenv("MAX_BACKUPS", "10"))
        self._model_cache_dir = os.getenv("MODEL_CACHE_DIR", "").strip()
        self._preloaded_model_cache_dir = os.getenv("PRELOADED_MODEL_CACHE_DIR", "").strip()
        self._embedder_cache_dir: Optional[str] = None
        self._embed_provider = os.getenv("EMBED_PROVIDER", "onnx").strip().lower()
        self._embed_model = os.getenv("EMBED_MODEL", "").strip()

        requested_backend = os.getenv("STORAGE_BACKEND", "qdrant").strip().lower() or "qdrant"
        if requested_backend != "qdrant":
            logger.info("Hard cutover active: ignoring STORAGE_BACKEND=%s and using qdrant", requested_backend)
        self._storage_backend = "qdrant"

        embedder_cache_dir: Optional[str] = None
        if self._model_cache_dir:
            cache_path = Path(self._model_cache_dir)
            cache_path.mkdir(parents=True, exist_ok=True)

            if self._preloaded_model_cache_dir:
                preload_path = Path(self._preloaded_model_cache_dir)
                try:
                    cache_is_empty = not any(cache_path.iterdir())
                except OSError:
                    cache_is_empty = False

                if cache_is_empty and preload_path.exists():
                    try:
                        if any(preload_path.iterdir()):
                            shutil.copytree(preload_path, cache_path, dirs_exist_ok=True)
                            logger.info(
                                "Seeded model cache from preloaded assets: %s -> %s",
                                preload_path,
                                cache_path,
                            )
                    except OSError as exc:
                        logger.warning(
                            "Failed to seed model cache from %s: %s",
                            preload_path,
                            exc,
                        )

            embedder_cache_dir = str(cache_path)

        self._embedder_cache_dir = embedder_cache_dir
        self._embedder_lock = threading.RLock()
        self.model = self._make_embedder()
        self.dim = self.model.get_sentence_embedding_dimension()

        self._write_lock = threading.RLock()
        self._entity_locks = EntityLockManager()

        self.metadata: List[Dict[str, Any]] = []
        self.config = {
            "model": self._active_embed_model(),
            "embed_provider": self._embed_provider,
            "dimension": self.dim,
            "storage_backend": self._storage_backend,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": None,
        }

        self.bm25_index: Optional[BM25Okapi] = None
        self._id_map: Dict[int, int] = {}      # memory_id -> index in self.metadata
        self._next_id: int = 0                  # monotonic counter for new IDs
        self._bm25_pos_to_id: List[int] = []   # BM25 corpus position -> memory_id

        self.qdrant_settings = QdrantSettings.from_env()
        self._qdrant_local_path = self.data_dir / "qdrant"
        self._qdrant_local_path.mkdir(parents=True, exist_ok=True)
        self.qdrant_store = QdrantStore(
            self.qdrant_settings,
            local_path=str(self._qdrant_local_path),
        )
        self.qdrant_store.ensure_collection(self.dim)

        self.cloud_sync: Optional[CloudSync] = None
        if CLOUD_SYNC_AVAILABLE:
            self.cloud_sync = CloudSync.from_env()

        if self.cloud_sync and not self.metadata_path.exists():
            try:
                latest = self.cloud_sync.get_latest_snapshot()
                if latest:
                    logger.info("Local metadata empty - downloading latest backup from cloud: %s", latest)
                    result = self.cloud_sync.download_backup(latest, self.backup_dir)
                    self.restore_from_backup(latest)
                    logger.info("Restored from cloud: %s", result)
            except Exception as e:
                logger.error("Auto-download from cloud failed: %s", e)

        if self.metadata_path.exists():
            self.load()
            self._finalize_legacy_faiss_cutover()

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def _check_integrity(self):
        """Verify store and metadata are in sync."""
        total_points = self.qdrant_store.count()
        if total_points != len(self.metadata):
            logger.error(
                "Integrity mismatch: qdrant has %d vectors, metadata has %d entries",
                total_points,
                len(self.metadata),
            )
            raise RuntimeError(
                f"Index/metadata mismatch: {total_points} vectors vs {len(self.metadata)} metadata entries. "
                "Restore from backup or rebuild the index."
            )

    # ------------------------------------------------------------------
    # BM25
    # ------------------------------------------------------------------

    def _rebuild_bm25(self):
        """Rebuild BM25 index from current metadata."""
        if not self.metadata:
            self.bm25_index = None
            self._bm25_pos_to_id = []
            return
        corpus = [m["text"].lower().split() for m in self.metadata]
        self.bm25_index = BM25Okapi(corpus)
        self._bm25_pos_to_id = [m["id"] for m in self.metadata]

    def _rebuild_id_map(self):
        """Rebuild the sparse ID lookup structures from current metadata."""
        self._id_map = {m["id"]: i for i, m in enumerate(self.metadata)}
        self._bm25_pos_to_id = [m["id"] for m in self.metadata]
        self._next_id = max(self._id_map.keys(), default=-1) + 1

    def _get_meta_by_id(self, memory_id: int) -> Dict[str, Any]:
        """Fetch metadata dict for a memory by its sparse ID."""
        idx = self._id_map.get(memory_id)
        if idx is None:
            raise ValueError(f"Memory ID {memory_id} not found")
        return self.metadata[idx]

    def _id_exists(self, memory_id: int) -> bool:
        return memory_id in self._id_map

    def _delete_ids_targeted(self, ids_to_remove: set):
        """Remove specific IDs from metadata + Qdrant without full reindex."""
        self.qdrant_store.delete_points(list(ids_to_remove))
        self.metadata = [m for m in self.metadata if m["id"] not in ids_to_remove]
        self._rebuild_id_map()

    def _entity_key(self, source: str) -> str:
        scoped = source.strip() if source else "__unknown__"
        return f"default:{scoped}"

    def _point_payload(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in meta.items() if k != "id"}

    def _migrate_timestamps(self):
        """Migrate existing memories from single `timestamp` to created_at/updated_at."""
        migrated = 0
        for meta in self.metadata:
            if not meta:
                continue
            if "created_at" not in meta:
                ts = meta.get("timestamp", datetime.now(timezone.utc).isoformat())
                meta["created_at"] = ts
                meta["updated_at"] = ts
                meta["timestamp"] = ts
                migrated += 1
        if migrated:
            logger.info("Migrated %d memories to created_at/updated_at timestamps", migrated)
            self.save()
            # NOTE: Qdrant payloads are not updated here to avoid slow bulk writes.
            # They will sync on next update_memory or reindex.

    def _encode(
        self,
        texts: List[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        with self._embedder_lock:
            return self.model.encode(
                texts,
                normalize_embeddings=normalize_embeddings,
                show_progress_bar=show_progress_bar,
            )

    def _reindex_store_from_metadata(self):
        self.qdrant_store.recreate_collection(self.dim)
        if not self.metadata:
            return

        texts = [m["text"] for m in self.metadata]
        embeddings = self._encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        batch_size = 256
        for start in range(0, len(self.metadata), batch_size):
            end = min(start + batch_size, len(self.metadata))
            points = []
            for i in range(start, end):
                points.append(
                    {
                        "id": self.metadata[i]["id"],
                        "vector": embeddings[i].astype("float32").tolist(),
                        "payload": self._point_payload(self.metadata[i]),
                    }
                )
            self.qdrant_store.upsert_points(points)

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    @staticmethod
    def chunk_markdown(
        content: str,
        source_name: str,
        max_chunk_size: int = 1500,
        overlap_size: int = 200,
    ) -> List[Tuple[str, str]]:
        """Split markdown into semantically meaningful chunks."""
        chunks: List[Tuple[str, str]] = []

        parts = re.split(r"(^#{1,4}\s+.+$)", content, flags=re.MULTILINE)

        current_header = ""
        buffer = ""
        chunk_idx = 0

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if re.match(r"^#{1,4}\s+", part):
                if buffer.strip() and len(buffer.strip()) > 30:
                    chunk_text = f"{current_header}\n\n{buffer.strip()}" if current_header else buffer.strip()
                    chunks.append((chunk_text, f"{source_name}:chunk_{chunk_idx}"))
                    chunk_idx += 1
                    buffer = ""
                current_header = part
                continue

            paragraphs = re.split(r"\n\s*\n", part)
            for para in paragraphs:
                para = para.strip()
                if not para or len(para) < 20:
                    continue

                candidate = f"{buffer}\n\n{para}".strip() if buffer else para
                if len(candidate) > max_chunk_size and buffer:
                    chunk_text = f"{current_header}\n\n{buffer.strip()}" if current_header else buffer.strip()
                    chunks.append((chunk_text, f"{source_name}:chunk_{chunk_idx}"))
                    chunk_idx += 1
                    if len(buffer) > overlap_size:
                        buffer = buffer[-overlap_size:] + "\n\n" + para
                    else:
                        buffer = para
                else:
                    buffer = candidate

        if buffer.strip() and len(buffer.strip()) > 30:
            chunk_text = f"{current_header}\n\n{buffer.strip()}" if current_header else buffer.strip()
            chunks.append((chunk_text, f"{source_name}:chunk_{chunk_idx}"))

        return chunks

    # ------------------------------------------------------------------
    # Backup / Restore
    # ------------------------------------------------------------------

    def _backup(self, prefix: str = "auto") -> Path:
        """Create timestamped backup of state files."""
        prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", prefix)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"{prefix}_{timestamp}"
        backup_path = self.backup_dir / backup_name
        backup_path.mkdir(exist_ok=True)

        if self.metadata_path.exists():
            shutil.copy2(self.metadata_path, backup_path / "metadata.json")
        if self.config_path.exists():
            shutil.copy2(self.config_path, backup_path / "config.json")
        if self.index_path.exists():
            shutil.copy2(self.index_path, backup_path / "index.faiss")

        self._cleanup_old_backups(keep=self._max_backups)

        if self.cloud_sync:
            try:
                logger.info("Uploading backup to cloud: %s", backup_name)
                self.cloud_sync.upload_backup(backup_path)
                logger.info("Cloud upload complete: %s", backup_name)
            except Exception as e:
                logger.error("Cloud upload failed: %s", e)

        return backup_path

    def _cleanup_old_backups(self, keep: int = 10):
        """Keep only N most recent backups."""
        backups = sorted(self.backup_dir.glob("*_*"), key=lambda p: p.name, reverse=True)
        for old_backup in backups[keep:]:
            shutil.rmtree(old_backup, ignore_errors=True)

    def _finalize_legacy_faiss_cutover(self) -> bool:
        """Archive legacy index.faiss and write one-time cutover marker."""
        if self.faiss_migration_marker.exists():
            return False
        if not self.index_path.exists():
            return False

        total_points = self.qdrant_store.count()
        if total_points != len(self.metadata):
            logger.warning(
                "Skipping FAISS finalization: qdrant=%d metadata=%d",
                total_points,
                len(self.metadata),
            )
            return False

        self.migration_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archived_path = self.migration_dir / f"index.faiss.legacy_{timestamp}"
        shutil.move(str(self.index_path), str(archived_path))

        marker_payload = {
            "migration": "faiss_to_qdrant",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "metadata_count": len(self.metadata),
            "qdrant_count": total_points,
            "archived_index_path": str(archived_path),
        }
        self.faiss_migration_marker.write_text(
            json.dumps(marker_payload, indent=2),
            encoding="utf-8",
        )
        return True

    def restore_from_backup(self, backup_name: str) -> Dict[str, Any]:
        """Restore metadata/config and rebuild Qdrant vectors."""
        if ".." in backup_name or "/" in backup_name or "\\" in backup_name:
            raise ValueError(f"Invalid backup name: {backup_name}")
        backup_path = self.backup_dir / backup_name
        if not backup_path.resolve().is_relative_to(self.backup_dir.resolve()):
            raise ValueError(f"Invalid backup path: {backup_name}")
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup '{backup_name}' not found")

        meta_file = backup_path / "metadata.json"
        if not meta_file.exists():
            raise FileNotFoundError(f"Backup '{backup_name}' is incomplete")

        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                self._backup(prefix="pre_restore")

                shutil.copy2(meta_file, self.metadata_path)
                config_file = backup_path / "config.json"
                if config_file.exists():
                    shutil.copy2(config_file, self.config_path)

                self.load(rebuild_on_mismatch=True)

        return {
            "restored_from": backup_name,
            "total_memories": len(self.metadata),
        }

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_memories(
        self,
        texts: List[str],
        sources: List[str],
        metadata_list: Optional[List[Dict]] = None,
        deduplicate: bool = False,
        dedup_threshold: float = 0.90,
        _chunk_size: int = 100,
    ) -> List[int]:
        """Add new memories to Qdrant + metadata (thread-safe).

        Large batches are encoded in chunks of ``_chunk_size`` to avoid
        embedding timeouts while still doing a single save/BM25 rebuild.
        """
        if not texts:
            return []

        keys = [self._entity_key(source) for source in sources]
        with self._entity_locks.acquire_many(keys):
            if deduplicate and self.metadata:
                novel_texts = []
                novel_sources = []
                novel_meta = []
                for i, text in enumerate(texts):
                    is_new, _ = self.is_novel(text, threshold=dedup_threshold)
                    if is_new:
                        novel_texts.append(text)
                        novel_sources.append(sources[i])
                        if metadata_list and i < len(metadata_list):
                            novel_meta.append(metadata_list[i])
                texts = novel_texts
                sources = novel_sources
                metadata_list = novel_meta if novel_meta else None

            if not texts:
                return []

            # Encode in chunks to avoid timeout on large batches
            import numpy as np  # local import to keep top-level unchanged
            all_embeddings: List[np.ndarray] = []
            for chunk_start in range(0, len(texts), _chunk_size):
                chunk_texts = texts[chunk_start : chunk_start + _chunk_size]
                chunk_emb = self._encode(
                    chunk_texts,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                all_embeddings.append(chunk_emb)
            embeddings = np.concatenate(all_embeddings, axis=0)

            with self._write_lock:
                if len(texts) > 10:
                    self._backup(prefix="pre_add")

                start_id = self._next_id
                added_ids = []
                _reserved_add = {"id", "text", "source", "timestamp", "created_at", "updated_at"}

                # Build metadata + points in chunks and upsert per chunk
                for chunk_start in range(0, len(texts), _chunk_size):
                    chunk_end = min(chunk_start + _chunk_size, len(texts))
                    points = []
                    for i in range(chunk_start, chunk_end):
                        mem_id = start_id + i
                        now = datetime.now(timezone.utc).isoformat()
                        extra = metadata_list[i] if metadata_list and i < len(metadata_list) else {}
                        filtered_extra = {k: v for k, v in extra.items() if k not in _reserved_add}
                        meta = {
                            "id": mem_id,
                            "text": texts[i],
                            "source": sources[i],
                            "created_at": now,
                            "updated_at": now,
                            "timestamp": now,  # backward compat alias
                            **filtered_extra,
                        }
                        self.metadata.append(meta)
                        points.append(
                            {
                                "id": mem_id,
                                "vector": embeddings[i].astype("float32").tolist(),
                                "payload": self._point_payload(meta),
                            }
                        )
                        added_ids.append(mem_id)
                    self.qdrant_store.upsert_points(points)

                self._next_id = start_id + len(texts)
                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()
                self._rebuild_id_map()

        return added_ids

    def delete_memory(self, memory_id: int) -> Dict[str, Any]:
        """Delete a single memory by ID."""
        if not self._id_exists(memory_id):
            raise ValueError(f"Memory ID {memory_id} not found")
        key = self._entity_key(self._get_meta_by_id(memory_id).get("source", ""))
        with self._entity_locks.acquire_many([key]):
            with self._write_lock:
                if not self._id_exists(memory_id):
                    raise ValueError(f"Memory ID {memory_id} not found")

                self._backup(prefix="pre_delete")

                deleted = dict(self._get_meta_by_id(memory_id))
                self._delete_ids_targeted({memory_id})

                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {"deleted_id": memory_id, "deleted_text": deleted["text"][:100]}

    def delete_memories(self, memory_ids: List[int]) -> Dict[str, Any]:
        """Delete multiple memories by IDs in one pass."""
        unique_ids = sorted(set(memory_ids))
        if not unique_ids:
            return {"deleted_count": 0, "deleted_ids": [], "missing_ids": []}

        existing = [mid for mid in unique_ids if self._id_exists(mid)]
        existing_set = set(existing)
        missing = [mid for mid in unique_ids if mid not in existing_set]
        if not existing:
            return {"deleted_count": 0, "deleted_ids": [], "missing_ids": missing}

        keys = [self._entity_key(self._get_meta_by_id(mid).get("source", "")) for mid in existing]
        with self._entity_locks.acquire_many(keys):
            with self._write_lock:
                existing_now = [mid for mid in existing if self._id_exists(mid)]
                if not existing_now:
                    return {"deleted_count": 0, "deleted_ids": [], "missing_ids": missing}

                self._backup(prefix="pre_delete_batch")

                self._delete_ids_targeted(set(existing_now))

                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {
            "deleted_count": len(existing),
            "deleted_ids": existing,
            "missing_ids": missing,
        }

    def supersede(self, old_id: int, new_text: str, source: str = "") -> dict:
        """Replace a memory with an updated version, preserving audit trail."""
        if not self._id_exists(old_id):
            raise ValueError(f"Memory {old_id} not found")

        previous_text = self._get_meta_by_id(old_id).get("text", "")

        self.delete_memory(old_id)

        added_ids = self.add_memories(
            texts=[new_text],
            sources=[source],
            deduplicate=False,
        )
        new_id = added_ids[0] if added_ids else None

        if new_id is not None and self._id_exists(new_id):
            self._get_meta_by_id(new_id)["supersedes"] = old_id
            self._get_meta_by_id(new_id)["previous_text"] = previous_text
            self.save()

        logger.info("Superseded memory %d → %d", old_id, new_id)
        return {"old_id": old_id, "new_id": new_id, "previous_text": previous_text}

    def delete_by_source(self, source_pattern: str) -> Dict[str, Any]:
        """Delete all memories matching a source pattern."""
        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                matching = [m for m in self.metadata if source_pattern in m.get("source", "")]
                if not matching:
                    return {"deleted_count": 0}

                self._backup(prefix="pre_delete_source")

                ids_to_remove = {m["id"] for m in matching}
                self._delete_ids_targeted(ids_to_remove)

                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {"deleted_count": len(matching)}

    def delete_by_prefix(self, source_prefix: str) -> Dict[str, Any]:
        """Delete all memories whose source starts with a prefix."""
        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                matching = [m for m in self.metadata if m.get("source", "").startswith(source_prefix)]
                if not matching:
                    return {"deleted_count": 0}

                self._backup(prefix="pre_delete_prefix")
                ids_to_remove = {m["id"] for m in matching}
                self._delete_ids_targeted(ids_to_remove)
                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {"deleted_count": len(matching)}

    def get_memory(self, memory_id: int) -> Dict[str, Any]:
        """Fetch a single memory by ID."""
        return dict(self._get_meta_by_id(memory_id))

    def get_memories(self, memory_ids: List[int]) -> Dict[str, Any]:
        """Fetch multiple memories by ID."""
        memories: List[Dict[str, Any]] = []
        missing_ids: List[int] = []
        for memory_id in memory_ids:
            if self._id_exists(memory_id):
                memories.append(dict(self._get_meta_by_id(memory_id)))
            else:
                missing_ids.append(memory_id)
        return {"memories": memories, "missing_ids": missing_ids}

    def update_memory(
        self,
        memory_id: int,
        text: Optional[str] = None,
        source: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update fields on an existing memory without changing its ID."""
        if not self._id_exists(memory_id):
            raise ValueError(f"Memory ID {memory_id} not found")

        current = self._get_meta_by_id(memory_id)
        old_key = self._entity_key(current.get("source", ""))
        new_key = self._entity_key(source if source is not None else current.get("source", ""))
        updated_fields: List[str] = []

        # Fast path: source-only change skips backup + re-embed
        source_only = (
            source is not None
            and text is None
            and not metadata_patch
            and source != current.get("source", "")
        )

        with self._entity_locks.acquire_many([old_key, new_key]):
            with self._write_lock:
                if not self._id_exists(memory_id):
                    raise ValueError(f"Memory ID {memory_id} not found")

                meta = self._get_meta_by_id(memory_id)

                if source_only:
                    meta["source"] = source
                    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                    # Don't touch created_at or timestamp
                    self.qdrant_store.set_payload(memory_id, self._point_payload(meta))
                    self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                    self.save()
                    return {"id": memory_id, "updated_fields": ["source"]}

                self._backup(prefix="pre_update")

                if text is not None and text != meta.get("text"):
                    meta["text"] = text
                    updated_fields.append("text")

                if source is not None and source != meta.get("source"):
                    meta["source"] = source
                    updated_fields.append("source")

                if metadata_patch:
                    _reserved = {"id", "text", "source", "timestamp", "created_at", "updated_at", "entity_key"}
                    for key, value in metadata_patch.items():
                        if key in _reserved:
                            continue
                        meta[key] = value
                    updated_fields.append("metadata")

                meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                # Don't touch created_at or timestamp

                embedding = self._encode(
                    [meta["text"]],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )[0].astype("float32").tolist()
                self.qdrant_store.upsert_points(
                    [
                        {
                            "id": memory_id,
                            "vector": embedding,
                            "payload": self._point_payload(meta),
                        }
                    ]
                )

                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                if "text" in updated_fields:
                    self._rebuild_bm25()

        return {"id": memory_id, "updated_fields": updated_fields}

    def upsert_memory(
        self,
        text: str,
        source: str,
        key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Upsert a memory by stable entity key + source."""
        metadata = dict(metadata or {})
        metadata["entity_key"] = key

        existing_id: Optional[int] = None
        for m in self.metadata:
            if m.get("source") == source and m.get("entity_key") == key:
                existing_id = int(m["id"])
                break

        if existing_id is None:
            ids = self.add_memories(
                texts=[text],
                sources=[source],
                metadata_list=[metadata],
                deduplicate=False,
            )
            return {"id": ids[0], "action": "created"}

        result = self.update_memory(
            memory_id=existing_id,
            text=text,
            source=source,
            metadata_patch=metadata,
        )
        return {"id": result["id"], "action": "updated"}

    def upsert_memories(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Upsert multiple memories by stable keys."""
        created = 0
        updated = 0
        errors = 0
        results: List[Dict[str, Any]] = []

        for entry in entries:
            try:
                result = self.upsert_memory(
                    text=entry["text"],
                    source=entry["source"],
                    key=entry["key"],
                    metadata=entry.get("metadata"),
                )
                results.append(result)
                if result["action"] == "created":
                    created += 1
                else:
                    updated += 1
            except Exception:
                errors += 1

        return {
            "created": created,
            "updated": updated,
            "errors": errors,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        k: int = 5,
        threshold: Optional[float] = None,
        source_prefix: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Vector-only search for similar memories."""
        if not self.metadata:
            return []

        k = min(k, len(self.metadata), 100)

        query_vec = self._encode(
            [query],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0].astype("float32").tolist()

        hits = self.qdrant_store.search(
            query_vector=query_vec,
            limit=k,
            score_threshold=threshold,
            consistency=self.qdrant_settings.read_consistency,
        )

        results: List[Dict[str, Any]] = []
        for hit in hits:
            mem_id = hit.get("id")
            if not isinstance(mem_id, int) or not self._id_exists(mem_id):
                continue

            meta = self._get_meta_by_id(mem_id)
            source = str(meta.get("source", ""))
            if source_prefix and not source.startswith(source_prefix):
                continue

            similarity = float(hit.get("score", 0.0))
            if threshold is not None and similarity < threshold:
                continue

            result = {**meta, "similarity": round(similarity, 6)}
            results.append(result)

        return results

    def hybrid_search(
        self,
        query: str,
        k: int = 5,
        threshold: Optional[float] = None,
        vector_weight: float = 0.7,
        source_prefix: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid BM25 + vector search with Reciprocal Rank Fusion."""
        if not self.metadata:
            return []

        k = min(k, len(self.metadata), 100)
        oversample = min(k * 3, len(self.metadata))

        vector_results = self.search(
            query=query,
            k=oversample,
            threshold=threshold,
            source_prefix=source_prefix,
        )

        bm25_ranked = []
        if self.bm25_index is not None:
            tokenized = query.lower().split()
            bm25_scores = self.bm25_index.get_scores(tokenized)
            if source_prefix:
                bm25_ranked = [
                    (pos, score)
                    for pos, score in enumerate(bm25_scores)
                    if pos < len(self._bm25_pos_to_id)
                    and self._get_meta_by_id(self._bm25_pos_to_id[pos]).get("source", "").startswith(source_prefix)
                ]
                bm25_ranked = sorted(bm25_ranked, key=lambda x: x[1], reverse=True)[:oversample]
            else:
                bm25_ranked = sorted(enumerate(bm25_scores), key=lambda x: x[1], reverse=True)[:oversample]

        rrf_k = 60
        rrf_scores: Dict[int, float] = {}

        for rank, result in enumerate(vector_results):
            doc_id = result["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + vector_weight * (1.0 / (rank + rrf_k))

        bm25_weight = 1.0 - vector_weight
        for rank, (pos, score) in enumerate(bm25_ranked):
            if score > 0 and pos < len(self._bm25_pos_to_id):
                doc_id = self._bm25_pos_to_id[pos]
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + bm25_weight * (1.0 / (rank + rrf_k))

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:k]

        results = []
        for doc_id, rrf_score in sorted_ids:
            if self._id_exists(doc_id):
                meta = self._get_meta_by_id(doc_id)
                result = {**meta, "rrf_score": round(rrf_score, 6)}
                if threshold is not None:
                    vec_match = next((r for r in vector_results if r["id"] == doc_id), None)
                    if vec_match and vec_match["similarity"] < threshold:
                        continue
                results.append(result)

        return results

    def is_novel(self, text: str, threshold: float = 0.88) -> Tuple[bool, Optional[Dict]]:
        """Check if text is novel (not too similar to existing memories)."""
        results = self.search(text, k=1)
        if not results:
            return True, None
        top_match = results[0]
        return top_match["similarity"] < threshold, top_match

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def find_duplicates(self, threshold: float = 0.90) -> List[Dict[str, Any]]:
        """Find all near-duplicate pairs in memory texts."""
        if len(self.metadata) < 2:
            return []

        all_embeddings = self._encode(
            [m["text"] for m in self.metadata],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")

        similarities = np.matmul(all_embeddings, all_embeddings.T)
        search_k = min(5, len(self.metadata))
        id_list = [m["id"] for m in self.metadata]

        duplicates: List[Dict[str, Any]] = []
        seen = set()

        for i in range(len(self.metadata)):
            row = similarities[i]
            nearest = np.argsort(row)[::-1]
            neighbors = [j for j in nearest if j != i][:search_k]
            for j in neighbors:
                sim = float(row[j])
                id_a, id_b = id_list[i], id_list[int(j)]
                pair_key = (min(id_a, id_b), max(id_a, id_b))
                if sim >= threshold and pair_key not in seen:
                    seen.add(pair_key)
                    duplicates.append(
                        {
                            "id_a": pair_key[0],
                            "id_b": pair_key[1],
                            "similarity": round(sim, 4),
                            "text_a": self._get_meta_by_id(pair_key[0])["text"][:120],
                            "text_b": self._get_meta_by_id(pair_key[1])["text"][:120],
                        }
                    )

        return sorted(duplicates, key=lambda x: x["similarity"], reverse=True)

    def deduplicate(self, threshold: float = 0.90, dry_run: bool = True) -> Dict[str, Any]:
        """Remove near-duplicate memories, keeping the earliest entry."""
        pairs = self.find_duplicates(threshold)
        if not pairs:
            return {"duplicate_pairs": 0, "removed": 0, "dry_run": dry_run}

        ids_to_remove = set()
        for pair in pairs:
            ids_to_remove.add(max(pair["id_a"], pair["id_b"]))

        if dry_run:
            return {
                "duplicate_pairs": len(pairs),
                "would_remove": len(ids_to_remove),
                "dry_run": True,
                "pairs": pairs[:20],
            }

        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                self._backup(prefix="pre_dedup")

                self._delete_ids_targeted(ids_to_remove)

                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {
            "duplicate_pairs": len(pairs),
            "removed": len(ids_to_remove),
            "remaining": len(self.metadata),
            "dry_run": False,
        }

    # ------------------------------------------------------------------
    # Browse / List
    # ------------------------------------------------------------------

    def count_memories(self, source_prefix: Optional[str] = None) -> int:
        """Count memories, optionally filtered by source prefix."""
        if not source_prefix:
            return len(self.metadata)
        return sum(1 for m in self.metadata if m.get("source", "").startswith(source_prefix))

    def list_memories(
        self,
        offset: int = 0,
        limit: int = 20,
        source_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List memories with pagination and optional source filter."""
        filtered = self.metadata
        if source_filter:
            filtered = [m for m in filtered if m.get("source", "").startswith(source_filter)]

        total = len(filtered)
        page = filtered[offset : offset + limit]

        return {
            "memories": page,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        """Persist metadata/config to disk."""
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, indent=2)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def load(self, rebuild_on_mismatch: bool = False):
        """Load metadata/config and validate against Qdrant state."""
        with open(self.metadata_path, encoding="utf-8") as f:
            self.metadata = json.load(f)
        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as f:
                self.config.update(json.load(f))

        self.config["model"] = self._active_embed_model()
        self.config["embed_provider"] = self._embed_provider
        self.config["dimension"] = self.dim

        self._rebuild_id_map()

        # Hold write lock during reindex to prevent concurrent mutations (Option C)
        with self._write_lock:
            collection_dim = self.qdrant_store.get_collection_dimension()
            if collection_dim is not None and collection_dim != self.dim:
                if self.metadata:
                    logger.info(
                        "Embedding dimension changed (%d -> %d). Rebuilding vectors from metadata.",
                        collection_dim,
                        self.dim,
                    )
                    self._reindex_store_from_metadata()
                else:
                    logger.info(
                        "Embedding dimension changed (%d -> %d) with empty metadata. Recreating collection.",
                        collection_dim,
                        self.dim,
                    )
                    self.qdrant_store.recreate_collection(self.dim)

            total_points = self.qdrant_store.count()
            if total_points == 0 and self.metadata:
                logger.info("Qdrant collection empty with existing metadata. Rebuilding vectors.")
                self._reindex_store_from_metadata()
            elif rebuild_on_mismatch and total_points != len(self.metadata):
                logger.info(
                    "Qdrant mismatch during restore/load (%d vs %d). Rebuilding vectors.",
                    total_points,
                    len(self.metadata),
                )
                self._reindex_store_from_metadata()

        self._check_integrity()
        self._rebuild_bm25()
        self._migrate_timestamps()

    def rebuild_from_files(self, file_paths: List[str]) -> Dict[str, Any]:
        """Rebuild index from markdown files using proper chunking."""
        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                backup_path = self._backup(prefix="pre_rebuild")

                self.metadata = []

                texts = []
                sources = []
                files_processed = 0

                for file_path in file_paths:
                    path = Path(file_path)
                    if not path.exists():
                        continue
                    try:
                        content = path.read_text()
                        chunks = self.chunk_markdown(content, path.name)
                        for chunk_text, chunk_source in chunks:
                            texts.append(chunk_text)
                            sources.append(chunk_source)
                        files_processed += 1
                    except Exception as e:
                        logger.error("Error reading %s: %s", file_path, e)

                if texts:
                    embeddings = self._encode(
                        texts,
                        normalize_embeddings=True,
                        show_progress_bar=False,
                    )

                    self.qdrant_store.recreate_collection(self.dim)

                    points = []
                    for i, (text, source) in enumerate(zip(texts, sources)):
                        meta = {
                            "id": i,
                            "text": text,
                            "source": source,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        self.metadata.append(meta)
                        points.append(
                            {
                                "id": i,
                                "vector": embeddings[i].astype("float32").tolist(),
                                "payload": self._point_payload(meta),
                            }
                        )

                    self.qdrant_store.upsert_points(points)
                    self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                    self.save()
                    self._rebuild_bm25()
                else:
                    self.qdrant_store.recreate_collection(self.dim)
                    self.save()
                    self._rebuild_bm25()

        return {
            "files_processed": files_processed,
            "memories_added": len(texts),
            "backup_location": str(backup_path),
        }

    def stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        qdrant_size = 0
        if self._qdrant_local_path.exists():
            for path in self._qdrant_local_path.rglob("*"):
                if path.is_file():
                    qdrant_size += path.stat().st_size

        return {
            "total_memories": len(self.metadata),
            "dimension": self.dim,
            "model": self.config.get("model"),
            "created_at": self.config.get("created_at"),
            "last_updated": self.config.get("last_updated"),
            "index_size_bytes": qdrant_size,
            "backup_count": len(list(self.backup_dir.glob("*_*"))),
        }

    def stats_light(self) -> Dict[str, Any]:
        """Cheap stats for health checks (no filesystem I/O)."""
        return {
            "total_memories": len(self.metadata),
            "dimension": self.dim,
            "model": self.config.get("model"),
        }

    def is_ready(self) -> Dict[str, Any]:
        """Readiness probe for cutover and orchestration."""
        try:
            qdrant_count = self.qdrant_store.count()
            metadata_count = len(self.metadata)
            ready = qdrant_count == metadata_count
            return {
                "ready": ready,
                "status": "ready" if ready else "degraded",
                "qdrant_count": qdrant_count,
                "metadata_count": metadata_count,
            }
        except Exception as exc:
            return {
                "ready": False,
                "status": "error",
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Public API methods (for API endpoints)
    # ------------------------------------------------------------------

    def create_backup(self, prefix: str = "manual") -> Path:
        """Create a manual backup with optional prefix."""
        return self._backup(prefix=prefix)

    def _active_embed_model(self) -> str:
        if self._embed_provider == "openai":
            return self._embed_model or "text-embedding-3-small"
        return self._embed_model or self._model_name

    def _make_embedder(self):
        """Instantiate the configured embedding provider."""
        if self._embed_provider == "onnx":
            from onnx_embedder import OnnxEmbedder
            model = self._active_embed_model()
            logger.info("Embedder: provider=onnx, model=%s", model)
            return OnnxEmbedder(model, cache_dir=self._embedder_cache_dir)

        if self._embed_provider == "openai":
            from openai_embedder import OpenAIEmbedder
            model = self._active_embed_model()
            api_key = os.getenv("OPENAI_API_KEY", "").strip() or None
            if not api_key:
                raise ValueError(
                    "EMBED_PROVIDER=openai requires OPENAI_API_KEY to be set"
                )
            logger.info("Embedder: provider=openai, model=%s", model)
            return OpenAIEmbedder(model, api_key=api_key)

        raise ValueError(
            f"Unknown EMBED_PROVIDER={self._embed_provider!r}. "
            "Valid values: openai, onnx"
        )

    def reload_embedder(self) -> Dict[str, Any]:
        """Recreate embedder runtime and release old inference objects."""
        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                with self._embedder_lock:
                    old_model = self.model
                    new_model = self._make_embedder()
                    new_dim = new_model.get_sentence_embedding_dimension()
                    if new_dim != self.dim:
                        close_fn = getattr(new_model, "close", None)
                        if callable(close_fn):
                            close_fn()
                        raise RuntimeError(
                            f"Embedder dimension mismatch: current={self.dim} new={new_dim}"
                        )

                    self.model = new_model
                    self.dim = new_dim
                    self.config["model"] = self._active_embed_model()
                    self.config["embed_provider"] = self._embed_provider
                    self.config["dimension"] = new_dim
                    self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                    self.save()

        close_fn = getattr(old_model, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception as exc:
                logger.warning("Failed to close old embedder runtime: %s", exc)

        gc_collected = gc.collect()
        return {
            "reloaded": True,
            "model": self.config.get("model"),
            "dimension": self.dim,
            "gc_collected": gc_collected,
        }

    def get_cloud_sync(self) -> Optional["CloudSync"]:
        """Get cloud sync client (None if not available/enabled)."""
        return self.cloud_sync

    def get_backup_dir(self) -> Path:
        """Get backup directory path."""
        return self.backup_dir
