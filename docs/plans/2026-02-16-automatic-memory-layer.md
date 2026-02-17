# Automatic Memory Layer Implementation Plan

> Implementation plan for the automatic memory layer feature. See the [design doc](./2026-02-16-automatic-memory-layer-design.md) for architecture rationale.

**Goal:** Add automatic memory retrieval and extraction to FAISS Memory so Claude Code, Codex, and OpenClaw inject/store memories without the agent choosing to.

**Architecture:** Server-side LLM extraction pipeline (Anthropic/OpenAI/Ollama) behind `POST /memory/extract`. Client hooks (5 shell scripts) fire on SessionStart, UserPromptSubmit, Stop, PreCompact, SessionEnd. Extraction is opt-in via `EXTRACT_PROVIDER` env var.

**Tech Stack:** Python (FastAPI), Anthropic/OpenAI/Ollama SDKs, bash hooks, jq, curl

**Design Doc:** `docs/plans/2026-02-16-automatic-memory-layer-design.md`

---

### Task 1: LLM Provider Abstraction — Tests

**Files:**
- Create: `tests/test_llm_provider.py`

**Step 1: Write failing tests for LLM provider**

```python
"""Tests for llm_provider module."""
import os
import pytest
import json
from unittest.mock import patch, MagicMock


class TestProviderFactory:
    """Test get_provider() factory function."""

    def test_returns_none_when_no_provider_set(self):
        with patch.dict(os.environ, {}, clear=True):
            from llm_provider import get_provider
            assert get_provider() is None

    def test_returns_none_for_empty_provider(self):
        with patch.dict(os.environ, {"EXTRACT_PROVIDER": ""}):
            from llm_provider import get_provider
            assert get_provider() is None

    def test_raises_for_unknown_provider(self):
        with patch.dict(os.environ, {"EXTRACT_PROVIDER": "unknown"}):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="Unknown.*unknown"):
                get_provider()

    def test_anthropic_provider_requires_key(self):
        env = {"EXTRACT_PROVIDER": "anthropic"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                get_provider()

    def test_openai_provider_requires_key(self):
        env = {"EXTRACT_PROVIDER": "openai"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                get_provider()

    def test_ollama_provider_no_key_needed(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            provider = get_provider()
            assert provider is not None
            assert provider.provider_name == "ollama"
            assert provider.supports_audn is False


class TestOllamaProvider:
    """Test OllamaProvider without network calls."""

    def test_default_model(self):
        with patch.dict(os.environ, {"EXTRACT_PROVIDER": "ollama"}):
            from llm_provider import get_provider
            provider = get_provider()
            assert provider.model == "gemma3:4b"

    def test_custom_model(self):
        env = {"EXTRACT_PROVIDER": "ollama", "EXTRACT_MODEL": "llama3:8b"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()
            assert provider.model == "llama3:8b"

    def test_custom_url(self):
        env = {"EXTRACT_PROVIDER": "ollama", "OLLAMA_URL": "http://myhost:11434"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()
            assert "myhost" in provider.base_url

    def test_complete_calls_ollama_api(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider, OllamaProvider
            provider = get_provider()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"response": "test output"}

            with patch("llm_provider.requests.post", return_value=mock_response) as mock_post:
                result = provider.complete("system prompt", "user prompt")
                assert result == "test output"
                mock_post.assert_called_once()

    def test_health_check(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()

            mock_response = MagicMock()
            mock_response.status_code = 200

            with patch("llm_provider.requests.get", return_value=mock_response):
                assert provider.health_check() is True

    def test_health_check_failure(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()

            with patch("llm_provider.requests.get", side_effect=Exception("conn refused")):
                assert provider.health_check() is False


class TestProviderInterface:
    """Test that all providers expose the same interface."""

    def test_ollama_has_required_attrs(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()
            assert hasattr(provider, "complete")
            assert hasattr(provider, "health_check")
            assert hasattr(provider, "provider_name")
            assert hasattr(provider, "model")
            assert hasattr(provider, "supports_audn")
```

**Step 2: Run tests to verify they fail**

Run: `cd /path/to/memories &&python -m pytest tests/test_llm_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_provider'`

---

### Task 2: LLM Provider Abstraction — Implementation

**Files:**
- Create: `llm_provider.py`

**Step 3: Write llm_provider.py**

```python
"""LLM provider abstraction for memory extraction.

Supports Anthropic, OpenAI, and Ollama. Configured via environment variables:
  EXTRACT_PROVIDER: "anthropic", "openai", or "ollama" (empty = disabled)
  EXTRACT_MODEL: model name (defaults per provider)
  ANTHROPIC_API_KEY: required for anthropic
  OPENAI_API_KEY: required for openai
  OLLAMA_URL: ollama server URL (default: http://host.docker.internal:11434)
"""
import os
import json
import logging
from abc import ABC, abstractmethod

import requests

logger = logging.getLogger(__name__)

# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4.1-nano",
    "ollama": "gemma3:4b",
}


class LLMProvider(ABC):
    """Base class for LLM providers."""

    provider_name: str
    model: str
    supports_audn: bool

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Send a completion request. Returns the response text."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the provider is reachable and working."""
        ...


class AnthropicProvider(LLMProvider):
    """Anthropic API provider (Claude models)."""

    provider_name = "anthropic"
    supports_audn = True

    def __init__(self, api_key: str, model: str | None = None):
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package required. Install with: pip install anthropic>=0.40.0"
            )
        self.model = model or DEFAULT_MODELS["anthropic"]
        self.client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    def health_check(self) -> bool:
        try:
            self.complete("Reply with OK", "health check")
            return True
        except Exception as e:
            logger.warning("Anthropic health check failed: %s", e)
            return False


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    provider_name = "openai"
    supports_audn = True

    def __init__(self, api_key: str, model: str | None = None):
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package required. Install with: pip install openai>=1.50.0"
            )
        self.model = model or DEFAULT_MODELS["openai"]
        self.client = openai.OpenAI(api_key=api_key)

    def complete(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=1024,
        )
        return response.choices[0].message.content

    def health_check(self) -> bool:
        try:
            self.complete("Reply with OK", "health check")
            return True
        except Exception as e:
            logger.warning("OpenAI health check failed: %s", e)
            return False


class OllamaProvider(LLMProvider):
    """Ollama local provider. Extraction only — no AUDN support."""

    provider_name = "ollama"
    supports_audn = False

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or "http://host.docker.internal:11434").rstrip("/")
        self.model = model or DEFAULT_MODELS["ollama"]

    def complete(self, system: str, user: str) -> str:
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "system": system,
                "prompt": user,
                "stream": False,
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["response"]

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.warning("Ollama health check failed: %s", e)
            return False


def get_provider() -> LLMProvider | None:
    """Factory: create an LLM provider from environment variables.

    Returns None if EXTRACT_PROVIDER is not set (extraction disabled).
    Raises ValueError for invalid configuration.
    """
    provider_name = os.environ.get("EXTRACT_PROVIDER", "").strip().lower()
    if not provider_name:
        return None

    model = os.environ.get("EXTRACT_MODEL", "").strip() or None

    if provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY required when EXTRACT_PROVIDER=anthropic")
        return AnthropicProvider(api_key=api_key, model=model)

    elif provider_name == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY required when EXTRACT_PROVIDER=openai")
        return OpenAIProvider(api_key=api_key, model=model)

    elif provider_name == "ollama":
        base_url = os.environ.get("OLLAMA_URL", "").strip() or None
        return OllamaProvider(base_url=base_url, model=model)

    else:
        raise ValueError(f"Unknown EXTRACT_PROVIDER: '{provider_name}'. Use: anthropic, openai, or ollama")
```

**Step 4: Run tests to verify they pass**

Run: `cd /path/to/memories &&python -m pytest tests/test_llm_provider.py -v`
Expected: All 12 tests PASS

**Step 5: Commit**

```bash
git add llm_provider.py tests/test_llm_provider.py
git commit -m "feat: add LLM provider abstraction (Anthropic/OpenAI/Ollama)"
```

---

### Task 3: Extraction Pipeline — Tests

**Files:**
- Create: `tests/test_llm_extract.py`

**Step 6: Write failing tests for extraction pipeline**

```python
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
        assert "everything" in system_prompt.lower() or "aggressive" in system_prompt.lower()


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
        mock_engine.hybrid_search.return_value = []  # no similar memories

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
        mock_provider.supports_audn = False  # Ollama

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = True

        decisions = run_audn(
            mock_provider, mock_engine,
            facts=["New fact"],
            source="test/project"
        )
        assert len(decisions) == 1
        assert decisions[0]["action"] == "ADD"
        mock_engine.is_novel.assert_called_once()
        mock_provider.complete.assert_not_called()  # no LLM call for AUDN

    def test_ollama_noop_for_existing(self):
        from llm_extract import run_audn

        mock_provider = MagicMock()
        mock_provider.supports_audn = False

        mock_engine = MagicMock()
        mock_engine.is_novel.return_value = False

        decisions = run_audn(
            mock_provider, mock_engine,
            facts=["Existing fact"],
            source="test/project"
        )
        assert decisions[0]["action"] == "NOOP"


class TestExecuteActions:
    """Test execute_actions() function."""

    def test_execute_add(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = {"ids": [100]}

        actions = [{"action": "ADD", "fact_index": 0}]
        facts = ["New fact to store"]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        assert result["stored_count"] == 1
        mock_engine.add_memories.assert_called_once()

    def test_execute_update_calls_supersede(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = {"ids": [101]}

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
        # First call: fact extraction
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
        mock_engine.add_memories.return_value = {"ids": [121]}

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
```

**Step 7: Run tests to verify they fail**

Run: `cd /path/to/memories &&python -m pytest tests/test_llm_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_extract'`

---

### Task 4: Extraction Pipeline — Implementation

**Files:**
- Create: `llm_extract.py`

**Step 8: Write llm_extract.py**

```python
"""Memory extraction pipeline with AUDN (Add/Update/Delete/Noop).

Two-call pipeline:
  1. LLM extracts atomic facts from conversation
  2. LLM (or novelty check for Ollama) decides AUDN action per fact

Usage:
  result = run_extraction(provider, engine, messages, source, context)
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# --- Prompts ---

FACT_EXTRACTION_PROMPT = """Extract atomic facts worth remembering from this conversation.
Focus on: decisions made, preferences expressed, bugs found + root causes + fixes,
architectural choices, tool/library selections, project conventions.
Output a JSON array of strings, one fact per element.
Each fact should be self-contained and understandable without the conversation.
If nothing worth storing, output []."""

FACT_EXTRACTION_PROMPT_AGGRESSIVE = """Extract ALL potentially useful facts from this conversation.
This context is about to be lost, so be thorough. Include:
- Decisions made, preferences expressed
- Bugs found + root causes + fixes
- Architectural choices, tool/library selections
- Project conventions, file paths mentioned
- Any technical detail that might be useful later
Output a JSON array of strings, one fact per element.
Each fact should be self-contained and understandable without the conversation.
If nothing worth storing, output []."""

AUDN_PROMPT = """You are a memory manager. For each new fact, decide what to do given
the existing similar memories.

Actions:
- ADD: No similar memory exists. Store as new.
- UPDATE: An existing memory covers the same topic but the information
  has changed. Provide old_id and new_text that replaces it.
- DELETE: An existing memory is now contradicted or obsolete. Provide old_id.
- NOOP: The fact is already captured by an existing memory. Provide existing_id.

New facts:
{facts_json}

Existing similar memories (per fact):
{similar_json}

Output a JSON array of decisions. Each decision must have:
- "action": "ADD" | "UPDATE" | "DELETE" | "NOOP"
- "fact_index": index of the fact in the input array
- For UPDATE: "old_id" (int) and "new_text" (string)
- For DELETE: "old_id" (int)
- For NOOP: "existing_id" (int)"""


def _parse_json_array(text: str) -> list:
    """Parse a JSON array from LLM output, handling common edge cases."""
    text = text.strip()
    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    # Try extracting JSON from markdown code blocks
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                result = json.loads(block)
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                continue
    # Try finding array in text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    return []


def extract_facts(provider, messages: str, context: str = "stop") -> list[str]:
    """Extract atomic facts from conversation using LLM.

    Args:
        provider: LLM provider instance
        messages: conversation text
        context: "stop", "pre_compact", or "session_end"

    Returns: list of fact strings
    """
    if context == "pre_compact":
        system = FACT_EXTRACTION_PROMPT_AGGRESSIVE
    else:
        system = FACT_EXTRACTION_PROMPT

    try:
        response = provider.complete(system, messages)
        facts = _parse_json_array(response)
        # Filter to strings only
        facts = [f for f in facts if isinstance(f, str) and f.strip()]
        logger.info("Extracted %d facts (context=%s)", len(facts), context)
        return facts
    except Exception as e:
        logger.error("Fact extraction failed: %s", e)
        return []


def run_audn(provider, engine, facts: list[str], source: str) -> list[dict]:
    """Run AUDN cycle on extracted facts.

    For providers with supports_audn=True: uses LLM to decide action per fact.
    For Ollama (supports_audn=False): uses engine.is_novel() for ADD/NOOP only.

    Returns: list of action dicts
    """
    if not facts:
        return []

    if not provider.supports_audn:
        # Ollama fallback: novelty check only
        decisions = []
        for i, fact in enumerate(facts):
            is_new = engine.is_novel(fact, threshold=0.88)
            if is_new:
                decisions.append({"action": "ADD", "fact_index": i})
            else:
                decisions.append({"action": "NOOP", "fact_index": i})
        return decisions

    # Full AUDN with LLM
    # Gather similar memories for each fact
    similar_per_fact = {}
    for i, fact in enumerate(facts):
        try:
            results = engine.hybrid_search(fact, k=5)
            similar_per_fact[i] = results
        except Exception:
            similar_per_fact[i] = []

    # Format for AUDN prompt
    facts_json = json.dumps([{"index": i, "text": f} for i, f in enumerate(facts)], indent=2)
    similar_json = json.dumps({
        str(i): [{"id": m.get("id"), "text": m.get("text"), "similarity": round(m.get("similarity", 0), 3)}
                 for m in mems[:5]]
        for i, mems in similar_per_fact.items()
    }, indent=2)

    prompt = AUDN_PROMPT.format(facts_json=facts_json, similar_json=similar_json)

    try:
        response = provider.complete("You are a memory manager. Output only valid JSON.", prompt)
        decisions = _parse_json_array(response)
        # Validate decisions
        valid = []
        for d in decisions:
            if isinstance(d, dict) and "action" in d:
                d["action"] = d["action"].upper()
                valid.append(d)
        return valid
    except Exception as e:
        logger.error("AUDN cycle failed: %s", e)
        # Fallback: add all facts
        return [{"action": "ADD", "fact_index": i} for i in range(len(facts))]


def execute_actions(engine, actions: list[dict], facts: list[str], source: str) -> dict:
    """Execute AUDN decisions against the memory engine.

    Returns: summary dict with counts
    """
    stored_count = 0
    updated_count = 0
    deleted_count = 0
    result_actions = []

    for action in actions:
        act = action.get("action", "").upper()
        fact_idx = action.get("fact_index", 0)
        fact_text = facts[fact_idx] if fact_idx < len(facts) else ""

        try:
            if act == "ADD":
                ids = engine.add_memories(
                    texts=[fact_text],
                    source=source,
                    deduplicate=True
                )
                new_id = ids.get("ids", [None])[0]
                result_actions.append({"action": "add", "text": fact_text, "id": new_id})
                stored_count += 1

            elif act == "UPDATE":
                old_id = action.get("old_id")
                new_text = action.get("new_text", fact_text)
                if old_id is not None:
                    # Get old text for audit
                    engine.delete_memory(old_id)
                ids = engine.add_memories(
                    texts=[new_text],
                    source=source,
                    deduplicate=False,
                    metadata={"supersedes": old_id}
                )
                new_id = ids.get("ids", [None])[0]
                result_actions.append({"action": "update", "old_id": old_id, "text": new_text, "new_id": new_id})
                updated_count += 1

            elif act == "DELETE":
                old_id = action.get("old_id")
                if old_id is not None:
                    engine.delete_memory(old_id)
                    result_actions.append({"action": "delete", "old_id": old_id})
                    deleted_count += 1

            elif act == "NOOP":
                existing_id = action.get("existing_id")
                result_actions.append({"action": "noop", "text": fact_text, "existing_id": existing_id})

        except Exception as e:
            logger.error("Failed to execute %s for fact '%s': %s", act, fact_text[:50], e)
            result_actions.append({"action": "error", "text": fact_text, "error": str(e)})

    return {
        "actions": result_actions,
        "stored_count": stored_count,
        "updated_count": updated_count,
        "deleted_count": deleted_count,
    }


def run_extraction(
    provider: Optional[object],
    engine,
    messages: str,
    source: str,
    context: str = "stop"
) -> dict:
    """Full extraction pipeline: extract facts → AUDN → execute.

    Args:
        provider: LLM provider (None = extraction disabled)
        engine: MemoryEngine instance
        messages: conversation text
        source: memory source identifier
        context: "stop", "pre_compact", or "session_end"

    Returns: result dict with actions and counts
    """
    if provider is None:
        return {"error": "extraction_disabled"}

    # Step 1: Extract facts
    facts = extract_facts(provider, messages, context=context)
    if not facts:
        return {
            "actions": [],
            "extracted_count": 0,
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
        }

    # Step 2: AUDN decisions
    decisions = run_audn(provider, engine, facts, source)

    # Step 3: Execute
    result = execute_actions(engine, decisions, facts, source)
    result["extracted_count"] = len(facts)

    logger.info(
        "Extraction complete: %d extracted, %d stored, %d updated, %d deleted",
        len(facts), result["stored_count"], result["updated_count"], result.get("deleted_count", 0)
    )

    return result
```

**Step 9: Run tests to verify they pass**

Run: `cd /path/to/memories &&python -m pytest tests/test_llm_extract.py -v`
Expected: All 13 tests PASS

**Step 10: Commit**

```bash
git add llm_extract.py tests/test_llm_extract.py
git commit -m "feat: add extraction pipeline with AUDN (Add/Update/Delete/Noop)"
```

---

### Task 5: Supersede Endpoint — Test

**Files:**
- Modify: `tests/test_memory_engine.py`

**Step 11: Add supersede test to existing test file**

Add a new test class at the end of `tests/test_memory_engine.py`:

```python
class TestSupersede:
    """Test memory supersede (targeted update with audit trail)."""

    def test_supersede_replaces_memory(self, populated_engine):
        """Supersede deletes old memory and adds new one with link."""
        old_count = populated_engine.index.ntotal
        old_id = 0  # first memory in populated_engine

        result = populated_engine.supersede(
            old_id=old_id,
            new_text="Updated: switched from Prisma to Drizzle",
            source="test/supersede"
        )

        assert result["old_id"] == old_id
        assert result["new_id"] is not None
        assert populated_engine.index.ntotal == old_count  # same count (delete + add)

    def test_supersede_nonexistent_id_raises(self, populated_engine):
        """Superseding a nonexistent memory raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            populated_engine.supersede(
                old_id=9999,
                new_text="does not matter",
                source="test"
            )
```

**Step 12: Run test to verify it fails**

Run: `cd /path/to/memories &&python -m pytest tests/test_memory_engine.py::TestSupersede -v`
Expected: FAIL — `AttributeError: 'MemoryEngine' object has no attribute 'supersede'`

---

### Task 6: Supersede — Implementation

**Files:**
- Modify: `memory_engine.py` (add `supersede()` method after `delete_memory()`)

**Step 13: Add supersede method to MemoryEngine**

Add after `delete_memory()` method (around line 338):

```python
def supersede(self, old_id: int, new_text: str, source: str = "") -> dict:
    """Replace a memory with an updated version, preserving audit trail.

    Deletes the old memory and creates a new one with metadata linking
    back to the original.

    Args:
        old_id: ID of the memory to supersede
        new_text: Updated memory text
        source: Source identifier for the new memory

    Returns:
        dict with old_id, new_id, and previous_text

    Raises:
        ValueError: if old_id doesn't exist
    """
    # Verify old memory exists
    if old_id >= len(self.metadata) or self.metadata[old_id] is None:
        raise ValueError(f"Memory {old_id} not found")

    previous_text = self.metadata[old_id].get("text", "")

    # Delete old
    self.delete_memory(old_id)

    # Add new with supersede metadata
    result = self.add_memories(
        texts=[new_text],
        source=source,
        deduplicate=False,
        metadata={"supersedes": old_id, "previous_text": previous_text}
    )
    new_id = result["ids"][0] if result.get("ids") else None

    logger.info("Superseded memory %d → %d", old_id, new_id)
    return {"old_id": old_id, "new_id": new_id, "previous_text": previous_text}
```

**Step 14: Run test to verify it passes**

Run: `cd /path/to/memories &&python -m pytest tests/test_memory_engine.py::TestSupersede -v`
Expected: PASS

**Step 15: Run all existing tests to check for regressions**

Run: `cd /path/to/memories &&python -m pytest tests/test_memory_engine.py -v`
Expected: All tests PASS (31 existing + 2 new = 33)

**Step 16: Commit**

```bash
git add memory_engine.py tests/test_memory_engine.py
git commit -m "feat: add supersede() method for targeted memory updates"
```

---

### Task 7: API Endpoints — Tests

**Files:**
- Create: `tests/test_extract_api.py`

**Step 17: Write failing API endpoint tests**

```python
"""Tests for extraction API endpoints in app.py."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked memory engine."""
    with patch("app.MemoryEngine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5}
        MockEngine.return_value = mock_engine

        from app import app
        app.state.memory = mock_engine
        yield TestClient(app), mock_engine


class TestExtractEndpoint:
    """Test POST /memory/extract."""

    def test_extract_returns_501_when_disabled(self, client):
        test_client, mock_engine = client
        with patch("app.extract_provider", None):
            response = test_client.post(
                "/memory/extract",
                json={"messages": "test", "source": "test", "context": "stop"},
                headers={"X-API-Key": "test-key"}
            )
            assert response.status_code == 501

    def test_extract_returns_results(self, client):
        test_client, mock_engine = client
        mock_result = {
            "actions": [{"action": "add", "text": "test fact", "id": 1}],
            "extracted_count": 1,
            "stored_count": 1,
            "updated_count": 0,
            "deleted_count": 0,
        }
        with patch("app.extract_provider", MagicMock()), \
             patch("app.run_extraction", return_value=mock_result):
            response = test_client.post(
                "/memory/extract",
                json={"messages": "User: test\nAssistant: ok", "source": "test/proj", "context": "stop"},
                headers={"X-API-Key": "test-key"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["extracted_count"] == 1


class TestSupersedeEndpoint:
    """Test POST /memory/supersede."""

    def test_supersede_success(self, client):
        test_client, mock_engine = client
        mock_engine.supersede.return_value = {
            "old_id": 42, "new_id": 100, "previous_text": "old text"
        }
        response = test_client.post(
            "/memory/supersede",
            json={"old_id": 42, "new_text": "new text", "source": "test"},
            headers={"X-API-Key": "test-key"}
        )
        assert response.status_code == 200
        assert response.json()["new_id"] == 100

    def test_supersede_not_found(self, client):
        test_client, mock_engine = client
        mock_engine.supersede.side_effect = ValueError("Memory 999 not found")
        response = test_client.post(
            "/memory/supersede",
            json={"old_id": 999, "new_text": "new text", "source": "test"},
            headers={"X-API-Key": "test-key"}
        )
        assert response.status_code == 404


class TestExtractStatusEndpoint:
    """Test GET /extract/status."""

    def test_status_when_disabled(self, client):
        test_client, _ = client
        with patch("app.extract_provider", None):
            response = test_client.get(
                "/extract/status",
                headers={"X-API-Key": "test-key"}
            )
            assert response.status_code == 200
            assert response.json()["enabled"] is False

    def test_status_when_enabled(self, client):
        test_client, _ = client
        mock_provider = MagicMock()
        mock_provider.provider_name = "anthropic"
        mock_provider.model = "claude-haiku-4-5-20251001"
        mock_provider.health_check.return_value = True

        with patch("app.extract_provider", mock_provider):
            response = test_client.get(
                "/extract/status",
                headers={"X-API-Key": "test-key"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True
            assert data["provider"] == "anthropic"
            assert data["status"] == "healthy"
```

**Step 18: Run tests to verify they fail**

Run: `cd /path/to/memories &&python -m pytest tests/test_extract_api.py -v`
Expected: FAIL — endpoints don't exist yet

---

### Task 8: API Endpoints — Implementation

**Files:**
- Modify: `app.py`

**Step 19: Add extraction endpoints to app.py**

Add imports near the top of `app.py` (after existing imports):

```python
# Optional extraction support
try:
    from llm_provider import get_provider
    from llm_extract import run_extraction
    extract_provider = get_provider()
    if extract_provider:
        logger.info("Extraction enabled: provider=%s, model=%s", extract_provider.provider_name, extract_provider.model)
    else:
        logger.info("Extraction disabled (EXTRACT_PROVIDER not set)")
except Exception as e:
    logger.warning("Extraction setup failed: %s", e)
    extract_provider = None
    run_extraction = None
```

Add Pydantic models (after existing models around line 134):

```python
class ExtractRequest(BaseModel):
    messages: str = Field(..., description="Conversation text to extract facts from")
    source: str = Field(default="", description="Source identifier (e.g., 'claude-code/my-project')")
    context: str = Field(default="stop", description="Extraction context: stop, pre_compact, session_end")

class SupersedeRequest(BaseModel):
    old_id: int = Field(..., description="ID of memory to supersede")
    new_text: str = Field(..., description="Updated memory text")
    source: str = Field(default="", description="Source identifier")
```

Add endpoints (before the `if __name__` block):

```python
# --- Extraction endpoints ---

@app.post("/memory/extract")
async def memory_extract(request: ExtractRequest):
    """Extract facts from conversation and store via AUDN pipeline."""
    if extract_provider is None:
        raise HTTPException(status_code=501, detail="Extraction not configured. Set EXTRACT_PROVIDER env var.")
    logger.info("Extract: source=%s, context=%s, message_length=%d", request.source, request.context, len(request.messages))
    try:
        result = run_extraction(
            provider=extract_provider,
            engine=memory,
            messages=request.messages,
            source=request.source,
            context=request.context,
        )
        return result
    except Exception as e:
        logger.exception("Extraction failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/supersede")
async def memory_supersede(request: SupersedeRequest):
    """Replace a memory with an updated version (audit trail preserved)."""
    logger.info("Supersede: old_id=%d, source=%s", request.old_id, request.source)
    try:
        result = memory.supersede(
            old_id=request.old_id,
            new_text=request.new_text,
            source=request.source,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Supersede failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/extract/status")
async def extract_status():
    """Check extraction provider health and configuration."""
    if extract_provider is None:
        return {"enabled": False}
    try:
        healthy = extract_provider.health_check()
        return {
            "enabled": True,
            "provider": extract_provider.provider_name,
            "model": extract_provider.model,
            "status": "healthy" if healthy else "unhealthy",
        }
    except Exception as e:
        return {
            "enabled": True,
            "provider": extract_provider.provider_name,
            "model": extract_provider.model,
            "status": f"error: {e}",
        }
```

**Step 20: Run API tests to verify they pass**

Run: `cd /path/to/memories &&python -m pytest tests/test_extract_api.py -v`
Expected: All 6 tests PASS

**Step 21: Run all tests to check for regressions**

Run: `cd /path/to/memories &&python -m pytest tests/ -v`
Expected: All tests PASS

**Step 22: Commit**

```bash
git add app.py tests/test_extract_api.py
git commit -m "feat: add /memory/extract, /memory/supersede, /extract/status endpoints"
```

---

### Task 9: Requirements and Dockerfile

**Files:**
- Create: `requirements-extract.txt`
- Modify: `Dockerfile`

**Step 23: Create requirements-extract.txt**

```
# Optional: LLM extraction support
# Only needed if EXTRACT_PROVIDER is set to "anthropic" or "openai"
# Ollama uses HTTP directly (no SDK needed)
anthropic>=0.40.0
openai>=1.50.0
```

**Step 24: Update Dockerfile with ENABLE_EXTRACT build arg**

Add after the existing `ENABLE_CLOUD_SYNC` pattern:

```dockerfile
ARG ENABLE_EXTRACT=false
COPY requirements-extract.txt .
RUN if [ "$ENABLE_EXTRACT" = "true" ]; then pip install --no-cache-dir -r requirements-extract.txt; fi
```

Copy new source files in the runtime stage:

```dockerfile
COPY llm_provider.py .
COPY llm_extract.py .
```

**Step 25: Verify Docker build succeeds**

Run: `cd /path/to/memories &&docker compose build faiss-memory`
Expected: Build succeeds. Image size should be similar to before (~650MB) since extraction deps not included by default.

**Step 26: Commit**

```bash
git add requirements-extract.txt Dockerfile
git commit -m "feat: add optional extraction deps and Dockerfile build arg"
```

---

### Task 10: Hook Scripts — Retrieval (memory-recall.sh, memory-query.sh)

**Files:**
- Create: `integrations/claude-code/hooks/memory-recall.sh`
- Create: `integrations/claude-code/hooks/memory-query.sh`

**Step 27: Write memory-recall.sh (SessionStart hook)**

```bash
#!/bin/bash
# memory-recall.sh — SessionStart hook
# Loads project-relevant memories into Claude Code context.
# Sync hook: blocks until done, injects additionalContext.

set -euo pipefail

FAISS_URL="${FAISS_URL:-http://localhost:8900}"
FAISS_API_KEY="${FAISS_API_KEY:-}"

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
if [ -z "$CWD" ]; then
  exit 0
fi

PROJECT=$(basename "$CWD")

RESULTS=$(curl -sf -X POST "$FAISS_URL/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FAISS_API_KEY" \
  -d "{\"query\": \"project $PROJECT conventions decisions patterns\", \"k\": 10, \"hybrid\": true}" \
  2>/dev/null \
  | jq -r '[.results[] | select(.similarity > 0.3)] | .[0:8] | map("- \(.text)") | join("\n")' 2>/dev/null) || true

if [ -z "$RESULTS" ] || [ "$RESULTS" = "null" ]; then
  exit 0
fi

jq -n --arg memories "$RESULTS" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: ("## Relevant Memories\n\n" + $memories)
  }
}'
```

**Step 28: Write memory-query.sh (UserPromptSubmit hook)**

```bash
#!/bin/bash
# memory-query.sh — UserPromptSubmit hook
# Searches FAISS for memories relevant to the current prompt.
# Sync hook: blocks until done, injects additionalContext.

set -euo pipefail

FAISS_URL="${FAISS_URL:-http://localhost:8900}"
FAISS_API_KEY="${FAISS_API_KEY:-}"

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

# Skip short/trivial prompts
if [ ${#PROMPT} -lt 20 ]; then
  exit 0
fi

RESULTS=$(curl -sf -X POST "$FAISS_URL/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FAISS_API_KEY" \
  -d "{\"query\": $(echo "$PROMPT" | jq -Rs), \"k\": 5, \"hybrid\": true, \"threshold\": 0.4}" \
  2>/dev/null \
  | jq -r '[.results[] | select(.similarity > 0.4)] | .[0:5] | map("- [\(.source)] \(.text)") | join("\n")' 2>/dev/null) || true

if [ -z "$RESULTS" ] || [ "$RESULTS" = "null" ]; then
  exit 0
fi

jq -n --arg memories "$RESULTS" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: ("## Retrieved Memories\n" + $memories)
  }
}'
```

**Step 29: Make scripts executable and commit**

```bash
chmod +x integrations/claude-code/hooks/memory-recall.sh
chmod +x integrations/claude-code/hooks/memory-query.sh
git add integrations/claude-code/hooks/memory-recall.sh integrations/claude-code/hooks/memory-query.sh
git commit -m "feat: add retrieval hooks (memory-recall, memory-query)"
```

---

### Task 11: Hook Scripts — Extraction (memory-extract.sh, memory-flush.sh, memory-commit.sh)

**Files:**
- Create: `integrations/claude-code/hooks/memory-extract.sh`
- Create: `integrations/claude-code/hooks/memory-flush.sh`
- Create: `integrations/claude-code/hooks/memory-commit.sh`

**Step 30: Write memory-extract.sh (Stop hook, async)**

```bash
#!/bin/bash
# memory-extract.sh — Stop hook (async)
# Extracts facts from the last exchange and stores via AUDN pipeline.
# Requires FAISS service with extraction enabled (EXTRACT_PROVIDER set).

set -euo pipefail

FAISS_URL="${FAISS_URL:-http://localhost:8900}"
FAISS_API_KEY="${FAISS_API_KEY:-}"

INPUT=$(cat)
STOP_REASON=$(echo "$INPUT" | jq -r '.stop_reason // "end_turn"')

# Only extract on normal completions
if [ "$STOP_REASON" != "end_turn" ]; then
  exit 0
fi

MESSAGES=$(echo "$INPUT" | jq -r '.messages // empty')
if [ -z "$MESSAGES" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

# POST to extraction endpoint (fire-and-forget, async hook)
curl -sf -X POST "$FAISS_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FAISS_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"stop\"}" \
  > /dev/null 2>&1 || true
```

**Step 31: Write memory-flush.sh (PreCompact hook, async)**

```bash
#!/bin/bash
# memory-flush.sh — PreCompact hook (async)
# Aggressive extraction before context compaction.
# Same as memory-extract.sh but with context=pre_compact.

set -euo pipefail

FAISS_URL="${FAISS_URL:-http://localhost:8900}"
FAISS_API_KEY="${FAISS_API_KEY:-}"

INPUT=$(cat)
MESSAGES=$(echo "$INPUT" | jq -r '.messages // empty')
if [ -z "$MESSAGES" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

curl -sf -X POST "$FAISS_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FAISS_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"pre_compact\"}" \
  > /dev/null 2>&1 || true
```

**Step 32: Write memory-commit.sh (SessionEnd hook, async)**

```bash
#!/bin/bash
# memory-commit.sh — SessionEnd hook (async)
# Final extraction pass before session terminates.

set -euo pipefail

FAISS_URL="${FAISS_URL:-http://localhost:8900}"
FAISS_API_KEY="${FAISS_API_KEY:-}"

INPUT=$(cat)
MESSAGES=$(echo "$INPUT" | jq -r '.messages // empty')
if [ -z "$MESSAGES" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

curl -sf -X POST "$FAISS_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FAISS_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"session_end\"}" \
  > /dev/null 2>&1 || true
```

**Step 33: Make scripts executable and commit**

```bash
chmod +x integrations/claude-code/hooks/memory-extract.sh
chmod +x integrations/claude-code/hooks/memory-flush.sh
chmod +x integrations/claude-code/hooks/memory-commit.sh
git add integrations/claude-code/hooks/memory-extract.sh integrations/claude-code/hooks/memory-flush.sh integrations/claude-code/hooks/memory-commit.sh
git commit -m "feat: add extraction hooks (memory-extract, memory-flush, memory-commit)"
```

---

### Task 12: Hooks Configuration (hooks.json)

**Files:**
- Create: `integrations/claude-code/hooks/hooks.json`

**Step 34: Write hooks.json**

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-recall.sh",
        "timeout": 5
      }]
    }],
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-query.sh",
        "timeout": 3
      }]
    }],
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-extract.sh",
        "timeout": 30
      }]
    }],
    "PreCompact": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-flush.sh",
        "timeout": 30
      }]
    }],
    "SessionEnd": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-commit.sh",
        "timeout": 30
      }]
    }]
  }
}
```

**Step 35: Commit**

```bash
git add integrations/claude-code/hooks/hooks.json
git commit -m "feat: add hooks.json configuration for Claude Code/Codex"
```

---

### Task 13: Interactive Installer

**Files:**
- Create: `integrations/claude-code/install.sh`

**Step 36: Write install.sh**

Interactive installer that:
1. Checks FAISS service health
2. Prompts for extraction provider choice
3. Validates API key / Ollama connectivity
4. Copies hooks to `~/.claude/hooks/memory/`
5. Merges hook config into `~/.claude/settings.json`
6. Adds env vars to `~/.zshrc` or `~/.bashrc`

The script should handle `--codex` flag for Codex install path (`~/.codex/` instead of `~/.claude/`).

Full implementation: ~120 lines of bash with read prompts, curl validation, jq config merging, and colored output.

**Step 37: Test installer manually**

Run: `cd /path/to/memories &&bash integrations/claude-code/install.sh`
Expected: Interactive prompts, successful hook installation

**Step 38: Commit**

```bash
chmod +x integrations/claude-code/install.sh
git add integrations/claude-code/install.sh
git commit -m "feat: add interactive installer for Claude Code/Codex hooks"
```

---

### Task 14: OpenClaw Skill Update

**Files:**
- Modify: `integrations/openclaw-skill.md`

**Step 39: Add extraction functions to OpenClaw skill**

Add two new functions to the skill:
- `memory_recall_faiss`: called at task start, searches for project context
- `memory_extract_faiss`: called after completing tasks, POSTs conversation to `/memory/extract`

Add instructions in the skill telling the agent when to call these functions.

**Step 40: Commit**

```bash
git add integrations/openclaw-skill.md
git commit -m "feat: update OpenClaw skill with auto-recall and extract functions"
```

---

### Task 15: LLM QuickStart Guide

**Files:**
- Create: `integrations/QUICKSTART-LLM.md` (already created — review and finalize)

**Step 41: Review and finalize QUICKSTART-LLM.md**

The file `integrations/QUICKSTART-LLM.md` is a standalone guide designed to be fed directly to any LLM so it can set up automatic memory hooks without human intervention. It covers:

- Prerequisites (service running, API key, jq)
- Claude Code setup (installer + manual)
- Codex setup (symlink or --codex flag)
- OpenClaw setup (skill-based)
- Hook behavior table
- Provider comparison table
- Environment variables reference
- Verification steps (retrieval + extraction)
- Disabling / uninstalling
- Troubleshooting

Review the file to make sure all paths and commands match the final implementation. Update any references if hook scripts changed during implementation.

**Step 42: Commit**

```bash
git add integrations/QUICKSTART-LLM.md
git commit -m "docs: add LLM-friendly QuickStart guide for automatic memory setup"
```

---

### Task 16: README Documentation

**Files:**
- Modify: `README.md`

**Step 43: Add Automatic Memory Layer section to README**

Add a new section covering:
- What it does and why
- Architecture overview (simplified diagram)
- Quick setup for Claude Code: `./integrations/claude-code/install.sh`
- Quick setup for Codex: `./integrations/claude-code/install.sh --codex`
- Quick setup for OpenClaw: link to updated skill
- Link to `integrations/QUICKSTART-LLM.md` — "Feed this to your LLM to set it up automatically"
- Provider comparison table (Anthropic vs OpenAI vs Ollama)
- Token budget table
- Environment variables reference
- How to disable/uninstall

**Step 44: Commit**

```bash
git add README.md
git commit -m "docs: add Automatic Memory Layer documentation to README"
```

---

### Task 17: Final Verification

**Step 45: Run all tests**

Run: `cd /path/to/memories &&python -m pytest tests/ -v`
Expected: All tests PASS (31 existing + 2 supersede + 12 provider + 13 extract + 6 API = 64 tests)

**Step 46: Docker build test**

Run: `cd /path/to/memories &&docker compose build faiss-memory`
Expected: Build succeeds, image size ~650MB (no extraction deps)

**Step 47: Docker build with extraction**

Run: `cd /path/to/memories &&docker compose build --build-arg ENABLE_EXTRACT=true faiss-memory`
Expected: Build succeeds with anthropic + openai SDKs installed

**Step 48: Start container and verify health**

Run: `docker compose up -d faiss-memory && sleep 3 && curl -s http://localhost:8900/health`
Expected: `{"status":"healthy",...}`

**Step 49: Verify extract status endpoint**

Run: `curl -s -H "X-API-Key: $FAISS_API_KEY" http://localhost:8900/extract/status`
Expected: `{"enabled": false}` (since EXTRACT_PROVIDER not set in container)

**Step 50: Push and update PR**

```bash
git push origin feature/automatic-memory-layer
```

Expected: PR #3 automatically updated with all new commits.
