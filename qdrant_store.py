"""Qdrant storage adapter.

This module isolates Qdrant API specifics behind a small interface so the
memory engine can swap vector backends with minimal surface-area changes.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from qdrant_client import QdrantClient, models

from qdrant_config import QdrantSettings

_LOCAL_CLIENTS: Dict[str, QdrantClient] = {}


def _normalize_point_id(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class QdrantStore:
    def __init__(
        self,
        settings: QdrantSettings,
        client: Optional[QdrantClient] = None,
        local_path: Optional[str] = None,
    ):
        self.settings = settings
        self.collection = settings.collection
        if client is not None:
            self.client = client
            self._local_path: Optional[str] = None
        else:
            if settings.url:
                kwargs: Dict[str, Any] = {}
                if settings.api_key:
                    kwargs["api_key"] = settings.api_key
                self.client = QdrantClient(url=settings.url, **kwargs)
                self._local_path = None
            else:
                if not local_path:
                    raise ValueError("local_path is required when QDRANT_URL is empty")
                existing = _LOCAL_CLIENTS.get(local_path)
                if existing is None:
                    existing = QdrantClient(path=local_path)
                    _LOCAL_CLIENTS[local_path] = existing
                self.client = existing
                self._local_path = local_path

    def ensure_collection(self, dim: int) -> None:
        try:
            self.client.get_collection(collection_name=self.collection)
            return
        except Exception:
            pass

        self._create_collection(dim=dim)

    def get_collection_dimension(self) -> Optional[int]:
        try:
            collection_info = self.client.get_collection(collection_name=self.collection)
        except Exception:
            return None

        config = getattr(collection_info, "config", None)
        params = getattr(config, "params", None) if config is not None else None
        vectors = getattr(params, "vectors", None) if params is not None else None
        if vectors is None:
            return None

        size = getattr(vectors, "size", None)
        if size is not None:
            return int(size)

        if isinstance(vectors, dict):
            for vector_cfg in vectors.values():
                named_size = getattr(vector_cfg, "size", None)
                if named_size is not None:
                    return int(named_size)

        return None

    def _create_collection(self, dim: int) -> None:
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            replication_factor=self.settings.replication_factor,
            write_consistency_factor=self.settings.write_consistency_factor,
        )

    def recreate_collection(self, dim: int) -> None:
        try:
            self.client.delete_collection(collection_name=self.collection)
        except Exception:
            pass
        self._create_collection(dim=dim)
        self.ensure_payload_indexes()

    def count(self, exact: bool = True) -> int:
        result = self.client.count(collection_name=self.collection, exact=exact)
        return int(getattr(result, "count", 0))

    def set_payload(self, point_id: int, payload: Dict[str, Any]) -> None:
        """Update only the payload of an existing point (no vector change)."""
        self.client.set_payload(
            collection_name=self.collection,
            payload=payload,
            points=[_normalize_point_id(point_id)],
            wait=self.settings.wait,
            ordering=self.settings.write_ordering,
        )

    def upsert_points(self, points: List[Dict[str, Any]]) -> None:
        point_structs = [
            models.PointStruct(
                id=_normalize_point_id(point["id"]),
                vector=point["vector"],
                payload=point.get("payload") or {},
            )
            for point in points
        ]
        self.client.upsert(
            collection_name=self.collection,
            points=point_structs,
            wait=self.settings.wait,
            ordering=self.settings.write_ordering,
        )

    def ensure_payload_indexes(self) -> None:
        """Create payload indexes for efficient filtering (idempotent)."""
        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="source",
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
        except Exception:
            pass  # Already exists or collection missing — safe to ignore

        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="archived",
                field_schema=models.PayloadSchemaType.BOOL,
            )
        except Exception:
            pass

    def count_filtered(
        self,
        count_filter: Optional[models.Filter] = None,
        exact: bool = True,
    ) -> int:
        """Count points with optional Qdrant-level filtering."""
        result = self.client.count(
            collection_name=self.collection,
            count_filter=count_filter,
            exact=exact,
        )
        return int(getattr(result, "count", 0))

    def search(
        self,
        query_vector: List[float],
        limit: int,
        score_threshold: Optional[float] = None,
        consistency: Optional[str] = None,
        query_filter: Optional[models.Filter] = None,
    ) -> List[Dict[str, Any]]:
        response = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
            with_vectors=False,
            consistency=consistency or self.settings.read_consistency,
            query_filter=query_filter,
        )
        points = getattr(response, "points", response)

        return [
            {
                "id": _normalize_point_id(getattr(point, "id", None)),
                "payload": dict(getattr(point, "payload", {}) or {}),
                "score": float(getattr(point, "score", 0.0)),
            }
            for point in points
        ]

    def scroll_all(self, offset: Optional[Any] = None, limit: int = 100) -> Tuple[List[Dict[str, Any]], Any]:
        points, next_offset = self.client.scroll(
            collection_name=self.collection,
            offset=offset,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        normalized = [
            {
                "id": _normalize_point_id(getattr(point, "id", None)),
                "payload": dict(getattr(point, "payload", {}) or {}),
            }
            for point in points
        ]
        return normalized, next_offset

    def delete_points(self, ids: Iterable[Any]) -> None:
        normalized_ids = [_normalize_point_id(point_id) for point_id in ids]
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=normalized_ids),
            wait=self.settings.wait,
            ordering=self.settings.write_ordering,
        )

    def create_snapshot(self) -> str:
        if self._local_path is not None:
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            backup_name = f"local-backup-{timestamp}"
            snapshots_dir = os.path.join(self._local_path, ".snapshots")
            os.makedirs(snapshots_dir, exist_ok=True)
            dst = os.path.join(snapshots_dir, backup_name)
            shutil.copytree(self._local_path, dst,
                            ignore=shutil.ignore_patterns(".snapshots"))
            return backup_name
        snapshot = self.client.create_snapshot(collection_name=self.collection)
        return snapshot.name

    def list_snapshots(self) -> List[Dict[str, Any]]:
        if self._local_path is not None:
            snapshots_dir = os.path.join(self._local_path, ".snapshots")
            if not os.path.isdir(snapshots_dir):
                return []
            return [
                {"name": entry, "creation_time": None, "size": None}
                for entry in sorted(os.listdir(snapshots_dir))
                if os.path.isdir(os.path.join(snapshots_dir, entry))
            ]
        snapshots = self.client.list_snapshots(collection_name=self.collection)
        return [
            {
                "name": s.name,
                "creation_time": s.creation_time,
                "size": s.size,
            }
            for s in snapshots
        ]

    def restore_snapshot(self, name: str) -> None:
        if self._local_path is not None:
            snapshots_dir = os.path.join(self._local_path, ".snapshots")
            backup_path = os.path.join(snapshots_dir, name)
            # Remove everything in local_path except .snapshots
            for entry in os.listdir(self._local_path):
                if entry == ".snapshots":
                    continue
                full = os.path.join(self._local_path, entry)
                if os.path.isdir(full):
                    shutil.rmtree(full)
                else:
                    os.remove(full)
            shutil.copytree(backup_path, self._local_path, dirs_exist_ok=True)
            return
        self.client.recover_snapshot(
            collection_name=self.collection,
            location=name,
        )
