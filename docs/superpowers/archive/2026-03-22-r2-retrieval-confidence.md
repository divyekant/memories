# R2: Retrieval Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the feedback loop — make search ranking improve from operator feedback, surface deferred work proactively, and give operators diagnostic views for search quality.

**Architecture:** Feedback as 4th RRF signal in hybrid search (SQLite lookup, no Qdrant changes). Hook-side query enrichment (no LLM calls). Health page sections for operator views. All changes additive — no breaking changes to existing API contracts.

**Tech Stack:** Python (FastAPI), SQLite (usage.db), Bash (hooks), JavaScript (MCP + UI)

**Design spec:** `docs/superpowers/specs/2026-03-22-r2-retrieval-confidence-design.md`

**Test baseline:** 896 tests passing (post-R1 merge)

---

## File Map

| File | Role | Action |
|------|------|--------|
| `usage_tracker.py` | Feedback scores, history, problem queries, stale memories | **Modify** |
| `memory_engine.py` | Feedback as 4th RRF signal in hybrid_search | **Modify** |
| `app.py` | SearchRequest feedback_weight, new endpoints | **Modify** |
| `mcp-server/index.js` | feedback_weight on memory_search, memory_deferred tool | **Modify** |
| `webui/app.js` | Feedback in lifecycle tab, health page sections | **Modify** |
| `webui/styles.css` | Problem queries and stale memories styles | **Modify** |
| `integrations/claude-code/hooks/memory-query.sh` | Context enrichment, intent-based prefixes | **Modify** |
| `integrations/claude-code/hooks/memory-recall.sh` | Deferred-work surfacing | **Modify** |
| `tests/test_feedback_ranking.py` | Feedback ranking tests | **Create** |
| `tests/test_feedback_history.py` | Feedback history/retraction tests | **Create** |
| `tests/test_operator_views.py` | Problem queries + stale memories tests | **Create** |

---

## B1: Feedback Engine

### Task 1: Feedback index + batch score lookup

**Files:**
- Modify: `usage_tracker.py` (~line 130 for index, new method after line 253)
- Test: `tests/test_feedback_ranking.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_feedback_ranking.py
import pytest
from usage_tracker import UsageTracker

@pytest.fixture
def tracker(tmp_path):
    return UsageTracker(str(tmp_path / "usage.db"))

def test_get_feedback_scores_returns_net_scores(tracker):
    """get_feedback_scores returns useful minus not_useful per memory."""
    tracker.log_search_feedback(memory_id=1, query="test", signal="useful")
    tracker.log_search_feedback(memory_id=1, query="test", signal="useful")
    tracker.log_search_feedback(memory_id=1, query="test", signal="not_useful")
    tracker.log_search_feedback(memory_id=2, query="test", signal="not_useful")

    scores = tracker.get_feedback_scores([1, 2, 3])
    assert scores[1] == 1   # 2 useful - 1 not_useful
    assert scores[2] == -1  # 0 useful - 1 not_useful
    assert 3 not in scores  # no feedback = not in dict

def test_get_feedback_scores_empty_ids(tracker):
    """Empty ID list returns empty dict."""
    assert tracker.get_feedback_scores([]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_feedback_ranking.py -v`
Expected: FAIL — `get_feedback_scores` not defined

- [ ] **Step 3: Add memory_id index and get_feedback_scores method**

In `usage_tracker.py`:

1. After line 130 (`idx_feedback_ts`), add:
```python
CREATE INDEX IF NOT EXISTS idx_feedback_memory ON search_feedback(memory_id);
```

2. After `log_search_feedback()` method (~line 253), add:
```python
def get_feedback_scores(self, memory_ids: List[int]) -> Dict[int, int]:
    """Batch fetch net feedback score (useful - not_useful) for given memory IDs."""
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

3. Add stub to `NullTracker` class (~line 59):
```python
def get_feedback_scores(self, memory_ids: List[int]) -> Dict[int, int]:
    return {}
```

- [ ] **Step 4: Run test**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_feedback_ranking.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add usage_tracker.py tests/test_feedback_ranking.py
git commit -m "feat: add feedback score batch lookup with memory_id index"
```

---

### Task 2: Feedback as 4th RRF signal in hybrid_search

**Files:**
- Modify: `memory_engine.py` (~line 1238 hybrid_search, ~line 1377 hybrid_search_explain)
- Modify: `app.py` (~line 1242 SearchRequest, ~line 1807 POST /search)
- Test: `tests/test_feedback_ranking.py`

- [ ] **Step 1: Write failing test**

```python
def test_hybrid_search_feedback_weight_boosts_useful(engine_with_feedback):
    """Memories with positive feedback should rank higher when feedback_weight > 0."""
    # Setup: two memories with similar text, one has positive feedback
    # Search with feedback_weight=0.3
    results = engine.hybrid_search("test query", k=5, feedback_weight=0.3)
    # The memory with useful feedback should rank higher
    assert results[0]["id"] == useful_memory_id
```

Note: the exact fixture setup depends on the existing test patterns. Read `tests/conftest.py` and existing engine test fixtures to match.

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `hybrid_search()` does not accept `feedback_weight`

- [ ] **Step 3: Add feedback_weight to hybrid_search**

In `memory_engine.py`, modify `hybrid_search()`:

1. Add param: `feedback_weight: float = 0.0` (after `recency_half_life_days`)

2. **Replace** the existing 2-weight scaling at lines 1301-1302 with 4-weight scaling:
```python
# Scale all weights to sum to 1.0 with feedback
total_non_feedback = 1.0 - feedback_weight
effective_vector_weight = vector_weight * total_non_feedback * (1.0 - recency_weight)
effective_bm25_weight = (1.0 - vector_weight) * total_non_feedback * (1.0 - recency_weight)
# recency_weight scaled by total_non_feedback
effective_recency_weight = recency_weight * total_non_feedback
```

3. Add new param: `feedback_scores: Optional[Dict[int, int]] = None` — pre-computed feedback scores passed from app.py (the engine does NOT access usage_tracker directly; it's a module-level global in app.py, not available to the engine class).

4. After the recency RRF block (after line 1325), add feedback RRF:
```python
if feedback_weight > 0 and feedback_scores:
    # Only boost memories with net positive feedback (zero/negative = neutral)
    positive = [(doc_id, score) for doc_id, score in feedback_scores.items()
                if score > 0 and doc_id in rrf_scores]
    positive.sort(key=lambda x: x[1], reverse=True)
    for rank, (doc_id, _) in enumerate(positive):
        rrf_scores[doc_id] += feedback_weight * (1.0 / (rank + rrf_k))
```

4. Update `hybrid_search_explain()` similarly — add `feedback_weight` param, add to scoring_weights dict:
```python
"feedback": round(feedback_weight, 4),
```

- [ ] **Step 4: Add feedback_weight to SearchRequest in app.py**

After `recency_half_life_days` field (~line 1250):
```python
feedback_weight: float = Field(0.0, ge=0.0, le=1.0, description="Weight for feedback-based ranking signal (0=disabled)")
```

In the POST /search handler (~line 1814), compute feedback scores BEFORE calling hybrid_search, then pass both:
```python
# Compute feedback scores if weight > 0
fb_scores = None
if request_body.feedback_weight > 0:
    # We'll get candidate IDs after search — but we need them before.
    # Solution: pre-fetch ALL feedback scores (cheap at current scale).
    fb_scores = usage_tracker.get_feedback_scores(
        [m["id"] for m in getattr(memory, "metadata", [])]
    ) if request_body.feedback_weight > 0 else None

results = memory.hybrid_search(
    ...,
    feedback_weight=request_body.feedback_weight,
    feedback_scores=fb_scores,
)
```

Note: at ~9,500 memories, pre-fetching all scores is one SQL query returning only memories with feedback (likely <100 rows). This avoids a circular dependency between "need candidates to get scores" and "need scores to rank candidates".

- [ ] **Step 5: Run tests**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_feedback_ranking.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add memory_engine.py app.py tests/test_feedback_ranking.py
git commit -m "feat: add feedback as 4th RRF signal in hybrid search"
```

---

### Task 3: MCP memory_search feedback_weight param

**Files:**
- Modify: `mcp-server/index.js` (~line 43 memory_search tool)

- [ ] **Step 1: Add feedback_weight param to memory_search**

In `mcp-server/index.js`, in the `memory_search` tool schema (~line 48), add:
```javascript
feedback_weight: z.number().min(0).max(1).default(0.1).describe("Weight for feedback-based ranking (0=disabled, default 0.1)"),
```

In the handler body, pass it:
```javascript
if (feedback_weight !== undefined) body.feedback_weight = feedback_weight;
```

- [ ] **Step 2: Commit**

```bash
git add mcp-server/index.js
git commit -m "feat: add feedback_weight param to MCP memory_search tool"
```

---

### Task 4: Feedback history and retraction

**Files:**
- Modify: `usage_tracker.py` (new methods)
- Modify: `app.py` (new endpoints)
- Test: `tests/test_feedback_history.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_feedback_history.py
import pytest
from usage_tracker import UsageTracker

@pytest.fixture
def tracker(tmp_path):
    return UsageTracker(str(tmp_path / "usage.db"))

def test_get_feedback_history(tracker):
    """get_feedback_history returns entries for a specific memory."""
    tracker.log_search_feedback(memory_id=1, query="q1", signal="useful")
    tracker.log_search_feedback(memory_id=1, query="q2", signal="not_useful")
    tracker.log_search_feedback(memory_id=2, query="q3", signal="useful")

    history = tracker.get_feedback_history(memory_id=1)
    assert len(history) == 2
    assert all(h["memory_id"] == 1 for h in history)

def test_delete_feedback(tracker):
    """delete_feedback removes a specific entry."""
    tracker.log_search_feedback(memory_id=1, query="q1", signal="useful")
    history = tracker.get_feedback_history(memory_id=1)
    assert len(history) == 1

    deleted = tracker.delete_feedback(history[0]["id"])
    assert deleted is True
    assert len(tracker.get_feedback_history(memory_id=1)) == 0

def test_delete_feedback_nonexistent(tracker):
    """delete_feedback returns False for nonexistent ID."""
    assert tracker.delete_feedback(9999) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_feedback_history.py -v`
Expected: FAIL

- [ ] **Step 3: Add methods to UsageTracker**

```python
def get_feedback_history(self, memory_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Get feedback entries for a specific memory, newest first."""
    conn = self._connect()
    try:
        rows = conn.execute(
            "SELECT id, ts, memory_id, query, signal, search_id "
            "FROM search_feedback WHERE memory_id = ? "
            "ORDER BY ts DESC LIMIT ?",
            (memory_id, limit),
        ).fetchall()
        return [dict(zip(["id", "ts", "memory_id", "query", "signal", "search_id"], r)) for r in rows]
    finally:
        conn.close()

def delete_feedback(self, feedback_id: int) -> bool:
    """Delete a feedback entry by ID. Returns True if deleted."""
    conn = self._connect()
    try:
        cursor = conn.execute("DELETE FROM search_feedback WHERE id = ?", (feedback_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
```

Add NullTracker stubs:
```python
def get_feedback_history(self, memory_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    return []

def delete_feedback(self, feedback_id: int) -> bool:
    return False
```

- [ ] **Step 4: Add API endpoints in app.py**

```python
@app.get("/search/feedback/history")
async def feedback_history(
    request: Request,
    memory_id: int = Query(..., description="Memory ID to get feedback for"),
    limit: int = Query(50, ge=1, le=200),
):
    """Get feedback history for a specific memory."""
    auth = _get_auth(request)
    mem = memory.get_memory(memory_id)
    if auth.prefixes is not None and not auth.can_read(mem.get("source", "")):
        raise HTTPException(status_code=403, detail="Memory outside your scope")
    entries = usage_tracker.get_feedback_history(memory_id, limit)
    return {"entries": entries, "count": len(entries)}

@app.delete("/search/feedback/{feedback_id}")
async def retract_feedback(feedback_id: int, request: Request):
    """Retract a specific feedback entry."""
    auth = _get_auth(request)
    deleted = usage_tracker.delete_feedback(feedback_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Feedback entry {feedback_id} not found")
    return {"status": "retracted", "id": feedback_id}
```

- [ ] **Step 5: Run tests**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_feedback_history.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add usage_tracker.py app.py tests/test_feedback_history.py
git commit -m "feat: add feedback history and retraction endpoints"
```

---

### Task 5: Feedback section in lifecycle tab UI

**Files:**
- Modify: `webui/app.js` (renderLifecycleTab ~line 1670)

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_web_ui.py
def test_feedback_section_in_lifecycle(client):
    """Lifecycle tab should have feedback section."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "feedback/history" in js
    assert "retract" in js or "DELETE" in js
```

- [ ] **Step 2: Implement feedback section**

In `webui/app.js`, inside `renderLifecycleTab()`, after the confidence section and before the timeline section, add:

```javascript
// Feedback section
const feedbackSection = h("div", { className: "lifecycle-section" });
feedbackSection.appendChild(h("div", { className: "lifecycle-section-title" }, "Feedback"));
try {
  const fbData = await api(`/search/feedback/history?memory_id=${mem.id}&limit=10`);
  if (fbData.entries && fbData.entries.length > 0) {
    fbData.entries.forEach(entry => {
      const row = h("div", { className: "feedback-history-row" },
        h("span", { className: `badge badge-${entry.signal === "useful" ? "success" : "error"}` }, entry.signal),
        h("span", { className: "feedback-query" }, entry.query ? `"${entry.query.slice(0, 60)}"` : "(no query)"),
        h("span", { className: "timeline-ts" }, timeAgo(entry.ts)),
        h("button", {
          className: "btn btn-sm",
          onclick: async () => {
            await api(`/search/feedback/${entry.id}`, { method: "DELETE" });
            showToast("Feedback retracted", "info");
            renderLifecycleTab(mem, container);
          },
        }, "Retract"),
      );
      feedbackSection.appendChild(row);
    });
  } else {
    feedbackSection.appendChild(h("div", { className: "timeline-detail" }, "No feedback yet"));
  }
} catch {
  feedbackSection.appendChild(h("div", { className: "timeline-detail" }, "Feedback unavailable"));
}
container.appendChild(feedbackSection);
```

- [ ] **Step 3: Add CSS**

```css
.feedback-history-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
.feedback-query { font-size: 0.75rem; color: var(--color-text-muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 4: Run tests and commit**

```bash
git add webui/app.js webui/styles.css tests/test_web_ui.py
git commit -m "feat: add feedback history with retraction to lifecycle tab"
```

---

## B2: Smart Queries

### Task 6: Smarter query construction in memory-query.sh

**Files:**
- Modify: `integrations/claude-code/hooks/memory-query.sh` (~line 140)

- [ ] **Step 1: Add file context extraction**

After the existing context extraction (~line 140), add:

```bash
# Extract file context from recent transcript
FILE_CONTEXT=""
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  ACTIVE_FILES=$(tail -20 "$TRANSCRIPT_PATH" | grep -oE '(Read|Edit|Write) /[^ "]+' | awk '{print $2}' | xargs -I{} basename {} 2>/dev/null | sort -u | head -5 | tr '\n' ', ')
  if [ -n "$ACTIVE_FILES" ]; then
    FILE_CONTEXT="Files: ${ACTIVE_FILES%, }"
  fi
fi
```

- [ ] **Step 2: Add key term extraction**

```bash
# Extract identifiers from prompt
KEY_TERMS=$(echo "$PROMPT" | grep -oE '[A-Z][a-z]+([A-Z][a-z]+)+|[a-z_]+_[a-z_]+|[A-Z_]{3,}' | sort -u | head -10 | tr '\n' ', ')
if [ -n "$KEY_TERMS" ]; then
  KEY_TERMS="Terms: ${KEY_TERMS%, }"
fi
```

- [ ] **Step 3: Add intent-based prefix biasing**

```bash
# Intent detection for prefix biasing
PROMPT_LOWER=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]')
INTENT_PREFIXES=""
case "$PROMPT_LOWER" in
  fix*|debug*|error*|bug*|broken*|crash*)
    INTENT_PREFIXES="learning/$PROJECT bug-fix/$PROJECT"
    ;;
  how*|setup*|configure*|install*)
    INTENT_PREFIXES="decision/$PROJECT learning/$PROJECT"
    ;;
esac
```

- [ ] **Step 4: Enrich the search query**

Modify the query construction to prepend context:
```bash
ENRICHED_QUERY="Project: $PROJECT"
[ -n "$FILE_CONTEXT" ] && ENRICHED_QUERY="$ENRICHED_QUERY\n$FILE_CONTEXT"
[ -n "$KEY_TERMS" ] && ENRICHED_QUERY="$ENRICHED_QUERY\n$KEY_TERMS"
ENRICHED_QUERY="$ENRICHED_QUERY\nRecent conversation:\n$CONTEXT\nCurrent prompt: $PROMPT"
```

Add intent prefixes to the scoped search loop if present.

- [ ] **Step 5: Commit**

```bash
git add integrations/claude-code/hooks/memory-query.sh
git commit -m "feat: enrich query construction with file context, key terms, and intent"
```

---

### Task 7: Proactive deferred-work surfacing

**Files:**
- Modify: `integrations/claude-code/hooks/memory-recall.sh` (~line 121)
- Modify: `mcp-server/index.js` (new memory_deferred tool)

- [ ] **Step 1: Enhance memory-recall.sh**

After the existing multi-prefix search loop (~line 145), add a dedicated deferred-work search:

```bash
# Dedicated deferred-work surfacing
WIP_QUERY="deferred incomplete blocked todo revisit wip"
WIP_RESULTS=$(search_memories "$WIP_QUERY" "wip/$PROJECT" 5 0.3)
WIP_COUNT=$(echo "$WIP_RESULTS" | jq -r '.count // 0')

DEFERRED_SECTION=""
if [ "$WIP_COUNT" -gt 0 ]; then
  DEFERRED_ITEMS=$(echo "$WIP_RESULTS" | jq -r '.results[:5][] | "- [\(.source)] \(.text[0:150])"')
  DEFERRED_SECTION="\n## Deferred Work\n$DEFERRED_ITEMS\n"
fi
```

Inject `$DEFERRED_SECTION` before the Memory Playbook in the output.

- [ ] **Step 2: Add memory_deferred MCP tool**

In `mcp-server/index.js`, after the `memory_missed` tool, add:

```javascript
server.tool(
  "memory_deferred",
  "List deferred/WIP memories for a project. Surfaces incomplete threads from wip/{project} source prefix.",
  {
    project: z.string().min(1).describe("Project name to search wip/ prefix for"),
    k: z.number().int().min(1).max(20).default(5).describe("Number of results"),
  },
  async ({ project, k = 5 }) => {
    const data = await memoriesRequest("/search", {
      method: "POST",
      body: JSON.stringify({
        query: "deferred incomplete blocked todo revisit wip",
        k,
        hybrid: true,
        source_prefix: `wip/${project}`,
      }),
    });

    if (data.count === 0) {
      return { content: [{ type: "text", text: `No deferred work found for project "${project}"` }] };
    }

    const lines = data.results.map((r, i) =>
      `[${i + 1}] ${r.source}\n${r.text}`
    );

    return {
      content: [{
        type: "text",
        text: `${data.count} deferred item(s) for "${project}":\n\n${lines.join("\n\n---\n\n")}`,
      }],
    };
  }
);
```

- [ ] **Step 3: Commit**

```bash
git add integrations/claude-code/hooks/memory-recall.sh mcp-server/index.js
git commit -m "feat: proactive deferred-work surfacing in hooks and MCP"
```

---

## B3: Operator Views

### Task 8: Problem queries endpoint and view

**Files:**
- Modify: `usage_tracker.py` (new method)
- Modify: `app.py` (new endpoint)
- Modify: `webui/app.js` (health page)
- Test: `tests/test_operator_views.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_operator_views.py
import pytest
from usage_tracker import UsageTracker

@pytest.fixture
def tracker(tmp_path):
    return UsageTracker(str(tmp_path / "usage.db"))

def test_get_problem_queries(tracker):
    """get_problem_queries returns queries with mostly negative feedback."""
    # Query with 3 not_useful, 0 useful
    for _ in range(3):
        tracker.log_search_feedback(memory_id=1, query="bad query", signal="not_useful")
    # Query with 1 not_useful, 2 useful
    tracker.log_search_feedback(memory_id=2, query="ok query", signal="not_useful")
    tracker.log_search_feedback(memory_id=2, query="ok query", signal="useful")
    tracker.log_search_feedback(memory_id=2, query="ok query", signal="useful")

    problems = tracker.get_problem_queries(min_feedback=2, min_negative_ratio=0.5)
    assert len(problems) == 1
    assert problems[0]["query"] == "bad query"
    assert problems[0]["not_useful"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add get_problem_queries to UsageTracker**

```python
def get_problem_queries(self, min_feedback: int = 2, min_negative_ratio: float = 0.5,
                        limit: int = 20, memory_ids: list | None = None) -> List[Dict[str, Any]]:
    """Queries with consistently negative feedback."""
    conn = self._connect()
    try:
        mem_filter = ""
        params: list = []
        if memory_ids is not None:
            placeholders = ",".join("?" * len(memory_ids))
            mem_filter = f"AND memory_id IN ({placeholders}) "
            params.extend(memory_ids)
        params.extend([min_feedback, min_negative_ratio, limit])
        rows = conn.execute(
            f"SELECT query, COUNT(*) as total, "
            f"SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as not_useful "
            f"FROM search_feedback WHERE query != '' {mem_filter}"
            f"GROUP BY query "
            f"HAVING not_useful >= ? AND CAST(not_useful AS FLOAT) / COUNT(*) >= ? "
            f"ORDER BY not_useful DESC LIMIT ?",
            params,
        ).fetchall()
        return [{"query": r[0], "total": r[1], "not_useful": r[2],
                 "ratio": round(r[2] / r[1], 2) if r[1] > 0 else 0} for r in rows]
    finally:
        conn.close()
```

Add NullTracker stub.

- [ ] **Step 4: Add endpoint in app.py**

```python
@app.get("/metrics/problem-queries")
async def problem_queries(
    request: Request,
    min_feedback: int = Query(2, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Queries with consistently negative feedback. Admin only."""
    auth = _get_auth(request)
    _require_admin(auth)
    return {"queries": usage_tracker.get_problem_queries(min_feedback=min_feedback, limit=limit)}
```

- [ ] **Step 5: Add health page UI section**

In `webui/app.js` health page, after the existing conflicts section, add a "Problem Queries" section that fetches `GET /metrics/problem-queries` and renders a table with Query, Total, Negative %, and a "Replay" link that navigates to `#/memories?q={query}`.

- [ ] **Step 6: Add search URL param support to memories page**

In the memories page `registerPage("memories", ...)`, check for `?q=` in the URL hash on load and auto-execute a search if present.

- [ ] **Step 7: Run tests and commit**

```bash
git add usage_tracker.py app.py webui/app.js tests/test_operator_views.py
git commit -m "feat: add problem queries view to health page"
```

---

### Task 9: Stale memories endpoint and view

**Files:**
- Modify: `usage_tracker.py` (new method)
- Modify: `app.py` (new endpoint)
- Modify: `webui/app.js` (health page)
- Modify: `webui/styles.css`
- Test: `tests/test_operator_views.py`

- [ ] **Step 1: Write failing test**

```python
def test_get_stale_memories(tracker):
    """get_stale_memories returns frequently retrieved but never useful memories."""
    # Memory 1: 5 retrievals, no useful feedback
    for _ in range(5):
        tracker.log_retrieval(memory_id=1, query="q", source="test")
    tracker.log_search_feedback(memory_id=1, query="q", signal="not_useful")

    # Memory 2: 3 retrievals, 1 useful
    for _ in range(3):
        tracker.log_retrieval(memory_id=2, query="q", source="test")
    tracker.log_search_feedback(memory_id=2, query="q", signal="useful")

    stale = tracker.get_stale_memories(min_retrievals=3)
    assert len(stale) == 1
    assert stale[0]["memory_id"] == 1
    assert stale[0]["retrievals"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add get_stale_memories to UsageTracker**

```python
def get_stale_memories(self, min_retrievals: int = 3, limit: int = 20,
                       memory_ids: list | None = None) -> List[Dict[str, Any]]:
    """Memories retrieved often but never rated useful."""
    conn = self._connect()
    try:
        mem_filter = ""
        params: list = []
        if memory_ids is not None:
            placeholders = ",".join("?" * len(memory_ids))
            mem_filter = f"AND r.memory_id IN ({placeholders}) "
            params.extend(memory_ids)
        params.extend([min_retrievals, limit])
        # Use subqueries to avoid cartesian join between retrieval_log and search_feedback
        rows = conn.execute(
            f"SELECT r.memory_id, r.retrievals, "
            f"COALESCE(f.useful, 0) as useful, COALESCE(f.not_useful, 0) as not_useful "
            f"FROM (SELECT memory_id, COUNT(*) as retrievals FROM retrieval_log "
            f"      WHERE 1=1 {mem_filter.replace('r.memory_id', 'memory_id')} GROUP BY memory_id) r "
            f"LEFT JOIN (SELECT memory_id, "
            f"  SUM(CASE WHEN signal='useful' THEN 1 ELSE 0 END) as useful, "
            f"  SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as not_useful "
            f"  FROM search_feedback GROUP BY memory_id) f ON r.memory_id = f.memory_id "
            f"WHERE 1=1 {mem_filter}"
            f"GROUP BY r.memory_id "
            f"HAVING retrievals >= ? AND useful = 0 "
            f"ORDER BY retrievals DESC LIMIT ?",
            params,
        ).fetchall()
        return [{"memory_id": r[0], "retrievals": r[1], "useful": r[2], "not_useful": r[3]} for r in rows]
    finally:
        conn.close()
```

Add NullTracker stub.

- [ ] **Step 4: Add endpoint in app.py**

```python
@app.get("/metrics/stale-memories")
async def stale_memories(
    request: Request,
    min_retrievals: int = Query(3, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Frequently retrieved but never useful memories. Admin only."""
    auth = _get_auth(request)
    _require_admin(auth)
    return {"memories": usage_tracker.get_stale_memories(min_retrievals=min_retrievals, limit=limit)}
```

- [ ] **Step 5: Add health page UI section**

In `webui/app.js` health page, after the problem queries section, add a "Stale Memories" section. Fetch `GET /metrics/stale-memories`, render table with Memory ID, Retrievals, "0 useful", and action buttons (Archive, View).

- [ ] **Step 6: Add CSS**

```css
.stale-memory-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--color-border); }
.problem-query-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--color-border); }
.problem-query-text { flex: 1; font-size: 0.8125rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.replay-link { font-size: 0.75rem; color: var(--color-primary); cursor: pointer; text-decoration: none; }
.replay-link:hover { text-decoration: underline; }
```

- [ ] **Step 7: Run full test suite**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/ -q`
Expected: All pass (896 baseline + new R2 tests)

- [ ] **Step 8: Commit**

```bash
git add usage_tracker.py app.py webui/app.js webui/styles.css tests/test_operator_views.py
git commit -m "feat: add stale memories view to health page"
```

---

## Post-Implementation Checklist

- [ ] All baseline tests still pass
- [ ] All new R2 tests pass
- [ ] `hybrid_search(feedback_weight=0.1)` boosts useful memories
- [ ] `hybrid_search(feedback_weight=0.0)` behaves identically to pre-R2
- [ ] Feedback history shows in lifecycle tab with retract buttons
- [ ] Problem queries and stale memories visible on health page (admin only)
- [ ] `memory-recall.sh` surfaces deferred work at session start
- [ ] `memory-query.sh` includes file context and key terms
- [ ] MCP `memory_deferred` tool works
- [ ] MCP `memory_search` passes `feedback_weight`
