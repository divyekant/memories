# Temporal Reasoning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add temporal metadata, version preservation, and date-range search filters so the engine can support temporal reasoning queries.

**Architecture:** Four layers: (1) `reinforce()` writes `last_reinforced_at` instead of `updated_at`, confidence reads from it; (2) UPDATE archives old memory + creates supersedes link instead of deleting; (3) `since`/`until` filters added to all search methods + API; (4) Qdrant payload indexes on `document_at` and `is_latest`.

**Tech Stack:** Python 3.11, pytest, Node.js (MCP server), FastAPI, Qdrant

**Spec:** `docs/superpowers/specs/2026-03-26-temporal-reasoning-design.md`

---

### Task 1: `reinforce()` writes `last_reinforced_at` — tests + implementation

**Files:**
- Modify: `memory_engine.py:945-950`
- Modify: `memory_engine.py:952-954` (confidence anchor)
- Create: `tests/test_temporal.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for temporal reasoning features."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    with patch("memory_engine.QdrantStore") as MockStore, \
         patch("memory_engine.QdrantSettings") as MockSettings:
        mock_store = MagicMock()
        mock_store.ensure_collection.return_value = None
        mock_store.ensure_payload_indexes.return_value = None
        mock_store.count.return_value = 0
        mock_store.search.return_value = []
        MockStore.return_value = mock_store
        mock_settings = MagicMock()
        mock_settings.read_consistency = "majority"
        MockSettings.from_env.return_value = mock_settings
        eng = MemoryEngine(data_dir=str(tmp_path / "data"))
        return eng


class TestReinforce:
    def test_reinforce_sets_last_reinforced_at(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "test", "source": "t", "created_at": now, "updated_at": now}]
        engine._rebuild_id_map()
        engine.reinforce(1)
        assert "last_reinforced_at" in engine.metadata[0]
        assert engine.metadata[0]["last_reinforced_at"] != now  # should be newer

    def test_reinforce_does_not_change_updated_at(self, engine):
        old_time = "2025-01-01T00:00:00+00:00"
        engine.metadata = [{"id": 1, "text": "test", "source": "t", "created_at": old_time, "updated_at": old_time}]
        engine._rebuild_id_map()
        engine.reinforce(1)
        assert engine.metadata[0]["updated_at"] == old_time

    def test_confidence_uses_last_reinforced_at(self, engine):
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        engine.metadata = [{"id": 1, "text": "t", "source": "t", "created_at": old, "updated_at": old, "last_reinforced_at": recent}]
        engine._rebuild_id_map()
        result = engine._enrich_with_confidence(engine.metadata[0].copy())
        # Confidence should be high because last_reinforced_at is recent
        assert result.get("confidence", 0) > 0.5
```

- [ ] **Step 2: Implement reinforce() change**

In `memory_engine.py`, change `reinforce()` (line 945-950):
```python
def reinforce(self, memory_id: int) -> None:
    """Reinforce a memory by updating its reinforcement timestamp."""
    if not self._id_exists(memory_id):
        return
    meta = self._get_meta_by_id(memory_id)
    meta["last_reinforced_at"] = datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 3: Update confidence anchor in `_enrich_with_confidence()`**

Change line 954 from:
```python
anchor = mem.get("updated_at") or mem.get("created_at") or mem.get("timestamp")
```
To:
```python
anchor = mem.get("last_reinforced_at") or mem.get("updated_at") or mem.get("created_at") or mem.get("timestamp")
```

- [ ] **Step 4: Update confidence anchor in `hybrid_search()` confidence block**

Change line 1813 from:
```python
anchor = meta.get("updated_at") or meta.get("created_at") or meta.get("timestamp")
```
To:
```python
anchor = meta.get("last_reinforced_at") or meta.get("updated_at") or meta.get("created_at") or meta.get("timestamp")
```

Do the same in `hybrid_search_explain()` (find the equivalent confidence block).

- [ ] **Step 5: Update recency scoring to prefer document_at**

Change line 1791 from:
```python
created_at = meta.get("created_at") or meta.get("timestamp")
```
To:
```python
created_at = meta.get("document_at") or meta.get("created_at") or meta.get("timestamp")
```

Do the same in `hybrid_search_explain()`.

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/test_temporal.py -v --tb=short
```

- [ ] **Step 7: Run regression tests**

```bash
.venv/bin/pytest tests/test_recency_boost.py tests/test_search_feedback.py -v --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add memory_engine.py tests/test_temporal.py
git commit -m "feat: reinforce() writes last_reinforced_at, recency reads document_at"
```

---

### Task 2: Version preservation on UPDATE — tests + implementation

**Files:**
- Modify: `llm_extract.py:481-510`
- Modify: `tests/test_temporal.py`

- [ ] **Step 1: Write tests**

Add to `tests/test_temporal.py`:

```python
class TestVersionPreservation:
    def test_update_archives_old_memory(self):
        from llm_extract import execute_actions
        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        mock_engine.add_memories.return_value = [101]
        mock_engine.add_link.return_value = {}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original", "category": "decision"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        # Old memory should be archived, not deleted
        mock_engine.delete_memory.assert_not_called()
        mock_engine.update_memory.assert_called_once()
        call_kwargs = mock_engine.update_memory.call_args
        assert call_kwargs.kwargs.get("archived") is True or call_kwargs[1].get("archived") is True

    def test_update_creates_supersedes_link(self):
        from llm_extract import execute_actions
        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        mock_engine.add_memories.return_value = [101]
        mock_engine.add_link.return_value = {}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original", "category": "decision"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        mock_engine.add_link.assert_called_once_with(101, 42, "supersedes")

    def test_update_sets_is_latest(self):
        from llm_extract import execute_actions
        mock_engine = MagicMock()
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}
        mock_engine.add_memories.return_value = [101]
        mock_engine.add_link.return_value = {}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated text"}]
        facts = [{"text": "original", "category": "decision"}]

        result = execute_actions(mock_engine, actions, facts, source="test/proj")
        # New memory should have is_latest=True in metadata
        add_call = mock_engine.add_memories.call_args
        metadata = add_call.kwargs.get("metadata_list", [{}])[0]
        assert metadata.get("is_latest") is True
```

- [ ] **Step 2: Implement UPDATE version preservation**

In `llm_extract.py`, replace lines 496-507 (the UPDATE execution block):

```python
                if old_id is not None:
                    # Archive old memory instead of deleting (version preservation)
                    engine.update_memory(old_id, archived=True, metadata_patch={"is_latest": False})
                fact_meta = {"category": fact.get("category", "detail"), "supersedes": old_id, "is_latest": True} if isinstance(fact, dict) else {"supersedes": old_id, "is_latest": True}
                if job_id:
                    fact_meta["extraction_job_id"] = job_id
                    fact_meta["extract_source"] = source
                added_ids = engine.add_memories(
                    texts=[new_text],
                    sources=[source],
                    metadata_list=[fact_meta],
                    deduplicate=False,
                )
                new_id = added_ids[0] if added_ids else None
                # Create supersedes link from new → old
                if new_id and old_id is not None:
                    try:
                        engine.add_link(new_id, old_id, "supersedes")
                    except (ValueError, Exception):
                        pass  # Link creation is non-fatal
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest tests/test_temporal.py tests/test_llm_extract.py -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add llm_extract.py tests/test_temporal.py
git commit -m "feat: UPDATE archives old memory + creates supersedes link"
```

---

### Task 3: Temporal search filters (`since`/`until`) — tests + implementation

**Files:**
- Modify: `memory_engine.py` (hybrid_search, search, _search_no_reinforce, hybrid_search_explain)
- Modify: `tests/test_temporal.py`

- [ ] **Step 1: Write tests**

Add to `tests/test_temporal.py`:

```python
class TestTemporalFilters:
    def test_since_filter_excludes_old_memories(self, engine):
        engine.metadata = [
            {"id": 1, "text": "old fact", "source": "t", "created_at": "2023-01-01T00:00:00+00:00", "document_at": "2023-01-01T00:00:00+00:00"},
            {"id": 2, "text": "new fact", "source": "t", "created_at": "2023-06-01T00:00:00+00:00", "document_at": "2023-06-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact", since="2023-03-01T00:00:00+00:00")
        result_ids = [r["id"] for r in results]
        assert 1 not in result_ids
        assert 2 in result_ids

    def test_until_filter_excludes_future_memories(self, engine):
        engine.metadata = [
            {"id": 1, "text": "old fact", "source": "t", "created_at": "2023-01-01T00:00:00+00:00", "document_at": "2023-01-01T00:00:00+00:00"},
            {"id": 2, "text": "new fact", "source": "t", "created_at": "2023-06-01T00:00:00+00:00", "document_at": "2023-06-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact", until="2023-03-01T00:00:00+00:00")
        result_ids = [r["id"] for r in results]
        assert 1 in result_ids
        assert 2 not in result_ids

    def test_since_until_combined_range(self, engine):
        engine.metadata = [
            {"id": 1, "text": "jan fact", "source": "t", "created_at": "2023-01-15T00:00:00+00:00", "document_at": "2023-01-15T00:00:00+00:00"},
            {"id": 2, "text": "mar fact", "source": "t", "created_at": "2023-03-15T00:00:00+00:00", "document_at": "2023-03-15T00:00:00+00:00"},
            {"id": 3, "text": "jun fact", "source": "t", "created_at": "2023-06-15T00:00:00+00:00", "document_at": "2023-06-15T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact", since="2023-02-01T00:00:00+00:00", until="2023-05-01T00:00:00+00:00")
        result_ids = [r["id"] for r in results]
        assert 1 not in result_ids
        assert 2 in result_ids
        assert 3 not in result_ids

    def test_missing_document_at_passes_through(self, engine):
        engine.metadata = [
            {"id": 1, "text": "no date fact", "source": "t", "created_at": "2023-03-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact", since="2023-01-01T00:00:00+00:00")
        # Falls back to created_at — 2023-03-01 is after since
        assert len(results) >= 1

    def test_no_filters_returns_all(self, engine):
        engine.metadata = [
            {"id": 1, "text": "fact one", "source": "t", "created_at": "2023-01-01T00:00:00+00:00"},
            {"id": 2, "text": "fact two", "source": "t", "created_at": "2023-06-01T00:00:00+00:00"},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()
        results = engine.hybrid_search(query="fact")
        assert len(results) == 2
```

- [ ] **Step 2: Add `since`/`until` params + helper**

Add a helper function near the top of `MemoryEngine` class:

```python
@staticmethod
def _passes_temporal_filter(meta: dict, since: Optional[str], until: Optional[str]) -> bool:
    """Check if memory passes temporal since/until filter."""
    if not since and not until:
        return True
    doc_date = meta.get("document_at") or meta.get("created_at") or meta.get("timestamp")
    if not doc_date:
        return True  # No date → pass through
    try:
        ts = datetime.fromisoformat(doc_date)
        if since:
            since_ts = datetime.fromisoformat(since)
            if ts < since_ts:
                return False
        if until:
            until_ts = datetime.fromisoformat(until)
            if ts > until_ts:
                return False
        return True
    except (ValueError, TypeError):
        return True  # Unparseable → pass through
```

- [ ] **Step 3: Add `since`/`until` to `hybrid_search()` signature and filtering**

Add params after `graph_weight`:
```python
since: Optional[str] = None,
until: Optional[str] = None,
```

Add temporal filtering in the BM25 candidate loop and vector results filtering. The easiest approach: filter `rrf_scores` after all signals are computed but before graph expansion:

```python
# Temporal filtering
if since or until:
    rrf_scores = {
        doc_id: score for doc_id, score in rrf_scores.items()
        if self._passes_temporal_filter(self._get_meta_by_id(doc_id), since, until)
    }
```

Add this just before the `# --- Zero-overhead fast path when graph is disabled ---` line.

- [ ] **Step 4: Add `since`/`until` to `search()`, `_search_no_reinforce()`, `hybrid_search_explain()`**

Same params added to each signature. Same filtering pattern applied to results before returning.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_temporal.py -v --tb=short
```

- [ ] **Step 6: Run regression tests**

```bash
.venv/bin/pytest tests/test_recency_boost.py tests/test_graph_search.py tests/test_search_explain.py -v --tb=short
```

- [ ] **Step 7: Commit**

```bash
git add memory_engine.py tests/test_temporal.py
git commit -m "feat: add since/until temporal filters to all search methods"
```

---

### Task 4: HTTP + MCP pass-through for temporal filters

**Files:**
- Modify: `app.py` (SearchRequest)
- Modify: `mcp-server/index.js`

- [ ] **Step 1: Add `since`/`until` to SearchRequest**

After `graph_weight` field:
```python
    since: Optional[str] = Field(
        None,
        max_length=50,
        description="Filter memories created/documented at or after this ISO 8601 date",
    )
    until: Optional[str] = Field(
        None,
        max_length=50,
        description="Filter memories created/documented at or before this ISO 8601 date",
    )
```

- [ ] **Step 2: Pass through in search endpoint**

Add to hybrid_search call:
```python
since=request_body.since,
until=request_body.until,
```

Add to search/explain and search/batch calls too.

- [ ] **Step 3: Add to MCP memory_search**

In `mcp-server/index.js`, add to schema:
```javascript
since: z.string().optional().describe("Filter memories at or after this ISO date"),
until: z.string().optional().describe("Filter memories at or before this ISO date"),
```

Update destructuring and body building.

- [ ] **Step 4: Run API tests**

```bash
.venv/bin/pytest tests/test_memory_api.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add app.py mcp-server/index.js
git commit -m "feat: add since/until temporal filters to HTTP + MCP search"
```

---

### Task 5: Qdrant payload indexes + eval harness date normalization

**Files:**
- Modify: `qdrant_store.py:136-155`
- Modify: `eval/longmemeval.py` (normalize dates to ISO)

- [ ] **Step 1: Add payload indexes**

In `ensure_payload_indexes()`, after the `archived` index, add:

```python
        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="document_at",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass

        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="is_latest",
                field_schema=models.PayloadSchemaType.BOOL,
            )
        except Exception:
            pass
```

- [ ] **Step 2: Normalize LongMemEval dates to ISO in eval harness**

In `eval/longmemeval.py`, in `seed_question()`, normalize the raw date string:

```python
def _normalize_date(raw: str) -> str:
    """Convert LongMemEval date format to ISO 8601."""
    # Input: "2023/05/20 (Sat) 02:21"
    # Output: "2023-05-20T02:21:00+00:00"
    try:
        from datetime import datetime
        # Strip day-of-week in parens
        clean = raw.split("(")[0].strip() + raw.split(")")[-1] if "(" in raw else raw
        clean = clean.strip()
        dt = datetime.strptime(clean, "%Y/%m/%d %H:%M")
        return dt.strftime("%Y-%m-%dT%H:%M:00+00:00")
    except Exception:
        return raw  # Return as-is if unparseable
```

Use this when setting `metadata["document_at"]`.

- [ ] **Step 3: Run full test suite**

```bash
.venv/bin/pytest --tb=short -q
```

- [ ] **Step 4: Commit**

```bash
git add qdrant_store.py eval/longmemeval.py
git commit -m "feat: add Qdrant indexes for document_at/is_latest + normalize eval dates"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/pytest --tb=short -q
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -m py_compile memory_engine.py
python3 -m py_compile llm_extract.py
python3 -m py_compile app.py
node -c mcp-server/index.js
```

- [ ] **Step 3: Run temporal tests**

```bash
.venv/bin/pytest tests/test_temporal.py -v
```

- [ ] **Step 4: Commit cleanup if needed**

```bash
git add -A
git commit -m "chore: cleanup after temporal reasoning implementation"
```
