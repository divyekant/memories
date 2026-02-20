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

        decisions, _ = run_audn(
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

        decisions, _ = run_audn(
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

        decisions, _ = run_audn(
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

        decisions, _ = run_audn(
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

        decisions, _ = run_audn(
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
        mock_engine.add_memories.return_value = [101]

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original fact", "category": "decision"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["updated_count"] == 1
        mock_engine.delete_memory.assert_called_once_with(42)
        # Verify metadata includes both category and supersedes
        call_kwargs = mock_engine.add_memories.call_args
        assert call_kwargs.kwargs.get("metadata_list") == [{"category": "decision", "supersedes": 42}]

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
