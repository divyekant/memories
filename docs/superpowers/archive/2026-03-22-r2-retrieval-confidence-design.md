# R2: Retrieval Confidence — Design Spec

## Context

v3.1.0 shipped R1 (controllable memory) with write-path UI, extraction engine, and safety foundations. The feedback loop is open: `POST /search/feedback` collects useful/not_useful signals, but search ranking ignores them entirely. R2 closes the loop.

**Product thesis:** Memory that learns from operator feedback. The system gets smarter with use — good memories rise, bad ones sink, stale ones surface for cleanup.

**Scale context:** ~9,500 memories, single-digit concurrent users, SQLite for feedback/metrics. All designs optimize for simplicity at this scale — no materialized views, no background jobs, no Qdrant schema changes.

**Existing infrastructure:**
- `search_feedback` table: `{id, ts, memory_id, query, signal, search_id}`
- `retrieval_log` table: `{id, ts, memory_id, query, source, rank, result_count}`
- Hybrid search uses RRF with vector + BM25 + optional recency signals
- Claude Code hooks: `memory-recall.sh` (SessionStart), `memory-query.sh` (UserPromptSubmit)
- MCP server: `mcp-server/index.js` with `memory_search`, `memory_add`, etc.

---

## Batching Strategy

| Batch | Items | Theme | Dependency |
|-------|-------|-------|------------|
| **B1: Feedback Engine** | 19, 23 | Wire feedback into ranking + manage feedback | None |
| **B2: Smart Queries** | 17, 18 | Better query construction + deferred-work surfacing | None (parallel with B1) |
| **B3: Operator Views** | 20, 21, 22 | Health page diagnostic views | B1 (feedback must be flowing) |

---

## B1: Feedback Engine

### Item 19: Feedback-Weighted Retrieval

**Files:** `memory_engine.py` (hybrid_search), `usage_tracker.py` (new method), `app.py` (SearchRequest)

**Pre-requisite index:** Add `CREATE INDEX IF NOT EXISTS idx_feedback_memory ON search_feedback(memory_id)` to `usage_tracker.py` init. The existing table only has an index on `ts`.

**New method in `usage_tracker.py`:**

```python
def get_feedback_scores(self, memory_ids: List[int]) -> Dict[int, int]:
    """Batch fetch net feedback score (useful - not_useful) for given memory IDs.
    Returns {memory_id: net_score}. Missing IDs have score 0 (implicit)."""
    if not memory_ids:
        return {}
    conn = self._connect()
    try:
        placeholders = ",".join("?" * len(memory_ids))
        rows = conn.execute(
            f"SELECT memory_id, "
            f"SUM(CASE WHEN signal='useful' THEN 1 ELSE 0 END) - "
            f"SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as net "
            f"FROM search_feedback WHERE memory_id IN ({placeholders}) "
            f"GROUP BY memory_id",
            memory_ids,
        ).fetchall()
        return {row[0]: row[1] for row in rows}
    finally:
        conn.close()
```

**Changes to `hybrid_search()` in `memory_engine.py`:**

New parameter: `feedback_weight: float = 0.1`

After collecting vector and BM25 candidates (before final RRF computation):

1. Collect all candidate memory IDs
2. Call `usage_tracker.get_feedback_scores(candidate_ids)` — one SQL query
3. Only include memories with `net_score > 0` in the feedback ranking. Memories with zero or negative feedback get no feedback RRF contribution (truly neutral). This means positive feedback is a boost, while zero and negative feedback are equivalent — no penalty, just no bonus.
4. Rank qualifying candidates by net feedback score descending
5. Add to RRF: `feedback_weight * (1.0 / (rank + rrf_k))`

Weight scaling (same pattern as recency):
```python
total_non_feedback = 1.0 - feedback_weight
effective_vector_weight = vector_weight * total_non_feedback * (1.0 - recency_weight)
effective_bm25_weight = (1.0 - vector_weight) * total_non_feedback * (1.0 - recency_weight)
effective_recency_weight = recency_weight * total_non_feedback
# feedback_weight stays as-is
# All four weights sum to 1.0
```

**Edge case:** If `feedback_weight + recency_weight` exceeds ~0.8, vector+BM25 signals are crushed below 10%. This is allowed (operator's choice) but means search becomes mostly feedback+recency driven. No guard is added — consistent with existing recency_weight behavior which also has no upper bound guard.

**NullTracker stubs:** Add `get_feedback_scores`, `get_feedback_history`, `delete_feedback` no-op methods to `NullTracker` class.

**Changes to `SearchRequest` in `app.py`:**

```python
feedback_weight: float = Field(0.1, ge=0.0, le=1.0, description="Weight for feedback-based ranking signal")
```

Pass through to `hybrid_search()`. Non-hybrid search (`search()`) is not affected — feedback only applies to hybrid mode.

**Changes to `hybrid_search_explain()`:**

Add feedback to the explain payload:
```python
"scoring_weights": {
    "vector": effective_vector_weight,
    "bm25": effective_bm25_weight,
    "recency": effective_recency_weight,
    "feedback": feedback_weight,
    "rrf_k": rrf_k,
}
```

**MCP server:** Add `feedback_weight` param to `memory_search` tool (default 0.1).

### Item 23: Feedback History & Retraction

**Files:** `usage_tracker.py` (new methods), `app.py` (new endpoints), `webui/app.js` (lifecycle tab), `mcp-server/index.js`

**New methods in `usage_tracker.py`:**

```python
def get_feedback_history(self, memory_id: int, limit: int = 50) -> List[Dict]:
    """Get feedback entries for a specific memory, newest first."""

def delete_feedback(self, feedback_id: int) -> bool:
    """Delete a feedback entry by ID. Returns True if deleted."""
```

**New endpoints in `app.py`:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/search/feedback/history?memory_id={id}&limit=50` | List feedback for a memory |
| `DELETE` | `/search/feedback/{id}` | Retract a feedback entry |

Auth: scoped to memories the key can read. Admin can see/delete all.

**UI changes in `webui/app.js`:**

In the Lifecycle tab, add a "Feedback" section below the audit timeline:
- Table: timestamp, query (truncated), signal (useful/not_useful badge), retract button
- Retract button: `DELETE /search/feedback/{id}`, remove row, toast "Feedback retracted"
- Show "No feedback yet" if empty

**MCP:** No new tools needed — feedback retraction is an operator UI action, not an agent action.

---

## B2: Smart Queries

### Item 18: Smarter Query Construction

**Files:** `integrations/claude-code/hooks/memory-query.sh`

**Current behavior:** Hook sends `"Project: {name}\nRecent conversation:\n{context}\nCurrent prompt: {prompt}"` as the search query.

**Enhancements (all in the hook, no backend changes):**

1. **File context extraction:** If the transcript mentions a file path being edited (common pattern: `Read /path/to/file.py`), extract the filename and include it: `"File: {filename}"`. Use simple grep on last 10 transcript lines for `Read `, `Edit `, `Write ` tool calls.

2. **Key term extraction:** Pull identifiers from the prompt — look for `CamelCase`, `snake_case`, `SCREAMING_CASE` patterns, error message fragments (strings in quotes), and function/class names. Prepend as `"Terms: {terms}"`.

3. **Intent-based prefix biasing:** If prompt starts with fix/debug/error/bug keywords → add `learning/` and `bug-fix/` to source prefix search. If starts with how/setup/configure → add `decision/` prefix. Expand the existing scoped prefix list, don't replace it.

**Implementation:** ~30 lines of bash in `memory-query.sh`. No API changes.

### Item 17: Proactive Deferred-Work Surfacing

**Files:** `integrations/claude-code/hooks/memory-recall.sh`, `mcp-server/index.js`

**Hook enhancement (memory-recall.sh):**

After the existing per-prefix recall searches, add a dedicated search:
```bash
# Search for deferred/WIP items
WIP_RESULTS=$(curl -s -X POST "$MEMORIES_URL/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "{\"query\": \"deferred incomplete todo blocked\", \"k\": 5, \"hybrid\": true, \"source_prefix\": \"wip/$PROJECT\"}")
```

Inject results under a `## Deferred Work` heading in the session context, before the recall playbook. Format: bulleted list with source and text preview.

**New MCP tool (mcp-server/index.js):**

```javascript
server.tool(
  "memory_deferred",
  "List deferred/WIP memories for a project. Surfaces incomplete threads from wip/{project} source prefix.",
  {
    project: z.string().describe("Project name to search wip/ prefix for"),
    k: z.number().int().min(1).max(20).default(5),
  },
  async ({ project, k = 5 }) => {
    const data = await memoriesRequest("/search", {
      method: "POST",
      body: JSON.stringify({
        query: "deferred incomplete todo blocked wip",
        k,
        hybrid: true,
        source_prefix: `wip/${project}`,
      }),
    });
    // Format results
  }
);
```

---

## B3: Operator Views

All views added as new sections on the existing Health page. No new pages.

### Item 20: Problem Queries View

**Files:** `usage_tracker.py` (new method), `app.py` (new endpoint), `webui/app.js` (health page)

**New method in `usage_tracker.py`:**

```python
def get_problem_queries(self, min_feedback: int = 2, min_negative_ratio: float = 0.5,
                        limit: int = 20, memory_ids: list | None = None) -> List[Dict]:
    """Queries with consistently negative feedback.
    Returns [{query, total, not_useful, ratio}] sorted by not_useful desc."""
```

SQL:
```sql
SELECT query, COUNT(*) as total,
       SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as not_useful
FROM search_feedback
WHERE query != ''
GROUP BY query
HAVING not_useful >= ? AND CAST(not_useful AS FLOAT) / COUNT(*) >= ?
ORDER BY not_useful DESC
LIMIT ?
```

**New endpoint:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/metrics/problem-queries?min_feedback=2&limit=20` | Queries with bad feedback (admin only) |

**UI:** Table on Health page with columns: Query, Total Feedback, Negative %, "Replay" link. Replay link navigates to `#/memories` and triggers a search with that query.

### Item 21: Stale Memories View

**Files:** `usage_tracker.py` (new method), `app.py` (new endpoint), `webui/app.js` (health page)

**New method in `usage_tracker.py`:**

```python
def get_stale_memories(self, min_retrievals: int = 3, limit: int = 20,
                       memory_ids: list | None = None) -> List[Dict]:
    """Memories retrieved often but never rated useful.
    Returns [{memory_id, retrievals, useful_count, not_useful_count}]."""
```

SQL:
```sql
SELECT r.memory_id, COUNT(DISTINCT r.id) as retrievals,
       COALESCE(SUM(CASE WHEN f.signal='useful' THEN 1 ELSE 0 END), 0) as useful,
       COALESCE(SUM(CASE WHEN f.signal='not_useful' THEN 1 ELSE 0 END), 0) as not_useful
FROM retrieval_log r
LEFT JOIN search_feedback f ON r.memory_id = f.memory_id
GROUP BY r.memory_id
HAVING retrievals >= ? AND useful = 0
ORDER BY retrievals DESC
LIMIT ?
```

**New endpoint:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/metrics/stale-memories?min_retrievals=3&limit=20` | Frequently retrieved, never useful (admin only) |

**UI:** Table on Health page with columns: Memory ID, Source, Retrievals, "0 useful". Action buttons: "Archive" (PATCH archived), "View" (navigates to memory detail).

To show source and text preview, the endpoint joins with or fetches memory data via engine. Alternatively, the UI fetches memory details client-side after getting the IDs (simpler, avoids cross-store joins).

### Item 22: Replayable Failed Searches (folded into Item 20)

No separate implementation. The "Replay" link on Problem Queries (item 20) navigates to the Memories page with the query pre-filled in the search box, executing the search. This lets operators see current results after making changes (archiving bad memories, editing content) and verify improvement.

**Implementation:** The Memories page search already supports URL hash params. Add `?q={query}` support to the hash router so `#/memories?q=broken+search` auto-executes a search on load.

---

## Test Strategy

| File | Key Scenarios |
|------|--------------|
| `tests/test_feedback_ranking.py` | Feedback scores computed correctly; hybrid_search ranks useful memories higher; zero-feedback memories unaffected; feedback_weight=0 disables; weight scaling sums to 1.0 |
| `tests/test_feedback_history.py` | History endpoint returns entries; delete retracts entry; auth scoping |
| `tests/test_problem_queries.py` | Problem queries aggregation; min_feedback threshold; negative ratio filter |
| `tests/test_stale_memories.py` | Stale detection; min_retrievals threshold; useful=0 filter |
| `tests/test_web_ui.py` | New Health page sections exist; feedback section in lifecycle tab |

Hook changes (`memory-query.sh`, `memory-recall.sh`) are tested manually — bash hooks don't have a test harness.

---

## Files Modified

| File | Batch | Changes |
|------|-------|---------|
| `memory_engine.py` | B1 | feedback_weight param, feedback RRF signal in hybrid_search |
| `usage_tracker.py` | B1, B3 | get_feedback_scores, get_feedback_history, delete_feedback, get_problem_queries, get_stale_memories |
| `app.py` | B1, B3 | SearchRequest feedback_weight, feedback history/delete endpoints, problem-queries endpoint, stale-memories endpoint |
| `mcp-server/index.js` | B1, B2 | feedback_weight on memory_search, memory_deferred tool |
| `webui/app.js` | B1, B3 | Feedback section in lifecycle tab, problem queries + stale memories on health page, search URL param |
| `webui/styles.css` | B3 | Problem queries and stale memories table styles |
| `integrations/claude-code/hooks/memory-query.sh` | B2 | File context, key terms, intent-based prefix biasing |
| `integrations/claude-code/hooks/memory-recall.sh` | B2 | Deferred-work wip/ prefix search |

## What Is Explicitly Out of Scope

- Auto-archive based on confidence thresholds (R3 item 25)
- Confidence affecting search ranking (R3 item 28)
- Per-project isolation / project entity (R3)
- LLM-powered query rewriting (too expensive for per-prompt hook)
- Background materialization of feedback scores (unnecessary at current scale)
- Qdrant payload changes for feedback (keep feedback in SQLite)
