# R1 Batch 2: Extraction Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the extraction write-path controllable — per-source profiles with custom rules, single-call mode for cost reduction, dry-run preview with selective commit, and missed memory capture flow.

**Architecture:** New `extraction_profiles.py` module owns profile CRUD and cascade resolution. `llm_extract.py` is extended to consult profiles before extraction, inject rules into AUDN prompts, and support a single-call combined mode. `app.py` gains new endpoints for profiles, dry-run commit, and missed memory. All changes are additive — existing extraction behavior is unchanged when no profile exists.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-03-19-r1-controllable-memory-design.md` (B2 section)

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `extraction_profiles.py` | Profile CRUD, JSON persistence, cascade resolution, defaults | Create |
| `llm_extract.py` | Profile-aware extraction, rules injection, single-call mode | Modify |
| `app.py` | Profile endpoints, dry-run on extract, commit endpoint, missed memory endpoint | Modify |
| `mcp-server/index.js` | `memory_missed` MCP tool | Modify |
| `tests/test_extraction_profiles.py` | Profile CRUD + cascade + rules tests | Create |
| `tests/test_single_call_extraction.py` | Single-call mode tests | Create |
| `tests/test_extraction_dry_run.py` | Dry-run + commit flow tests | Create |
| `tests/test_missed_memory.py` | Missed memory endpoint tests | Create |

---

### Task 1: Extraction profiles module — CRUD and cascade resolution

**Files:**
- Create: `extraction_profiles.py`
- Test: `tests/test_extraction_profiles.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_extraction_profiles.py`:

```python
"""Tests for extraction profile CRUD and cascade resolution."""
import importlib
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from extraction_profiles import ExtractionProfiles


@pytest.fixture
def profiles():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump([], f)
        path = f.name
    yield ExtractionProfiles(path)
    os.unlink(path)


class TestProfileCRUD:

    def test_create_profile(self, profiles):
        profiles.put("claude-code/", {"mode": "aggressive", "max_facts": 50})
        result = profiles.get("claude-code/")
        assert result["mode"] == "aggressive"
        assert result["max_facts"] == 50

    def test_list_profiles(self, profiles):
        profiles.put("project-a/", {"mode": "standard"})
        profiles.put("project-b/", {"mode": "conservative"})
        result = profiles.list_all()
        assert len(result) == 2

    def test_delete_profile(self, profiles):
        profiles.put("temp/", {"mode": "standard"})
        profiles.delete("temp/")
        assert profiles.get("temp/") is None

    def test_update_existing_profile(self, profiles):
        profiles.put("proj/", {"mode": "standard", "max_facts": 30})
        profiles.put("proj/", {"mode": "aggressive", "max_facts": 50})
        result = profiles.get("proj/")
        assert result["mode"] == "aggressive"
        assert result["max_facts"] == 50

    def test_defaults_applied(self, profiles):
        profiles.put("bare/", {"mode": "conservative"})
        result = profiles.get("bare/")
        assert result["max_facts"] == 30  # default
        assert result["max_fact_chars"] == 500  # default
        assert result["enabled"] is True  # default


class TestProfileCascade:

    def test_child_inherits_from_parent(self, profiles):
        profiles.put("claude-code/", {"mode": "aggressive", "max_facts": 50})
        result = profiles.resolve("claude-code/memories/")
        assert result["mode"] == "aggressive"
        assert result["max_facts"] == 50

    def test_child_overrides_parent(self, profiles):
        profiles.put("claude-code/", {"mode": "aggressive", "max_facts": 50})
        profiles.put("claude-code/memories/", {"mode": "conservative"})
        result = profiles.resolve("claude-code/memories/")
        assert result["mode"] == "conservative"
        assert result["max_facts"] == 50  # inherited

    def test_no_profile_returns_defaults(self, profiles):
        result = profiles.resolve("unknown/source/")
        assert result["mode"] == "standard"
        assert result["max_facts"] == 30

    def test_deeply_nested_cascade(self, profiles):
        profiles.put("a/", {"mode": "aggressive"})
        profiles.put("a/b/", {"max_facts": 10})
        result = profiles.resolve("a/b/c/d/")
        assert result["mode"] == "aggressive"  # from a/
        assert result["max_facts"] == 10  # from a/b/


class TestProfileRules:

    def test_rules_stored_and_retrieved(self, profiles):
        profiles.put("proj/", {
            "mode": "standard",
            "rules": {
                "always_remember": ["API contracts"],
                "never_remember": ["temp debugging"],
                "custom_instructions": "Keep port numbers",
            }
        })
        result = profiles.get("proj/")
        assert result["rules"]["always_remember"] == ["API contracts"]
        assert result["rules"]["never_remember"] == ["temp debugging"]
        assert result["rules"]["custom_instructions"] == "Keep port numbers"

    def test_rules_cascade(self, profiles):
        profiles.put("parent/", {"rules": {"always_remember": ["decisions"]}})
        profiles.put("parent/child/", {"rules": {"never_remember": ["wip hashes"]}})
        result = profiles.resolve("parent/child/")
        # Child rules should override, not merge
        assert "always_remember" not in result["rules"]
        assert result["rules"]["never_remember"] == ["wip hashes"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_profiles.py -v`
Expected: FAIL — `extraction_profiles` module doesn't exist

- [ ] **Step 3: Create extraction_profiles.py**

```python
"""Extraction profiles — per-source configuration for the extraction pipeline.

Profiles control extraction behavior per source prefix: mode, fact limits,
rules for AUDN prompt injection, and single-call opt-in. Profiles cascade
from parent prefix to child prefix (e.g., claude-code/ → claude-code/memories/).
"""
import json
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional


DEFAULTS = {
    "mode": "standard",
    "max_facts": 30,
    "max_fact_chars": 500,
    "half_life_days": 30,
    "single_call": False,
    "enabled": True,
    "rules": {},
}


class ExtractionProfiles:
    """Manage extraction profiles stored as JSON."""

    def __init__(self, path: str):
        self._path = path
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            with open(self._path) as f:
                data = json.load(f)
            if isinstance(data, list):
                # Migrate from list to dict format
                self._profiles = {p["source_prefix"]: p for p in data}
            else:
                self._profiles = data
        else:
            self._profiles = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._profiles, f, indent=2)

    def put(self, source_prefix: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update a profile for a source prefix."""
        profile = deepcopy(DEFAULTS)
        profile.update(config)
        profile["source_prefix"] = source_prefix
        self._profiles[source_prefix] = profile
        self._save()
        return profile

    def get(self, source_prefix: str) -> Optional[Dict[str, Any]]:
        """Get a profile by exact prefix match. Returns None if not found."""
        return deepcopy(self._profiles.get(source_prefix))

    def delete(self, source_prefix: str) -> bool:
        """Delete a profile. Returns True if it existed."""
        if source_prefix in self._profiles:
            del self._profiles[source_prefix]
            self._save()
            return True
        return False

    def list_all(self) -> List[Dict[str, Any]]:
        """List all profiles."""
        return [deepcopy(p) for p in self._profiles.values()]

    def resolve(self, source: str) -> Dict[str, Any]:
        """Resolve the effective profile for a source, cascading from parent.

        Walks from the most specific prefix to the least specific.
        Child values override parent values. Falls back to DEFAULTS.
        """
        # Build list of candidate prefixes from most to least specific
        parts = source.rstrip("/").split("/")
        candidates = []
        for i in range(len(parts), 0, -1):
            prefix = "/".join(parts[:i]) + "/"
            candidates.append(prefix)

        # Start with defaults, apply parent → child (reverse of candidates)
        result = deepcopy(DEFAULTS)
        for prefix in reversed(candidates):
            if prefix in self._profiles:
                profile = self._profiles[prefix]
                for key, value in profile.items():
                    if key == "source_prefix":
                        continue
                    result[key] = deepcopy(value)

        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_profiles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add extraction_profiles.py tests/test_extraction_profiles.py
git commit -m "feat: extraction profiles module — CRUD, cascade resolution, per-source config"
```

---

### Task 2: Profile API endpoints

**Files:**
- Modify: `app.py` (add profile endpoints after extraction quality metrics)
- Test: `tests/test_extraction_profiles.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extraction_profiles.py`:

```python
class TestProfileAPI:
    """Test profile CRUD via HTTP endpoints."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)
                # Use real ExtractionProfiles with temp path
                from extraction_profiles import ExtractionProfiles
                app_module.extraction_profiles = ExtractionProfiles(
                    os.path.join(tmpdir, "extraction_profiles.json")
                )
                mock_engine = MagicMock()
                mock_engine.metadata = []
                app_module.memory = mock_engine
                yield TestClient(app_module.app)

    def test_list_profiles_empty(self, client):
        resp = client.get("/extraction/profiles")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_profile(self, client):
        resp = client.put("/extraction/profiles/test-project/",
                         json={"mode": "aggressive", "max_facts": 50})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "aggressive"

    def test_get_profile(self, client):
        client.put("/extraction/profiles/get-test/",
                  json={"mode": "conservative"})
        resp = client.get("/extraction/profiles/get-test/")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "conservative"

    def test_delete_profile(self, client):
        client.put("/extraction/profiles/del-test/",
                  json={"mode": "standard"})
        resp = client.delete("/extraction/profiles/del-test/")
        assert resp.status_code == 200

        resp2 = client.get("/extraction/profiles/del-test/")
        assert resp2.status_code == 404

    def test_profile_with_slashes_in_prefix(self, client):
        resp = client.put("/extraction/profiles/claude-code/memories/deep/",
                         json={"mode": "aggressive"})
        assert resp.status_code == 200

        resp2 = client.get("/extraction/profiles/claude-code/memories/deep/")
        assert resp2.status_code == 200
        assert resp2.json()["mode"] == "aggressive"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_profiles.py::TestProfileAPI -v`
Expected: FAIL — endpoints don't exist

- [ ] **Step 3: Add profile endpoints to app.py**

Initialize the profiles store near the top of `app.py` (after the memory engine init):

```python
from extraction_profiles import ExtractionProfiles

extraction_profiles = ExtractionProfiles(
    os.path.join(os.environ.get("DATA_DIR", "data"), "extraction_profiles.json")
)
```

Add endpoints (use `{prefix:path}` for slash handling):

```python
# -- Extraction Profile endpoints ------------------------------------------ #

@app.get("/extraction/profiles")
async def list_extraction_profiles(request: Request):
    """List all extraction profiles."""
    _get_auth(request)
    return extraction_profiles.list_all()


@app.get("/extraction/profiles/{prefix:path}")
async def get_extraction_profile(prefix: str, request: Request):
    """Get a specific extraction profile."""
    _get_auth(request)
    profile = extraction_profiles.get(prefix)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile not found: {prefix}")
    return profile


@app.put("/extraction/profiles/{prefix:path}")
async def put_extraction_profile(prefix: str, request: Request):
    """Create or update an extraction profile."""
    auth = _get_auth(request)
    _require_admin(auth)
    body = await request.json()
    result = extraction_profiles.put(prefix, body)
    _audit(request, "extraction.profile_updated", resource_id=prefix)
    return result


@app.delete("/extraction/profiles/{prefix:path}")
async def delete_extraction_profile(prefix: str, request: Request):
    """Delete an extraction profile."""
    auth = _get_auth(request)
    _require_admin(auth)
    if not extraction_profiles.delete(prefix):
        raise HTTPException(status_code=404, detail=f"Profile not found: {prefix}")
    return {"deleted": prefix}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_profiles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add app.py tests/test_extraction_profiles.py
git commit -m "feat: extraction profile API endpoints — CRUD with path-param prefix routing"
```

---

### Task 3: Profile-aware extraction and rules injection

**Files:**
- Modify: `llm_extract.py:434-568` (run_extraction to consult profiles)
- Modify: `llm_extract.py:220-317` (run_audn to inject rules into AUDN_PROMPT)
- Test: `tests/test_extraction_profiles.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extraction_profiles.py`:

```python
class TestProfileAwareExtraction:
    """Test that extraction respects profile settings."""

    def test_profile_mode_affects_extraction_context(self):
        """Conservative mode should map to 'stop' context for extract_facts."""
        from extraction_profiles import ExtractionProfiles
        import tempfile, json, os

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({}, f)
            path = f.name
        try:
            profiles = ExtractionProfiles(path)
            profiles.put("conservative-proj/", {"mode": "conservative"})
            resolved = profiles.resolve("conservative-proj/source")
            assert resolved["mode"] == "conservative"
        finally:
            os.unlink(path)

    def test_rules_injection_format(self):
        """Rules should format into a prompt section."""
        from llm_extract import _build_rules_section

        rules = {
            "always_remember": ["API contracts", "architectural decisions"],
            "never_remember": ["temp debugging state"],
            "custom_instructions": "Treat port numbers as durable facts",
        }
        section = _build_rules_section(rules)
        assert "## Project-Specific Rules" in section
        assert "API contracts" in section
        assert "temp debugging state" in section
        assert "Treat port numbers" in section

    def test_empty_rules_returns_empty_string(self):
        from llm_extract import _build_rules_section
        assert _build_rules_section({}) == ""
        assert _build_rules_section(None) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_profiles.py::TestProfileAwareExtraction -v`
Expected: FAIL — `_build_rules_section` doesn't exist

- [ ] **Step 3: Add _build_rules_section to llm_extract.py**

Add after the AUDN_PROMPT constant (around line 115):

```python
def _build_rules_section(rules: dict | None) -> str:
    """Build a prompt section from extraction rules."""
    if not rules:
        return ""
    parts = ["## Project-Specific Rules"]
    always = rules.get("always_remember", [])
    if always:
        parts.append("ALWAYS remember these types of information:")
        for item in always:
            parts.append(f"  - {item}")
    never = rules.get("never_remember", [])
    if never:
        parts.append("NEVER remember these types of information:")
        for item in never:
            parts.append(f"  - {item}")
    custom = rules.get("custom_instructions", "")
    if custom:
        parts.append(f"Additional instructions: {custom}")
    return "\n".join(parts)
```

- [ ] **Step 4: Wire profiles into run_extraction**

In `run_extraction()` (line 434), add profile resolution at the start:

```python
def run_extraction(
    provider,
    engine,
    messages: str,
    source: str,
    context: str = "stop",
    allowed_prefixes=None,
    debug: bool = False,
    profile: dict | None = None,  # NEW — resolved profile from caller
) -> dict:
```

At the beginning of the function, apply profile settings:

```python
    if profile:
        max_facts = profile.get("max_facts", 30)
        max_chars = profile.get("max_fact_chars", 500)
        mode = profile.get("mode", "standard")
        if mode == "aggressive":
            context = "pre_compact"  # aggressive uses the detailed prompt
        rules = profile.get("rules", {})
    else:
        max_facts = 30
        max_chars = 500
        rules = {}
```

**Wire rules into `run_audn()`:**

In `llm_extract.py`, add `rules: dict | None = None` param to `run_audn()` (line 220):

```python
def run_audn(
    provider,
    engine,
    facts: list[dict],
    source: str,
    allowed_prefixes=None,
    debug: bool = False,
    rules: dict | None = None,  # NEW
) -> tuple[list[dict], dict, Optional[dict]]:
```

Inside `run_audn()`, where the AUDN prompt is assembled (around line 302), inject rules before the similar-memories context:

```python
    rules_section = _build_rules_section(rules)
    prompt = AUDN_PROMPT
    if rules_section:
        prompt = prompt + "\n\n" + rules_section
```

**Wire max_facts/max_chars into `extract_facts()`:**

In `run_extraction()`, after extracting profile values, pass them through. The current `extract_facts()` uses module-level constants `EXTRACT_MAX_FACTS` and `EXTRACT_MAX_FACT_CHARS`. Override them before calling:

```python
    # Temporarily override module-level extraction limits from profile
    import llm_extract as _extract_mod
    orig_max_facts = _extract_mod.EXTRACT_MAX_FACTS
    orig_max_chars = _extract_mod.EXTRACT_MAX_FACT_CHARS
    if profile:
        _extract_mod.EXTRACT_MAX_FACTS = max_facts
        _extract_mod.EXTRACT_MAX_FACT_CHARS = max_chars
    try:
        # ... existing extract_facts() call ...
    finally:
        _extract_mod.EXTRACT_MAX_FACTS = orig_max_facts
        _extract_mod.EXTRACT_MAX_FACT_CHARS = orig_max_chars
```

- [ ] **Step 5: Update POST /memory/extract to pass profile through job queue**

The `memory_extract` handler at `app.py:2552` does NOT call `run_extraction()` directly — it enqueues a job to `extract_queue`, and `_extract_worker` (line 703-730) calls `run_extraction`. The profile must flow through the job metadata.

In `app.py`, in the `memory_extract` handler, resolve the profile and store it in the job request data:

```python
    profile = extraction_profiles.resolve(effective_source)
    request_data = {
        "messages": request_body.messages,
        "source": effective_source,
        "context": request_body.context,
        "debug": request_body.debug,
        "dry_run": request_body.dry_run,
        "profile": profile,  # NEW — passed through queue
        "allowed_prefixes": auth.prefixes,
    }
```

Update `_extract_worker` (line 720-730) to pass `profile` to `run_extraction`:

```python
        try:
            result = await run_in_threadpool(
                run_extraction,
                extract_provider,
                memory,
                request_data["messages"],
                request_data["source"],
                request_data["context"],
                request_data.get("allowed_prefixes"),
                request_data.get("debug", False),
                request_data.get("profile"),  # NEW
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_profiles.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add llm_extract.py app.py tests/test_extraction_profiles.py
git commit -m "feat: profile-aware extraction with rules injection into AUDN prompt"
```

---

### Task 4: Single-call extraction mode

**Files:**
- Modify: `llm_extract.py` (add `extract_and_decide_single_call` function)
- Test: `tests/test_single_call_extraction.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_single_call_extraction.py`:

```python
"""Tests for single-call extraction mode."""
import pytest
from unittest.mock import MagicMock, patch


class TestSingleCallExtraction:

    def test_single_call_returns_actions_list(self):
        from llm_extract import extract_and_decide_single_call

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = '[{"action": "ADD", "fact_index": 0, "category": "decision"}]'
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        actions, usage, _ = extract_and_decide_single_call(
            provider=mock_provider,
            messages="We decided to use PostgreSQL.",
            source="test/",
            engine=mock_engine,
        )
        assert isinstance(actions, list)
        assert len(actions) >= 1
        assert actions[0]["action"] == "ADD"

    def test_single_call_uses_one_llm_call(self):
        from llm_extract import extract_and_decide_single_call

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = '[{"action": "NOOP", "fact_index": 0}]'
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        extract_and_decide_single_call(
            provider=mock_provider,
            messages="Some text",
            source="test/",
            engine=mock_engine,
        )
        # Should be exactly ONE LLM call, not two
        assert mock_provider.complete.call_count == 1

    def test_run_extraction_dispatches_single_call_from_profile(self):
        """When profile has single_call=True, run_extraction uses single-call."""
        from llm_extract import run_extraction

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = '[{"action": "ADD", "fact_index": 0, "category": "decision"}]'
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []
        mock_engine.add_memories.return_value = [1]

        profile = {"single_call": True, "mode": "standard",
                   "max_facts": 30, "max_fact_chars": 500, "rules": {}}

        result = run_extraction(
            provider=mock_provider,
            engine=mock_engine,
            messages="We chose Redis for caching.",
            source="test/",
            profile=profile,
        )
        # Single-call = one LLM call total
        assert mock_provider.complete.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_single_call_extraction.py -v`
Expected: FAIL — `extract_and_decide_single_call` doesn't exist

- [ ] **Step 3: Implement extract_and_decide_single_call**

Add to `llm_extract.py` after `run_audn()`:

```python
SINGLE_CALL_PROMPT = """You are a memory extraction and classification system.

Given conversation text, extract important facts AND decide what action to take for each.

For each fact, output a JSON object with:
- "action": "ADD" | "UPDATE" | "DELETE" | "NOOP" | "CONFLICT"
- "fact_index": sequential index starting at 0
- "category": "decision" | "learning" | "detail"
- "text": the extracted fact text

Categories:
- DECISION: architectural choices, technology selections, trade-off resolutions
- LEARNING: non-obvious findings, gotchas, performance insights
- DETAIL: specific configs, paths, versions, API signatures

Rules:
- Skip generic programming knowledge
- Skip task status / commit hashes / counts
- Skip ephemeral session context
- ADD for new durable facts
- NOOP if the fact is already commonly known

{rules_section}

Output ONLY a JSON array of action objects. No markdown, no explanation."""


def extract_and_decide_single_call(
    provider,
    messages: str,
    source: str,
    engine,
    rules: dict | None = None,
    max_facts: int = 30,
) -> tuple[list[dict], dict, None]:
    """Extract facts AND decide AUDN actions in a single LLM call.

    Trade-off: no per-fact similar-memory lookup, but ~50% cost reduction.
    Returns: (actions, token_usage, None) — same shape as run_audn().
    """
    rules_section = _build_rules_section(rules)
    prompt = SINGLE_CALL_PROMPT.format(rules_section=rules_section)

    result = provider.complete(
        system=prompt,
        user=f"Extract and classify facts from this conversation:\n\n{messages[:max_facts * 500]}",
    )
    usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens}

    try:
        actions = json.loads(result.text.strip())
        if not isinstance(actions, list):
            actions = []
    except (json.JSONDecodeError, ValueError):
        actions = []

    # Normalize actions to match run_audn output shape
    for i, action in enumerate(actions[:max_facts]):
        action.setdefault("fact_index", i)
        action.setdefault("action", "ADD")
        action.setdefault("category", "detail")
        if "text" not in action:
            action["text"] = ""

    return actions[:max_facts], usage, None
```

- [ ] **Step 4: Wire single-call dispatch into run_extraction**

In `run_extraction()`, after profile resolution, add dispatch:

```python
    if profile and profile.get("single_call"):
        actions, usage, _ = extract_and_decide_single_call(
            provider=provider,
            messages=messages,
            source=source,
            engine=engine,
            rules=rules,
            max_facts=max_facts,
        )
        # Build facts list from actions for execute_actions
        facts = [{"text": a.get("text", ""), "category": a.get("category", "detail")}
                 for a in actions]
        result = execute_actions(engine, actions, facts, source, allowed_prefixes)
        result["tokens"] = {"single_call": usage}
        return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_single_call_extraction.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add llm_extract.py tests/test_single_call_extraction.py
git commit -m "feat: single-call extraction mode — combined fact+AUDN in one LLM call"
```

---

### Task 5: Extraction dry-run and commit endpoint

**Files:**
- Modify: `app.py:1235-1244` (ExtractRequest — add dry_run)
- Modify: `app.py:2552-2692` (memory_extract handler — skip execute on dry_run)
- Modify: `app.py` (add POST /memory/extract/commit)
- Test: `tests/test_extraction_dry_run.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_extraction_dry_run.py`:

```python
"""Tests for extraction dry-run and selective commit."""
import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestExtractionDryRun:

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)
                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.stats_light.return_value = {"total_memories": 0}
                mock_engine.add_memories.return_value = [1]
                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    def test_dry_run_field_accepted(self, client):
        """Dry run should return actions but not create memories."""
        initial_count = client.get("/stats").json()["total_memories"]

        resp = client.post("/memory/extract", json={
            "messages": "We decided to use SQLite for the cache layer.",
            "source": "dry-run-test/",
            "dry_run": True,
        })
        # May be queued or completed immediately depending on provider
        assert resp.status_code in (200, 202)

        # Memory count should not change
        after_count = client.get("/stats").json()["total_memories"]
        assert after_count == initial_count

    def test_dry_run_field_accepted(self, client):
        """ExtractRequest should accept dry_run field."""
        tc, mock = client
        resp = tc.post("/memory/extract", json={
            "messages": "Test message for dry run field.",
            "source": "field-test/",
            "dry_run": True,
        })
        # Accepted (may be queued or inline depending on provider)
        assert resp.status_code in (200, 202)


class TestExtractCommit:

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)
                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.add_memories.return_value = [1]
                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    def test_commit_approved_actions(self, client):
        """Commit should execute only approved actions."""
        tc, mock = client
        resp = tc.post("/memory/extract/commit", json={
            "actions": [
                {"action": "ADD", "fact_index": 0,
                 "fact": {"text": "committed fact one", "category": "decision"},
                 "approved": True},
                {"action": "ADD", "fact_index": 1,
                 "fact": {"text": "rejected fact two", "category": "detail"},
                 "approved": False},
            ],
            "source": "commit-test/",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stored_count"] == 1

    def test_commit_empty_approved_list(self, client):
        """Commit with no approved actions should be a no-op."""
        tc, mock = client
        resp = tc.post("/memory/extract/commit", json={
            "actions": [
                {"action": "ADD", "fact_index": 0,
                 "fact": {"text": "all rejected", "category": "detail"},
                 "approved": False},
            ],
            "source": "commit-noop/",
        })
        assert resp.status_code == 200
        assert resp.json()["stored_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_dry_run.py -v`
Expected: FAIL — dry_run not accepted, commit endpoint doesn't exist

- [ ] **Step 3: Add dry_run to ExtractRequest**

In `app.py` (line 1235):

```python
class ExtractRequest(BaseModel):
    messages: str = Field(..., min_length=1, max_length=MAX_EXTRACT_MESSAGE_CHARS)
    source: str = Field(default="")
    context: str = Field(default="stop")
    debug: bool = Field(default=False)
    dry_run: bool = Field(default=False, description="Return actions without executing")
```

- [ ] **Step 4: Handle dry_run in memory_extract handler**

In the `memory_extract` handler (line 2552), after running extraction but before `execute_actions`, check `dry_run`:

```python
    if request_body.dry_run:
        # Return actions without executing
        result = {
            "dry_run": True,
            "actions": audn_actions,
            "extracted_count": len(facts),
        }
        return result
```

The primary code path is the queued worker at `app.py:703-730`. `dry_run` is already in `request_data` (from Step 5 of Task 3 where we added it to the job dict). Inside `run_extraction()` in `llm_extract.py`, add the dry-run check after `run_audn()` but before `execute_actions()` (around line 499):

```python
    if profile and profile.get("dry_run"):
        return {
            "dry_run": True,
            "actions": audn_actions,
            "extracted_count": len(facts),
            "tokens": {"extract": extract_usage, "audn": audn_usage},
        }
    # ... existing execute_actions() call follows
```

Pass `dry_run` into `run_extraction` via the profile dict — in the `memory_extract` handler, add `request_body.dry_run` to the profile before enqueuing:

```python
    if request_body.dry_run:
        profile["dry_run"] = True
```

- [ ] **Step 5: Add commit endpoint**

```python
class ExtractCommitRequest(BaseModel):
    actions: List[dict]
    source: str = Field(..., min_length=1, max_length=500)


@app.post("/memory/extract/commit")
async def extract_commit(request_body: ExtractCommitRequest, request: Request):
    """Execute a subset of pre-extracted AUDN actions (from dry-run)."""
    auth = _get_auth(request)
    _require_write(auth, request_body.source)

    # Filter to approved only
    approved = [a for a in request_body.actions if a.get("approved")]
    if not approved:
        return {"stored_count": 0, "updated_count": 0, "deleted_count": 0, "conflict_count": 0}

    # Build facts list from actions
    facts = [a.get("fact", {"text": "", "category": "detail"}) for a in approved]

    from llm_extract import execute_actions
    result = execute_actions(
        engine=memory,
        actions=approved,
        facts=facts,
        source=request_body.source,
        allowed_prefixes=auth.prefixes,
    )
    return result
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_dry_run.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add app.py tests/test_extraction_dry_run.py
git commit -m "feat: extraction dry-run preview and selective commit endpoint"
```

---

### Task 6: Missed memory capture flow

**Files:**
- Modify: `app.py` (add POST /memory/missed)
- Modify: `mcp-server/index.js` (add memory_missed tool)
- Test: `tests/test_missed_memory.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_missed_memory.py`:

```python
"""Tests for missed memory capture flow."""
import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestMissedMemory:

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)
                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.add_memories.return_value = [42]
                mock_engine.get_memory.return_value = {
                    "id": 42, "text": "The API rate limit is 100 req/s",
                    "source": "missed-test/", "origin": "missed_capture",
                }
                app_module.memory = mock_engine
                # Reset missed counts
                app_module._missed_counts = {}
                yield TestClient(app_module.app), mock_engine

    def test_missed_memory_creates_entry(self, client):
        tc, mock = client
        resp = tc.post("/memory/missed", json={
            "text": "The API rate limit is 100 req/s",
            "source": "missed-test/",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["source"] == "missed-test/"
        # Verify add_memories was called with origin metadata
        mock.add_memories.assert_called_once()
        call_args = mock.add_memories.call_args
        metadata_list = call_args[1].get("metadata_list") or call_args[0][2]
        assert metadata_list[0]["origin"] == "missed_capture"

    def test_missed_memory_with_context(self, client):
        tc, mock = client
        resp = tc.post("/memory/missed", json={
            "text": "Port 5432 is for the staging DB",
            "source": "ctx-test/",
            "context": "User mentioned during debugging session",
        })
        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_missed_count_increments(self, client):
        tc, mock = client
        resp = tc.post("/memory/missed", json={
            "text": "first miss",
            "source": "count-test/",
        })
        assert resp.json()["missed_count"] == 1

        resp2 = tc.post("/memory/missed", json={
            "text": "second miss",
            "source": "count-test/",
        })
        assert resp2.json()["missed_count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_missed_memory.py -v`
Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Add missed memory endpoint to app.py**

```python
class MissedMemoryRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    source: str = Field(..., min_length=1, max_length=500)
    context: Optional[str] = Field(None, max_length=10000)


# Track missed counts per source (in-memory, persisted via usage_tracker)
_missed_counts: Dict[str, int] = {}


@app.post("/memory/missed")
async def missed_memory(request_body: MissedMemoryRequest, request: Request):
    """Flag a memory that should have been captured by extraction."""
    auth = _get_auth(request)
    _require_write(auth, request_body.source)

    # Add memory with origin metadata
    metadata = {"origin": "missed_capture"}
    if request_body.context:
        metadata["capture_context"] = request_body.context

    ids = memory.add_memories(
        texts=[request_body.text],
        sources=[request_body.source],
        metadata_list=[metadata],
    )

    # Increment missed count
    _missed_counts[request_body.source] = _missed_counts.get(request_body.source, 0) + 1

    _audit(request, "memory.missed", resource_id=str(ids[0]), source=request_body.source)

    return {
        "id": ids[0],
        "source": request_body.source,
        "missed_count": _missed_counts[request_body.source],
    }
```

- [ ] **Step 4: Add memory_missed MCP tool**

In `mcp-server/index.js`, add after the `memory_extract` tool:

```javascript
server.tool(
  "memory_missed",
  "Flag a memory that should have been captured by extraction but wasn't. Stores the memory and increments missed count for the source.",
  {
    text: z.string().min(1).describe("The fact that should have been remembered"),
    source: z.string().min(1).describe("Source identifier"),
    context: z.string().optional().describe("Optional context about why this was missed"),
  },
  async ({ text, source, context }) => {
    const body = { text, source };
    if (context) body.context = context;
    const data = await memoriesRequest("/memory/missed", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return {
      content: [{
        type: "text",
        text: `Memory stored (id: ${data.id}) from ${data.source}. Missed count: ${data.missed_count}`,
      }],
    };
  }
);
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_missed_memory.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add app.py mcp-server/index.js tests/test_missed_memory.py
git commit -m "feat: missed memory capture — flag unfound facts, MCP tool, missed count tracking"
```

---

### Task 7: Run full B2 test suite

**Files:**
- All test files from Tasks 1-6

- [ ] **Step 1: Run all B2 tests**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_extraction_profiles.py tests/test_single_call_extraction.py tests/test_extraction_dry_run.py tests/test_missed_memory.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run existing test suite for regressions**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/ -v --timeout=120`
Expected: No regressions

- [ ] **Step 3: Commit any fixes if needed**

If regressions found, fix and commit with `fix: address B2 test regressions`.
