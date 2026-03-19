# Extraction Quality Overhaul — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Dramatically improve automated memory extraction quality by replacing noisy prompts with category-aware, source-aware extraction; fixing hook context; adding timestamps; building retrieval tracking, consolidation, and pruning.

**Architecture:** Two waves. Wave 1 changes the extraction pipeline (prompts, hooks, timestamps) in a single PR. Wave 2 adds maintenance infrastructure (retrieval tracking, consolidation, pruning) as a second PR. Both waves share the `feat/extraction-quality` branch.

**Tech Stack:** Python 3.11, FastAPI, Qdrant, SQLite (usage_tracker), bash hooks, pytest

---

## Wave 1: Extraction Quality

### Task 1: Add `created_at` + `updated_at` timestamps to memory engine

**Files:**
- Modify: `memory_engine.py:440-446` (add_memories meta dict)
- Modify: `memory_engine.py:637,661` (update_memory timestamp lines)
- Modify: `memory_engine.py:654` (reserved metadata keys set)
- Test: `tests/test_memory_engine.py`

**Step 1: Write failing tests**

Add to `tests/test_memory_engine.py` in `TestAddAndSearch` class:

```python
def test_add_sets_created_at_and_updated_at(self):
    ids = self.engine.add_memories(["timestamp test"], ["test/ts"])
    meta = self.engine.metadata[ids[0]]
    assert "created_at" in meta
    assert "updated_at" in meta
    assert meta["created_at"] == meta["updated_at"]
    # Backward compat alias
    assert "timestamp" in meta
    assert meta["timestamp"] == meta["created_at"]
```

Add to `TestFetchAndUpsert` class:

```python
def test_update_preserves_created_at(self):
    ids = self.engine.add_memories(["will be updated"], ["test/ts"])
    original_created = self.engine.metadata[ids[0]]["created_at"]
    import time; time.sleep(0.01)
    self.engine.update_memory(ids[0], text="updated text")
    meta = self.engine.metadata[ids[0]]
    assert meta["created_at"] == original_created
    assert meta["updated_at"] > original_created
    assert meta["timestamp"] == meta["created_at"]  # alias stays at creation
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_memory_engine.py::TestAddAndSearch::test_add_sets_created_at_and_updated_at tests/test_memory_engine.py::TestFetchAndUpsert::test_update_preserves_created_at -v`
Expected: FAIL — `created_at` key not in meta

**Step 3: Implement timestamps in `memory_engine.py`**

In `add_memories` (line 440-446), replace the meta dict:

```python
                    now = datetime.now(timezone.utc).isoformat()
                    meta = {
                        "id": mem_id,
                        "text": text,
                        "source": source,
                        "created_at": now,
                        "updated_at": now,
                        "timestamp": now,  # backward compat alias
                        **(metadata_list[i] if metadata_list and i < len(metadata_list) else {}),
                    }
```

In `update_memory` (line 637, source-only fast path), replace:

```python
                    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                    # Don't touch created_at or timestamp
```

In `update_memory` (line 661, general path), replace:

```python
                meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                # Don't touch created_at or timestamp
```

In reserved metadata keys (line 654), update:

```python
                    _reserved = {"id", "text", "source", "timestamp", "created_at", "updated_at", "entity_key"}
```

**Step 4: Handle migration for existing memories**

Add a method to `MemoryEngine` class (after `_point_payload`):

```python
def _migrate_timestamps(self):
    """Migrate existing memories from single `timestamp` to created_at/updated_at."""
    migrated = 0
    for meta in self.metadata:
        if not meta:
            continue
        if "created_at" not in meta:
            ts = meta.get("timestamp", datetime.now(timezone.utc).isoformat())
            meta["created_at"] = ts
            meta["updated_at"] = ts
            meta["timestamp"] = ts
            migrated += 1
    if migrated:
        logger.info("Migrated %d memories to created_at/updated_at timestamps", migrated)
        self.save()
```

Call `self._migrate_timestamps()` at the end of `load()` method (after loading metadata from disk).

**Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_memory_engine.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add memory_engine.py tests/test_memory_engine.py
git commit -m "feat: add created_at/updated_at timestamps with backward compat"
```

---

### Task 2: Redesign extraction prompts with categories and source-awareness

**Files:**
- Modify: `llm_extract.py:45-84` (all three prompt constants)
- Modify: `llm_extract.py:122-170` (extract_facts parsing)
- Test: `tests/test_llm_extract.py`

**Step 1: Write failing tests for category extraction**

Add to `tests/test_llm_extract.py` a new test class after `TestFactExtraction`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm_extract.py::TestCategoryExtraction -v`
Expected: FAIL — extract_facts doesn't accept `source` param, returns list[str] not list[dict]

**Step 3: Replace extraction prompts in `llm_extract.py`**

Replace `FACT_EXTRACTION_PROMPT` (lines 45-50):

```python
FACT_EXTRACTION_PROMPT = """Extract durable facts worth remembering from this conversation about the {project} project.

Categorize each fact:
- DECISION: Architectural choices, library selections, design patterns, preferences. WHY something was chosen matters more than WHAT.
- LEARNING: Bug root causes + fixes, gotchas discovered, workarounds, performance findings.
- DETAIL: File paths, API signatures, config values that are project-specific conventions.

Skip anything that fails this test: "Would this still be useful 30 days from now?"

DO NOT extract:
- Task completion status ("done", "all tests pass", "deployed successfully")
- Commit hashes, PR numbers, or branch names
- Counts or metrics ("44 tests", "5 files changed")
- Session-specific context ("currently working on...", "next step is...")
- Generic programming knowledge any developer would know

Output a JSON array of objects: [{{"category": "DECISION"|"LEARNING"|"DETAIL", "text": "..."}}]
Each fact must be self-contained and understandable without the conversation.
If nothing worth storing, output []."""
```

Replace `FACT_EXTRACTION_PROMPT_AGGRESSIVE` (lines 52-61):

```python
FACT_EXTRACTION_PROMPT_AGGRESSIVE = """Extract durable facts worth remembering from this conversation about the {project} project.
This context is about to be lost permanently. Be thorough but still apply the 30-day test.

Categorize each fact:
- DECISION: Architectural choices, library selections, design patterns, preferences. WHY > WHAT.
- LEARNING: Bug root causes + fixes, gotchas discovered, workarounds, performance findings.
- DETAIL: File paths, API signatures, config values, naming conventions — project-specific patterns.

Include DETAIL-category items you would normally skip — file paths, config patterns, naming conventions.

DO NOT extract:
- Task completion status ("done", "all tests pass", "deployed successfully")
- Commit hashes, PR numbers, or branch names
- Counts or metrics ("44 tests", "5 files changed")
- Session-specific context ("currently working on...", "next step is...")
- Generic programming knowledge any developer would know

Output a JSON array of objects: [{{"category": "DECISION"|"LEARNING"|"DETAIL", "text": "..."}}]
Each fact must be self-contained and understandable without the conversation.
If nothing worth storing, output []."""
```

**Step 4: Update `extract_facts` to accept `source` and return categorized dicts**

Modify `extract_facts` signature (line 122) to add `source`:

```python
def extract_facts(
    provider,
    messages: str,
    context: str = "stop",
    return_error: bool = False,
    source: str = "",
):
```

Extract project name from source and format prompt (after line 133):

```python
    project = source.rsplit("/", 1)[-1] if "/" in source else source or "this"
    system = system.format(project=project)
```

Update the parsing section (lines 143-165) to handle categorized objects:

```python
    try:
        result = provider.complete(system, messages)
        raw_facts = _parse_json_array(result.text)
        tokens = {"input": result.input_tokens, "output": result.output_tokens}

        facts = []
        for item in raw_facts:
            if isinstance(item, dict) and "text" in item:
                # New format: {"category": "...", "text": "..."}
                cat = item.get("category", "detail").lower()
                if cat not in ("decision", "learning", "detail"):
                    cat = "detail"
                text = _clip_text(str(item["text"]), EXTRACT_MAX_FACT_CHARS)
                if text:
                    facts.append({"category": cat, "text": text})
            elif isinstance(item, str) and item.strip():
                # Backward compat: plain string → detail
                text = _clip_text(item, EXTRACT_MAX_FACT_CHARS)
                if text:
                    facts.append({"category": "detail", "text": text})

        if len(facts) > EXTRACT_MAX_FACTS:
            logger.info(
                "Extracted %d facts; keeping first %d",
                len(facts), EXTRACT_MAX_FACTS,
            )
            facts = facts[:EXTRACT_MAX_FACTS]

        logger.info("Extracted %d facts (context=%s)", len(facts), context)
        if return_error:
            return facts, None, tokens
        return facts
```

**Step 5: Update `run_audn` to handle categorized facts**

In `run_audn` (line 204), update `facts_json` construction:

```python
    facts_json = json.dumps(
        [{"index": i, "text": _clip_text(f["text"], EXTRACT_MAX_FACT_CHARS), "category": f.get("category", "detail")} for i, f in enumerate(facts)],
        separators=(",", ":"),
    )
```

**Step 6: Update `execute_actions` to pass category into metadata**

In `execute_actions`, for the ADD path (lines 254-262):

```python
            if act == "ADD":
                fact_meta = {"category": fact.get("category", "detail")} if isinstance(fact, dict) else {}
                added_ids = engine.add_memories(
                    texts=[fact_text],
                    sources=[source],
                    metadata_list=[fact_meta],
                    deduplicate=True,
                )
```

Where `fact` is the full dict and `fact_text` is extracted. Update `fact_text` extraction at the top of the loop:

```python
        fi = action.get("fact_index", -1)
        fact = facts[fi] if 0 <= fi < len(facts) else {"text": "", "category": "detail"}
        fact_text = fact["text"] if isinstance(fact, dict) else str(fact)
```

Do the same for the UPDATE path — pass `{"category": fact.get("category", "detail"), "supersedes": old_id}` as metadata.

**Step 7: Update `run_extraction` to pass `source` to `extract_facts`**

In `run_extraction` (line 323), add `source`:

```python
    facts, extract_error, extract_tokens = extract_facts(
        provider,
        messages,
        context=context,
        return_error=True,
        source=source,
    )
```

**Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm_extract.py -v`
Expected: ALL PASS

**Step 9: Update existing tests for new return format**

The existing tests in `TestFactExtraction` return plain string arrays — these should still work via backward compat. But tests that check `facts[0]` as a string need updating to check `facts[0]["text"]`. Update each test:

- `test_extracts_facts_from_conversation`: `assert "Drizzle" in facts[0]["text"]`
- `test_returns_empty_when_nothing_worth_storing`: `assert facts == []` (unchanged — empty list)
- `test_handles_llm_returning_non_json`: `assert facts == []` (unchanged)
- `test_caps_fact_count_and_length`: `assert all(len(f["text"]) <= EXTRACT_MAX_FACT_CHARS for f in facts)`

Update `TestAUDNCycle` tests — `run_audn` now receives list[dict] not list[str]:
- Change `facts=["Uses Drizzle ORM"]` to `facts=[{"text": "Uses Drizzle ORM", "category": "decision"}]`

Update `TestFullPipeline::test_full_extraction_pipeline` — the `side_effect` first item should return categorized JSON.

**Step 10: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 11: Commit**

```bash
git add llm_extract.py tests/test_llm_extract.py
git commit -m "feat: category-aware extraction prompts with 30-day durability test"
```

---

### Task 3: Fix Stop hook to include user+assistant context

**Files:**
- Modify: `integrations/claude-code/hooks/memory-extract.sh`
- Modify: `integrations/codex/memory-codex-notify.sh` (minor — add context handling)

**Step 1: Rewrite `memory-extract.sh`**

Replace the entire file:

```bash
#!/bin/bash
# memory-extract.sh — Stop hook
# Extracts facts from the last user+assistant message pair.
# CC Stop hook provides: session_id, transcript_path, cwd, last_assistant_message

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')

# Expand tilde if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

MESSAGES=""

# Try to read last user+assistant pair from transcript for decision context
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  MESSAGES=$(tail -200 "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr '
    [
      .[]
      | select(.type == "user" or .type == "assistant")
      | {
          role: .type,
          text: (
            if .message.content | type == "string" then
              .message.content
            elif .message.content | type == "array" then
              [.message.content[] | select(.type == "text") | .text] | join(" ")
            else
              ""
            end
          )
        }
      | select(.text != "" and (.text | length) > 10)
    ]
    | .[-2:]
    | map(.role + ": " + (.text | .[0:2000]))
    | join("\n\n")
  ' 2>/dev/null) || true
fi

# Fallback to last_assistant_message if transcript read failed
if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  MESSAGES=$(echo "$INPUT" | jq -r '.last_assistant_message // empty')
fi

if [ -z "$MESSAGES" ]; then
  exit 0
fi

# Cap at 4000 chars (one pair is plenty for the Stop hook)
MESSAGES="${MESSAGES:0:4000}"

curl -sf -X POST "$MEMORIES_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"stop\"}" \
  > /dev/null 2>&1 || true
```

**Step 2: Verify Codex hook (no changes needed)**

Read `integrations/codex/memory-codex-notify.sh` — it already sends both user input-messages and last-assistant-message with context `"after_agent"`. No changes needed.

**Step 3: Update `llm_extract.py` to recognize `after_agent` context**

In `extract_facts` where the system prompt is selected (around line 130), ensure `after_agent` uses the standard prompt (same as `stop`):

```python
    if context == "pre_compact":
        system = FACT_EXTRACTION_PROMPT_AGGRESSIVE
    else:
        system = FACT_EXTRACTION_PROMPT
```

This already works — `after_agent` falls through to `else`. No change needed, just verify.

**Step 4: Commit**

```bash
git add integrations/claude-code/hooks/memory-extract.sh
git commit -m "fix: Stop hook reads user+assistant pair for decision context"
```

---

### Task 4: Update OpenClaw skill docs with new format

**Files:**
- Modify: `integrations/openclaw-skill.md`

**Step 1: Update extraction examples**

In the skill doc, find any extraction examples that show plain string arrays and update to show the new categorized format. Add a note that the extraction API now returns categories.

**Step 2: Commit**

```bash
git add integrations/openclaw-skill.md
git commit -m "docs: update OpenClaw skill with categorized extraction format"
```

---

### Task 5: Wave 1 integration test + deploy

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL 211+ tests pass

**Step 2: Build and deploy**

```bash
docker compose build memories && docker compose up -d memories
```

**Step 3: Smoke test extraction**

```bash
API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2)
curl -s -X POST 'http://localhost:8900/memory/extract' \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"messages": "User: should we use Redis or Memcached for caching?\nAssistant: Let'\''s go with Redis - it supports data structures beyond simple key-value, and we need sorted sets for the leaderboard feature.", "source": "claude-code/test-project", "context": "stop"}'
```

Verify the stored memory has `category` and `created_at` fields.

**Step 4: Commit and push**

```bash
git push origin feat/extraction-quality
```

---

## Wave 2: Maintenance System

### Task 6: Add retrieval tracking to usage_tracker

**Files:**
- Modify: `usage_tracker.py`
- Test: `tests/test_usage_tracker.py` (create new)

**Step 1: Write failing tests**

Create `tests/test_usage_tracker.py`:

```python
"""Tests for usage_tracker module."""
import os
import tempfile
import pytest
from usage_tracker import UsageTracker, NullTracker


class TestRetrievalTracking:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "usage.db")
        self.tracker = UsageTracker(self.db_path)

    def test_log_retrieval_stores_record(self):
        self.tracker.log_retrieval(memory_id=42, query="test query", source="test")
        stats = self.tracker.get_retrieval_stats(memory_ids=[42])
        assert stats[42]["count"] == 1

    def test_log_retrieval_multiple_increments(self):
        self.tracker.log_retrieval(memory_id=10, query="q1")
        self.tracker.log_retrieval(memory_id=10, query="q2")
        stats = self.tracker.get_retrieval_stats(memory_ids=[10])
        assert stats[10]["count"] == 2

    def test_get_unretrieved_memory_ids(self):
        self.tracker.log_retrieval(memory_id=1, query="q1")
        unretrieved = self.tracker.get_unretrieved_memory_ids(
            all_memory_ids=[1, 2, 3]
        )
        assert set(unretrieved) == {2, 3}

    def test_null_tracker_noop(self):
        tracker = NullTracker()
        tracker.log_retrieval(memory_id=1, query="q")
        # Should not raise
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_usage_tracker.py -v`
Expected: FAIL — `log_retrieval` method doesn't exist

**Step 3: Implement retrieval tracking in `usage_tracker.py`**

Add to `UsageTracker.__init__` SQL schema (after extraction_tokens table):

```python
            CREATE TABLE IF NOT EXISTS retrieval_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                memory_id INTEGER NOT NULL,
                query TEXT DEFAULT '',
                source TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_retrieval_memory ON retrieval_log(memory_id);
            CREATE INDEX IF NOT EXISTS idx_retrieval_ts ON retrieval_log(ts);
```

Add methods to `UsageTracker`:

```python
    def log_retrieval(self, memory_id: int, query: str = "", source: str = "") -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO retrieval_log (memory_id, query, source) VALUES (?, ?, ?)",
                (memory_id, query[:500], source),
            )
            conn.commit()
        except Exception:
            logger.debug("Failed to log retrieval", exc_info=True)

    def get_retrieval_stats(self, memory_ids: list[int]) -> dict[int, dict]:
        conn = self._connect()
        try:
            placeholders = ",".join("?" * len(memory_ids))
            rows = conn.execute(
                f"SELECT memory_id, COUNT(*) as cnt, MAX(ts) as last_ts "
                f"FROM retrieval_log WHERE memory_id IN ({placeholders}) "
                f"GROUP BY memory_id",
                memory_ids,
            ).fetchall()
            stats = {mid: {"count": 0, "last_retrieved_at": None} for mid in memory_ids}
            for row in rows:
                stats[row[0]] = {"count": row[1], "last_retrieved_at": row[2]}
            return stats
        finally:
            conn.close()

    def get_unretrieved_memory_ids(self, all_memory_ids: list[int]) -> list[int]:
        conn = self._connect()
        try:
            retrieved = set(
                row[0] for row in
                conn.execute("SELECT DISTINCT memory_id FROM retrieval_log").fetchall()
            )
            return [mid for mid in all_memory_ids if mid not in retrieved]
        finally:
            conn.close()
```

Add to `NullTracker`:

```python
    def log_retrieval(self, memory_id: int, query: str = "", source: str = "") -> None:
        pass

    def get_retrieval_stats(self, memory_ids: list[int]) -> dict:
        return {}

    def get_unretrieved_memory_ids(self, all_memory_ids: list[int]) -> list[int]:
        return []
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_usage_tracker.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add usage_tracker.py tests/test_usage_tracker.py
git commit -m "feat: add retrieval tracking to usage_tracker"
```

---

### Task 7: Instrument search endpoints with retrieval logging

**Files:**
- Modify: `app.py:1048-1072` (/search endpoint)
- Modify: `app.py:1075-1101` (/search/batch endpoint)

**Step 1: Add retrieval logging after search results**

In the `/search` endpoint (after line 1068), add:

```python
        for r in results:
            if "id" in r:
                usage_tracker.log_retrieval(
                    memory_id=r["id"],
                    query=request.query[:200],
                    source=request.source or "",
                )
```

In the `/search/batch` endpoint, add same logging inside the results loop.

**Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat: instrument search endpoints with retrieval logging"
```

---

### Task 8: Build consolidation module

**Files:**
- Create: `consolidator.py`
- Test: `tests/test_consolidator.py`

**Step 1: Write failing tests**

Create `tests/test_consolidator.py`:

```python
"""Tests for consolidator module."""
import json
from unittest.mock import MagicMock, patch
from llm_provider import CompletionResult


def _cr(text, input_tokens=10, output_tokens=5):
    return CompletionResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


class TestClusterDetection:
    def test_finds_clusters_by_similarity(self):
        from consolidator import find_clusters

        mock_engine = MagicMock()
        # 5 memories about the same topic
        mock_engine.metadata = [
            {"id": 0, "text": "Linear source uses Bearer auth", "source": "claude-code/indexer", "created_at": "2026-01-01T00:00:00"},
            {"id": 1, "text": "Linear source auth via Bearer token", "source": "claude-code/indexer", "created_at": "2026-01-01T00:00:00"},
            {"id": 2, "text": "Linear source needs team_key setting", "source": "claude-code/indexer", "created_at": "2026-01-01T00:00:00"},
            {"id": 3, "text": "Unrelated memory about React", "source": "claude-code/other", "created_at": "2026-01-01T00:00:00"},
        ]
        mock_engine.hybrid_search.side_effect = lambda query, k=5, **kw: [
            {"id": 1, "text": "Linear source auth via Bearer token", "rrf_score": 0.85},
            {"id": 2, "text": "Linear source needs team_key setting", "rrf_score": 0.80},
        ] if "Bearer" in query else [
            {"id": 0, "text": "Linear source uses Bearer auth", "rrf_score": 0.85},
            {"id": 2, "text": "Linear source needs team_key setting", "rrf_score": 0.78},
        ] if "Bearer token" in query else []

        clusters = find_clusters(mock_engine, source_prefix="claude-code/indexer", similarity_threshold=0.75, min_cluster_size=2)
        assert len(clusters) >= 1
        # Cluster should contain related memories
        assert any(len(c) >= 2 for c in clusters)

    def test_no_clusters_when_all_unique(self):
        from consolidator import find_clusters

        mock_engine = MagicMock()
        mock_engine.metadata = [
            {"id": 0, "text": "Topic A", "source": "test/src", "created_at": "2026-01-01T00:00:00"},
            {"id": 1, "text": "Topic B completely different", "source": "test/src", "created_at": "2026-01-01T00:00:00"},
        ]
        mock_engine.hybrid_search.return_value = []

        clusters = find_clusters(mock_engine, source_prefix="test/src")
        assert len(clusters) == 0


class TestConsolidation:
    def test_consolidate_cluster_merges_memories(self):
        from consolidator import consolidate_cluster

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps([
            "Linear source uses Bearer token auth with team_key setting, fetches issues via GraphQL API"
        ]))

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        cluster = [
            {"id": 10, "text": "Linear source uses Bearer auth", "source": "claude-code/indexer", "category": "detail"},
            {"id": 11, "text": "Linear auth via Bearer token header", "source": "claude-code/indexer", "category": "detail"},
            {"id": 12, "text": "Linear needs team_key setting", "source": "claude-code/indexer", "category": "detail"},
        ]

        result = consolidate_cluster(mock_provider, mock_engine, cluster, dry_run=False)
        assert result["merged_count"] == 3
        assert result["new_count"] == 1
        assert not result["dry_run"]
        mock_engine.delete_memory.assert_called()
        mock_engine.add_memories.assert_called_once()

    def test_dry_run_does_not_mutate(self):
        from consolidator import consolidate_cluster

        mock_provider = MagicMock()
        mock_provider.complete.return_value = _cr(json.dumps(["Consolidated text"]))

        mock_engine = MagicMock()

        cluster = [
            {"id": 10, "text": "A", "source": "s", "category": "detail"},
            {"id": 11, "text": "B", "source": "s", "category": "detail"},
            {"id": 12, "text": "C", "source": "s", "category": "detail"},
        ]

        result = consolidate_cluster(mock_provider, mock_engine, cluster, dry_run=True)
        assert result["dry_run"] is True
        mock_engine.delete_memory.assert_not_called()
        mock_engine.add_memories.assert_not_called()


class TestPruning:
    def test_identifies_prune_candidates(self):
        from consolidator import find_prune_candidates

        all_memories = [
            {"id": 0, "text": "Old detail", "source": "s", "category": "detail", "created_at": "2025-11-01T00:00:00"},
            {"id": 1, "text": "Old decision", "source": "s", "category": "decision", "created_at": "2025-11-01T00:00:00"},
            {"id": 2, "text": "Recent detail", "source": "s", "category": "detail", "created_at": "2026-02-15T00:00:00"},
        ]
        unretrieved_ids = [0, 1, 2]

        candidates = find_prune_candidates(
            all_memories, unretrieved_ids,
            detail_days=60, decision_days=120,
        )
        # id 0: detail, 112 days old, unretrieved → prune
        # id 1: decision, 112 days old, unretrieved → within 120 days → keep
        # id 2: recent → keep
        assert 0 in [c["id"] for c in candidates]
        assert 2 not in [c["id"] for c in candidates]
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_consolidator.py -v`
Expected: FAIL — `consolidator` module doesn't exist

**Step 3: Implement `consolidator.py`**

```python
"""Memory consolidation and pruning.

Groups related memories by semantic similarity, merges redundant clusters
via LLM, and prunes stale unretrieved memories.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import json

logger = logging.getLogger(__name__)

CONSOLIDATION_PROMPT = """These {n} memories are about the same topic in the {project} project.
Consolidate them into 1-2 concise memories that capture ALL unique information.
Drop redundant or overlapping details. Preserve: decisions and reasoning, bug fixes, conventions.

Memories to consolidate:
{memories_json}

Output a JSON array of consolidated text strings. Each must be self-contained."""


def find_clusters(
    engine,
    source_prefix: str = "",
    similarity_threshold: float = 0.75,
    min_cluster_size: int = 3,
) -> List[List[Dict]]:
    """Find clusters of semantically similar memories."""
    candidates = [
        m for m in engine.metadata
        if m and (not source_prefix or m.get("source", "").startswith(source_prefix))
    ]

    clustered_ids = set()
    clusters = []

    for mem in candidates:
        if mem["id"] in clustered_ids:
            continue
        similar = engine.hybrid_search(
            mem["text"], k=10,
            source_prefix=source_prefix or None,
        )
        cluster_members = [mem]
        for s in similar:
            sid = s.get("id")
            score = s.get("rrf_score", s.get("similarity", 0))
            if sid != mem["id"] and sid not in clustered_ids and score >= similarity_threshold:
                match = next((m for m in candidates if m["id"] == sid), None)
                if match:
                    cluster_members.append(match)

        if len(cluster_members) >= min_cluster_size:
            clusters.append(cluster_members)
            for m in cluster_members:
                clustered_ids.add(m["id"])

    logger.info("Found %d clusters (prefix=%r)", len(clusters), source_prefix)
    return clusters


def consolidate_cluster(
    provider,
    engine,
    cluster: List[Dict],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Merge a cluster of related memories into 1-2 consolidated facts."""
    source = cluster[0].get("source", "")
    project = source.rsplit("/", 1)[-1] if "/" in source else source or "unknown"

    memories_json = json.dumps(
        [{"id": m["id"], "text": m["text"], "category": m.get("category", "detail")} for m in cluster],
        indent=2,
    )
    prompt = CONSOLIDATION_PROMPT.format(n=len(cluster), project=project, memories_json=memories_json)

    result = provider.complete("You are a memory consolidator. Output only valid JSON.", prompt)
    try:
        consolidated = json.loads(result.text)
        if not isinstance(consolidated, list):
            consolidated = [str(consolidated)]
    except (json.JSONDecodeError, TypeError):
        consolidated = [result.text.strip()]

    old_ids = [m["id"] for m in cluster]
    # Pick the most common category
    categories = [m.get("category", "detail") for m in cluster]
    dominant_cat = max(set(categories), key=categories.count)

    if not dry_run:
        for old_id in old_ids:
            try:
                engine.delete_memory(old_id)
            except Exception:
                logger.warning("Failed to delete memory %d during consolidation", old_id)
        engine.add_memories(
            texts=consolidated,
            sources=[source] * len(consolidated),
            metadata_list=[{"category": dominant_cat, "consolidated_from": old_ids}] * len(consolidated),
            deduplicate=False,
        )

    return {
        "merged_count": len(old_ids),
        "new_count": len(consolidated),
        "old_ids": old_ids,
        "new_texts": consolidated,
        "dry_run": dry_run,
    }


def find_prune_candidates(
    all_memories: List[Dict],
    unretrieved_ids: List[int],
    detail_days: int = 60,
    decision_days: int = 120,
) -> List[Dict]:
    """Find memories eligible for pruning based on age and retrieval."""
    unretrieved_set = set(unretrieved_ids)
    now = datetime.now(timezone.utc)
    candidates = []

    for mem in all_memories:
        if not mem or mem.get("id") not in unretrieved_set:
            continue
        created = mem.get("created_at", mem.get("timestamp", ""))
        if not created:
            continue
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        cat = mem.get("category", "detail")
        max_days = detail_days if cat == "detail" else decision_days
        age_days = (now - created_dt).days

        if age_days > max_days:
            candidates.append({**mem, "_age_days": age_days})

    return candidates
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_consolidator.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add consolidator.py tests/test_consolidator.py
git commit -m "feat: add consolidation and pruning module"
```

---

### Task 9: Add maintenance endpoints and scheduler to app.py

**Files:**
- Modify: `app.py`
- Modify: `Dockerfile` (add consolidator.py to COPY)
- Modify: `docker-compose.yml` (add MAINTENANCE_ENABLED env var)

**Step 1: Add maintenance endpoints**

In `app.py`, after the usage endpoint, add:

```python
@app.post("/maintenance/consolidate")
async def consolidate(
    dry_run: bool = Query(True),
    source_prefix: str = Query(""),
):
    """Run memory consolidation. Merges redundant memory clusters."""
    if not extract_provider:
        raise HTTPException(503, "No LLM provider configured for consolidation")
    from consolidator import find_clusters, consolidate_cluster
    clusters = find_clusters(memory, source_prefix=source_prefix)
    results = []
    for cluster in clusters:
        r = await run_in_threadpool(
            consolidate_cluster, extract_provider, memory, cluster, dry_run=dry_run,
        )
        results.append(r)
    return {"clusters_found": len(clusters), "results": results, "dry_run": dry_run}


@app.post("/maintenance/prune")
async def prune(dry_run: bool = Query(True)):
    """Prune stale unretrieved memories."""
    from consolidator import find_prune_candidates
    all_mems = [m for m in memory.metadata if m]
    all_ids = [m["id"] for m in all_mems]
    unretrieved = usage_tracker.get_unretrieved_memory_ids(all_ids)
    candidates = find_prune_candidates(all_mems, unretrieved)
    if not dry_run:
        for c in candidates:
            memory.delete_memory(c["id"])
    return {
        "candidates": len(candidates),
        "pruned": 0 if dry_run else len(candidates),
        "dry_run": dry_run,
    }
```

**Step 2: Add background scheduler**

In `app.py` startup event, if `MAINTENANCE_ENABLED=true`:

```python
import asyncio

MAINTENANCE_ENABLED = _env_bool("MAINTENANCE_ENABLED", False)

async def _maintenance_scheduler():
    """Run consolidation daily and pruning weekly."""
    while True:
        now = datetime.now(timezone.utc)
        # Consolidation: daily at 3 AM UTC
        if now.hour == 3 and now.minute < 5:
            try:
                logger.info("Running scheduled consolidation")
                from consolidator import find_clusters, consolidate_cluster
                clusters = find_clusters(memory)
                for cluster in clusters:
                    consolidate_cluster(extract_provider, memory, cluster, dry_run=False)
            except Exception:
                logger.exception("Scheduled consolidation failed")
        # Pruning: Sunday at 4 AM UTC
        if now.weekday() == 6 and now.hour == 4 and now.minute < 5:
            try:
                logger.info("Running scheduled pruning")
                from consolidator import find_prune_candidates
                all_mems = [m for m in memory.metadata if m]
                all_ids = [m["id"] for m in all_mems]
                unretrieved = usage_tracker.get_unretrieved_memory_ids(all_ids)
                candidates = find_prune_candidates(all_mems, unretrieved)
                for c in candidates:
                    memory.delete_memory(c["id"])
                logger.info("Pruned %d stale memories", len(candidates))
            except Exception:
                logger.exception("Scheduled pruning failed")
        await asyncio.sleep(300)  # Check every 5 minutes
```

Register in startup if enabled:

```python
if MAINTENANCE_ENABLED and extract_provider:
    asyncio.create_task(_maintenance_scheduler())
```

**Step 3: Update Dockerfile and docker-compose.yml**

Dockerfile — add `COPY consolidator.py .` after the other COPY lines.

docker-compose.yml — add:
```yaml
      - MAINTENANCE_ENABLED=${MAINTENANCE_ENABLED:-false}
```

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Build, deploy, smoke test**

```bash
docker compose build memories && docker compose up -d memories
```

Test consolidation dry run:
```bash
curl -s -X POST 'http://localhost:8900/maintenance/consolidate?dry_run=true' \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

**Step 6: Commit**

```bash
git add app.py consolidator.py Dockerfile docker-compose.yml
git commit -m "feat: add maintenance endpoints and scheduler for consolidation/pruning"
```

---

### Task 10: Final integration, push, and PR

**Step 1: Run full test suite one final time**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 2: Push and create PR**

```bash
git push origin feat/extraction-quality
gh pr create --title "feat: extraction quality overhaul" --body "..."
```

**Step 3: Deploy**

```bash
docker compose build memories && docker compose up -d memories
```

---

Plan complete and saved to `docs/plans/2026-02-21-extraction-quality-plan.md`.

**Two execution options:**

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?