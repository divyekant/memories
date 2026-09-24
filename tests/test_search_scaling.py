"""Per-search work must not rescan the whole corpus when nothing changed."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    with patch("memory_engine.QdrantStore") as MockStore, \
         patch("memory_engine.QdrantSettings") as MockSettings:
        store = MagicMock()
        store.count.return_value = 0
        store.search.return_value = []
        MockStore.return_value = store
        MockSettings.from_env.return_value = MagicMock(read_consistency="majority")
        eng = MemoryEngine(data_dir=str(tmp_path / "data"))
    now = datetime.now(timezone.utc).isoformat()
    eng.metadata = [
        {"id": 1, "text": "qdrant payload filter", "source": "claude-code/p", "created_at": now,
         "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
        {"id": 2, "text": "qdrant cluster", "source": "codex/p", "created_at": now},
        {"id": 3, "text": "unrelated note", "source": "wip/p", "created_at": now},
    ]
    eng._rebuild_id_map()
    eng._rebuild_bm25()
    return eng


def test_same_query_across_prefixes_scores_bm25_once(engine):
    with patch.object(engine.bm25_index, "get_scores", wraps=engine.bm25_index.get_scores) as spy:
        for prefix in (None, "claude-code/p", "codex/p", "wip/p"):
            engine.hybrid_search(query="qdrant payload", source_prefix=prefix, graph_weight=0)
    assert spy.call_count == 1


def test_bm25_rebuild_invalidates_cached_scores(engine):
    before = engine.hybrid_search(query="zebra", graph_weight=0)
    engine.metadata.append({"id": 4, "text": "zebra facts", "source": "wip/p", "created_at": "2026-01-01T00:00:00+00:00"})
    engine._rebuild_id_map()
    engine._rebuild_bm25()
    after = engine.hybrid_search(query="zebra", graph_weight=0)
    assert [r["id"] for r in before] == []
    assert [r["id"] for r in after] == [4]


def test_related_graph_is_built_once_until_links_change(engine):
    with patch.object(engine, "_build_adjacency", wraps=engine._build_adjacency) as spy:
        engine.hybrid_search(query="qdrant", graph_weight=0.1)
        engine.hybrid_search(query="qdrant", source_prefix="codex/p", graph_weight=0.1)
        assert spy.call_count == 1
        engine.add_link(1, 3, "related_to")
        engine.hybrid_search(query="qdrant", graph_weight=0.1)
        assert spy.call_count == 2
        engine.remove_link(1, 3, "related_to")
        engine.hybrid_search(query="qdrant", graph_weight=0.1)
        assert spy.call_count == 3


def test_search_between_delete_and_bm25_rebuild_keeps_ids_aligned(engine):
    """Searches run on worker threads, so they can land after a delete has
    rebuilt the id map but before the BM25 index is rebuilt."""
    now = datetime.now(timezone.utc).isoformat()
    engine.metadata = [
        {"id": 1, "text": "alpha", "source": "t", "created_at": now},
        {"id": 2, "text": "zebra stripes", "source": "t", "created_at": now},
        {"id": 3, "text": "other thing", "source": "t", "created_at": now},
    ]
    engine._rebuild_id_map()
    engine._rebuild_bm25()

    engine.metadata = [m for m in engine.metadata if m["id"] != 1]
    engine._rebuild_id_map()  # delete path, before _rebuild_bm25()

    for prefix in (None, "t"):
        ids = [r["id"] for r in engine.hybrid_search(query="zebra", source_prefix=prefix, graph_weight=0)]
        assert ids == [2], (prefix, ids)
