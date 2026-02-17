"""
Memories Engine
Local semantic search with hybrid BM25+vector retrieval, markdown-aware
chunking, automatic backups, and concurrency safety.
"""

import faiss
import numpy as np
import json
import re
import shutil
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from onnx_embedder import OnnxEmbedder
from rank_bm25 import BM25Okapi

logger = logging.getLogger("faiss-memory")

# Cloud sync (optional dependency)
try:
    from cloud_sync import CloudSync
    CLOUD_SYNC_AVAILABLE = True
except ImportError:
    CLOUD_SYNC_AVAILABLE = False
    logger.info("Cloud sync not available (cloud_sync.py not found or boto3 not installed)")


class MemoryEngine:
    """FAISS-based semantic memory with hybrid search and backup support"""

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

        self.index_path = self.data_dir / "index.faiss"
        self.metadata_path = self.data_dir / "metadata.json"
        self.config_path = self.data_dir / "config.json"

        # Read env with fallbacks
        import os
        self._model_name = model_name or os.getenv("MODEL_NAME", "all-MiniLM-L6-v2")
        self._max_backups = max_backups or int(os.getenv("MAX_BACKUPS", "10"))

        # Load ONNX embedder (drop-in replacement for SentenceTransformer)
        self.model = OnnxEmbedder(self._model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

        # Concurrency lock for write operations
        self._write_lock = threading.Lock()

        # Initialize or load index
        self.index = faiss.IndexFlatIP(self.dim)
        self.metadata: List[Dict[str, Any]] = []
        self.config = {
            "model": self._model_name,
            "dimension": self.dim,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": None,
        }

        # BM25 index for hybrid search
        self.bm25_index: Optional[BM25Okapi] = None

        # Cloud sync (optional)
        self.cloud_sync: Optional[CloudSync] = None
        if CLOUD_SYNC_AVAILABLE:
            self.cloud_sync = CloudSync.from_env()

        # Auto-download from cloud if local is empty
        if self.cloud_sync and not self.index_path.exists():
            try:
                latest = self.cloud_sync.get_latest_snapshot()
                if latest:
                    logger.info("Local index empty - downloading latest backup from cloud: %s", latest)
                    result = self.cloud_sync.download_backup(latest, self.backup_dir)
                    # Restore from downloaded backup
                    self.restore_from_backup(latest)
                    logger.info("Restored from cloud: %s", result)
            except Exception as e:
                logger.error("Auto-download from cloud failed: %s", e)

        # Load existing index if present
        if self.index_path.exists():
            self.load()

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def _check_integrity(self):
        """Verify index and metadata are in sync"""
        if self.index.ntotal != len(self.metadata):
            logger.error(
                "Integrity mismatch: index has %d vectors, metadata has %d entries",
                self.index.ntotal,
                len(self.metadata),
            )
            raise RuntimeError(
                f"Index/metadata mismatch: {self.index.ntotal} vectors vs {len(self.metadata)} metadata entries. "
                "Restore from backup or rebuild the index."
            )

    # ------------------------------------------------------------------
    # BM25
    # ------------------------------------------------------------------

    def _rebuild_bm25(self):
        """Rebuild BM25 index from current metadata"""
        if not self.metadata:
            self.bm25_index = None
            return
        corpus = [m["text"].lower().split() for m in self.metadata]
        self.bm25_index = BM25Okapi(corpus)

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
        """Split markdown into semantically meaningful chunks.

        Splits on headers first, then paragraphs, with overlap between chunks.
        Each chunk carries its section header for context.
        Returns list of (text, source) tuples.
        """
        chunks: List[Tuple[str, str]] = []

        # Split on markdown headers (##, ###, etc.)
        parts = re.split(r"(^#{1,4}\s+.+$)", content, flags=re.MULTILINE)

        current_header = ""
        buffer = ""
        chunk_idx = 0

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Detect header lines
            if re.match(r"^#{1,4}\s+", part):
                # Flush buffer before switching sections
                if buffer.strip() and len(buffer.strip()) > 30:
                    chunk_text = f"{current_header}\n\n{buffer.strip()}" if current_header else buffer.strip()
                    chunks.append((chunk_text, f"{source_name}:chunk_{chunk_idx}"))
                    chunk_idx += 1
                    buffer = ""
                current_header = part
                continue

            # Split section content by paragraphs
            paragraphs = re.split(r"\n\s*\n", part)
            for para in paragraphs:
                para = para.strip()
                if not para or len(para) < 20:
                    continue

                candidate = f"{buffer}\n\n{para}".strip() if buffer else para
                if len(candidate) > max_chunk_size and buffer:
                    # Flush current buffer as a chunk
                    chunk_text = f"{current_header}\n\n{buffer.strip()}" if current_header else buffer.strip()
                    chunks.append((chunk_text, f"{source_name}:chunk_{chunk_idx}"))
                    chunk_idx += 1
                    # Overlap: keep the tail of the previous buffer
                    if len(buffer) > overlap_size:
                        buffer = buffer[-overlap_size:] + "\n\n" + para
                    else:
                        buffer = para
                else:
                    buffer = candidate

        # Flush remaining
        if buffer.strip() and len(buffer.strip()) > 30:
            chunk_text = f"{current_header}\n\n{buffer.strip()}" if current_header else buffer.strip()
            chunks.append((chunk_text, f"{source_name}:chunk_{chunk_idx}"))

        return chunks

    # ------------------------------------------------------------------
    # Backup / Restore
    # ------------------------------------------------------------------

    def _backup(self, prefix: str = "auto") -> Path:
        """Create timestamped backup of index and metadata"""
        # Sanitize prefix to prevent path traversal
        prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", prefix)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"{prefix}_{timestamp}"
        backup_path = self.backup_dir / backup_name
        backup_path.mkdir(exist_ok=True)

        if self.index_path.exists():
            shutil.copy2(self.index_path, backup_path / "index.faiss")
        if self.metadata_path.exists():
            shutil.copy2(self.metadata_path, backup_path / "metadata.json")
        if self.config_path.exists():
            shutil.copy2(self.config_path, backup_path / "config.json")

        self._cleanup_old_backups(keep=self._max_backups)

        # Upload to cloud if enabled
        if self.cloud_sync:
            try:
                logger.info("Uploading backup to cloud: %s", backup_name)
                self.cloud_sync.upload_backup(backup_path)
                logger.info("Cloud upload complete: %s", backup_name)
            except Exception as e:
                logger.error("Cloud upload failed: %s", e)

        return backup_path

    def _cleanup_old_backups(self, keep: int = 10):
        """Keep only N most recent backups"""
        backups = sorted(
            self.backup_dir.glob("*_*"), key=lambda p: p.name, reverse=True
        )
        for old_backup in backups[keep:]:
            shutil.rmtree(old_backup, ignore_errors=True)

    def restore_from_backup(self, backup_name: str) -> Dict[str, Any]:
        """Restore index and metadata from a named backup"""
        backup_path = self.backup_dir / backup_name
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup '{backup_name}' not found")

        index_file = backup_path / "index.faiss"
        meta_file = backup_path / "metadata.json"
        if not index_file.exists() or not meta_file.exists():
            raise FileNotFoundError(f"Backup '{backup_name}' is incomplete")

        with self._write_lock:
            # Create safety backup of current state first
            self._backup(prefix="pre_restore")

            shutil.copy2(index_file, self.index_path)
            shutil.copy2(meta_file, self.metadata_path)
            config_file = backup_path / "config.json"
            if config_file.exists():
                shutil.copy2(config_file, self.config_path)

            self.load()

        return {
            "restored_from": backup_name,
            "total_memories": self.index.ntotal,
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
    ) -> List[int]:
        """Add new memories to index (thread-safe)"""
        if not texts:
            return []

        with self._write_lock:
            if len(texts) > 10:
                self._backup(prefix="pre_add")

            # Optional deduplication
            if deduplicate and self.index.ntotal > 0:
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

            embeddings = self.model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )

            start_id = len(self.metadata)
            self.index.add(embeddings.astype("float32"))

            added_ids = []
            for i, (text, source) in enumerate(zip(texts, sources)):
                mem_id = start_id + i
                meta = {
                    "id": mem_id,
                    "text": text,
                    "source": source,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **(
                        metadata_list[i]
                        if metadata_list and i < len(metadata_list)
                        else {}
                    ),
                }
                self.metadata.append(meta)
                added_ids.append(mem_id)

            self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            self._rebuild_bm25()

        return added_ids

    def delete_memory(self, memory_id: int) -> Dict[str, Any]:
        """Delete a single memory by ID. Requires index rebuild."""
        with self._write_lock:
            if memory_id < 0 or memory_id >= len(self.metadata):
                raise ValueError(f"Memory ID {memory_id} not found")

            self._backup(prefix="pre_delete")

            deleted = self.metadata[memory_id]
            remaining = [m for i, m in enumerate(self.metadata) if i != memory_id]

            # Rebuild index without the deleted entry
            self.index = faiss.IndexFlatIP(self.dim)
            self.metadata = []

            if remaining:
                texts = [m["text"] for m in remaining]
                embeddings = self.model.encode(
                    texts, normalize_embeddings=True, show_progress_bar=False
                )
                self.index.add(embeddings.astype("float32"))
                for i, m in enumerate(remaining):
                    m["id"] = i
                self.metadata = remaining

            self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            self._rebuild_bm25()

        return {"deleted_id": memory_id, "deleted_text": deleted["text"][:100]}

    def delete_by_source(self, source_pattern: str) -> Dict[str, Any]:
        """Delete all memories matching a source pattern"""
        with self._write_lock:
            matching = [
                m for m in self.metadata if source_pattern in m.get("source", "")
            ]
            if not matching:
                return {"deleted_count": 0}

            self._backup(prefix="pre_delete_source")

            remaining = [
                m for m in self.metadata if source_pattern not in m.get("source", "")
            ]

            self.index = faiss.IndexFlatIP(self.dim)
            self.metadata = []

            if remaining:
                texts = [m["text"] for m in remaining]
                embeddings = self.model.encode(
                    texts, normalize_embeddings=True, show_progress_bar=False
                )
                self.index.add(embeddings.astype("float32"))
                for i, m in enumerate(remaining):
                    m["id"] = i
                self.metadata = remaining

            self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.save()
            self._rebuild_bm25()

        return {"deleted_count": len(matching)}

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self, query: str, k: int = 5, threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Vector-only search for similar memories"""
        if self.index.ntotal == 0:
            return []

        k = min(k, self.index.ntotal, 100)

        query_vec = self.model.encode(
            [query], normalize_embeddings=True, show_progress_bar=False
        )

        distances, indices = self.index.search(query_vec.astype("float32"), k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            similarity = float(dist)
            if threshold is not None and similarity < threshold:
                continue
            result = {**self.metadata[idx], "similarity": round(similarity, 6)}
            results.append(result)

        return results

    def hybrid_search(
        self,
        query: str,
        k: int = 5,
        threshold: Optional[float] = None,
        vector_weight: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """Hybrid BM25 + vector search with Reciprocal Rank Fusion"""
        if self.index.ntotal == 0:
            return []

        k = min(k, self.index.ntotal, 100)
        oversample = min(k * 3, self.index.ntotal)

        # Vector search
        vector_results = self.search(query, k=oversample)

        # BM25 search
        bm25_ranked = []
        if self.bm25_index is not None:
            tokenized = query.lower().split()
            bm25_scores = self.bm25_index.get_scores(tokenized)
            bm25_ranked = sorted(
                enumerate(bm25_scores), key=lambda x: x[1], reverse=True
            )[:oversample]

        # Reciprocal Rank Fusion
        RRF_K = 60
        rrf_scores: Dict[int, float] = {}

        for rank, result in enumerate(vector_results):
            doc_id = result["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + vector_weight * (
                1.0 / (rank + RRF_K)
            )

        bm25_weight = 1.0 - vector_weight
        for rank, (idx, score) in enumerate(bm25_ranked):
            if score > 0 and idx < len(self.metadata):
                doc_id = self.metadata[idx]["id"]
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + bm25_weight * (
                    1.0 / (rank + RRF_K)
                )

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:k]

        results = []
        for doc_id, rrf_score in sorted_ids:
            if doc_id < len(self.metadata):
                result = {**self.metadata[doc_id], "rrf_score": round(rrf_score, 6)}
                if threshold is not None:
                    # Use vector similarity for threshold filtering
                    vec_match = next(
                        (r for r in vector_results if r["id"] == doc_id), None
                    )
                    if vec_match and vec_match["similarity"] < threshold:
                        continue
                results.append(result)

        return results

    def is_novel(
        self, text: str, threshold: float = 0.88
    ) -> Tuple[bool, Optional[Dict]]:
        """Check if text is novel (not too similar to existing memories)"""
        results = self.search(text, k=1)
        if not results:
            return True, None
        top_match = results[0]
        return top_match["similarity"] < threshold, top_match

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def find_duplicates(
        self, threshold: float = 0.90
    ) -> List[Dict[str, Any]]:
        """Find all near-duplicate pairs in the index"""
        if self.index.ntotal < 2:
            return []

        all_embeddings = self.model.encode(
            [m["text"] for m in self.metadata],
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        search_k = min(5, self.index.ntotal)
        distances, indices = self.index.search(
            all_embeddings.astype("float32"), search_k
        )

        duplicates = []
        seen = set()
        for i in range(len(self.metadata)):
            for j_pos in range(1, distances.shape[1]):
                j = int(indices[i][j_pos])
                if j == -1:
                    continue
                sim = float(distances[i][j_pos])
                pair_key = (min(i, j), max(i, j))
                if sim >= threshold and pair_key not in seen:
                    seen.add(pair_key)
                    duplicates.append(
                        {
                            "id_a": i,
                            "id_b": j,
                            "similarity": round(sim, 4),
                            "text_a": self.metadata[i]["text"][:120],
                            "text_b": self.metadata[j]["text"][:120],
                        }
                    )

        return sorted(duplicates, key=lambda x: x["similarity"], reverse=True)

    def deduplicate(
        self, threshold: float = 0.90, dry_run: bool = True
    ) -> Dict[str, Any]:
        """Remove near-duplicate memories, keeping the earliest entry"""
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

        with self._write_lock:
            self._backup(prefix="pre_dedup")

            remaining = [
                m for i, m in enumerate(self.metadata) if i not in ids_to_remove
            ]

            self.index = faiss.IndexFlatIP(self.dim)
            self.metadata = []

            if remaining:
                texts = [m["text"] for m in remaining]
                embeddings = self.model.encode(
                    texts, normalize_embeddings=True, show_progress_bar=False
                )
                self.index.add(embeddings.astype("float32"))
                for i, m in enumerate(remaining):
                    m["id"] = i
                self.metadata = remaining

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

    def list_memories(
        self, offset: int = 0, limit: int = 20, source_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """List memories with pagination and optional source filter"""
        filtered = self.metadata
        if source_filter:
            filtered = [
                m for m in filtered if source_filter in m.get("source", "")
            ]

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
        """Persist index and metadata to disk"""
        faiss.write_index(self.index, str(self.index_path))
        with open(self.metadata_path, "w") as f:
            json.dump(self.metadata, f, indent=2)
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=2)

    def load(self):
        """Load index and metadata from disk"""
        self.index = faiss.read_index(str(self.index_path))
        with open(self.metadata_path) as f:
            self.metadata = json.load(f)
        if self.config_path.exists():
            with open(self.config_path) as f:
                self.config.update(json.load(f))
        self._check_integrity()
        self._rebuild_bm25()

    def rebuild_from_files(self, file_paths: List[str]) -> Dict[str, Any]:
        """Rebuild index from markdown files using proper chunking"""
        with self._write_lock:
            backup_path = self._backup(prefix="pre_rebuild")

            self.index = faiss.IndexFlatIP(self.dim)
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
                # Bypass lock since we already hold it
                embeddings = self.model.encode(
                    texts, normalize_embeddings=True, show_progress_bar=False
                )
                self.index.add(embeddings.astype("float32"))
                for i, (text, source) in enumerate(zip(texts, sources)):
                    self.metadata.append(
                        {
                            "id": i,
                            "text": text,
                            "source": source,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()

        return {
            "files_processed": files_processed,
            "memories_added": len(texts),
            "backup_location": str(backup_path),
        }

    def stats(self) -> Dict[str, Any]:
        """Get memory statistics"""
        return {
            "total_memories": self.index.ntotal,
            "dimension": self.dim,
            "model": self.config.get("model"),
            "created_at": self.config.get("created_at"),
            "last_updated": self.config.get("last_updated"),
            "index_size_bytes": (
                self.index_path.stat().st_size if self.index_path.exists() else 0
            ),
            "backup_count": len(list(self.backup_dir.glob("*_*"))),
        }

    def stats_light(self) -> Dict[str, Any]:
        """Cheap stats for health checks (no filesystem I/O)"""
        return {
            "total_memories": self.index.ntotal,
            "dimension": self.dim,
            "model": self.config.get("model"),
        }

    # ------------------------------------------------------------------
    # Public API methods (for API endpoints)
    # ------------------------------------------------------------------

    def create_backup(self, prefix: str = "manual") -> Path:
        """Create a manual backup with optional prefix
        
        Args:
            prefix: Prefix for backup name (e.g., "manual" creates "manual_20260214_120000")
        
        Returns:
            Path to the created backup directory
        """
        return self._backup(prefix=prefix)

    def get_cloud_sync(self) -> Optional["CloudSync"]:
        """Get cloud sync client (None if not available/enabled)"""
        return self.cloud_sync

    def get_backup_dir(self) -> Path:
        """Get backup directory path"""
        return self.backup_dir
