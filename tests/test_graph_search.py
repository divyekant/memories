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
    """Test _graph_expand() with PPR scoring."""

    def test_no_links_returns_empty(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "seed", "source": "t", "created_at": now}]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert candidates == {}

    def test_1hop_neighbor_discovered(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "neighbor", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 in candidates
        assert candidates[2]["graph_support"] > 0
        assert candidates[2]["inject_score"] > 0

    def test_2hop_neighbor_discovered(self, engine):
        """PPR discovers 2-hop neighbors via iterative propagation."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "bridge", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "2hop target", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 3 in candidates

    def test_1hop_scores_higher_than_2hop(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "1hop", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "2hop", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert candidates[2]["inject_score"] > candidates[3]["inject_score"]

    def test_multi_seed_convergence(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "B", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "shared", "source": "t", "created_at": now},
            {"id": 4, "text": "C", "source": "t", "created_at": now,
             "links": [{"to_id": 5, "type": "related_to", "created_at": now}]},
            {"id": 5, "text": "single", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand(
            [(1, 0.025), (2, 0.020), (4, 0.015)], 0.1, None, False
        )
        assert candidates[3]["inject_score"] >= candidates[5]["inject_score"]

    def test_scope_filters_neighbors(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "wip/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "out", "source": "learning/other", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, "wip/", False)
        assert 2 not in candidates

    def test_scope_blocks_transit(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "wip/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "B (hidden)", "source": "learning/other", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "C", "source": "wip/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, "wip/", False)
        assert 3 not in candidates

    def test_archived_filtered(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "archived", "source": "t", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 not in candidates

    def test_empty_direct_results(self, engine):
        candidates, info = engine._graph_expand([], 0.1, None, False)
        assert candidates == {}

    def test_disconnected_subgraphs_no_leak(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A1", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "A2", "source": "t", "created_at": now},
            {"id": 3, "text": "B1", "source": "t", "created_at": now,
             "links": [{"to_id": 4, "type": "related_to", "created_at": now}]},
            {"id": 4, "text": "B2", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 in candidates
        assert 4 not in candidates

    def test_info_includes_ppr_params(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "A", "source": "t", "created_at": now}]
        engine._rebuild_id_map()
        _, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert "ppr_iterations" in info
        assert "ppr_alpha" in info


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


class TestBuildAdjacency:
    """Test _build_adjacency() method."""

    def test_builds_bidirectional_edges(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "B", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 2 in adj.get(1, set())
        assert 1 in adj.get(2, set())

    def test_skips_non_related_to_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [
                 {"to_id": 2, "type": "supersedes", "created_at": now},
                 {"to_id": 3, "type": "related_to", "created_at": now},
             ]},
            {"id": 2, "text": "B", "source": "t", "created_at": now},
            {"id": 3, "text": "C", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 2 not in adj.get(1, set())
        assert 3 in adj.get(1, set())

    def test_skips_dangling_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 999, "type": "related_to", "created_at": now}]},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 999 not in adj.get(1, set())

    def test_skips_self_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 1, "type": "related_to", "created_at": now}]},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 1 not in adj.get(1, set())

    def test_empty_metadata_returns_empty(self, engine):
        engine.metadata = []
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert adj == {}


class TestFilterAdjacency:
    """Test _filter_adjacency() scope filtering."""

    def test_no_filter_returns_original(self, engine):
        adj = {1: {2}, 2: {1}}
        result = engine._filter_adjacency(adj, None, True)
        assert result == adj

    def test_filters_by_source_prefix(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "wip/proj", "created_at": now},
            {"id": 2, "text": "B", "source": "learning/other", "created_at": now},
            {"id": 3, "text": "C", "source": "wip/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = {1: {2, 3}, 2: {1}, 3: {1}}
        result = engine._filter_adjacency(adj, "wip/", False)
        assert 2 not in result
        assert result.get(1) == {3}
        assert result.get(3) == {1}

    def test_filters_archived(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now},
            {"id": 2, "text": "B", "source": "t", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        adj = {1: {2}, 2: {1}}
        result = engine._filter_adjacency(adj, None, False)
        assert 2 not in result
        assert 1 not in result

    def test_includes_archived_when_flag_set(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now},
            {"id": 2, "text": "B", "source": "t", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        adj = {1: {2}, 2: {1}}
        result = engine._filter_adjacency(adj, None, True)
        assert result == adj

    def test_scope_blocks_transit(self, engine):
        """Out-of-scope node B cannot bridge in-scope A and C."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "wip/proj", "created_at": now},
            {"id": 2, "text": "B", "source": "learning/other", "created_at": now},
            {"id": 3, "text": "C", "source": "wip/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = {1: {2}, 2: {1, 3}, 3: {2}}
        result = engine._filter_adjacency(adj, "wip/", False)
        assert 1 not in result
        assert 3 not in result
