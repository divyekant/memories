"""Tests for conflict detection in the extraction pipeline."""

import json
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

from llm_extract import execute_actions, AUDN_PROMPT


class TestConflictInAUDNPrompt:
    """Verify the AUDN prompt includes CONFLICT as a valid action."""

    def test_audn_prompt_mentions_conflict(self):
        assert "CONFLICT" in AUDN_PROMPT

    def test_audn_prompt_describes_conflict_action(self):
        assert "old_id" in AUDN_PROMPT.lower() or "contradicts" in AUDN_PROMPT.lower()


class TestExecuteConflictAction:
    """Test that execute_actions handles CONFLICT decisions."""

    @pytest.fixture
    def engine(self):
        eng = MagicMock()
        eng.add_memories.return_value = [42]
        eng.get_memory.return_value = {"id": 10, "text": "We chose Postgres", "source": "test"}
        eng.is_novel.return_value = (True, None)
        return eng

    def test_conflict_stores_new_memory(self, engine):
        facts = [{"category": "decision", "text": "We chose SQLite for the cache layer"}]
        actions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]

        result = execute_actions(engine, actions, facts, source="test")

        engine.add_memories.assert_called_once()
        assert result["stored_count"] == 1

    def test_conflict_preserves_existing_memory(self, engine):
        facts = [{"category": "decision", "text": "We chose SQLite"}]
        actions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]

        result = execute_actions(engine, actions, facts, source="test")

        # Should NOT delete the existing memory
        engine.delete_memory.assert_not_called()

    def test_conflict_metadata_includes_conflicts_with(self, engine):
        facts = [{"category": "decision", "text": "We chose SQLite"}]
        actions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]

        execute_actions(engine, actions, facts, source="test")

        call_args = engine.add_memories.call_args
        metadata_list = call_args.kwargs.get("metadata_list") or call_args[1].get("metadata_list", [])
        assert metadata_list[0]["conflicts_with"] == 10

    def test_conflict_action_in_results(self, engine):
        facts = [{"category": "decision", "text": "We chose SQLite"}]
        actions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]

        result = execute_actions(engine, actions, facts, source="test")

        action_result = result["actions"][0]
        assert action_result["action"] == "conflict"
        assert action_result["conflicts_with"] == 10

    def test_conflict_count_tracked(self, engine):
        facts = [
            {"category": "decision", "text": "We chose SQLite"},
            {"category": "decision", "text": "We use REST not GraphQL"},
        ]
        actions = [
            {"action": "CONFLICT", "fact_index": 0, "old_id": 10},
            {"action": "CONFLICT", "fact_index": 1, "old_id": 11},
        ]
        engine.get_memory.side_effect = [
            {"id": 10, "text": "We chose Postgres", "source": "test"},
            {"id": 11, "text": "We use GraphQL", "source": "test"},
        ]

        result = execute_actions(engine, actions, facts, source="test")

        assert result["conflict_count"] == 2

    def test_conflict_without_old_id_falls_back_to_add(self, engine):
        facts = [{"category": "decision", "text": "We chose SQLite"}]
        actions = [{"action": "CONFLICT", "fact_index": 0}]

        result = execute_actions(engine, actions, facts, source="test")

        # Should still store, just without conflicts_with
        assert result["stored_count"] == 1

    def test_conflict_old_id_must_be_inside_allowed_prefixes(self, engine):
        """Scoped extraction must not attach conflicts to out-of-scope memories."""
        engine.get_memory.return_value = {
            "id": 10,
            "text": "We chose Postgres",
            "source": "other-project/decisions",
        }
        facts = [{"category": "decision", "text": "We chose SQLite"}]
        actions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]

        result = execute_actions(
            engine,
            actions,
            facts,
            source="allowed-project/decisions",
            allowed_prefixes=["allowed-project/"],
        )

        engine.add_memories.assert_not_called()
        assert result["stored_count"] == 0
        assert result["conflict_count"] == 0
        assert result["actions"][0]["action"] == "error"
        assert "old_id not authorized for conflict" in result["actions"][0]["error"]


class TestConflictsEndpoint:
    """Test the /memory/conflicts API endpoint."""

    @pytest.fixture
    def client(self):
        import importlib
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = [
                    {"id": 1, "text": "We chose Postgres", "source": "test"},
                    {"id": 2, "text": "We chose SQLite", "source": "test",
                     "conflicts_with": 1},
                    {"id": 3, "text": "Unrelated fact", "source": "test"},
                ]
                app_module.memory = mock_engine

                from fastapi.testclient import TestClient
                yield TestClient(app_module.app), mock_engine

    def test_conflicts_endpoint_returns_conflicts(self, client):
        tc, mock = client
        resp = tc.get("/memory/conflicts")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["conflicts"]) == 1
        assert body["conflicts"][0]["id"] == 2
        assert body["conflicts"][0]["conflicts_with"] == 1

    def test_conflicts_endpoint_includes_conflicting_memory(self, client):
        tc, mock = client
        mock.get_memory.return_value = {"id": 1, "text": "We chose Postgres", "source": "test"}
        resp = tc.get("/memory/conflicts")
        body = resp.json()
        conflict = body["conflicts"][0]
        assert "conflicting_memory" in conflict
