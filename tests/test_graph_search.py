"""Tests for graph-aware search expansion in hybrid_search()."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    with patch("memory_engine.QdrantStore") as MockStore, \
         patch("memory_engine.QdrantSettings") as MockSettings:
        mock_store = MagicMock()
        mock_store.ensure_collection.return_value = None
        mock_store.ensure_payload_indexes.return_value = None
        mock_store.count.return_value = 0
        mock_store.search.return_value = []
        MockStore.return_value = mock_store
        mock_settings = MagicMock()
        mock_settings.read_consistency = "majority"
        MockSettings.from_env.return_value = mock_settings
        eng = MemoryEngine(data_dir=str(tmp_path / "data"))
        return eng


class TestGraphExpand:
    """Test _graph_expand() private method."""

    def test_returns_empty_when_no_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "seed", "source": "test/proj", "created_at": now}]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert candidates == {}
        assert info["neighbors_found"] == 0

    def test_finds_outgoing_neighbor(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "test/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "neighbor", "source": "test/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 in candidates
        assert candidates[2]["graph_support"] > 0
        assert 1 in candidates[2]["graph_via"]

    def test_finds_incoming_neighbor(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed (old)", "source": "test/proj", "created_at": now},
            {"id": 2, "text": "newer related", "source": "test/proj", "created_at": now,
             "links": [{"to_id": 1, "type": "related_to", "created_at": now}]},
        ]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 in candidates

    def test_filters_source_prefix(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "wip/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "out of scope", "source": "learning/other", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, "wip/", False)
        assert 2 not in candidates
        assert info["neighbors_filtered"] >= 1

    def test_filters_archived(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "test/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "archived", "source": "test/proj", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 not in candidates

    def test_includes_archived_when_flag_set(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "test/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "archived", "source": "test/proj", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, True)
        assert 2 in candidates

    def test_caps_neighbors_per_seed(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "test/proj", "created_at": now,
             "links": [{"to_id": i, "type": "related_to", "created_at": now} for i in range(2, 12)]},
        ] + [{"id": i, "text": f"n{i}", "source": "test/proj", "created_at": now} for i in range(2, 12)]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert len(candidates) <= 2  # SEARCH_GRAPH_MAX_NEIGHBORS default

    def test_accumulates_multi_seed_support(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed A", "source": "test/proj", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "seed B", "source": "test/proj", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "shared neighbor", "source": "test/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025), (2, 0.020)], 0.1, None, False)
        assert 3 in candidates
        assert len(candidates[3]["graph_via"]) == 2
        assert candidates[3]["graph_support"] == pytest.approx(0.00225, abs=0.0001)

    def test_caps_bonus(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "s1", "source": "t", "created_at": now,
             "links": [{"to_id": 4, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "s2", "source": "t", "created_at": now,
             "links": [{"to_id": 4, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "s3", "source": "t", "created_at": now,
             "links": [{"to_id": 4, "type": "related_to", "created_at": now}]},
            {"id": 4, "text": "popular", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025), (2, 0.020), (3, 0.018)], 1.0, None, False)
        assert candidates[4]["graph_support"] <= 0.33 * 0.025 + 0.0001

    def test_excludes_self_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 1, "type": "related_to", "created_at": now}]},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 1 not in candidates

    def test_filter_then_cap_order(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "wip/proj", "created_at": now,
             "links": [
                 {"to_id": 2, "type": "related_to", "created_at": now},
                 {"to_id": 3, "type": "related_to", "created_at": now},
                 {"to_id": 4, "type": "related_to", "created_at": now},
             ]},
            {"id": 2, "text": "wrong scope", "source": "learning/other", "created_at": now},
            {"id": 3, "text": "wrong scope", "source": "learning/other", "created_at": now},
            {"id": 4, "text": "valid", "source": "wip/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, "wip/", False)
        assert 4 in candidates

    def test_empty_direct_results(self, engine):
        candidates, info = engine._graph_expand([], 0.1, None, False)
        assert candidates == {}
        assert info["seeds"] == []

    def test_only_related_to_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [
                 {"to_id": 2, "type": "supersedes", "created_at": now},
                 {"to_id": 3, "type": "related_to", "created_at": now},
             ]},
            {"id": 2, "text": "superseded", "source": "t", "created_at": now},
            {"id": 3, "text": "related", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 3 in candidates
        assert 2 not in candidates


class TestHybridSearchGraph:
    """Test graph_weight parameter integration in hybrid_search()."""

    @pytest.fixture
    def engine(self, tmp_path):
        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.ensure_payload_indexes.return_value = None
            mock_store.count.return_value = 0
            mock_store.search.return_value = []
            MockStore.return_value = mock_store
            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings
            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            return eng

    def test_graph_weight_zero_no_annotations(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "test fact", "source": "test", "created_at": now}]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="test", graph_weight=0.0)
        for r in results:
            assert "match_type" not in r
            assert "graph_support" not in r

    def test_graph_weight_positive_adds_annotations(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "test fact", "source": "test", "created_at": now}]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="test", graph_weight=0.1)
        for r in results:
            assert r["match_type"] == "direct"
            assert r["graph_support"] == 0.0
            assert r["graph_via"] == []
            assert r["base_rrf_score"] == r["rrf_score"]

    def test_graph_weight_clamped(self, engine):
        engine.hybrid_search(query="test", graph_weight=-0.5)  # should not raise
        engine.hybrid_search(query="test", graph_weight=5.0)   # should not raise

    def test_graph_neighbor_appears_in_results(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "Python project uses FastAPI", "source": "test/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "Deployment uses Docker", "source": "test/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="Python FastAPI", k=5, graph_weight=0.1)
        result_ids = [r["id"] for r in results]
        if 2 in result_ids:
            r2 = next(r for r in results if r["id"] == 2)
            assert r2["match_type"] in ("graph", "direct+graph")
            assert r2["graph_support"] > 0

    def test_graph_only_not_reinforced(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "direct match query word", "source": "test", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "completely unrelated different text", "source": "test", "created_at": now},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        original_updated = engine.metadata[1].get("updated_at")
        engine.hybrid_search(query="direct match query word", graph_weight=0.1)
        assert engine.metadata[1].get("updated_at") == original_updated


class TestHybridSearchExplainGraph:
    """Test graph section in hybrid_search_explain()."""

    @pytest.fixture
    def engine(self, tmp_path):
        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.ensure_payload_indexes.return_value = None
            mock_store.count.return_value = 0
            mock_store.search.return_value = []
            MockStore.return_value = mock_store
            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings
            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            return eng

    def test_explain_includes_graph_section(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "test fact", "source": "test", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "linked fact", "source": "test", "created_at": now},
            {"id": 3, "text": "unrelated other document", "source": "test", "created_at": now},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        result = engine.hybrid_search_explain(query="test", graph_weight=0.1)
        assert "graph" in result["explain"]
        assert result["explain"]["graph"]["enabled"] is True
        assert len(result["explain"]["graph"]["seeds"]) > 0

    def test_explain_graph_disabled_when_zero(self, engine):
        result = engine.hybrid_search_explain(query="test", graph_weight=0.0)
        assert "graph" in result["explain"]
        assert result["explain"]["graph"]["enabled"] is False
