"""
Memories Engine
Local semantic search with hybrid BM25+vector retrieval, markdown-aware
chunking, automatic backups, and concurrency safety.
"""

import json
import logging
import math
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
from qdrant_client import models as qdrant_models
from event_bus import event_bus
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
        self.qdrant_store.ensure_payload_indexes()

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

    def reload_from_qdrant(self):
        """Rebuild in-memory state (metadata, _id_map, BM25) from Qdrant points.

        Used after restoring a snapshot so the engine reflects the restored data.
        """
        with self._write_lock:
            all_points: list = []
            offset = None
            while True:
                points, next_offset = self.qdrant_store.scroll_all(offset=offset, limit=100)
                all_points.extend(points)
                if next_offset is None:
                    break
                offset = next_offset

            self.metadata = []
            for p in all_points:
                meta = dict(p["payload"])
                meta["id"] = p["id"]
                self.metadata.append(meta)

            self._rebuild_id_map()
            self._rebuild_bm25()
            self.save()

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
        if not self.metadata:
            self.qdrant_store.recreate_collection(self.dim)
            return

        # Phase 1: embed all texts BEFORE touching the collection.
        # If embedding fails, the original collection is untouched.
        texts = [m["text"] for m in self.metadata]
        embeddings = self._encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        # Phase 2: cache all points in memory before destructive recreate.
        batch_size = 256
        all_points: list = []
        for i in range(len(self.metadata)):
            all_points.append(
                {
                    "id": self.metadata[i]["id"],
                    "vector": embeddings[i].astype("float32").tolist(),
                    "payload": self._point_payload(self.metadata[i]),
                }
            )

        # Phase 3: recreate collection and upsert.
        # If upsert fails mid-way, we still have all_points in memory and
        # can attempt to rebuild.
        self.qdrant_store.recreate_collection(self.dim)
        try:
            for start in range(0, len(all_points), batch_size):
                end = min(start + batch_size, len(all_points))
                self.qdrant_store.upsert_points(all_points[start:end])
        except Exception:
            # Upsert failed — attempt to restore from cached points
            logger.error("Re-embed upsert failed; attempting rollback from cached points")
            try:
                self.qdrant_store.recreate_collection(self.dim)
                for start in range(0, len(all_points), batch_size):
                    end = min(start + batch_size, len(all_points))
                    self.qdrant_store.upsert_points(all_points[start:end])
            except Exception:
                logger.error("Rollback also failed; collection may be inconsistent")
            raise

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

        for mid in added_ids:
            if self._id_exists(mid):
                meta = self._get_meta_by_id(mid)
                event_bus.emit("memory.added", {"id": mid, "source": meta.get("source", ""), "text": meta.get("text", "")[:200]})

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

                # Scrub incoming links referencing this memory
                self._scrub_links_to(memory_id)

                deleted = dict(self._get_meta_by_id(memory_id))
                self._delete_ids_targeted({memory_id})

                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        event_bus.emit("memory.deleted", {"id": memory_id, "source": deleted.get("source", "")})
        return {"deleted_id": memory_id, "deleted_text": deleted["text"][:100]}

    def _snapshot_before_delete(self, reason: str) -> str:
        """Create a Qdrant snapshot and record it in the manifest."""
        name = self.qdrant_store.create_snapshot()
        snapshots_dir = self.data_dir / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = snapshots_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                entries = json.load(f)
        else:
            entries = []
        entries.append({
            "name": name,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "point_count": self.qdrant_store.count(),
        })
        with open(manifest_path, "w") as f:
            json.dump(entries, f)
        return name

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """Return entries from the snapshot manifest."""
        manifest_path = self.data_dir / "snapshots" / "manifest.json"
        if not manifest_path.exists():
            return []
        with open(manifest_path) as f:
            return json.load(f)

    def delete_memories(self, memory_ids: List[int], skip_snapshot: bool = False) -> Dict[str, Any]:
        """Delete multiple memories by IDs in one pass."""
        unique_ids = sorted(set(memory_ids))
        if not unique_ids:
            return {"deleted_count": 0, "deleted_ids": [], "missing_ids": []}

        existing = [mid for mid in unique_ids if self._id_exists(mid)]
        existing_set = set(existing)
        missing = [mid for mid in unique_ids if mid not in existing_set]
        if not existing:
            return {"deleted_count": 0, "deleted_ids": [], "missing_ids": missing}

        if len(unique_ids) > 10 and not skip_snapshot:
            self._snapshot_before_delete("pre_delete_batch")

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

    def merge_memories(self, ids: List[int], merged_text: str, source: str) -> Dict[str, Any]:
        """Merge multiple memories into one, archiving originals and linking via supersedes."""
        if len(ids) < 2:
            raise ValueError("merge_memories requires at least 2 IDs")
        for mid in ids:
            if not self._id_exists(mid):
                raise ValueError(f"Memory {mid} not found")

        added_ids = self.add_memories(texts=[merged_text], sources=[source], deduplicate=False)
        new_id = added_ids[0]

        for mid in ids:
            self.add_link(new_id, mid, "supersedes")

        for mid in ids:
            meta = self._get_meta_by_id(mid)
            meta["archived"] = True
            self.qdrant_store.set_payload(mid, {"archived": True})

        self.save()
        logger.info("Merged memories %s → %d", ids, new_id)
        return {"id": new_id, "archived": ids}

    # ------------------------------------------------------------------
    # Memory Links (lightweight graph edges)
    # ------------------------------------------------------------------

    VALID_LINK_TYPES = {"supersedes", "related_to", "blocked_by", "caused_by", "reinforces"}

    def add_link(self, from_id: int, to_id: int, link_type: str) -> Dict[str, Any]:
        """Add a typed directional link between two memories."""
        if link_type not in self.VALID_LINK_TYPES:
            raise ValueError(f"Invalid link type: {link_type}. Valid types: {sorted(self.VALID_LINK_TYPES)}")
        if from_id == to_id:
            raise ValueError("A memory cannot link to itself")
        if not self._id_exists(from_id):
            raise ValueError(f"Source memory {from_id} not found")
        if not self._id_exists(to_id):
            raise ValueError(f"Target memory {to_id} not found")

        meta = self._get_meta_by_id(from_id)
        links = meta.setdefault("links", [])

        # Check for duplicate
        if any(l["to_id"] == to_id and l["type"] == link_type for l in links):
            raise ValueError(f"Link {from_id} --{link_type}--> {to_id} already exists")

        created_at = datetime.now(timezone.utc).isoformat()
        link = {"to_id": to_id, "type": link_type, "created_at": created_at}
        links.append(link)
        self.save()

        logger.info("Link added: %d --%s--> %d", from_id, link_type, to_id)
        event_bus.emit("memory.linked", {"from_id": from_id, "to_id": to_id, "type": link_type, "source": meta.get("source", "")})
        return {"from_id": from_id, "to_id": to_id, "type": link_type, "created_at": created_at}

    def remove_link(self, from_id: int, to_id: int, link_type: str) -> Dict[str, Any]:
        """Remove a specific link between two memories."""
        if not self._id_exists(from_id):
            return {"removed": False}

        meta = self._get_meta_by_id(from_id)
        links = meta.get("links", [])
        original_len = len(links)
        meta["links"] = [l for l in links if not (l["to_id"] == to_id and l["type"] == link_type)]

        removed = len(meta["links"]) < original_len
        if removed:
            if not meta["links"]:
                del meta["links"]
            self.save()
            logger.info("Link removed: %d --%s--> %d", from_id, link_type, to_id)

        return {"removed": removed}

    def _scrub_links_to(self, target_id: int) -> None:
        """Remove all incoming links pointing to target_id from other memories."""
        for m in self.metadata:
            links = m.get("links")
            if not links:
                continue
            filtered = [l for l in links if l.get("to_id") != target_id]
            if len(filtered) < len(links):
                if filtered:
                    m["links"] = filtered
                else:
                    del m["links"]

    def get_links(
        self,
        memory_id: int,
        link_type: Optional[str] = None,
        include_incoming: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get links for a memory. Outgoing by default; optionally include incoming."""
        results = []

        # Outgoing links (stored on this memory, skip dangling targets)
        if self._id_exists(memory_id):
            meta = self._get_meta_by_id(memory_id)
            for link in meta.get("links", []):
                if link_type and link["type"] != link_type:
                    continue
                if not self._id_exists(link["to_id"]):
                    continue  # Target was deleted
                results.append({**link, "from_id": memory_id, "direction": "outgoing"})

        # Incoming links (scan other memories)
        if include_incoming:
            for m in self.metadata:
                if m["id"] == memory_id:
                    continue
                for link in m.get("links", []):
                    if link["to_id"] != memory_id:
                        continue
                    if link_type and link["type"] != link_type:
                        continue
                    results.append({**link, "from_id": m["id"], "direction": "incoming"})

        return results

    def delete_by_source(self, source_pattern: str, skip_snapshot: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """Delete all memories matching a source pattern."""
        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                matching = [m for m in self.metadata
                           if source_pattern in m.get("source", "")
                           and not m.get("pinned")]
                if not matching:
                    if dry_run:
                        return {"count": 0, "would_delete": []}
                    return {"deleted_count": 0}

                if dry_run:
                    return {"count": len(matching), "would_delete": [m["id"] for m in matching]}

                if not skip_snapshot:
                    self._snapshot_before_delete("pre_delete_source")

                self._backup(prefix="pre_delete_source")

                ids_to_remove = {m["id"] for m in matching}
                self._delete_ids_targeted(ids_to_remove)

                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {"deleted_count": len(matching)}

    def delete_by_prefix(self, source_prefix: str, skip_snapshot: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """Delete all memories whose source starts with a prefix."""
        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                matching = [m for m in self.metadata
                           if m.get("source", "").startswith(source_prefix)
                           and not m.get("pinned")]
                if not matching:
                    if dry_run:
                        return {"count": 0, "would_delete": []}
                    return {"deleted_count": 0}

                if dry_run:
                    return {"count": len(matching), "would_delete": [m["id"] for m in matching]}

                if not skip_snapshot:
                    self._snapshot_before_delete("pre_delete_prefix")

                self._backup(prefix="pre_delete_prefix")
                ids_to_remove = {m["id"] for m in matching}
                self._delete_ids_targeted(ids_to_remove)
                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {"deleted_count": len(matching)}

    @staticmethod
    def compute_confidence(
        anchor_timestamp: Optional[str],
        half_life_days: float = 90.0,
    ) -> float:
        """Compute confidence score based on time since last reinforcement.

        Uses exponential decay from the anchor timestamp (typically updated_at
        or created_at). Returns 1.0 for fresh memories, 0.5 after one half-life.
        """
        if not anchor_timestamp:
            return 0.0
        try:
            ts = datetime.fromisoformat(anchor_timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
            if age_days < 0:
                return 1.0
            if half_life_days <= 0:
                half_life_days = 90.0
            return math.pow(0.5, age_days / half_life_days)
        except (ValueError, TypeError, ZeroDivisionError):
            return 0.0

    def reinforce(self, memory_id: int) -> None:
        """Reinforce a memory by updating its timestamp, resetting decay clock."""
        if not self._id_exists(memory_id):
            return
        meta = self._get_meta_by_id(memory_id)
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()

    def _enrich_with_confidence(self, mem: Dict[str, Any]) -> Dict[str, Any]:
        """Add computed confidence to a memory dict."""
        anchor = mem.get("updated_at") or mem.get("created_at") or mem.get("timestamp")
        # Resolve per-prefix half-life
        half_life = 90.0  # default
        if hasattr(self, '_profiles') and self._profiles:
            source = mem.get("source", "")
            resolved = self._profiles.resolve(source)
            if resolved.get("confidence_half_life_days") is not None:
                half_life = resolved["confidence_half_life_days"]
        mem["confidence"] = round(self.compute_confidence(anchor, half_life_days=half_life), 4)
        return mem

    def get_memory(self, memory_id: int) -> Dict[str, Any]:
        """Fetch a single memory by ID, with computed confidence."""
        mem = dict(self._get_meta_by_id(memory_id))
        return self._enrich_with_confidence(mem)

    def get_memories(self, memory_ids: List[int]) -> Dict[str, Any]:
        """Fetch multiple memories by ID."""
        memories: List[Dict[str, Any]] = []
        missing_ids: List[int] = []
        for memory_id in memory_ids:
            if self._id_exists(memory_id):
                memories.append(self._enrich_with_confidence(dict(self._get_meta_by_id(memory_id))))
            else:
                missing_ids.append(memory_id)
        return {"memories": memories, "missing_ids": missing_ids}

    def update_memory(
        self,
        memory_id: int,
        text: Optional[str] = None,
        source: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
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
            and pinned is None
            and archived is None
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
                        if key in _reserved or key.startswith("_policy_"):
                            continue
                        meta[key] = value
                    updated_fields.append("metadata")

                if pinned is not None:
                    meta["pinned"] = pinned
                    self.qdrant_store.set_payload(memory_id, {"pinned": pinned})
                    updated_fields.append("pinned")

                if archived is not None:
                    meta["archived"] = archived
                    self.qdrant_store.set_payload(memory_id, {"archived": archived})
                    updated_fields.append("archived")

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

        mem_source = meta.get("source", "")
        event_bus.emit("memory.updated", {"id": memory_id, "updated_fields": updated_fields, "source": mem_source})
        return {"id": memory_id, "updated_fields": updated_fields}

    def enforce_policies(self, dry_run: bool = True) -> Dict[str, Any]:
        """Evaluate each memory against its resolved policy. Archive candidates if not dry_run."""
        actions: List[Dict[str, Any]] = []
        excluded_pinned = 0
        excluded_archived = 0
        candidates_scanned = 0
        by_rule: Dict[str, int] = {"ttl": 0, "confidence": 0}

        now = datetime.now(timezone.utc)

        for mem in self.metadata:
            if not mem:
                continue
            if mem.get("archived"):
                excluded_archived += 1
                continue
            if mem.get("pinned"):
                excluded_pinned += 1
                continue

            candidates_scanned += 1
            source = mem.get("source", "")
            policy = self._profiles.resolve(source) if hasattr(self, '_profiles') and self._profiles else {}

            # Compute age
            anchor = mem.get("updated_at") or mem.get("created_at") or mem.get("timestamp")
            age_days = 0
            if anchor:
                try:
                    ts = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age_days = (now - ts).days
                except (ValueError, TypeError):
                    pass

            # Compute confidence with per-prefix half-life
            half_life = policy.get("confidence_half_life_days") or 90.0
            confidence = self.compute_confidence(anchor, half_life_days=half_life)

            reasons: List[Dict[str, Any]] = []

            # TTL check
            ttl = policy.get("ttl_days")
            if ttl is not None and age_days > ttl:
                reasons.append({"rule": "ttl", "ttl_days": ttl, "age_days": age_days, "prefix": source})
                by_rule["ttl"] += 1

            # Confidence check
            threshold = policy.get("confidence_threshold")
            min_age = policy.get("min_age_days")
            if threshold is not None and min_age is not None:
                if confidence < threshold and age_days > min_age:
                    reasons.append({
                        "rule": "confidence", "threshold": threshold,
                        "confidence": round(confidence, 4), "min_age_days": min_age, "prefix": source,
                    })
                    by_rule["confidence"] += 1

            if not reasons:
                continue

            action_type = "would_archive" if dry_run else "archived"
            actions.append({
                "memory_id": mem["id"],
                "source": source,
                "age_days": age_days,
                "confidence": round(confidence, 4),
                "reasons": reasons,
                "action": action_type,
            })

        # Execute if not dry_run — acquire locks to protect shared state
        if not dry_run and actions:
            with self._entity_locks.acquire_many(["__all__"]):
                with self._write_lock:
                    archived_at = now.isoformat()
                    for a in actions:
                        mem_id = a["memory_id"]
                        primary_reason = a["reasons"][0]  # TTL takes precedence
                        evidence = {
                            "_policy_archived_reason": primary_reason["rule"],
                            "_policy_archived_policy": f"{primary_reason.get('prefix', '')} {primary_reason['rule']}",
                            "_policy_archived_at": archived_at,
                            "_policy_archived_confidence": a["confidence"],
                            "_policy_archived_age_days": a["age_days"],
                        }
                        # Direct meta update (bypasses _policy_ protection since we're the policy engine)
                        meta = self._get_meta_by_id(mem_id)
                        meta["archived"] = True
                        meta.update(evidence)
                        # Set archived in Qdrant payload
                        self.qdrant_store.set_payload(mem_id, {"archived": True, **evidence})
                    self.save()

        summary_key = "would_archive" if dry_run else "archived"
        return {
            "dry_run": dry_run,
            "actions": actions,
            "summary": {
                "candidates_scanned": candidates_scanned,
                summary_key: len(actions),
                "by_rule": by_rule,
                "excluded_pinned": excluded_pinned,
                "excluded_already_archived": excluded_archived,
            },
        }

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

    def distinct_sources(self) -> List[str]:
        """Return sorted list of unique source values across all memories."""
        return sorted({str(m.get("source", "")) for m in self.metadata})

    def _build_source_filter(
        self,
        source_prefix: Optional[str] = None,
        allowed_prefixes: Optional[List[str]] = None,
        include_archived: bool = False,
    ) -> Optional[qdrant_models.Filter]:
        """Build a Qdrant filter from source prefix, auth prefixes, and archive state.

        Returns None when no filtering is needed (admin / no prefix / include_archived),
        letting the existing call path behave exactly as before.
        """
        filter_obj: Optional[qdrant_models.Filter] = None

        if source_prefix is not None or allowed_prefixes is not None:
            all_sources = self.distinct_sources()

            # Narrow by source_prefix first
            if source_prefix:
                candidates = [s for s in all_sources if s.startswith(source_prefix)]
            else:
                candidates = all_sources

            # Further narrow by auth allowed_prefixes
            if allowed_prefixes is not None:
                from auth_context import source_matches_prefixes
                candidates = [s for s in candidates if source_matches_prefixes(s, allowed_prefixes)]

            if not candidates:
                filter_obj = qdrant_models.Filter(
                    must=[qdrant_models.FieldCondition(
                        key="source",
                        match=qdrant_models.MatchValue(value="__no_match__"),
                    )]
                )
            else:
                filter_obj = qdrant_models.Filter(
                    must=[qdrant_models.FieldCondition(
                        key="source",
                        match=qdrant_models.MatchAny(any=candidates),
                    )]
                )

        # Exclude archived memories unless explicitly requested
        must_not = []
        if not include_archived:
            must_not.append(
                qdrant_models.FieldCondition(
                    key="archived",
                    match=qdrant_models.MatchValue(value=True),
                )
            )
        if must_not:
            if filter_obj is None:
                filter_obj = qdrant_models.Filter(must_not=must_not)
            else:
                existing_must_not = list(filter_obj.must_not or [])
                existing_must_not.extend(must_not)
                filter_obj.must_not = existing_must_not

        return filter_obj

    def search(
        self,
        query: str,
        k: int = 5,
        threshold: Optional[float] = None,
        source_prefix: Optional[str] = None,
        include_archived: bool = False,
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

        # Pre-filter at Qdrant level when source_prefix is specified
        query_filter = self._build_source_filter(source_prefix=source_prefix, include_archived=include_archived)

        hits = self.qdrant_store.search(
            query_vector=query_vec,
            limit=k,
            score_threshold=threshold,
            consistency=self.qdrant_settings.read_consistency,
            query_filter=query_filter,
        )

        results: List[Dict[str, Any]] = []
        for hit in hits:
            mem_id = hit.get("id")
            if not isinstance(mem_id, int) or not self._id_exists(mem_id):
                continue

            meta = self._get_meta_by_id(mem_id)
            source = str(meta.get("source", ""))
            # Defense-in-depth: still verify source prefix in Python
            if source_prefix and not source.startswith(source_prefix):
                continue

            similarity = float(hit.get("score", 0.0))
            if threshold is not None and similarity < threshold:
                continue

            result = self._enrich_with_confidence({**meta, "similarity": round(similarity, 6)})
            results.append(result)
            self.reinforce(mem_id)

        return results

    @staticmethod
    def _recency_score(
        created_at: Optional[str],
        half_life_days: float = 30.0,
    ) -> float:
        """Exponential decay score based on memory age.

        Returns 1.0 for now, 0.5 after one half-life, 0.25 after two, etc.
        Returns 0.0 for missing or unparseable timestamps.
        """
        if not created_at:
            return 0.0
        try:
            ts = datetime.fromisoformat(created_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
            if age_days < 0:
                return 1.0  # Future timestamps clamped
            if half_life_days <= 0:
                half_life_days = 30.0  # Defensive clamp
            return math.pow(0.5, age_days / half_life_days)
        except (ValueError, TypeError, ZeroDivisionError):
            return 0.0

    def hybrid_search(
        self,
        query: str,
        k: int = 5,
        threshold: Optional[float] = None,
        vector_weight: float = 0.7,
        source_prefix: Optional[str] = None,
        recency_weight: float = 0.0,
        recency_half_life_days: float = 30.0,
        include_archived: bool = False,
        feedback_weight: float = 0.0,
        feedback_scores: Optional[Dict[int, int]] = None,
        confidence_weight: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Hybrid BM25 + vector search with Reciprocal Rank Fusion.

        When recency_weight > 0, a third recency signal is blended into RRF
        scoring. The vector_weight and bm25_weight are scaled down proportionally
        so that all weights sum to 1.0. With recency_weight=0.0 (default),
        behavior is identical to before.
        """
        if not self.metadata:
            return []

        k = min(k, len(self.metadata), 100)
        oversample = min(k * 3, len(self.metadata))

        vector_results = self.search(
            query=query,
            k=oversample,
            threshold=threshold,
            source_prefix=source_prefix,
            include_archived=include_archived,
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
                    and (include_archived or not self._get_meta_by_id(self._bm25_pos_to_id[pos]).get("archived"))
                ]
                bm25_ranked = sorted(bm25_ranked, key=lambda x: x[1], reverse=True)[:oversample]
            else:
                bm25_ranked = [
                    (pos, score)
                    for pos, score in enumerate(bm25_scores)
                    if pos < len(self._bm25_pos_to_id)
                    and (include_archived or not self._get_meta_by_id(self._bm25_pos_to_id[pos]).get("archived"))
                ]
                bm25_ranked = sorted(bm25_ranked, key=lambda x: x[1], reverse=True)[:oversample]

        rrf_k = 60
        rrf_scores: Dict[int, float] = {}

        # Validate recency params at engine level (defense-in-depth)
        recency_weight = max(0.0, min(1.0, recency_weight))
        if recency_half_life_days <= 0:
            recency_half_life_days = 30.0

        # 5-signal weight scaling (vector + BM25 + recency + feedback + confidence = 1.0)
        feedback_weight = max(0.0, min(1.0, feedback_weight))
        confidence_weight = max(0.0, min(1.0, confidence_weight))
        total_auxiliary = feedback_weight + confidence_weight
        if total_auxiliary > 1.0:
            feedback_weight = feedback_weight / total_auxiliary
            confidence_weight = confidence_weight / total_auxiliary
            total_auxiliary = 1.0
        total_core = 1.0 - total_auxiliary
        effective_vector_weight = vector_weight * total_core * (1.0 - recency_weight)
        effective_bm25_weight = (1.0 - vector_weight) * total_core * (1.0 - recency_weight)
        effective_recency_weight = recency_weight * total_core

        for rank, result in enumerate(vector_results):
            doc_id = result["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + effective_vector_weight * (1.0 / (rank + rrf_k))

        for rank, (pos, score) in enumerate(bm25_ranked):
            if score > 0 and pos < len(self._bm25_pos_to_id):
                doc_id = self._bm25_pos_to_id[pos]
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + effective_bm25_weight * (1.0 / (rank + rrf_k))

        # Blend recency as a rank-based RRF signal (same scale as vector/bm25)
        if recency_weight > 0:
            recency_scored = []
            for doc_id in rrf_scores:
                if self._id_exists(doc_id):
                    meta = self._get_meta_by_id(doc_id)
                    created_at = meta.get("created_at") or meta.get("timestamp")
                    rs = self._recency_score(created_at, half_life_days=recency_half_life_days)
                    recency_scored.append((doc_id, rs))
            # Sort by recency score descending to assign ranks
            recency_scored.sort(key=lambda x: x[1], reverse=True)
            for rank, (doc_id, _) in enumerate(recency_scored):
                rrf_scores[doc_id] += effective_recency_weight * (1.0 / (rank + rrf_k))

        # Feedback as 4th RRF signal (only positive net scores boost)
        if feedback_weight > 0 and feedback_scores:
            positive = [(doc_id, score) for doc_id, score in feedback_scores.items()
                        if score > 0 and doc_id in rrf_scores]
            positive.sort(key=lambda x: x[1], reverse=True)
            for rank, (doc_id, _) in enumerate(positive):
                rrf_scores[doc_id] += feedback_weight * (1.0 / (rank + rrf_k))

        # Confidence as 5th RRF signal (rank by confidence score descending)
        if confidence_weight > 0:
            conf_scored = []
            for doc_id in rrf_scores:
                if self._id_exists(doc_id):
                    meta = self._get_meta_by_id(doc_id)
                    anchor = meta.get("updated_at") or meta.get("created_at") or meta.get("timestamp")
                    # Per-prefix half-life from profiles if available
                    half_life = 90.0
                    profiles = getattr(self, '_profiles', None)
                    if profiles:
                        resolved = profiles.resolve(meta.get("source", ""))
                        if resolved.get("confidence_half_life_days") is not None:
                            half_life = resolved["confidence_half_life_days"]
                    conf = self.compute_confidence(anchor, half_life_days=half_life)
                    conf_scored.append((doc_id, conf))
            conf_scored.sort(key=lambda x: x[1], reverse=True)
            for rank, (doc_id, _) in enumerate(conf_scored):
                rrf_scores[doc_id] += confidence_weight * (1.0 / (rank + rrf_k))

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:k]

        results = []
        for doc_id, rrf_score in sorted_ids:
            if self._id_exists(doc_id):
                meta = self._get_meta_by_id(doc_id)
                result = self._enrich_with_confidence({**meta, "rrf_score": round(rrf_score, 6)})
                if threshold is not None:
                    vec_match = next((r for r in vector_results if r["id"] == doc_id), None)
                    if vec_match and vec_match["similarity"] < threshold:
                        continue
                results.append(result)
                self.reinforce(doc_id)

        return results

    def _search_no_reinforce(
        self,
        query: str,
        k: int = 5,
        threshold: Optional[float] = None,
        source_prefix: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        """Vector search without reinforcement side effects (for explain/debug)."""
        if not self.metadata:
            return []
        k = min(k, len(self.metadata), 100)
        query_vec = self._encode([query], normalize_embeddings=True, show_progress_bar=False)[0].astype("float32").tolist()
        query_filter = self._build_source_filter(source_prefix=source_prefix, include_archived=include_archived)
        hits = self.qdrant_store.search(
            query_vector=query_vec, limit=k, score_threshold=threshold,
            consistency=self.qdrant_settings.read_consistency, query_filter=query_filter,
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
            result = self._enrich_with_confidence({**meta, "similarity": round(similarity, 6)})
            results.append(result)
        return results

    def hybrid_search_explain(
        self,
        query: str,
        k: int = 5,
        threshold: Optional[float] = None,
        vector_weight: float = 0.7,
        source_prefix: Optional[str] = None,
        recency_weight: float = 0.0,
        recency_half_life_days: float = 30.0,
        include_archived: bool = False,
        feedback_weight: float = 0.0,
        feedback_scores: Optional[Dict[int, int]] = None,
        confidence_weight: float = 0.0,
    ) -> Dict[str, Any]:
        """Hybrid search with detailed scoring breakdown for explainability.

        Returns the same results as hybrid_search PLUS an ``explain`` dict
        containing candidate lists, filtering counts, and scoring parameters.
        """
        if not self.metadata:
            return {
                "results": [],
                "explain": {
                    "candidates_considered": 0,
                    "vector_candidates": [],
                    "bm25_candidates": [],
                    "filtered_by_source": 0,
                    "filtered_by_auth": 0,
                    "scoring_weights": {
                        "vector": vector_weight,
                        "bm25": round(1.0 - vector_weight, 4),
                        "recency": recency_weight,
                        "feedback": round(feedback_weight, 4),
                        "confidence": round(confidence_weight, 4),
                    },
                    "rrf_k": 60,
                },
            }

        k = min(k, len(self.metadata), 100)
        oversample = min(k * 3, len(self.metadata))

        # --- Vector retrieval (no reinforcement — explain is read-only) ---
        vector_results = self._search_no_reinforce(
            query=query,
            k=oversample,
            threshold=threshold,
            source_prefix=source_prefix,
            include_archived=include_archived,
        )

        vector_candidates = [
            {
                "id": r["id"],
                "text": r.get("text", "")[:200],
                "score": round(r.get("similarity", 0.0), 6),
            }
            for r in vector_results
        ]

        # --- BM25 retrieval ---
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
                    and (include_archived or not self._get_meta_by_id(self._bm25_pos_to_id[pos]).get("archived"))
                ]
                bm25_ranked = sorted(bm25_ranked, key=lambda x: x[1], reverse=True)[:oversample]
            else:
                bm25_ranked = [
                    (pos, score)
                    for pos, score in enumerate(bm25_scores)
                    if pos < len(self._bm25_pos_to_id)
                    and (include_archived or not self._get_meta_by_id(self._bm25_pos_to_id[pos]).get("archived"))
                ]
                bm25_ranked = sorted(bm25_ranked, key=lambda x: x[1], reverse=True)[:oversample]

        bm25_candidates = []
        for pos, score in bm25_ranked:
            if score > 0 and pos < len(self._bm25_pos_to_id):
                doc_id = self._bm25_pos_to_id[pos]
                if self._id_exists(doc_id):
                    meta = self._get_meta_by_id(doc_id)
                    bm25_candidates.append({
                        "id": doc_id,
                        "text": meta.get("text", "")[:200],
                        "score": round(float(score), 6),
                    })

        # Count unique candidates considered
        all_candidate_ids = set()
        for vc in vector_candidates:
            all_candidate_ids.add(vc["id"])
        for bc in bm25_candidates:
            all_candidate_ids.add(bc["id"])
        candidates_considered = len(all_candidate_ids)

        # Count how many were filtered by source prefix
        filtered_by_source = 0
        if source_prefix and self.bm25_index is not None:
            tokenized = query.lower().split()
            raw_bm25 = self.bm25_index.get_scores(tokenized)
            total_bm25_with_score = sum(1 for s in raw_bm25 if s > 0)
            bm25_after_source = len([
                pos for pos, score in enumerate(raw_bm25)
                if score > 0 and pos < len(self._bm25_pos_to_id)
                and self._get_meta_by_id(self._bm25_pos_to_id[pos]).get("source", "").startswith(source_prefix)
            ])
            filtered_by_source = total_bm25_with_score - bm25_after_source

        # --- RRF scoring (mirrors hybrid_search logic) ---
        rrf_k = 60
        rrf_scores: Dict[int, float] = {}

        recency_weight = max(0.0, min(1.0, recency_weight))
        if recency_half_life_days <= 0:
            recency_half_life_days = 30.0

        # 5-signal weight scaling (vector + BM25 + recency + feedback + confidence = 1.0)
        feedback_weight = max(0.0, min(1.0, feedback_weight))
        confidence_weight = max(0.0, min(1.0, confidence_weight))
        total_auxiliary = feedback_weight + confidence_weight
        if total_auxiliary > 1.0:
            feedback_weight = feedback_weight / total_auxiliary
            confidence_weight = confidence_weight / total_auxiliary
            total_auxiliary = 1.0
        total_core = 1.0 - total_auxiliary
        effective_vector_weight = vector_weight * total_core * (1.0 - recency_weight)
        effective_bm25_weight = (1.0 - vector_weight) * total_core * (1.0 - recency_weight)
        effective_recency_weight = recency_weight * total_core

        for rank, result in enumerate(vector_results):
            doc_id = result["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + effective_vector_weight * (1.0 / (rank + rrf_k))

        for rank, (pos, score) in enumerate(bm25_ranked):
            if score > 0 and pos < len(self._bm25_pos_to_id):
                doc_id = self._bm25_pos_to_id[pos]
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + effective_bm25_weight * (1.0 / (rank + rrf_k))

        if recency_weight > 0:
            recency_scored = []
            for doc_id in rrf_scores:
                if self._id_exists(doc_id):
                    meta = self._get_meta_by_id(doc_id)
                    created_at = meta.get("created_at") or meta.get("timestamp")
                    rs = self._recency_score(created_at, half_life_days=recency_half_life_days)
                    recency_scored.append((doc_id, rs))
            recency_scored.sort(key=lambda x: x[1], reverse=True)
            for rank, (doc_id, _) in enumerate(recency_scored):
                rrf_scores[doc_id] += effective_recency_weight * (1.0 / (rank + rrf_k))

        # Feedback as 4th RRF signal (only positive net scores boost)
        if feedback_weight > 0 and feedback_scores:
            positive = [(doc_id, score) for doc_id, score in feedback_scores.items()
                        if score > 0 and doc_id in rrf_scores]
            positive.sort(key=lambda x: x[1], reverse=True)
            for rank, (doc_id, _) in enumerate(positive):
                rrf_scores[doc_id] += feedback_weight * (1.0 / (rank + rrf_k))

        # Confidence as 5th RRF signal (rank by confidence score descending)
        if confidence_weight > 0:
            conf_scored = []
            for doc_id in rrf_scores:
                if self._id_exists(doc_id):
                    meta = self._get_meta_by_id(doc_id)
                    anchor = meta.get("updated_at") or meta.get("created_at") or meta.get("timestamp")
                    # Per-prefix half-life from profiles if available
                    half_life = 90.0
                    profiles = getattr(self, '_profiles', None)
                    if profiles:
                        resolved = profiles.resolve(meta.get("source", ""))
                        if resolved.get("confidence_half_life_days") is not None:
                            half_life = resolved["confidence_half_life_days"]
                    conf = self.compute_confidence(anchor, half_life_days=half_life)
                    conf_scored.append((doc_id, conf))
            conf_scored.sort(key=lambda x: x[1], reverse=True)
            for rank, (doc_id, _) in enumerate(conf_scored):
                rrf_scores[doc_id] += confidence_weight * (1.0 / (rank + rrf_k))

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:k]

        results = []
        for doc_id, rrf_score in sorted_ids:
            if self._id_exists(doc_id):
                meta = self._get_meta_by_id(doc_id)
                result = self._enrich_with_confidence({**meta, "rrf_score": round(rrf_score, 6)})
                if threshold is not None:
                    vec_match = next((r for r in vector_results if r["id"] == doc_id), None)
                    if vec_match and vec_match["similarity"] < threshold:
                        continue
                results.append(result)

        return {
            "results": results,
            "explain": {
                "candidates_considered": candidates_considered,
                "vector_candidates": vector_candidates,
                "bm25_candidates": bm25_candidates,
                "filtered_by_source": filtered_by_source,
                "filtered_by_auth": 0,  # populated by API layer after auth filtering
                "scoring_weights": {
                    "vector": round(vector_weight, 4),
                    "bm25": round(1.0 - vector_weight, 4),
                    "recency": round(recency_weight, 4),
                    "feedback": round(feedback_weight, 4),
                    "confidence": round(confidence_weight, 4),
                },
                "rrf_k": rrf_k,
            },
        }

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

    def find_similar_clusters(
        self,
        threshold: float = 0.85,
        min_cluster_size: int = 2,
    ) -> List[List[int]]:
        """Find clusters of similar memories above the threshold.

        Returns a list of clusters, where each cluster is a list of memory IDs
        that are mutually similar. Uses union-find on the pairwise similarity matrix.
        """
        if len(self.metadata) < min_cluster_size:
            return []

        pairs = self.find_duplicates(threshold)
        if not pairs:
            return []

        # Union-find to group connected pairs into clusters
        parent: Dict[int, int] = {}

        def find(x: int) -> int:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for pair in pairs:
            union(pair["id_a"], pair["id_b"])

        # Group by root
        groups: Dict[int, List[int]] = {}
        all_ids = {p["id_a"] for p in pairs} | {p["id_b"] for p in pairs}
        for mid in all_ids:
            root = find(mid)
            groups.setdefault(root, []).append(mid)

        return [sorted(ids) for ids in groups.values() if len(ids) >= min_cluster_size]

    # ------------------------------------------------------------------
    # Browse / List
    # ------------------------------------------------------------------

    def count_memories(self, source_prefix: Optional[str] = None) -> int:
        """Count memories, optionally filtered by source prefix."""
        if not source_prefix:
            return len(self.metadata)
        return sum(1 for m in self.metadata if m.get("source", "").startswith(source_prefix))

    def count_by_filter(
        self,
        source_prefix: Optional[str] = None,
        allowed_prefixes: Optional[List[str]] = None,
    ) -> int:
        """Count memories using Qdrant-level filtering (O(1) vs O(n) scan).

        Falls back to in-memory count if no filter is needed.
        """
        query_filter = self._build_source_filter(
            source_prefix=source_prefix,
            allowed_prefixes=allowed_prefixes,
        )
        if query_filter is None:
            return len(self.metadata)
        return self.qdrant_store.count_filtered(count_filter=query_filter)

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
        page = [self._enrich_with_confidence(dict(m)) for m in filtered[offset : offset + limit]]

        return {
            "memories": page,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    _EXPORT_STANDARD_FIELDS = {"id", "text", "source", "created_at", "updated_at", "timestamp"}

    def export_memories(
        self,
        source_prefix: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[str]:
        """Export memories as a list of NDJSON strings.

        First line is a header, remaining lines are memory records.
        IDs, embeddings, and the backward-compat ``timestamp`` alias are
        excluded so the output is portable across instances.
        """
        filtered = self.metadata

        if source_prefix:
            filtered = [m for m in filtered if m.get("source", "").startswith(source_prefix)]

        if since:
            filtered = [m for m in filtered if m.get("created_at", "") >= since]

        if until:
            filtered = [m for m in filtered if m.get("created_at", "") <= until]

        header = {
            "_header": True,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_filter": source_prefix,
            "since": since,
            "until": until,
            "count": len(filtered),
            "version": "3.4.0",
        }
        lines: List[str] = [json.dumps(header, separators=(",", ":"))]

        for mem in filtered:
            custom_fields = {
                k: v for k, v in mem.items() if k not in self._EXPORT_STANDARD_FIELDS
            }
            record = {
                "text": mem["text"],
                "source": mem.get("source", ""),
                "created_at": mem.get("created_at", ""),
                "updated_at": mem.get("updated_at", ""),
                "custom_fields": custom_fields,
            }
            lines.append(json.dumps(record, separators=(",", ":")))

        return lines

    def import_memories(
        self,
        lines: List[str],
        strategy: str = "add",
        source_remap: Optional[Tuple[str, str]] = None,
        create_backup: bool = True,
    ) -> Dict[str, Any]:
        """Import memories from NDJSON lines produced by :meth:`export_memories`.

        Parameters
        ----------
        lines:
            NDJSON strings — first line must be a header with ``_header: true``.
        strategy:
            ``"add"`` — bulk-add all records without novelty check.
            ``"smart"`` / ``"smart+extract"`` — similarity-based novelty
            check with timestamp resolution for borderline matches.
        source_remap:
            Optional ``(old_prefix, new_prefix)`` tuple.  If a record's
            ``source`` starts with *old_prefix* it is replaced with
            *new_prefix*.
        create_backup:
            When *True* and existing memories are present, create a
            ``pre-import`` backup before mutating state.

        Returns
        -------
        dict with keys ``imported``, ``skipped``, ``updated``, ``errors``,
        ``backup``.
        """
        result: Dict[str, Any] = {
            "imported": 0,
            "skipped": 0,
            "updated": 0,
            "errors": [],
            "backup": None,
        }

        if not lines:
            result["errors"].append({"line": 1, "error": "Missing header: first line must contain _header: true"})
            return result

        # --- validate header ---
        try:
            header = json.loads(lines[0])
        except (json.JSONDecodeError, TypeError):
            result["errors"].append({"line": 1, "error": "Missing header: first line must contain _header: true"})
            return result

        if not header.get("_header"):
            result["errors"].append({"line": 1, "error": "Missing header: first line must contain _header: true"})
            return result

        # --- backup ---
        backup_name: Optional[str] = None
        if create_backup and self.metadata:
            backup_path = self._backup(prefix="pre-import")
            backup_name = backup_path.name
            result["backup"] = backup_name

        # --- parse records ---
        parsed: List[Dict[str, Any]] = []
        for idx, line in enumerate(lines[1:], start=2):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                result["errors"].append({"line": idx, "error": "Invalid JSON"})
                continue

            if "text" not in record or "source" not in record:
                result["errors"].append({"line": idx, "error": "Missing required field (text or source)"})
                continue

            source = record["source"]
            if source_remap and source.startswith(source_remap[0]):
                source = source_remap[1] + source[len(source_remap[0]):]

            entry: Dict[str, Any] = {
                "text": record["text"],
                "source": source,
                "created_at": record.get("created_at", ""),
                "updated_at": record.get("updated_at", ""),
            }
            custom = record.get("custom_fields")
            if custom and isinstance(custom, dict):
                entry["custom_fields"] = custom
            parsed.append(entry)

        # --- dispatch by strategy ---
        if strategy == "add":
            self._import_add(parsed, result)
        elif strategy in ("smart", "smart+extract"):
            self._import_smart(parsed, result)
        else:
            result["errors"].append({"line": 0, "error": f"Unknown strategy: {strategy}"})

        return result

    # ------------------------------------------------------------------
    # Import strategies (private helpers)
    # ------------------------------------------------------------------

    def _import_add(
        self, parsed: List[Dict[str, Any]], result: Dict[str, Any]
    ) -> None:
        """Bulk-add all parsed records without novelty checking."""
        if not parsed:
            return
        texts = [r["text"] for r in parsed]
        sources = [r["source"] for r in parsed]
        metadata_list = [
            {"imported": True, "import_source": r["source"], **r.get("custom_fields", {})}
            for r in parsed
        ]
        self.add_memories(texts=texts, sources=sources, metadata_list=metadata_list, deduplicate=False)
        result["imported"] = len(texts)

    def _import_smart(
        self, parsed: List[Dict[str, Any]], result: Dict[str, Any]
    ) -> None:
        """Add records with similarity-based novelty checking.

        Thresholds
        ----------
        * similarity >= 0.95  →  skip (definite duplicate)
        * similarity <  0.80  →  add  (clearly novel)
        * 0.80 <= sim < 0.95  →  borderline — newer timestamp wins
        """
        _SKIP_THRESHOLD = 0.95
        _NOVEL_THRESHOLD = 0.80

        novel_texts: List[str] = []
        novel_sources: List[str] = []
        novel_meta: List[Dict[str, Any]] = []

        for rec in parsed:
            text = rec["text"]
            source = rec["source"]
            import_created = rec.get("created_at", "")
            custom = rec.get("custom_fields", {})
            rec_meta = {"imported": True, "import_source": source, **custom}

            # Empty engine — everything is novel
            if not self.metadata:
                novel_texts.append(text)
                novel_sources.append(source)
                novel_meta.append(rec_meta)
                continue

            hits = self.search(text, k=1)

            if not hits:
                novel_texts.append(text)
                novel_sources.append(source)
                novel_meta.append(rec_meta)
                continue

            best = hits[0]
            similarity = best.get("similarity", 0.0)

            if similarity >= _SKIP_THRESHOLD:
                # Definite duplicate — skip
                result["skipped"] += 1
            elif similarity < _NOVEL_THRESHOLD:
                # Clearly novel — add
                novel_texts.append(text)
                novel_sources.append(source)
                novel_meta.append(rec_meta)
            else:
                # Borderline (0.80 <= sim < 0.95) — timestamp resolution
                existing_created = best.get("created_at", "")
                if import_created > existing_created:
                    # Import record is newer — replace old with new
                    match_id = best.get("id")
                    if match_id is not None:
                        self.delete_memory(match_id)
                    novel_texts.append(text)
                    novel_sources.append(source)
                    novel_meta.append(rec_meta)
                    result["updated"] += 1
                else:
                    # Existing is newer or same — skip
                    result["skipped"] += 1

        # Batch-add all novel records at once
        if novel_texts:
            self.add_memories(
                texts=novel_texts, sources=novel_sources,
                metadata_list=novel_meta, deduplicate=False,
            )
            result["imported"] = len(novel_texts)

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

    def reembed(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Re-embed all memories, optionally with a different model.

        Creates a backup before re-embedding. If model_name is provided and
        differs from the current model, swaps the embedder first. If the new
        model has a different dimension, the collection is recreated.
        """
        old_model_name = self._active_embed_model()
        total = len(self.metadata)

        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                self._backup(prefix="pre_reembed")

                model_swapped = False
                old_embed_model = self._embed_model
                old_model_name_val = self._model_name
                old_dim = self.dim
                old_embedder = None

                if model_name and model_name != old_model_name:
                    # Swap embedder to new model
                    try:
                        self._embed_model = model_name
                        self._model_name = model_name
                        with self._embedder_lock:
                            old_embedder = self.model
                            self.model = self._make_embedder()
                            new_dim = self.model.get_sentence_embedding_dimension()
                            if new_dim != self.dim:
                                self.dim = new_dim
                        model_swapped = True
                    except Exception as e:
                        # Rollback on failure
                        self._embed_model = old_embed_model
                        self._model_name = old_model_name_val
                        raise RuntimeError(f"Failed to load model {model_name}: {e}")

                # Re-embed all memories
                try:
                    self._reindex_store_from_metadata()
                except Exception:
                    if model_swapped:
                        # Restore old embedder so queries stay consistent
                        with self._embedder_lock:
                            failed_embedder = self.model
                            self.model = old_embedder
                            old_embedder = None  # prevent cleanup below
                        self._embed_model = old_embed_model
                        self._model_name = old_model_name_val
                        self.dim = old_dim
                        close_fn = getattr(failed_embedder, "close", None)
                        if callable(close_fn):
                            try:
                                close_fn()
                            except Exception:
                                pass
                    raise

                # Reindex succeeded — safe to close old embedder
                if old_embedder is not None:
                    close_fn = getattr(old_embedder, "close", None)
                    if callable(close_fn):
                        try:
                            close_fn()
                        except Exception:
                            pass

                self.config["model"] = self._active_embed_model()
                self.config["dimension"] = self.dim
                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        new_model_name = self._active_embed_model()
        logger.info("Re-embed complete: %s -> %s (%d memories)", old_model_name, new_model_name, total)
        gc.collect()

        return {
            "status": "completed",
            "old_model": old_model_name,
            "new_model": new_model_name,
            "memories_processed": total,
            "dimension": self.dim,
        }

    def get_cloud_sync(self) -> Optional["CloudSync"]:
        """Get cloud sync client (None if not available/enabled)."""
        return self.cloud_sync

    def get_backup_dir(self) -> Path:
        """Get backup directory path."""
        return self.backup_dir
