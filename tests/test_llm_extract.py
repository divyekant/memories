"""Tests for llm_extract module."""
import os
import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from llm_provider import CompletionResult
from project_memory import ProjectMemoryPolicyError, TrustedAuthorship


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

    def test_ollama_no_source_uses_unscoped_novelty_lookup(self):
        """Legacy/env-admin extraction keeps the historical global lookup."""
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = False

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 5, "text": "Existing legacy fact", "source": "legacy/project"},
        )

        decisions, _, _ = run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "Existing legacy fact", "category": "detail"}],
            source="",
        )

        assert decisions[0]["action"] == "NOOP"
        assert "source_exact" not in mock_engine.is_novel.call_args.kwargs

    def test_provider_no_source_keeps_unscoped_similar_memory_candidates(self):
        """Provider AUDN must not filter all candidates when destination is empty."""
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "NOOP", "fact_index": 0, "existing_id": 7},
        ]))

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {
                "id": 7,
                "text": "Existing legacy fact",
                "source": "legacy/project",
                "similarity": 0.99,
            },
        ]

        run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "Existing legacy fact", "category": "detail"}],
            source="",
        )

        assert "source_exact" not in mock_engine.hybrid_search.call_args.kwargs
        prompt = mock_provider.complete.call_args[0][1]
        assert "Existing legacy fact" in prompt

    def test_provider_legacy_source_keeps_cross_client_duplicate_detection(self):
        """A nonempty legacy hook source must retain pre-project global AUDN lookup."""
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "NOOP", "fact_index": 0, "existing_id": 7},
        ]))
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 7, "text": "Existing fact", "source": "claude-code/demo", "similarity": 0.99},
        ]

        run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "Existing fact", "category": "detail"}],
            source="codex/demo",
        )

        assert "source_exact" not in mock_engine.hybrid_search.call_args.kwargs
        assert "Existing fact" in mock_provider.complete.call_args[0][1]

    def test_provider_legacy_source_excludes_authorized_structured_candidates(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "ADD", "fact_index": 0},
        ]))
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 7, "text": "Legacy match", "source": "claude-code/demo", "similarity": 0.95},
            {"id": 8, "text": "Shared secret", "source": "project/demo/knowledge", "similarity": 0.99},
        ]

        run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "Candidate", "category": "detail"}],
            source="codex/demo",
            allowed_prefixes=["codex/demo", "claude-code/demo", "project/demo"],
        )

        prompt = mock_provider.complete.call_args[0][1]
        assert "Legacy match" in prompt
        assert "Shared secret" not in prompt

    def test_ollama_legacy_source_ignores_structured_novelty_blocker(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = False
        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 8, "source": "project/demo/knowledge", "similarity": 0.99},
        )

        decisions, _, _ = run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "Legacy fact", "category": "detail"}],
            source="codex/demo",
            allowed_prefixes=["codex/demo", "project/demo"],
        )

        assert decisions == [{"action": "ADD", "fact_index": 0}]

    def test_ollama_legacy_source_ignores_unauthorized_legacy_blocker(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = False
        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 9, "source": "claude-code/other-project", "similarity": 0.99},
        )

        decisions, _, _ = run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "Legacy fact", "category": "detail"}],
            source="codex/demo",
            allowed_prefixes=["codex/demo", "claude-code/demo"],
        )

        assert decisions == [{"action": "ADD", "fact_index": 0}]

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
            {"id": 42, "text": long_memory, "similarity": 0.95, "source": "test/project"}
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
            {"id": 42, "text": "Uses Drizzle ORM", "rrf_score": 0.025, "source": "test/project"}
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

    def test_audn_filters_similar_memories_by_exact_destination_source(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([
            {"action": "ADD", "fact_index": 0}
        ]))
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 1, "text": "Same source", "source": "project/acme/decisions", "similarity": 0.9},
            {"id": 2, "text": "Other source secret", "source": "project/other/decisions", "similarity": 0.99},
        ]

        run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "Uses Postgres", "category": "decision"}],
            source="project/acme/decisions",
            allowed_prefixes=["project/"],
        )

        prompt = mock_provider.complete.call_args[0][1]
        assert "Same source" in prompt
        assert "Other source secret" not in prompt
        assert mock_engine.hybrid_search.call_args.kwargs["source_exact"] == "project/acme/decisions"

    def test_audn_returns_artifacts_dict_with_similar_per_fact(self):
        """run_audn() always returns audn_artifacts dict with similar_per_fact."""
        from llm_extract import run_audn
        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = _cr(json.dumps([{"action": "ADD", "fact_index": 0}]))
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 5, "text": "Existing memory", "rrf_score": 0.025, "source": "test/project"}
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

    def test_ollama_novelty_is_scoped_to_destination_source(self):
        from llm_extract import run_audn
        mock_provider = MagicMock()
        mock_provider.supports_audn = False
        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (True, None)

        run_audn(
            mock_provider,
            mock_engine,
            facts=[{"text": "New fact", "category": "detail"}],
            source="project/acme/decisions",
        )

        assert mock_engine.is_novel.call_args.kwargs["source_exact"] == "project/acme/decisions"

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

    def test_execute_add_passes_trusted_authorship(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]
        trusted = TrustedAuthorship.principal("alice", "codex")

        result = execute_actions(
            mock_engine,
            [{"action": "ADD", "fact_index": 0}],
            [{"text": "project fact", "category": "decision"}],
            source="project/demo/decisions",
            trusted_authorship=trusted,
        )

        assert result["stored_count"] == 1
        assert mock_engine.add_memories.call_args.kwargs["trusted_authorship"] == trusted

    def test_novelty_gate_no_source_accepts_unscoped_duplicate(self):
        """The legacy novelty gate must retain duplicate detection across sources."""
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 12, "text": "Existing legacy fact", "source": "legacy/project", "similarity": 0.99},
        )

        result = execute_actions(
            mock_engine,
            [{"action": "ADD", "fact_index": 0}],
            [{"text": "Existing legacy fact", "category": "detail"}],
            source="",
        )

        assert result["actions"][0]["action"] == "noop"
        assert result["actions"][0]["existing_id"] == 12
        assert "source_exact" not in mock_engine.is_novel.call_args.kwargs
        mock_engine.add_memories.assert_not_called()

    def test_novelty_gate_legacy_source_detects_cross_client_duplicate(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 13, "text": "Existing fact", "source": "claude-code/demo", "similarity": 0.99},
        )

        result = execute_actions(
            mock_engine,
            [{"action": "ADD", "fact_index": 0}],
            [{"text": "Existing fact", "category": "detail"}],
            source="codex/demo",
            allowed_prefixes=["codex/demo", "claude-code/demo"],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
        )

        assert result["actions"][0]["action"] == "noop"
        assert result["actions"][0]["existing_id"] == 13
        assert "source_exact" not in mock_engine.is_novel.call_args.kwargs
        mock_engine.add_memories.assert_not_called()

    def test_managed_legacy_novelty_ignores_unauthorized_global_match(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 99, "text": "Private fact", "source": "person/bob/demo/knowledge", "similarity": 0.99},
        )
        mock_engine.add_memories.return_value = [100]

        result = execute_actions(
            mock_engine,
            [{"action": "ADD", "fact_index": 0}],
            [{"text": "New fact", "category": "detail"}],
            source="codex/demo",
            allowed_prefixes=["codex/demo", "claude-code/demo"],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
        )

        assert result["actions"][0]["action"] == "add"
        mock_engine.add_memories.assert_called_once()

    def test_managed_legacy_novelty_ignores_authorized_structured_match(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 8, "text": "Shared fact", "source": "project/demo/knowledge", "similarity": 0.99},
        )
        mock_engine.add_memories.return_value = [100]

        result = execute_actions(
            mock_engine,
            [{"action": "ADD", "fact_index": 0}],
            [{"text": "Legacy fact", "category": "detail"}],
            source="codex/demo",
            allowed_prefixes=["codex/demo", "project/demo"],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
        )

        assert result["actions"][0]["action"] == "add"
        mock_engine.add_memories.assert_called_once()

    def test_managed_legacy_novelty_requests_filtered_multi_candidate_lookup(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = (
            False,
            {"id": 13, "text": "Legacy fact", "source": "claude-code/demo", "similarity": 0.95},
        )

        result = execute_actions(
            mock_engine,
            [{"action": "ADD", "fact_index": 0}],
            [{"text": "Legacy fact", "category": "detail"}],
            source="codex/demo",
            allowed_prefixes=["codex/demo", "claude-code/demo", "project/demo"],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
        )

        assert result["actions"][0]["action"] == "noop"
        kwargs = mock_engine.is_novel.call_args.kwargs
        assert kwargs["allowed_source_prefixes"] == [
            "codex/demo",
            "claude-code/demo",
            "project/demo",
        ]
        assert kwargs["exclude_reserved_sources"] is True

    def test_execute_update_rejects_project_credential_before_archiving(self, tmp_path):
        from llm_extract import execute_actions
        from memory_engine import MemoryEngine

        engine = MemoryEngine(data_dir=str(tmp_path))
        trusted = TrustedAuthorship.principal("alice", "codex")
        old_id = engine.add_memories(
            ["old decision"],
            ["project/demo/decisions"],
            trusted_authorship=trusted,
        )[0]
        secret = "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456"

        with pytest.raises(ProjectMemoryPolicyError, match="credential-shaped"):
            execute_actions(
                engine,
                [{"action": "UPDATE", "fact_index": 0, "old_id": old_id, "new_text": secret}],
                [{"text": secret, "category": "decision"}],
                source="project/demo/decisions",
                trusted_authorship=trusted,
            )

        old = engine._get_meta_by_id(old_id)
        assert old["text"] == "old decision"
        assert old.get("archived") is not True
        assert engine.qdrant_store.count() == 1

    def test_execute_actions_preflights_every_project_write_before_batch_mutation(self, tmp_path):
        from llm_extract import execute_actions
        from memory_engine import MemoryEngine

        engine = MemoryEngine(data_dir=str(tmp_path))
        trusted = TrustedAuthorship.principal("alice", "codex")
        facts = [
            {"text": "safe shared decision", "category": "decision"},
            {
                "text": "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456",
                "category": "detail",
            },
        ]

        with pytest.raises(ProjectMemoryPolicyError, match="credential-shaped"):
            execute_actions(
                engine,
                [
                    {"action": "ADD", "fact_index": 0},
                    {"action": "ADD", "fact_index": 1},
                ],
                facts,
                source="project/demo/knowledge",
                novelty_gate=False,
                trusted_authorship=trusted,
            )

        assert engine.qdrant_store.count() == 0
        assert engine.metadata == []

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
        mock_engine.get_memory.return_value = {"id": 42, "source": "test/proj", "text": "old"}
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
        mock_engine.get_memory.return_value = {"id": 55, "source": "test/proj"}
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

    @pytest.mark.parametrize("action_name", ["UPDATE", "DELETE", "CONFLICT"])
    def test_execute_actions_rejects_cross_source_old_id_even_with_broad_prefix(self, action_name):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {
            "id": 42,
            "source": "person/alice/acme/decisions",
            "text": "Alice's private fact",
        }
        mock_engine.add_memories.return_value = [100]
        facts = [{"text": "Replacement fact", "category": "decision"}]
        action = {"action": action_name, "fact_index": 0, "old_id": 42}
        if action_name == "UPDATE":
            action["new_text"] = "Replacement fact"

        result = execute_actions(
            mock_engine,
            [action],
            facts,
            source="project/acme/decisions",
            allowed_prefixes=["project/", "person/"],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
        )

        assert result["stored_count"] == 0
        assert result["updated_count"] == 0
        assert result["deleted_count"] == 0
        assert result["conflict_count"] == 0
        assert result["actions"][0]["action"] == "error"
        mock_engine.add_memories.assert_not_called()
        mock_engine.delete_memory.assert_not_called()


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
            {"id": 30, "text": "Prisma was the old ORM", "rrf_score": 0.022, "source": "test/project"}
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


class TestExtractFactsShadowFanout:
    """Tests for shadow fan-out wired into extract_facts."""

    def test_extract_facts_writes_shadow_log_when_configured(self, tmp_path):
        from llm_extract import extract_facts
        from shadow_runner import wait_for_shadows

        primary = MagicMock()
        primary.complete = MagicMock(return_value=CompletionResult(
            text='[{"category": "decision", "text": "use sqlite for memory store"}]',
            input_tokens=100, output_tokens=20,
        ))

        shadow = MagicMock()
        shadow.provider_name = "omlx"
        shadow.model = "fplv2"
        shadow.complete = MagicMock(return_value=CompletionResult(
            text='[{"category": "detail", "text": "shadow saw the same conversation"}]',
            input_tokens=99, output_tokens=18,
        ))

        with patch("llm_extract.build_shadow_providers", return_value=[shadow]):
            with patch.dict(os.environ, {"SHADOW_LOG_DIR": str(tmp_path)}):
                facts = extract_facts(primary, "messages text", source="claude-code/foo")
        wait_for_shadows(timeout=5)

        assert facts == [{"category": "decision", "text": "use sqlite for memory store"}]
        primary.complete.assert_called_once()
        shadow.complete.assert_called_once()

        log_file = tmp_path / "memories-shadow-fplv2.log"
        assert log_file.exists()
        rec = json.loads(log_file.read_text().strip())
        assert rec["call_type"] == "extract"
        assert rec["source"] == "claude-code/foo"
        assert "shadow saw" in rec["shadow_text"]

    def test_extract_facts_unaffected_when_no_shadows(self, tmp_path):
        from llm_extract import extract_facts
        from shadow_runner import wait_for_shadows

        primary = MagicMock()
        primary.complete = MagicMock(return_value=CompletionResult(
            text='[{"category": "decision", "text": "x"}]',
            input_tokens=10, output_tokens=5,
        ))

        env = {"SHADOW_LOG_DIR": str(tmp_path)}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SHADOW_PROVIDERS", None)
            facts = extract_facts(primary, "msgs", source="claude-code/foo")
        wait_for_shadows(timeout=2)

        assert facts == [{"category": "decision", "text": "x"}]
        assert list(tmp_path.iterdir()) == []


class TestRunAudnShadowFanout:
    """Tests for shadow fan-out wired into run_audn."""

    def test_run_audn_writes_shadow_log_when_configured(self, tmp_path):
        from llm_extract import run_audn
        from shadow_runner import wait_for_shadows

        primary = MagicMock()
        primary.provider_name = "anthropic"
        primary.supports_audn = True
        primary.complete = MagicMock(return_value=CompletionResult(
            text='[{"action": "ADD", "fact_index": 0}]',
            input_tokens=200, output_tokens=30,
        ))

        engine = MagicMock()
        engine.hybrid_search = MagicMock(return_value=[])

        shadow = MagicMock()
        shadow.provider_name = "omlx"
        shadow.model = "fplv2"
        shadow.complete = MagicMock(return_value=CompletionResult(
            text='[{"action": "NOOP", "fact_index": 0}]',
            input_tokens=190, output_tokens=25,
        ))

        facts = [{"category": "decision", "text": "x"}]
        with patch("llm_extract.build_shadow_providers", return_value=[shadow]):
            with patch.dict(os.environ, {"SHADOW_LOG_DIR": str(tmp_path)}):
                decisions, tokens, artifacts = run_audn(
                    primary, engine, facts, source="claude-code/foo",
                )
        wait_for_shadows(timeout=5)

        assert decisions == [{"action": "ADD", "fact_index": 0}]
        shadow.complete.assert_called_once()

        log_file = tmp_path / "memories-shadow-fplv2.log"
        assert log_file.exists()
        rec = json.loads(log_file.read_text().strip())
        assert rec["call_type"] == "audn"
        assert "NOOP" in rec["shadow_text"]


class TestSingleCallShadowFanout:
    """Tests for shadow fan-out wired into extract_and_decide_single_call."""

    def test_single_call_writes_shadow_log_when_configured(self, tmp_path):
        from llm_extract import extract_and_decide_single_call
        from shadow_runner import wait_for_shadows

        primary = MagicMock()
        primary.complete = MagicMock(return_value=CompletionResult(
            text='[{"action": "ADD", "fact_index": 0, "category": "decision", "text": "x"}]',
            input_tokens=300, output_tokens=40,
        ))

        engine = MagicMock()

        shadow = MagicMock()
        shadow.provider_name = "omlx"
        shadow.model = "fplv2"
        shadow.complete = MagicMock(return_value=CompletionResult(
            text='[{"action": "ADD", "fact_index": 0, "category": "detail", "text": "y"}]',
            input_tokens=290, output_tokens=35,
        ))

        with patch("llm_extract.build_shadow_providers", return_value=[shadow]):
            with patch.dict(os.environ, {"SHADOW_LOG_DIR": str(tmp_path)}):
                actions, usage, _ = extract_and_decide_single_call(
                    primary, "msgs text", source="claude-code/foo", engine=engine,
                )
        wait_for_shadows(timeout=5)

        assert len(actions) == 1
        assert actions[0]["action"] == "ADD"
        shadow.complete.assert_called_once()

        log_file = tmp_path / "memories-shadow-fplv2.log"
        assert log_file.exists()
        rec = json.loads(log_file.read_text().strip())
        assert rec["call_type"] == "single_call"


class TestPrimaryUnaffectedByShadowFailures:
    """Primary path must survive any shadow-side failure mode."""

    def test_extract_facts_succeeds_when_all_shadows_raise(self, tmp_path):
        from llm_extract import extract_facts
        from shadow_runner import wait_for_shadows

        primary = MagicMock()
        primary.complete = MagicMock(return_value=CompletionResult(
            text='[{"category": "decision", "text": "ok"}]',
            input_tokens=10, output_tokens=5,
        ))

        def make_boom(model):
            s = MagicMock()
            s.provider_name = "omlx"
            s.model = model
            s.complete = MagicMock(side_effect=RuntimeError(f"{model} exploded"))
            return s

        shadows = [make_boom("a"), make_boom("b"), make_boom("c")]

        with patch("llm_extract.build_shadow_providers", return_value=shadows):
            with patch.dict(os.environ, {"SHADOW_LOG_DIR": str(tmp_path)}):
                facts = extract_facts(primary, "msgs", source="src")
        wait_for_shadows(timeout=5)

        assert facts == [{"category": "decision", "text": "ok"}]

        for model in ("a", "b", "c"):
            log = tmp_path / f"memories-shadow-{model}.log"
            assert log.exists()
            rec = json.loads(log.read_text().strip())
            assert rec["error"] == f"{model} exploded"
            assert rec["shadow_text"] is None

    def test_extract_facts_succeeds_when_build_shadow_providers_raises(self, tmp_path):
        from llm_extract import extract_facts

        primary = MagicMock()
        primary.complete = MagicMock(return_value=CompletionResult(
            text='[{"category": "decision", "text": "ok"}]',
            input_tokens=10, output_tokens=5,
        ))

        with patch("llm_extract.build_shadow_providers",
                   side_effect=RuntimeError("build exploded")):
            facts = extract_facts(primary, "msgs", source="src")

        assert facts == [{"category": "decision", "text": "ok"}]
