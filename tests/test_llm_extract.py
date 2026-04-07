"""Tests for llm_extract module."""
import pytest
import json
from unittest.mock import MagicMock, patch
from llm_provider import CompletionResult


def _cr(text, input_tokens=10, output_tokens=5):
    """Helper to build CompletionResult from text."""
    return CompletionResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


class TestFactExtraction:
    """Test extract_facts() function."""

    def test_extracts_facts_from_conversation(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps([
            {"category": "DECISION", "text": "User prefers Drizzle ORM over Prisma"},
            {"category": "DETAIL", "text": "Project uses TypeScript strict mode"}
        ]))

        facts = extract_facts(mock_provider, "User: let's use drizzle\nAssistant: Good choice!")
        assert len(facts) == 2
        assert "Drizzle" in facts[0]["text"]

    def test_returns_empty_when_nothing_worth_storing(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr("[]")

        facts = extract_facts(mock_provider, "User: hi\nAssistant: hello!")
        assert facts == []

    def test_handles_llm_returning_non_json(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr("Sorry, I can't extract facts from this.")

        facts = extract_facts(mock_provider, "User: hi")
        assert facts == []

    def test_pre_compact_context_uses_aggressive_prompt(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr("[]")

        extract_facts(mock_provider, "some messages", context="pre_compact")
        call_args = mock_provider.complete.call_args
        system_prompt = call_args[0][0] if call_args[0] else call_args[1].get("system", "")
        assert "thorough" in system_prompt.lower()

    def test_caps_fact_count_and_length(self):
        from llm_extract import extract_facts, EXTRACT_MAX_FACTS, EXTRACT_MAX_FACT_CHARS

        mock_provider = MagicMock()
        oversized_fact = "x" * (EXTRACT_MAX_FACT_CHARS + 300)
        mock_provider.complete.return_value = _cr(json.dumps(
            [{"category": "DETAIL", "text": oversized_fact}] * (EXTRACT_MAX_FACTS + 10)
        ))

        facts = extract_facts(mock_provider, "User: test")
        assert len(facts) == EXTRACT_MAX_FACTS
        assert all(len(f["text"]) <= EXTRACT_MAX_FACT_CHARS for f in facts)
        assert all(f["text"].endswith("...") for f in facts)


class TestCategoryExtraction:
    """Test that extract_facts returns categorized facts."""

    def test_extracts_categorized_facts(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps([
            {"category": "DECISION", "text": "Chose Drizzle over Prisma for smaller Docker images"},
            {"category": "LEARNING", "text": "Prisma query engine adds 40MB to images"},
        ]))

        facts = extract_facts(mock_provider, "User: which ORM?\nAssistant: Let's use Drizzle")
        assert len(facts) == 2
        assert facts[0]["category"] == "decision"
        assert facts[0]["text"] == "Chose Drizzle over Prisma for smaller Docker images"
        assert facts[1]["category"] == "learning"

    def test_falls_back_to_plain_strings(self):
        """Old-format plain string arrays still work (backward compat)."""
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps([
            "Chose Drizzle over Prisma"
        ]))

        facts = extract_facts(mock_provider, "User: which ORM?")
        assert len(facts) == 1
        assert facts[0]["category"] == "detail"
        assert facts[0]["text"] == "Chose Drizzle over Prisma"

    def test_source_project_name_in_prompt(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr("[]")

        extract_facts(mock_provider, "some messages", source="claude-code/my-app")
        system_prompt = mock_provider.complete.call_args[0][0]
        assert "my-app" in system_prompt

    def test_source_without_slash_uses_whole_source(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr("[]")

        extract_facts(mock_provider, "some messages", source="my-project")
        system_prompt = mock_provider.complete.call_args[0][0]
        assert "my-project" in system_prompt

    def test_empty_source_uses_this(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr("[]")

        extract_facts(mock_provider, "some messages", source="")
        system_prompt = mock_provider.complete.call_args[0][0]
        assert "this" in system_prompt

    def test_invalid_category_falls_back_to_detail(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps([
            {"category": "UNKNOWN", "text": "Some fact"},
        ]))

        facts = extract_facts(mock_provider, "User: test")
        assert len(facts) == 1
        assert facts[0]["category"] == "detail"

    def test_mixed_format_old_and_new(self):
        """Mix of old plain strings and new categorized objects."""
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps([
            {"category": "DECISION", "text": "Chose Redis for caching"},
            "Project uses Python 3.12",
        ]))

        facts = extract_facts(mock_provider, "User: test")
        assert len(facts) == 2
        assert facts[0]["category"] == "decision"
        assert facts[0]["text"] == "Chose Redis for caching"
        assert facts[1]["category"] == "detail"
        assert facts[1]["text"] == "Project uses Python 3.12"

    def test_return_error_true_returns_tuple(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps([
            {"category": "LEARNING", "text": "Some learning"},
        ]))

        facts, error, tokens = extract_facts(
            mock_provider, "User: test", return_error=True
        )
        assert len(facts) == 1
        assert error is None
        assert tokens["input"] == 10
        assert tokens["output"] == 5

    def test_return_error_true_on_failure(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.side_effect = Exception("LLM error")

        facts, error, tokens = extract_facts(
            mock_provider, "User: test", return_error=True
        )
        assert facts == []
        assert error == "LLM error"
        assert tokens == {"input": 0, "output": 0}


class TestAUDNCycle:
    """Test run_audn() function."""

    def test_add_new_fact(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "ADD", "fact_index": 0}
        ]))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        decisions, _, _ = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "Uses Drizzle ORM", "category": "decision"}],
            source="test/project"
        )
        assert len(decisions) == 1
        assert decisions[0]["action"] == "ADD"

    def test_noop_existing_fact(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "NOOP", "fact_index": 0, "existing_id": 42}
        ]))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 42, "text": "Uses Drizzle ORM", "similarity": 0.95}
        ]

        decisions, _, _ = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "Uses Drizzle ORM", "category": "decision"}],
            source="test/project"
        )
        assert len(decisions) == 1
        assert decisions[0]["action"] == "NOOP"

    def test_update_existing_fact(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "UPDATE", "fact_index": 0, "old_id": 10, "new_text": "Uses Drizzle ORM (switched from Prisma)"}
        ]))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 10, "text": "Uses Prisma ORM", "similarity": 0.75}
        ]

        decisions, _, _ = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "Switched from Prisma to Drizzle ORM", "category": "decision"}],
            source="test/project"
        )
        assert decisions[0]["action"] == "UPDATE"
        assert decisions[0]["old_id"] == 10

    def test_ollama_skips_audn_uses_novelty(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = False

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (True, None)

        decisions, _, _ = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "New fact", "category": "detail"}],
            source="test/project"
        )
        assert len(decisions) == 1
        assert decisions[0]["action"] == "ADD"
        mock_engine.is_novel.assert_called_once()
        mock_provider.complete.assert_not_called()

    def test_ollama_noop_for_existing(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = False

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (False, {"id": 5, "text": "Existing fact", "similarity": 0.95})

        decisions, _, _ = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "Existing fact", "category": "detail"}],
            source="test/project"
        )
        assert decisions[0]["action"] == "NOOP"

    def test_audn_prompt_truncates_similar_memory_text(self):
        from llm_extract import run_audn, EXTRACT_SIMILAR_TEXT_CHARS

        long_memory = "m" * (EXTRACT_SIMILAR_TEXT_CHARS + 500)

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps(
            [{"action": "ADD", "fact_index": 0}]
        ))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 42, "text": long_memory, "similarity": 0.95}
        ]

        run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "Uses Drizzle ORM", "category": "decision"}],
            source="test/project"
        )

        prompt = mock_provider.complete.call_args[0][1]
        assert "m" * (EXTRACT_SIMILAR_TEXT_CHARS + 50) not in prompt
        assert "..." in prompt

    def test_audn_prompt_includes_rrf_score_not_zero(self):
        """Verify similar_json sent to LLM includes actual RRF score, not 0.0."""
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "ADD", "fact_index": 0}
        ]))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 42, "text": "Uses Drizzle ORM", "rrf_score": 0.025}
        ]

        run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "New fact", "category": "decision"}],
            source="test/project"
        )

        prompt = mock_provider.complete.call_args[0][1]
        assert '"relevance":0.025' in prompt or '"relevance":0.02' in prompt
        assert '"relevance":0.0,' not in prompt  # must NOT be zero

    def test_audn_facts_json_includes_category(self):
        """Verify the facts_json sent to the LLM includes category."""
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "ADD", "fact_index": 0}
        ]))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "Uses Drizzle ORM", "category": "decision"}],
            source="test/project"
        )

        prompt = mock_provider.complete.call_args[0][1]
        assert '"category":"decision"' in prompt

    def test_audn_filters_similar_memories_by_allowed_prefixes(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "ADD", "fact_index": 0}
        ]))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 1, "text": "Allowed", "source": "claude-code/proj", "similarity": 0.9},
            {"id": 2, "text": "Blocked", "source": "other/secret", "similarity": 0.95},
        ]

        run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "Uses Drizzle ORM", "category": "decision"}],
            source="claude-code/proj",
            allowed_prefixes=["claude-code/*"],
        )

        prompt = mock_provider.complete.call_args[0][1]
        assert "Allowed" in prompt
        assert "Blocked" not in prompt

    def test_audn_returns_artifacts_dict_with_similar_per_fact(self):
        """run_audn() always returns audn_artifacts dict with similar_per_fact."""
        from llm_extract import run_audn
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([{"action": "ADD", "fact_index": 0}]))
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 5, "text": "Existing memory", "rrf_score": 0.025, "source": "test/proj"}
        ]
        decisions, tokens, artifacts = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "New fact", "category": "decision"}],
            source="test/project"
        )
        assert isinstance(artifacts, dict)
        assert "similar_per_fact" in artifacts
        assert 0 in artifacts["similar_per_fact"]
        assert artifacts["similar_per_fact"][0][0]["id"] == 5
        assert "debug_similar" not in artifacts

    def test_audn_artifacts_includes_debug_similar_when_debug(self):
        from llm_extract import run_audn
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([{"action": "ADD", "fact_index": 0}]))
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [{"id": 5, "text": "Existing memory", "rrf_score": 0.025}]
        _, _, artifacts = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "New fact", "category": "decision"}],
            source="test/project", debug=True,
        )
        assert "debug_similar" in artifacts
        assert 0 in artifacts["debug_similar"]

    def test_audn_ollama_returns_empty_artifacts(self):
        from llm_extract import run_audn
        mock_provider = MagicMock()
        mock_provider.supports_audn = False
        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (True, None)
        _, _, artifacts = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "New fact", "category": "detail"}],
            source="test/project"
        )
        assert isinstance(artifacts, dict)
        assert artifacts["similar_per_fact"] == {}

    def test_audn_empty_facts_returns_empty_artifacts(self):
        from llm_extract import run_audn
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        decisions, _, artifacts = run_audn(
            mock_provider, MagicMock(), facts=[], source="test/project"
        )
        assert decisions == []
        assert isinstance(artifacts, dict)
        assert artifacts["similar_per_fact"] == {}


class TestExecuteActions:
    """Test execute_actions() function."""

    def test_execute_add(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "New fact to store", "category": "decision"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["stored_count"] == 1
        mock_engine.add_memories.assert_called_once()
        # Verify API contract: sources must be a list, metadata includes category
        call_kwargs = mock_engine.add_memories.call_args
        assert call_kwargs.kwargs.get("metadata_list") == [{"category": "decision"}]

    def test_execute_add_passes_category_metadata(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "Bug: Redis timeout at 5s", "category": "learning"}]

        execute_actions(mock_engine, actions, facts, source="test/proj")
        call_kwargs = mock_engine.add_memories.call_args
        assert call_kwargs.kwargs.get("metadata_list") == [{"category": "learning"}]

    def test_execute_update_calls_supersede(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        mock_engine.add_memories.return_value = [101]
        mock_engine.add_link.return_value = {}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original fact", "category": "decision"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["updated_count"] == 1
        # Old memory is archived, not deleted (version preservation)
        mock_engine.delete_memory.assert_not_called()
        mock_engine.update_memory.assert_called_once_with(42, archived=True, metadata_patch={"is_latest": False})
        # Verify metadata includes category, supersedes, and is_latest
        call_kwargs = mock_engine.add_memories.call_args
        assert call_kwargs.kwargs.get("metadata_list") == [{"category": "decision", "supersedes": 42, "is_latest": True}]
        # Verify supersedes link is created
        mock_engine.add_link.assert_called_once_with(101, 42, "supersedes")

    def test_execute_noop_does_nothing(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        actions = [{"action": "NOOP", "fact_index": 0, "existing_id": 30}]
        facts = [{"text": "existing fact", "category": "detail"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["stored_count"] == 0
        assert result["updated_count"] == 0
        mock_engine.add_memories.assert_not_called()

    def test_execute_delete(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        actions = [{"action": "DELETE", "fact_index": 0, "old_id": 55}]
        facts = [{"text": "contradicted fact", "category": "detail"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["deleted_count"] == 1
        mock_engine.delete_memory.assert_called_once_with(55)

    def test_execute_with_out_of_bounds_fact_index(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "ADD", "fact_index": 99}]
        facts = [{"text": "only fact", "category": "detail"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["stored_count"] == 1
        call_kwargs = mock_engine.add_memories.call_args
        # Out-of-bounds should use default empty text
        assert call_kwargs.kwargs.get("texts") == [""]

    def test_execute_update_skips_disallowed_old_id(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "other/secret", "text": "old"}
        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original fact", "category": "decision"}]

        result = execute_actions(
            mock_engine,
            actions,
            facts,
            source="claude-code/proj",
            allowed_prefixes=["claude-code/*"],
        )
        assert result["updated_count"] == 0
        mock_engine.delete_memory.assert_not_called()
        mock_engine.add_memories.assert_not_called()
        assert any(a.get("action") == "error" for a in result["actions"])

    def test_execute_delete_skips_disallowed_old_id(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 55, "source": "other/secret", "text": "old"}
        actions = [{"action": "DELETE", "fact_index": 0, "old_id": 55}]
        facts = [{"text": "contradicted fact", "category": "detail"}]

        result = execute_actions(
            mock_engine,
            actions,
            facts,
            source="claude-code/proj",
            allowed_prefixes=["claude-code/*"],
        )
        assert result["deleted_count"] == 0
        mock_engine.delete_memory.assert_not_called()
        assert any(a.get("action") == "error" for a in result["actions"])


class TestFullPipeline:
    """Test run_extraction() end-to-end with mocks."""

    def test_full_extraction_pipeline(self):
        from llm_extract import run_extraction

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = [
            _cr(json.dumps([
                {"category": "DECISION", "text": "Uses Drizzle ORM"},
                {"category": "DETAIL", "text": "TypeScript strict mode"},
            ])),
            _cr(json.dumps([
                {"action": "ADD", "fact_index": 0},
                {"action": "NOOP", "fact_index": 1, "existing_id": 30}
            ]))
        ]

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 30, "text": "TypeScript strict mode", "similarity": 0.92}
        ]
        mock_engine.add_memories.return_value = [121]

        result = run_extraction(
            mock_provider, mock_engine,
            messages="User: use drizzle\nAssistant: Done",
            source="test/project",
            context="stop"
        )

        assert result["extracted_count"] == 2
        assert result["stored_count"] == 1
        assert len(result["actions"]) == 2

    def test_extraction_disabled_returns_error(self):
        from llm_extract import run_extraction

        result = run_extraction(
            provider=None,
            engine=MagicMock(),
            messages="some messages",
            source="test",
            context="stop"
        )
        assert result["error"] == "extraction_disabled"

    def test_provider_runtime_failure_returns_error_signal(self):
        from llm_extract import run_extraction

        mock_provider = MagicMock()
        mock_provider.complete.side_effect = Exception("429 Too Many Requests")
        mock_provider.supports_audn = True

        result = run_extraction(
            provider=mock_provider,
            engine=MagicMock(),
            messages="User: capture this decision",
            source="test",
            context="stop",
        )

        assert result["error"] == "provider_runtime_failure"
        assert result["error_stage"] == "extract_facts"
        assert "429" in result["error_message"]
        assert result["stored_count"] == 0

    def test_source_passed_to_extract_facts(self):
        """Verify run_extraction passes source to extract_facts for prompt formatting."""
        from llm_extract import run_extraction

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = [
            _cr("[]"),  # extract_facts returns empty
        ]

        run_extraction(
            mock_provider, MagicMock(),
            messages="User: test",
            source="claude-code/my-app",
            context="stop"
        )

        # The first complete call is extract_facts; check that source was used in prompt
        system_prompt = mock_provider.complete.call_args_list[0][0][0]
        assert "my-app" in system_prompt


class TestMaintenanceConfig:
    """Test maintenance configuration env vars."""

    def test_extract_max_links_zero_allowed(self):
        """EXTRACT_MAX_LINKS=0 must be supported (disables auto-linking)."""
        from llm_extract import _env_int
        import os
        with patch.dict(os.environ, {"EXTRACT_MAX_LINKS": "0"}):
            val = _env_int("EXTRACT_MAX_LINKS", 3, minimum=0)
        assert val == 0

    def test_extract_min_link_score_default(self):
        from llm_extract import _env_float
        val = _env_float("EXTRACT_MIN_LINK_SCORE", 0.005)
        assert val == 0.005


class TestApplyMaintenance:
    """Test _apply_maintenance() auto-linking and compaction detection."""

    def test_auto_links_created_for_add_action(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        mock_engine.add_link.return_value = {"from_id": 100, "to_id": 5, "type": "related_to"}
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New fact", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": 5, "text": "Similar memory", "rrf_score": 0.025, "source": "test/proj"},
            {"id": 6, "text": "Another memory", "rrf_score": 0.020, "source": "test/proj"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert len(result["links_created"]) == 2
        assert result["links_created"][0]["from_id"] == 100
        assert result["links_created"][0]["to_id"] == 5
        mock_engine.add_link.assert_any_call(100, 5, "related_to")
        mock_engine.add_link.assert_any_call(100, 6, "related_to")

    def test_auto_links_created_for_conflict_action(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        mock_engine.add_link.return_value = {"from_id": 200, "to_id": 10}
        decisions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]
        exec_result = {"actions": [{"action": "conflict", "text": "Conflicting fact", "id": 200, "conflicts_with": 10}]}
        audn_artifacts = {"similar_per_fact": {0: [{"id": 10, "text": "Original", "rrf_score": 0.028, "source": "test/proj"}]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert len(result["links_created"]) == 1
        mock_engine.add_link.assert_called_once_with(200, 10, "related_to")

    def test_no_links_for_update_delete_noop(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [
            {"action": "UPDATE", "fact_index": 0, "old_id": 1},
            {"action": "DELETE", "fact_index": 1, "old_id": 2},
            {"action": "NOOP", "fact_index": 2, "existing_id": 3},
        ]
        exec_result = {"actions": [
            {"action": "update", "old_id": 1, "text": "updated", "new_id": 50},
            {"action": "delete", "old_id": 2},
            {"action": "noop", "text": "existing", "existing_id": 3},
        ]}
        audn_artifacts = {"similar_per_fact": {
            0: [{"id": 10, "rrf_score": 0.025, "source": "t"}],
            1: [{"id": 11, "rrf_score": 0.020, "source": "t"}],
            2: [{"id": 12, "rrf_score": 0.018, "source": "t"}],
        }}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert result["links_created"] == []
        mock_engine.add_link.assert_not_called()

    def test_auto_links_created_for_fallback_add_action(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        mock_engine.add_link.return_value = {"from_id": 100, "to_id": 5, "type": "related_to"}
        decisions = [{"action": "FALLBACK_ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "fallback_add", "text": "Fallback fact", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": 5, "text": "Similar memory", "rrf_score": 0.025, "source": "test/proj"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert len(result["links_created"]) == 1
        mock_engine.add_link.assert_called_once_with(100, 5, "related_to")

    def test_max_links_caps_per_memory(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        mock_engine.add_link.return_value = {}
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": i, "rrf_score": 0.03 - i * 0.001, "source": "t"} for i in range(10)
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, max_links=2)
        assert len(result["links_created"]) == 2
        assert mock_engine.add_link.call_count == 2

    def test_min_link_score_filters_weak_matches(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        mock_engine.add_link.return_value = {}
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": 5, "rrf_score": 0.025, "source": "t"},
            {"id": 6, "rrf_score": 0.003, "source": "t"},
            {"id": 7, "rrf_score": 0.001, "source": "t"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, min_link_score=0.005)
        assert len(result["links_created"]) == 1
        assert result["links_created"][0]["to_id"] == 5

    def test_max_links_zero_disables_linking(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [{"id": 5, "rrf_score": 0.025, "source": "t"}]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, max_links=0)
        assert result["links_created"] == []
        mock_engine.add_link.assert_not_called()

    def test_error_and_skipped_actions_ignored(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "ADD", "fact_index": 0}, {"action": "ADD", "fact_index": 1}]
        exec_result = {"actions": [
            {"action": "error", "text": "failed", "error": "some error"},
            {"action": "skipped", "reason": "protected", "old_id": 99},
        ]}
        audn_artifacts = {"similar_per_fact": {
            0: [{"id": 5, "rrf_score": 0.025, "source": "t"}],
            1: [{"id": 6, "rrf_score": 0.020, "source": "t"}],
        }}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert result["links_created"] == []
        mock_engine.add_link.assert_not_called()

    def test_add_link_value_error_skipped_gracefully(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        mock_engine.add_link.side_effect = ValueError("Target memory 5 not found")
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [{"id": 5, "rrf_score": 0.025, "source": "t"}]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert result["links_created"] == []

    def test_empty_similar_per_fact_no_errors(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert result["links_created"] == []
        assert result["compaction_candidates"] == []

    def test_two_new_memories_can_link_to_same_target(self):
        """Per-edge dedup: different new memories MAY both link to same target."""
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        mock_engine.add_link.return_value = {}
        decisions = [{"action": "ADD", "fact_index": 0}, {"action": "ADD", "fact_index": 1}]
        exec_result = {"actions": [
            {"action": "add", "text": "Fact A", "id": 100},
            {"action": "add", "text": "Fact B", "id": 101},
        ]}
        audn_artifacts = {"similar_per_fact": {
            0: [{"id": 5, "rrf_score": 0.025, "source": "t"}],
            1: [{"id": 5, "rrf_score": 0.022, "source": "t"}],
        }}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert len(result["links_created"]) == 2
        assert mock_engine.add_link.call_count == 2
        mock_engine.add_link.assert_any_call(100, 5, "related_to")
        mock_engine.add_link.assert_any_call(101, 5, "related_to")

    def test_deleted_target_skipped_in_auto_linking(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "DELETE", "fact_index": 0, "old_id": 5}, {"action": "ADD", "fact_index": 1}]
        exec_result = {"actions": [
            {"action": "delete", "old_id": 5},
            {"action": "add", "text": "New", "id": 100},
        ]}
        audn_artifacts = {"similar_per_fact": {1: [{"id": 5, "rrf_score": 0.025, "source": "t"}]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts)
        assert result["links_created"] == []
        mock_engine.add_link.assert_not_called()

    def test_compaction_candidate_detected_for_tight_cluster(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": 5, "rrf_score": 0.025, "source": "learning/proj"},
            {"id": 6, "rrf_score": 0.024, "source": "learning/proj"},
            {"id": 7, "rrf_score": 0.023, "source": "claude-code/proj"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, max_links=0)
        assert len(result["compaction_candidates"]) == 1
        candidate = result["compaction_candidates"][0]
        assert candidate["fact_index"] == 0
        assert set(candidate["memory_ids"]) == {5, 6, 7}
        assert candidate["cross_source"] is True
        assert "learning/proj" in candidate["sources"]
        assert "claude-code/proj" in candidate["sources"]

    def test_no_compaction_for_fewer_than_three(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": 5, "rrf_score": 0.025, "source": "t"},
            {"id": 6, "rrf_score": 0.024, "source": "t"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, max_links=0)
        assert result["compaction_candidates"] == []

    def test_no_compaction_for_spread_scores(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": 5, "rrf_score": 0.030, "source": "t"},
            {"id": 6, "rrf_score": 0.020, "source": "t"},
            {"id": 7, "rrf_score": 0.010, "source": "t"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, max_links=0)
        assert result["compaction_candidates"] == []

    def test_same_source_compaction_not_cross_source(self):
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [{"action": "ADD", "fact_index": 0}]
        exec_result = {"actions": [{"action": "add", "text": "New", "id": 100}]}
        audn_artifacts = {"similar_per_fact": {0: [
            {"id": 5, "rrf_score": 0.025, "source": "learning/proj"},
            {"id": 6, "rrf_score": 0.024, "source": "learning/proj"},
            {"id": 7, "rrf_score": 0.023, "source": "learning/proj"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, max_links=0)
        assert len(result["compaction_candidates"]) == 1
        assert result["compaction_candidates"][0]["cross_source"] is False

    def test_compaction_excludes_deleted_memories(self):
        """Memories deleted in the same batch should not appear in compaction candidates."""
        from llm_extract import _apply_maintenance
        mock_engine = MagicMock()
        decisions = [
            {"action": "DELETE", "fact_index": 0, "old_id": 5},
            {"action": "ADD", "fact_index": 1},
        ]
        exec_result = {"actions": [
            {"action": "delete", "old_id": 5},
            {"action": "add", "text": "New", "id": 100},
        ]}
        audn_artifacts = {"similar_per_fact": {1: [
            {"id": 5, "rrf_score": 0.025, "source": "t"},  # deleted in batch
            {"id": 6, "rrf_score": 0.024, "source": "t"},
            {"id": 7, "rrf_score": 0.023, "source": "t"},
        ]}}
        result = _apply_maintenance(mock_engine, decisions, exec_result, audn_artifacts, max_links=0)
        # Only 2 non-deleted memories remain — below threshold of 3
        assert result["compaction_candidates"] == []


class TestExtractionMaintenance:
    """Test _apply_maintenance() integration in run_extraction()."""

    def test_run_extraction_includes_maintenance_results(self):
        from llm_extract import run_extraction
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = [
            _cr(json.dumps([{"category": "DECISION", "text": "Uses Drizzle ORM"}])),
            _cr(json.dumps([{"action": "ADD", "fact_index": 0}]))
        ]
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 30, "text": "Prisma was the old ORM", "rrf_score": 0.022, "source": "test/proj"}
        ]
        mock_engine.add_memories.return_value = [121]
        mock_engine.add_link.return_value = {}
        result = run_extraction(
            mock_provider, mock_engine,
            messages="User: use drizzle\nAssistant: Done",
            source="test/project", context="stop"
        )
        assert "links_created" in result
        assert "compaction_candidates" in result
        assert len(result["links_created"]) == 1
        assert result["links_created"][0]["from_id"] == 121
        assert result["links_created"][0]["to_id"] == 30
        mock_engine.add_link.assert_called_once_with(121, 30, "related_to")

    def test_single_call_mode_skips_maintenance(self):
        from llm_extract import run_extraction
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "ADD", "fact_index": 0, "text": "Some fact", "category": "detail"}
        ]))
        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]
        result = run_extraction(
            mock_provider, mock_engine,
            messages="User: test", source="test/project",
            profile={"single_call": True},
        )
        mock_engine.add_link.assert_not_called()

    def test_dry_run_skips_maintenance(self):
        from llm_extract import run_extraction
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = [
            _cr(json.dumps([{"category": "DETAIL", "text": "Some fact"}])),
            _cr(json.dumps([{"action": "ADD", "fact_index": 0}]))
        ]
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []
        result = run_extraction(
            mock_provider, mock_engine,
            messages="User: test", source="test/project",
            profile={"dry_run": True},
        )
        assert result.get("dry_run") is True
        mock_engine.add_link.assert_not_called()

    def test_maintenance_failure_does_not_crash_extraction(self):
        """If _apply_maintenance raises, extraction result is still returned."""
        from llm_extract import run_extraction
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = [
            _cr(json.dumps([{"category": "DETAIL", "text": "Some fact"}])),
            _cr(json.dumps([{"action": "ADD", "fact_index": 0}]))
        ]
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [{"id": 5, "rrf_score": 0.025, "source": "t"}]
        mock_engine.add_memories.return_value = [100]
        with patch("llm_extract._apply_maintenance", side_effect=RuntimeError("Maintenance crashed")):
            result = run_extraction(
                mock_provider, mock_engine,
                messages="User: test", source="test/project",
            )
        assert result["stored_count"] == 1
        assert result["extracted_count"] == 1
        assert result["links_created"] == []
        assert result["compaction_candidates"] == []


class TestAUDNFallbackVisibility:
    """Test that AUDN exception fallback produces distinguishable actions."""

    def test_audn_exception_returns_fallback_add_not_plain_add(self):
        """When AUDN LLM call throws, actions should be tagged FALLBACK_ADD."""
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = RuntimeError("API timeout")

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        decisions, tokens, _ = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "fact one", "category": "detail"},
                   {"text": "fact two", "category": "decision"}],
            source="test/project"
        )
        assert len(decisions) == 2
        for d in decisions:
            assert d["action"] == "FALLBACK_ADD", \
                f"Expected FALLBACK_ADD, got {d['action']} — fallback should be distinguishable from real ADD"

    def test_fallback_add_tokens_are_zero(self):
        """Fallback path should report zero tokens (no LLM call succeeded)."""
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = Exception("connection reset")

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        _, tokens, _ = run_audn(
            mock_provider, mock_engine,
            facts=[{"text": "a fact", "category": "detail"}],
            source="test/project"
        )
        assert tokens == {"input": 0, "output": 0}

    def test_execute_actions_handles_fallback_add_as_add(self):
        """execute_actions should treat FALLBACK_ADD the same as ADD."""
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "FALLBACK_ADD", "fact_index": 0}]
        facts = [{"text": "Fallback fact", "category": "detail"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["stored_count"] == 1
        mock_engine.add_memories.assert_called_once()

    def test_execute_actions_preserves_fallback_add_label(self):
        """result_actions should use 'fallback_add' not 'add' for fallback actions."""
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "FALLBACK_ADD", "fact_index": 0}]
        facts = [{"text": "Fallback fact", "category": "detail"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["actions"][0]["action"] == "fallback_add", \
            "FALLBACK_ADD should be preserved in result_actions for metrics visibility"


class TestAUDNPromptDeleteSemantics:
    """Test that AUDN prompt gives DELETE distinct, non-overlapping semantics."""

    def test_delete_definition_mentions_no_replacement(self):
        """DELETE should be defined as 'no longer true AND no replacement exists'."""
        from llm_extract import AUDN_PROMPT

        delete_section = _extract_action_definition(AUDN_PROMPT, "DELETE")
        assert "no replacement" in delete_section.lower() or "no successor" in delete_section.lower(), \
            f"DELETE definition should mention 'no replacement' to distinguish from UPDATE. Got: {delete_section}"

    def test_delete_definition_not_same_as_update(self):
        """DELETE and UPDATE must have clearly different trigger conditions."""
        from llm_extract import AUDN_PROMPT

        delete_def = _extract_action_definition(AUDN_PROMPT, "DELETE")
        update_def = _extract_action_definition(AUDN_PROMPT, "UPDATE")

        # DELETE should NOT use "changed" (that's UPDATE's job)
        assert "changed" not in delete_def.lower(), \
            "DELETE definition should not use 'changed' — that's UPDATE's domain"
        # UPDATE should NOT use "obsolete" (that's DELETE's job)
        assert "obsolete" not in update_def.lower(), \
            "UPDATE definition should not use 'obsolete' — that's DELETE's domain"

    def test_delete_has_concrete_example(self):
        """DELETE definition should include a concrete example."""
        from llm_extract import AUDN_PROMPT

        delete_section = _extract_action_definition(AUDN_PROMPT, "DELETE")
        assert "e.g." in delete_section.lower() or "example" in delete_section.lower(), \
            f"DELETE definition should include an example. Got: {delete_section}"


class TestTrainingDataCollection:
    """Test _save_training_pair() passive data collection."""

    def test_saves_jsonl_when_dir_set(self, tmp_path):
        import llm_extract
        orig = llm_extract.TRAINING_DATA_DIR
        llm_extract.TRAINING_DATA_DIR = str(tmp_path)
        try:
            llm_extract._save_training_pair(
                "User: pick a DB\nAssistant: PostgreSQL for ACID.",
                [{"category": "decision", "text": "PostgreSQL chosen for ACID."}],
                "eval/test",
                "stop",
            )
            files = list(tmp_path.glob("extraction-training-*.jsonl"))
            assert len(files) == 1
            line = files[0].read_text().strip()
            record = json.loads(line)
            assert record["input"] == "User: pick a DB\nAssistant: PostgreSQL for ACID."
            assert record["output"] == [{"category": "decision", "text": "PostgreSQL chosen for ACID."}]
            assert record["source"] == "eval/test"
            assert record["context"] == "stop"
            assert "ts" in record
            assert record["extraction"]["user"] == record["input"]
            assert record["extraction"]["assistant"] == record["output"]
            assert "system" in record["extraction"]
        finally:
            llm_extract.TRAINING_DATA_DIR = orig

    def test_saves_audn_payload_when_provided(self, tmp_path):
        import llm_extract
        orig = llm_extract.TRAINING_DATA_DIR
        llm_extract.TRAINING_DATA_DIR = str(tmp_path)
        try:
            llm_extract._save_training_pair(
                "User: replace Redis cache",
                [{"category": "learning", "text": "Redis cache was removed."}],
                "eval/test",
                "stop",
                audn_system="You are a memory manager. Output only valid JSON.",
                audn_prompt='{"facts":[{"index":0,"text":"Redis cache was removed.","category":"learning"}]}',
                audn_decisions=[{"action": "DELETE", "fact_index": 0, "old_id": 12}],
                similar_per_fact={0: [{"id": 12, "text": "We use Redis for caching", "source": "eval/old", "rrf_score": 0.9}]},
                extract_tokens={"input": 10, "output": 11},
                audn_tokens={"input": 12, "output": 13},
            )
            files = list(tmp_path.glob("extraction-training-*.jsonl"))
            assert len(files) == 1
            record = json.loads(files[0].read_text().strip())
            assert record["audn"]["system"] == "You are a memory manager. Output only valid JSON."
            assert record["audn"]["user"].startswith('{"facts"')
            assert record["audn"]["assistant"] == [{"action": "DELETE", "fact_index": 0, "old_id": 12}]
            assert record["audn"]["similar_memories"]["0"][0]["id"] == 12
            assert record["audn"]["similar_memories"]["0"][0]["source"] == "eval/old"
            assert record["audn"]["tokens"] == {"input": 12, "output": 13}
            assert record["extraction"]["tokens"] == {"input": 10, "output": 11}
        finally:
            llm_extract.TRAINING_DATA_DIR = orig

    def test_noop_when_dir_not_set(self, tmp_path):
        import llm_extract
        orig = llm_extract.TRAINING_DATA_DIR
        llm_extract.TRAINING_DATA_DIR = ""
        try:
            llm_extract._save_training_pair("input", [{"text": "fact"}], "src", "stop")
            # No files written anywhere
            assert not list(tmp_path.glob("*.jsonl"))
        finally:
            llm_extract.TRAINING_DATA_DIR = orig

    def test_appends_multiple_records(self, tmp_path):
        import llm_extract
        orig = llm_extract.TRAINING_DATA_DIR
        llm_extract.TRAINING_DATA_DIR = str(tmp_path)
        try:
            for i in range(3):
                llm_extract._save_training_pair(f"msg-{i}", [{"text": f"fact-{i}"}], "src", "stop")
            files = list(tmp_path.glob("extraction-training-*.jsonl"))
            assert len(files) == 1
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 3
        finally:
            llm_extract.TRAINING_DATA_DIR = orig

    def test_never_raises_on_bad_dir(self):
        import llm_extract
        orig = llm_extract.TRAINING_DATA_DIR
        llm_extract.TRAINING_DATA_DIR = "/nonexistent/readonly/path"
        try:
            # Should not raise — best-effort, never fatal
            llm_extract._save_training_pair("msg", [{"text": "fact"}], "src", "stop")
        finally:
            llm_extract.TRAINING_DATA_DIR = orig


def _extract_action_definition(prompt: str, action: str) -> str:
    """Extract the definition text for a specific AUDN action from the prompt."""
    lines = prompt.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith(f"- {action}:"):
            parts = [line]
            for j in range(i + 1, len(lines)):
                next_line = lines[j]
                if next_line.strip().startswith("- ") and ":" in next_line.split("-", 1)[1][:20]:
                    break
                if next_line.strip():
                    parts.append(next_line)
            return " ".join(parts)
    return ""
