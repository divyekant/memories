"""Tests for transcript hygiene — injected context must never reach extraction.

Hooks inject recalled memories into the conversation (UserPromptSubmit
"## Retrieved Memories", SessionStart "## Relevant Memories",
<system-reminder> blocks, "<X> hook additional context:" blocks). Without
cleaning, those blocks get re-extracted as new memories every session,
producing the redundant-clusters duplication problem.
"""
import json
from unittest.mock import MagicMock

import pytest

from llm_provider import CompletionResult
from transcript_hygiene import clean_transcript


def _cr(text, input_tokens=10, output_tokens=5):
    return CompletionResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


INJECTED_MEMORY = "We chose Qdrant over FAISS because payload filtering simplifies source scoping"


class TestCleanTranscriptUnits:
    def test_strips_closed_system_reminder_block(self):
        text = (
            "user: <system-reminder>\n"
            f"## Retrieved Memories\n- [claude-code/memories] {INJECTED_MEMORY}\n"
            "</system-reminder> how do I run the tests?\n\n"
            "assistant: Run uv run pytest -q from the repo root."
        )
        cleaned = clean_transcript(text)
        assert INJECTED_MEMORY not in cleaned
        assert "system-reminder" not in cleaned
        assert "how do I run the tests?" in cleaned
        assert "uv run pytest -q" in cleaned

    def test_strips_truncated_system_reminder_until_next_role_line(self):
        # The hook clips each message at 2000 chars, which can cut off the
        # closing tag. The block must not swallow the next message.
        text = (
            "user: <system-reminder>\n"
            f"## Retrieved Memories\n- [claude-code/memories] {INJECTED_MEMORY}\n"
            "...this block was truncated by the per-message cap\n\n"
            "assistant: I fixed the race by acquiring the entity lock first."
        )
        cleaned = clean_transcript(text)
        assert INJECTED_MEMORY not in cleaned
        assert "entity lock" in cleaned

    def test_unclosed_reminder_does_not_eat_through_later_closed_pair(self):
        text = (
            "user: <system-reminder>injected one\n\n"
            "assistant: genuine decision: use pnpm not npm\n\n"
            "user: <system-reminder>injected two</system-reminder> real question?"
        )
        cleaned = clean_transcript(text)
        assert "injected one" not in cleaned
        assert "injected two" not in cleaned
        assert "use pnpm not npm" in cleaned
        assert "real question?" in cleaned

    def test_strips_retrieved_memories_section_without_reminder_wrapper(self):
        text = (
            "user: ## Retrieved Memories\n"
            f"- [claude-code/memories] {INJECTED_MEMORY}\n"
            "- [learning/memories] hooks must exit 0 on failure\n\n"
            "assistant: Understood, proceeding with the migration."
        )
        cleaned = clean_transcript(text)
        assert INJECTED_MEMORY not in cleaned
        assert "exit 0 on failure" not in cleaned
        assert "proceeding with the migration" in cleaned

    def test_strips_relevant_memories_section(self):
        text = (
            "user: ## Relevant Memories\n\n"
            f"- {INJECTED_MEMORY}\n\n"
            "## Project Status\nWe are mid-refactor of the search layer."
        )
        cleaned = clean_transcript(text)
        assert INJECTED_MEMORY not in cleaned
        # Stripping stops at the next heading
        assert "mid-refactor of the search layer" in cleaned

    def test_strips_memory_preamble_paragraph(self):
        text = (
            "user: IMPORTANT: The following memories from prior sessions are relevant to this prompt. "
            "These represent prior decisions and context that MUST be considered.\n\n"
            "## Retrieved Memories\n"
            f"- {INJECTED_MEMORY}\n\n"
            "assistant: On it."
        )
        cleaned = clean_transcript(text)
        assert "prior sessions are relevant" not in cleaned
        assert INJECTED_MEMORY not in cleaned
        assert "On it." in cleaned

    def test_strips_hook_additional_context_block(self):
        text = (
            "user: SessionStart hook additional context: ## Relevant Memories\n"
            f"- {INJECTED_MEMORY}\n\n"
            "assistant: Welcome back. Resuming the shadow-runner work."
        )
        cleaned = clean_transcript(text)
        assert INJECTED_MEMORY not in cleaned
        assert "hook additional context" not in cleaned
        assert "Resuming the shadow-runner work" in cleaned

    def test_strips_multi_word_hook_context_block(self):
        text = (
            "user: system SubagentStart hook additional context: IMPORTANT stuff\n"
            f"- {INJECTED_MEMORY}\n\n"
            "assistant: Done."
        )
        cleaned = clean_transcript(text)
        assert INJECTED_MEMORY not in cleaned
        assert "Done." in cleaned

    def test_preserves_genuine_conversation(self):
        text = (
            "user: let's switch the cache from Redis to SQLite because we only have one node\n\n"
            "assistant: Agreed — SQLite removes the network hop and the operational overhead."
        )
        assert clean_transcript(text) == text

    def test_case_insensitive_headings(self):
        text = "user: ## retrieved memories\n- secret injected fact\n\nassistant: ok"
        cleaned = clean_transcript(text)
        assert "secret injected fact" not in cleaned

    def test_idempotent(self):
        text = (
            "user: <system-reminder>## Retrieved Memories\n- x</system-reminder> question\n\n"
            "assistant: answer"
        )
        once = clean_transcript(text)
        assert clean_transcript(once) == once

    def test_empty_and_whitespace_input(self):
        assert clean_transcript("") == ""
        assert clean_transcript("   \n  ") == ""

    def test_pure_injection_collapses_to_empty(self):
        text = (
            "user: <system-reminder>\n## Retrieved Memories\n"
            f"- {INJECTED_MEMORY}\n</system-reminder>"
        )
        cleaned = clean_transcript(text)
        assert INJECTED_MEMORY not in cleaned
        # Only the bare role prefix may remain
        assert cleaned.replace("user:", "").strip() == ""


class TestExtractionNeverSeesInjectedContext:
    """Fixture transcript with an injected recalled memory -> the block must
    never reach the extraction LLM prompt and must not get re-ADDed."""

    FIXTURE_TRANSCRIPT = (
        "user: <system-reminder>\n"
        "UserPromptSubmit hook additional context: IMPORTANT: The following memories from prior "
        "sessions are relevant to this prompt.\n\n"
        "## Retrieved Memories\n"
        f"- [claude-code/memories] {INJECTED_MEMORY}\n"
        "</system-reminder> can you wire the novelty gate into the extraction path?\n\n"
        "assistant: Decision: gate extraction ADDs behind EXTRACT_NOVELTY_GATE so near-duplicates noop."
    )

    def _provider(self, responses):
        provider = MagicMock()
        provider.supports_audn = True
        provider.complete.side_effect = responses
        return provider

    def test_injected_block_never_reaches_llm_prompt(self):
        from llm_extract import run_extraction

        provider = self._provider([
            _cr("[]"),  # extraction returns nothing; we only care about the prompt
        ])
        engine = MagicMock()

        run_extraction(provider, engine, messages=self.FIXTURE_TRANSCRIPT,
                       source="claude-code/memories", context="stop")

        for call in provider.complete.call_args_list:
            args = call[0]
            kwargs = call[1]
            blob = " ".join(str(a) for a in args) + " " + " ".join(str(v) for v in kwargs.values())
            assert INJECTED_MEMORY not in blob
            assert "system-reminder" not in blob
            assert "Retrieved Memories" not in blob
        # The genuine conversation still reached the LLM
        user_payloads = [c[0][1] if len(c[0]) > 1 else c[1].get("user", "")
                         for c in provider.complete.call_args_list]
        assert any("novelty gate" in p for p in user_payloads)

    def test_injected_memory_not_re_added(self):
        from llm_extract import run_extraction

        # Even if the LLM parrots only what it saw, the injected text was
        # removed; simulate the LLM extracting the genuine decision only.
        provider = self._provider([
            _cr(json.dumps([{ "category": "DECISION",
                              "text": "Gate extraction ADDs behind EXTRACT_NOVELTY_GATE"}])),
            _cr(json.dumps([{ "action": "ADD", "fact_index": 0}])),
        ])
        engine = MagicMock()
        engine.hybrid_search.return_value = []
        engine.is_novel.return_value = (True, None)
        engine.add_memories.return_value = [201]

        result = run_extraction(provider, engine, messages=self.FIXTURE_TRANSCRIPT,
                                source="claude-code/memories", context="stop")

        assert result["stored_count"] == 1
        for call in engine.add_memories.call_args_list:
            texts = call.kwargs.get("texts") or (call[0][0] if call[0] else [])
            assert all(INJECTED_MEMORY not in t for t in texts)

    def test_pure_injection_transcript_skips_llm_entirely(self):
        from llm_extract import run_extraction

        provider = self._provider([_cr("[]")])
        engine = MagicMock()
        pure_injection = (
            "user: <system-reminder>\n## Retrieved Memories\n"
            f"- [claude-code/memories] {INJECTED_MEMORY}\n</system-reminder>"
        )

        result = run_extraction(provider, engine, messages=pure_injection,
                                source="claude-code/memories", context="stop")

        provider.complete.assert_not_called()
        engine.add_memories.assert_not_called()
        assert result["stored_count"] == 0
        assert result["extracted_count"] == 0
        assert result.get("skipped_reason") == "empty_after_hygiene"

    def test_single_call_mode_receives_cleaned_messages(self):
        from llm_extract import run_extraction

        provider = MagicMock()
        provider.supports_audn = True
        provider.complete.return_value = _cr("[]")
        engine = MagicMock()

        run_extraction(provider, engine, messages=self.FIXTURE_TRANSCRIPT,
                       source="claude-code/memories", context="stop",
                       profile={"single_call": True, "max_facts": 10,
                                "max_fact_chars": 500, "mode": "standard", "rules": {}})

        blob = str(provider.complete.call_args_list)
        assert INJECTED_MEMORY not in blob
        assert "novelty gate" in blob


class TestFallbackExtractionHygiene:
    """The provider-less fallback path is regex-based and especially prone to
    re-ingesting injected 'decided/chose' memory lines."""

    def test_fallback_does_not_re_add_injected_memories(self):
        import app as app_module

        engine = MagicMock()
        engine.is_novel.return_value = (True, None)
        engine.add_memories.return_value = [7]
        original = app_module.memory
        app_module.memory = engine
        try:
            messages = (
                "user: <system-reminder>\n## Retrieved Memories\n"
                f"- [claude-code/memories] {INJECTED_MEMORY}\n"
                "</system-reminder> what changed?\n\n"
                "assistant: nothing notable"
            )
            result = app_module._run_fallback_extraction(messages, "test/proj", "stop", None)
        finally:
            app_module.memory = original

        stored_texts = [
            call.kwargs.get("texts", call[0][0] if call[0] else [])
            for call in engine.add_memories.call_args_list
        ]
        flat = [t for texts in stored_texts for t in texts]
        assert all(INJECTED_MEMORY not in t for t in flat)
        assert result["stored_count"] == 0

    def test_fallback_still_extracts_genuine_decisions(self):
        import app as app_module

        engine = MagicMock()
        engine.is_novel.return_value = (True, None)
        engine.add_memories.return_value = [8]
        original = app_module.memory
        app_module.memory = engine
        try:
            messages = (
                "user: <system-reminder>injected</system-reminder> thoughts?\n\n"
                "assistant: We decided to use Qdrant for the production vector store deployment."
            )
            result = app_module._run_fallback_extraction(messages, "test/proj", "stop", None)
        finally:
            app_module.memory = original

        assert result["stored_count"] == 1
