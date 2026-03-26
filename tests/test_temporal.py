"""Tests for temporal reasoning features."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call
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

        # Make mock store return all metadata items as search hits so
        # vector search produces results for temporal filter tests.
        def _dynamic_search(**kwargs):
            return [{"id": m["id"], "score": 0.9} for m in eng.metadata]
        mock_store.search.side_effect = _dynamic_search

        return eng


class TestReinforce:
    def test_reinforce_sets_last_reinforced_at(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "test", "source": "t", "created_at": now, "updated_at": now}]
        engine._rebuild_id_map()
        engine.reinforce(1)
        assert "last_reinforced_at" in engine.metadata[0]

    def test_reinforce_does_not_change_updated_at(self, engine):
        old_time = "2025-01-01T00:00:00+00:00"
        engine.metadata = [{"id": 1, "text": "test", "source": "t", "created_at": old_time, "updated_at": old_time}]
        engine._rebuild_id_map()
        engine.reinforce(1)
        assert engine.metadata[0]["updated_at"] == old_time

    def test_confidence_uses_last_reinforced_at(self, engine):
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        engine.metadata = [{"id": 1, "text": "t", "source": "t", "created_at": old, "updated_at": old, "last_reinforced_at": recent}]
        engine._rebuild_id_map()
        result = engine._enrich_with_confidence(engine.metadata[0].copy())
        assert result.get("confidence", 0) > 0.5


class TestVersionPreservation:
    def test_update_archives_old_memory(self):
        from llm_extract import execute_actions
        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        mock_engine.add_memories.return_value = [101]
        mock_engine.add_link.return_value = {}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original", "category": "decision"}]
        result = execute_actions(mock_engine, actions, facts, source="test/proj")

        mock_engine.delete_memory.assert_not_called()
        mock_engine.update_memory.assert_called_once()
        call_args = mock_engine.update_memory.call_args
        assert call_args[1].get("archived") is True or (len(call_args[0]) > 1 and call_args[0][1] is True)

    def test_update_creates_supersedes_link(self):
        from llm_extract import execute_actions
        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        mock_engine.add_memories.return_value = [101]
        mock_engine.add_link.return_value = {}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original", "category": "decision"}]
        result = execute_actions(mock_engine, actions, facts, source="test/proj")

        mock_engine.add_link.assert_called_once_with(101, 42, "supersedes")

    def test_update_sets_is_latest_on_new_memory(self):
        from llm_extract import execute_actions
        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        mock_engine.add_memories.return_value = [101]
        mock_engine.add_link.return_value = {}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original", "category": "decision"}]
        result = execute_actions(mock_engine, actions, facts, source="test/proj")

        add_call = mock_engine.add_memories.call_args
        metadata = add_call.kwargs.get("metadata_list", [{}])[0]
        assert metadata.get("is_latest") is True


class TestTemporalFilters:
    def test_since_excludes_old(self, engine):
        engine.metadata = [
            {"id": 1, "text": "old fact", "source": "t", "created_at": "2023-01-01T00:00:00+00:00", "document_at": "2023-01-01T00:00:00+00:00"},
            {"id": 2, "text": "new fact", "source": "t", "created_at": "2023-06-01T00:00:00+00:00", "document_at": "2023-06-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact", since="2023-03-01T00:00:00+00:00")
        ids = [r["id"] for r in results]
        assert 1 not in ids
        assert 2 in ids

    def test_until_excludes_future(self, engine):
        engine.metadata = [
            {"id": 1, "text": "old fact", "source": "t", "created_at": "2023-01-01T00:00:00+00:00", "document_at": "2023-01-01T00:00:00+00:00"},
            {"id": 2, "text": "new fact", "source": "t", "created_at": "2023-06-01T00:00:00+00:00", "document_at": "2023-06-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact", until="2023-03-01T00:00:00+00:00")
        ids = [r["id"] for r in results]
        assert 1 in ids
        assert 2 not in ids

    def test_range_filter(self, engine):
        engine.metadata = [
            {"id": 1, "text": "jan fact", "source": "t", "created_at": "2023-01-15T00:00:00+00:00", "document_at": "2023-01-15T00:00:00+00:00"},
            {"id": 2, "text": "mar fact", "source": "t", "created_at": "2023-03-15T00:00:00+00:00", "document_at": "2023-03-15T00:00:00+00:00"},
            {"id": 3, "text": "jun fact", "source": "t", "created_at": "2023-06-15T00:00:00+00:00", "document_at": "2023-06-15T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact", since="2023-02-01T00:00:00+00:00", until="2023-05-01T00:00:00+00:00")
        ids = [r["id"] for r in results]
        assert 2 in ids
        assert 1 not in ids
        assert 3 not in ids

    def test_missing_document_at_uses_created_at(self, engine):
        engine.metadata = [
            {"id": 1, "text": "no doc date", "source": "t", "created_at": "2023-03-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="no doc date", since="2023-01-01T00:00:00+00:00")
        assert len(results) >= 1

    def test_no_filters_returns_all(self, engine):
        engine.metadata = [
            {"id": 1, "text": "fact one", "source": "t", "created_at": "2023-01-01T00:00:00+00:00"},
            {"id": 2, "text": "fact two", "source": "t", "created_at": "2023-06-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact")
        assert len(results) == 2
