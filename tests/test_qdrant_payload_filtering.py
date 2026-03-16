"""Tests for Qdrant payload filtering — pre-filter at vector DB level."""

from types import SimpleNamespace

from qdrant_client import models

from qdrant_config import QdrantSettings
from qdrant_store import QdrantStore


class FakeQdrantClient:
    """Fake Qdrant client that respects query_filter on search and count."""

    def __init__(self):
        self.created = None
        self._points = {}
        self._indexes = {}

    def get_collection(self, collection_name):
        if self.created is None:
            raise RuntimeError("missing")
        return self.created

    def create_collection(self, collection_name, vectors_config, replication_factor, write_consistency_factor):
        self.created = SimpleNamespace(
            collection_name=collection_name,
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors_config)),
        )

    def create_payload_index(self, collection_name, field_name, field_schema, wait=True):
        self._indexes[field_name] = field_schema
        return SimpleNamespace(status="ok")

    def upsert(self, collection_name, points, wait, ordering):
        for p in points:
            self._points[int(p.id)] = p
        return {"status": "ok"}

    def _matches_filter(self, point, query_filter):
        """Simplified filter matching for tests."""
        if query_filter is None:
            return True
        conditions = query_filter.must or []
        if not isinstance(conditions, list):
            conditions = [conditions]
        for cond in conditions:
            if not hasattr(cond, "key"):
                continue
            payload = dict(getattr(point, "payload", {}) or {})
            value = payload.get(cond.key)
            match = cond.match
            if hasattr(match, "value"):
                if value != match.value:
                    return False
            elif hasattr(match, "any"):
                if value not in match.any:
                    return False
        # Handle should (OR) conditions
        should = query_filter.should
        if should:
            if not isinstance(should, list):
                should = [should]
            any_match = False
            for cond in should:
                if not hasattr(cond, "key"):
                    continue
                payload = dict(getattr(point, "payload", {}) or {})
                value = payload.get(cond.key)
                match = cond.match
                if hasattr(match, "any") and value in match.any:
                    any_match = True
                    break
                if hasattr(match, "value") and value == match.value:
                    any_match = True
                    break
            if not any_match:
                return False
        return True

    def query_points(self, collection_name, query, limit, score_threshold,
                     with_payload, with_vectors, consistency, query_filter=None):
        filtered = [
            pt for pt in self._points.values()
            if self._matches_filter(pt, query_filter)
        ]
        points = [
            SimpleNamespace(id=pid, payload=pt.payload, score=0.95)
            for pid, pt in sorted((int(getattr(p, "id", 0)), p) for p in filtered)
        ][:limit]
        return SimpleNamespace(points=points)

    def count(self, collection_name, count_filter=None, exact=True):
        if count_filter is None:
            total = len(self._points)
        else:
            total = sum(1 for pt in self._points.values() if self._matches_filter(pt, count_filter))
        return SimpleNamespace(count=total)

    def delete(self, collection_name, points_selector, wait, ordering):
        for pid in points_selector.points:
            self._points.pop(int(pid), None)

    def scroll(self, collection_name, offset, limit, with_payload, with_vectors):
        return [], None


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


def _seed_store(fake, store):
    """Seed with 3 memories across 2 source prefixes."""
    store.ensure_collection(dim=4)
    store.upsert_points([
        {"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"text": "decision about auth", "source": "claude-code/memories"}},
        {"id": 2, "vector": [0.5, 0.6, 0.7, 0.8], "payload": {"text": "learning about tests", "source": "learning/memories"}},
        {"id": 3, "vector": [0.9, 0.1, 0.2, 0.3], "payload": {"text": "deferred auth fix", "source": "claude-code/memories"}},
    ])


# --- search with query_filter (backward compat) ---

def test_search_without_filter_returns_all():
    """Existing behavior: no filter returns all points."""
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    _seed_store(fake, store)

    hits = store.search([0.1, 0.2, 0.3, 0.4], limit=10)
    assert len(hits) == 3


def test_search_with_query_filter_exact_match():
    """New: filter by exact source value."""
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    _seed_store(fake, store)

    hits = store.search(
        [0.1, 0.2, 0.3, 0.4],
        limit=10,
        query_filter=models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchValue(value="learning/memories"))]
        ),
    )
    assert len(hits) == 1
    assert hits[0]["payload"]["source"] == "learning/memories"


def test_search_with_query_filter_match_any():
    """New: filter by any of multiple source values."""
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    _seed_store(fake, store)

    hits = store.search(
        [0.1, 0.2, 0.3, 0.4],
        limit=10,
        query_filter=models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchAny(any=["claude-code/memories"]))]
        ),
    )
    assert len(hits) == 2
    assert all(h["payload"]["source"] == "claude-code/memories" for h in hits)


# --- count_filtered ---

def test_count_filtered_without_filter():
    """count_filtered with no filter returns total."""
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    _seed_store(fake, store)

    assert store.count_filtered() == 3


def test_count_filtered_with_filter():
    """count_filtered with a filter returns matching count."""
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    _seed_store(fake, store)

    result = store.count_filtered(
        count_filter=models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchAny(any=["learning/memories"]))]
        ),
    )
    assert result == 1


# --- ensure_payload_indexes ---

def test_ensure_payload_indexes_creates_source_index():
    """ensure_payload_indexes creates a keyword index on 'source'."""
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    store.ensure_collection(dim=4)

    store.ensure_payload_indexes()
    assert "source" in fake._indexes


# --- backward compat: existing count() unchanged ---

def test_count_still_works():
    """Existing count() method remains unchanged."""
    fake = FakeQdrantClient()
    store = QdrantStore(settings=_settings(), client=fake)
    _seed_store(fake, store)

    assert store.count() == 3
