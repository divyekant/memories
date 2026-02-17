"""Tests for llm_extract module."""
import pytest
import json
from unittest.mock import MagicMock, patch


class TestFactExtraction:
    """Test extract_facts() function."""

    def test_extracts_facts_from_conversation(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = json.dumps([
            "User prefers Drizzle ORM over Prisma",
            "Project uses TypeScript strict mode"
        ])

        facts = extract_facts(mock_provider, "User: let's use drizzle\nAssistant: Good choice!")
        assert len(facts) == 2
        assert "Drizzle" in facts[0]

    def test_returns_empty_when_nothing_worth_storing(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = "[]"

        facts = extract_facts(mock_provider, "User: hi\nAssistant: hello!")
        assert facts == []

    def test_handles_llm_returning_non_json(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = "Sorry, I can't extract facts from this."

        facts = extract_facts(mock_provider, "User: hi")
        assert facts == []

    def test_pre_compact_context_uses_aggressive_prompt(self):
        from llm_extract import extract_facts

        mock_provider = MagicMock()
        mock_provider.complete.return_value = "[]"

        extract_facts(mock_provider, "some messages", context="pre_compact")
        call_args = mock_provider.complete.call_args
        system_prompt = call_args[0][0] if call_args[0] else call_args[1].get("system", "")
        assert "everything" in system_prompt.lower() or "aggressive" in system_prompt.lower() or "thorough" in system_prompt.lower()

    def test_caps_fact_count_and_length(self):
        from llm_extract import extract_facts, EXTRACT_MAX_FACTS, EXTRACT_MAX_FACT_CHARS

        mock_provider = MagicMock()
        oversized_fact = "x" * (EXTRACT_MAX_FACT_CHARS + 300)
        mock_provider.complete.return_value = json.dumps([oversized_fact] * (EXTRACT_MAX_FACTS + 10))

        facts = extract_facts(mock_provider, "User: test")
        assert len(facts) == EXTRACT_MAX_FACTS
        assert all(len(f) <= EXTRACT_MAX_FACT_CHARS for f in facts)
        assert all(f.endswith("...") for f in facts)


class TestAUDNCycle:
    """Test run_audn() function."""

    def test_add_new_fact(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = json.dumps([
            {"action": "ADD", "fact_index": 0}
        ])

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        decisions = run_audn(
            mock_provider, mock_engine,
            facts=["Uses Drizzle ORM"],
            source="test/project"
        )
        assert len(decisions) == 1
        assert decisions[0]["action"] == "ADD"

    def test_noop_existing_fact(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = json.dumps([
            {"action": "NOOP", "fact_index": 0, "existing_id": 42}
        ])

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 42, "text": "Uses Drizzle ORM", "similarity": 0.95}
        ]

        decisions = run_audn(
            mock_provider, mock_engine,
            facts=["Uses Drizzle ORM"],
            source="test/project"
        )
        assert len(decisions) == 1
        assert decisions[0]["action"] == "NOOP"

    def test_update_existing_fact(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = json.dumps([
            {"action": "UPDATE", "fact_index": 0, "old_id": 10, "new_text": "Uses Drizzle ORM (switched from Prisma)"}
        ])

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 10, "text": "Uses Prisma ORM", "similarity": 0.75}
        ]

        decisions = run_audn(
            mock_provider, mock_engine,
            facts=["Switched from Prisma to Drizzle ORM"],
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

        decisions = run_audn(
            mock_provider, mock_engine,
            facts=["New fact"],
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

        decisions = run_audn(
            mock_provider, mock_engine,
            facts=["Existing fact"],
            source="test/project"
        )
        assert decisions[0]["action"] == "NOOP"

    def test_audn_prompt_truncates_similar_memory_text(self):
        from llm_extract import run_audn, EXTRACT_SIMILAR_TEXT_CHARS

        long_memory = "m" * (EXTRACT_SIMILAR_TEXT_CHARS + 500)

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.return_value = json.dumps(
            [{"action": "ADD", "fact_index": 0}]
        )

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = [
            {"id": 42, "text": long_memory, "similarity": 0.95}
        ]

        run_audn(
            mock_provider, mock_engine,
            facts=["Uses Drizzle ORM"],
            source="test/project"
        )

        prompt = mock_provider.complete.call_args[0][1]
        assert "m" * (EXTRACT_SIMILAR_TEXT_CHARS + 50) not in prompt
        assert "..." in prompt


class TestExecuteActions:
    """Test execute_actions() function."""

    def test_execute_add(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "ADD", "fact_index": 0}]
        facts = ["New fact to store"]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["stored_count"] == 1
        mock_engine.add_memories.assert_called_once()
        # Verify API contract: sources must be a list, return is List[int]
        call_kwargs = mock_engine.add_memories.call_args
        assert "sources" in call_kwargs.kwargs or (len(call_kwargs.args) >= 2 and isinstance(call_kwargs.args[1], list))

    def test_execute_update_calls_supersede(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [101]

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = ["original fact"]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["updated_count"] == 1
        mock_engine.delete_memory.assert_called_once_with(42)

    def test_execute_noop_does_nothing(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        actions = [{"action": "NOOP", "fact_index": 0, "existing_id": 30}]
        facts = ["existing fact"]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["stored_count"] == 0
        assert result["updated_count"] == 0
        mock_engine.add_memories.assert_not_called()

    def test_execute_delete(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        actions = [{"action": "DELETE", "fact_index": 0, "old_id": 55}]
        facts = ["contradicted fact"]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["deleted_count"] == 1
        mock_engine.delete_memory.assert_called_once_with(55)


class TestFullPipeline:
    """Test run_extraction() end-to-end with mocks."""

    def test_full_extraction_pipeline(self):
        from llm_extract import run_extraction

        mock_provider = MagicMock()
        mock_provider.supports_audn = True
        mock_provider.complete.side_effect = [
            json.dumps(["Uses Drizzle ORM", "TypeScript strict mode"]),
            json.dumps([
                {"action": "ADD", "fact_index": 0},
                {"action": "NOOP", "fact_index": 1, "existing_id": 30}
            ])
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
