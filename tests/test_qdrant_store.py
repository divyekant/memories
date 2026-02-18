"""Unit tests for the Qdrant storage adapter."""

from types import SimpleNamespace

from qdrant_config import QdrantSettings
from qdrant_store import QdrantStore


class FakeQdrantClient:
    def __init__(self):
        self.created = None
        self.upsert_calls = []
        self.deleted = []
        self._points = {}

    def get_collection(self, collection_name):
        if self.created is None:
            raise RuntimeError("missing")
        return self.created

    def create_collection(self, collection_name, vectors_config, replication_factor, write_consistency_factor):
        self.created = SimpleNamespace(
            collection_name=collection_name,
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors_config)),
            replication_factor=replication_factor,
            write_consistency_factor=write_consistency_factor,
        )

    def upsert(self, collection_name, points, wait, ordering):
        self.upsert_calls.append((collection_name, wait, ordering))
        for p in points:
            self._points[int(p.id)] = p
        return {"status": "ok"}

    def query_points(self, collection_name, query, limit, score_threshold, with_payload, with_vectors, consistency):
        points = [
            SimpleNamespace(id=pid, payload=pt.payload, score=0.95)
            for pid, pt in sorted(self._points.items())
        ][:limit]
        return SimpleNamespace(points=points)

    def delete(self, collection_name, points_selector, wait, ordering):
        for pid in points_selector.points:
            self._points.pop(int(pid), None)
        self.deleted.append((collection_name, list(points_selector.points), wait, ordering))
        return {"status": "ok"}

    def scroll(self, collection_name, offset, limit, with_payload, with_vectors):
        ids = sorted(self._points.keys())
        start = int(offset) if offset is not None else 0
        chunk_ids = ids[start : start + limit]
        points = [self._points[i] for i in chunk_ids]
        next_offset = start + len(points)
        if next_offset >= len(ids):
            next_offset = None
        return points, next_offset


def _settings() -> QdrantSettings:
    return QdrantSettings(
        url="http://qdrant:6333",
        api_key="",
        collection="memories",
        wait=True,
        write_ordering="strong",
        read_consistency="majority",
        replication_factor=1,
        write_consistency_factor=1,
    )


def test_ensure_collection_creates_missing_collection():
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    store.ensure_collection(dim=384)
    assert fake.created is not None
    assert fake.created.collection_name == "memories"


def test_upsert_and_search_roundtrip():
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    store.ensure_collection(dim=384)
    store.upsert_points(
        [
            {
                "id": 1,
                "vector": [0.1] * 384,
                "payload": {"text": "hello", "source": "s"},
            }
        ]
    )
    hits = store.search([0.1] * 384, limit=5)
    assert hits
    assert hits[0]["id"] == 1
    assert hits[0]["payload"]["text"] == "hello"


def test_delete_points_removes_data():
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    store.ensure_collection(dim=384)
    store.upsert_points(
        [{"id": 1, "vector": [0.1] * 384, "payload": {"text": "hello", "source": "s"}}]
    )
    store.delete_points([1])
    hits = store.search([0.1] * 384, limit=5)
    assert hits == []

