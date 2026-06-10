"""Tests for the extraction novelty throttle.

Covers (1) the strengthened DEFAULT extraction profile (target decisions,
learnings, preferences, deferred work; noop session narration) and (2) the
EXTRACT_NOVELTY_GATE applied to extraction ADDs.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from extraction_profiles import DEFAULTS, DEFAULT_RULES, ExtractionProfiles
from llm_provider import CompletionResult


def _cr(text, input_tokens=10, output_tokens=5):
    return CompletionResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


@pytest.fixture
def ep(tmp_path):
    return ExtractionProfiles(str(tmp_path / "profiles.json"))


class TestDefaultProfileRules:
    def test_default_rules_are_non_empty(self):
        assert DEFAULT_RULES["always_remember"]
        assert DEFAULT_RULES["never_remember"]
        assert DEFAULTS["rules"] == DEFAULT_RULES

    def test_default_rules_target_durable_categories(self):
        always = " ".join(DEFAULT_RULES["always_remember"]).lower()
        assert "decision" in always
        assert "learning" in always
        assert "preference" in always
        assert "deferred" in always

    def test_default_rules_reject_session_narration(self):
        never = " ".join(DEFAULT_RULES["never_remember"]).lower()
        assert "session narration" in never or "running commentary" in never
        assert "code" in never  # restating code that lives in the repo
        assert "ephemeral" in never

    def test_resolve_returns_default_rules_when_no_profiles(self, ep):
        result = ep.resolve("claude-code/some-project")
        assert result["rules"] == DEFAULT_RULES

    def test_explicit_profile_rules_replace_defaults(self, ep):
        ep.put("claude-code/", {"rules": {"never_remember": ["passwords"]}})
        result = ep.resolve("claude-code/some-project")
        assert result["rules"] == {"never_remember": ["passwords"]}

    def test_explicit_empty_rules_disable_defaults(self, ep):
        ep.put("claude-code/", {"rules": {}})
        result = ep.resolve("claude-code/some-project")
        assert result["rules"] == {}

    def test_resolved_rules_are_a_copy(self, ep):
        result = ep.resolve("claude-code/some-project")
        result["rules"]["never_remember"].append("mutated")
        assert "mutated" not in DEFAULT_RULES["never_remember"]
        assert ep.resolve("claude-code/some-project")["rules"] == DEFAULT_RULES


class TestRulesReachExtractionPrompt:
    def test_extract_facts_appends_rules_to_system_prompt(self):
        from llm_extract import extract_facts

        provider = MagicMock()
        provider.complete.return_value = _cr("[]")
        extract_facts(provider, "user: hello", source="claude-code/app",
                      rules=DEFAULT_RULES)
        system_prompt = provider.complete.call_args[0][0]
        assert "NEVER remember" in system_prompt
        assert "Session narration" in system_prompt
        assert "ALWAYS remember" in system_prompt

    def test_extract_facts_without_rules_keeps_base_prompt(self):
        from llm_extract import extract_facts

        provider = MagicMock()
        provider.complete.return_value = _cr("[]")
        extract_facts(provider, "user: hello", source="claude-code/app")
        system_prompt = provider.complete.call_args[0][0]
        assert "NEVER remember" not in system_prompt

    def test_run_extraction_passes_default_profile_rules_to_extraction(self, ep):
        from llm_extract import run_extraction

        provider = MagicMock()
        provider.supports_audn = True
        provider.complete.side_effect = [_cr("[]")]
        profile = ep.resolve("claude-code/app")

        run_extraction(provider, MagicMock(), messages="user: real talk",
                       source="claude-code/app", context="stop", profile=profile)

        system_prompt = provider.complete.call_args_list[0][0][0]
        assert "Session narration" in system_prompt

    def test_run_extraction_passes_default_rules_to_audn(self, ep):
        from llm_extract import run_extraction

        provider = MagicMock()
        provider.supports_audn = True
        provider.complete.side_effect = [
            _cr(json.dumps([{"category": "DECISION", "text": "Use uv for Python deps"}])),
            _cr(json.dumps([{"action": "ADD", "fact_index": 0}])),
        ]
        engine = MagicMock()
        engine.hybrid_search.return_value = []
        engine.is_novel.return_value = (True, None)
        engine.add_memories.return_value = [11]
        profile = ep.resolve("claude-code/app")

        run_extraction(provider, engine, messages="user: let's use uv",
                       source="claude-code/app", context="stop", profile=profile)

        audn_prompt = provider.complete.call_args_list[1][0][1]
        assert "Session narration" in audn_prompt


class TestNoveltyGate:
    """EXTRACT_NOVELTY_GATE: extraction ADDs must pass an explicit novelty
    check; near-duplicates become noops instead of new memories."""

    def _engine(self, novel=True, similar=None):
        engine = MagicMock()
        engine.is_novel.return_value = (novel, similar)
        engine.add_memories.return_value = [100]
        return engine

    def test_gate_noops_near_duplicate_add(self):
        from llm_extract import execute_actions

        similar = {"id": 7, "text": "existing", "similarity": 0.91}
        engine = self._engine(novel=False, similar=similar)
        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "near duplicate fact", "category": "detail"}]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert result["stored_count"] == 0
        assert result["gated_count"] == 1
        engine.add_memories.assert_not_called()
        action = result["actions"][0]
        assert action["action"] == "noop"
        assert action["reason"] == "novelty_gate"
        assert action["existing_id"] == 7
        assert action["similarity"] == 0.91

    def test_gate_passes_novel_add(self):
        from llm_extract import execute_actions

        engine = self._engine(novel=True)
        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "a genuinely new fact", "category": "decision"}]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert result["stored_count"] == 1
        assert result["gated_count"] == 0
        engine.add_memories.assert_called_once()

    def test_gate_applies_to_fallback_add(self):
        from llm_extract import execute_actions

        engine = self._engine(novel=False, similar={"id": 3, "similarity": 0.9})
        actions = [{"action": "FALLBACK_ADD", "fact_index": 0}]
        facts = [{"text": "dup", "category": "detail"}]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert result["stored_count"] == 0
        assert result["fallback_count"] == 0
        engine.add_memories.assert_not_called()

    def test_gate_disabled_via_env(self, monkeypatch):
        from llm_extract import execute_actions

        monkeypatch.setenv("EXTRACT_NOVELTY_GATE", "0")
        engine = self._engine(novel=False, similar={"id": 3, "similarity": 0.99})
        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "dup but gate off", "category": "detail"}]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert result["stored_count"] == 1
        engine.is_novel.assert_not_called()

    def test_gate_disabled_via_param(self):
        from llm_extract import execute_actions

        engine = self._engine(novel=False, similar={"id": 3, "similarity": 0.99})
        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "dup but caller opted out", "category": "detail"}]

        result = execute_actions(engine, actions, facts, source="test/proj",
                                 novelty_gate=False)

        assert result["stored_count"] == 1
        engine.is_novel.assert_not_called()

    def test_gate_threshold_configurable_via_env(self, monkeypatch):
        from llm_extract import execute_actions

        monkeypatch.setenv("EXTRACT_NOVELTY_THRESHOLD", "0.7")
        engine = self._engine(novel=True)
        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "fact", "category": "detail"}]

        execute_actions(engine, actions, facts, source="test/proj")

        assert engine.is_novel.call_args.kwargs.get("threshold") == 0.7

    def test_gate_default_threshold(self, monkeypatch):
        from llm_extract import execute_actions

        monkeypatch.delenv("EXTRACT_NOVELTY_THRESHOLD", raising=False)
        engine = self._engine(novel=True)
        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "fact", "category": "detail"}]

        execute_actions(engine, actions, facts, source="test/proj")

        assert engine.is_novel.call_args.kwargs.get("threshold") == 0.85

    def test_gate_fails_open_on_engine_error(self):
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.is_novel.side_effect = RuntimeError("search backend down")
        engine.add_memories.return_value = [100]
        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "fact stored despite gate error", "category": "detail"}]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert result["stored_count"] == 1
        engine.add_memories.assert_called_once()

    def test_gate_does_not_apply_to_update(self):
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        engine.add_memories.return_value = [101]
        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated"}]
        facts = [{"text": "orig", "category": "decision"}]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert result["updated_count"] == 1
        engine.is_novel.assert_not_called()

    def test_gate_does_not_apply_to_conflict(self):
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.get_memory.return_value = {"id": 10, "source": "test", "text": "old"}
        engine.add_memories.return_value = [43]
        actions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]
        facts = [{"text": "contradicting fact", "category": "decision"}]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert result["conflict_count"] == 1
        engine.is_novel.assert_not_called()

    def test_gate_keeps_positional_correspondence(self):
        """_apply_maintenance depends on one result action per input action."""
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.is_novel.side_effect = [
            (False, {"id": 1, "similarity": 0.95}),
            (True, None),
        ]
        engine.add_memories.return_value = [200]
        actions = [
            {"action": "ADD", "fact_index": 0},
            {"action": "ADD", "fact_index": 1},
        ]
        facts = [
            {"text": "dup fact", "category": "detail"},
            {"text": "novel fact", "category": "detail"},
        ]

        result = execute_actions(engine, actions, facts, source="test/proj")

        assert len(result["actions"]) == 2
        assert result["actions"][0]["action"] == "noop"
        assert result["actions"][1]["action"] == "add"
        assert result["stored_count"] == 1
        assert result["gated_count"] == 1

    def test_full_pipeline_counts_gated_adds(self):
        from llm_extract import run_extraction

        provider = MagicMock()
        provider.supports_audn = True
        provider.complete.side_effect = [
            _cr(json.dumps([{"category": "DETAIL", "text": "Repeated session detail"}])),
            _cr(json.dumps([{"action": "ADD", "fact_index": 0}])),
        ]
        engine = MagicMock()
        engine.hybrid_search.return_value = []
        engine.is_novel.return_value = (False, {"id": 9, "similarity": 0.93})

        result = run_extraction(provider, engine, messages="user: same thing again",
                                source="test/proj", context="stop")

        assert result["stored_count"] == 0
        assert result["gated_count"] == 1
        engine.add_memories.assert_not_called()


class TestCommitBypassesGate:
    """Human-approved dry-run commits must not be silently gated."""

    def test_extract_commit_passes_novelty_gate_false(self):
        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {"API_KEY": "test-key"}):
            import importlib
            import app as app_module
            importlib.reload(app_module)

            app_module.memory = MagicMock()
            with patch("llm_extract.execute_actions") as exec_mock:
                exec_mock.return_value = {
                    "actions": [], "stored_count": 0, "updated_count": 0,
                    "deleted_count": 0, "conflict_count": 0, "fallback_count": 0,
                }
                client = TestClient(app_module.app)
                resp = client.post(
                    "/memory/extract/commit",
                    json={
                        "source": "test/proj",
                        "actions": [{"action": "ADD", "fact_index": 0, "approved": True,
                                     "fact": {"text": "approved fact", "category": "detail"}}],
                    },
                    headers={"X-API-Key": "test-key"},
                )
                assert resp.status_code == 200
                assert exec_mock.call_args.kwargs.get("novelty_gate") is False
