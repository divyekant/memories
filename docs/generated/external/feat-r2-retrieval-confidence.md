---
id: feat-013
type: feature-doc
title: "R2: Retrieval Confidence"
audience: external
generated: 2026-03-22
---

# R2: Retrieval Confidence

Memories now learns from how you use it. Search results improve over time based on your feedback, hooks construct smarter queries automatically, and deferred work surfaces when you start a new session. For operators, the Health page gains diagnostic views that highlight problem areas before they affect day-to-day use.

---

## What's New

- **Feedback-weighted search** -- Memories you mark "useful" rank higher in future searches. The system adds feedback as a fourth signal in hybrid search (alongside vector similarity, BM25 keywords, and recency).
- **Smarter query construction** -- The `memory-query.sh` hook now extracts file context and key terms from your conversation and applies intent-based source prefix biasing. You get better recall without changing how you work.
- **Deferred work surfacing** -- At session start, the `memory-recall.sh` hook searches the `wip/{project}` prefix and injects any incomplete work items into your session context automatically.
- **`memory_deferred` MCP tool** -- Query deferred/WIP memories for any project directly from your agent or editor.
- **Problem queries view** -- The Health page shows queries that consistently receive negative feedback, with a replay link so you can re-run them after making fixes (admin only).
- **Stale memories view** -- The Health page surfaces memories that are retrieved frequently but never marked useful, so you can archive or revise them (admin only).
- **Feedback history and retraction** -- The Lifecycle tab on each memory now displays its full feedback history, and you can retract any feedback entry with one click.

---

## Feature Guide

### Feedback-Weighted Search

Search ranking now incorporates a feedback signal. When you mark a search result as "useful" (via `POST /search/feedback`), that memory earns a positive score. Over time, memories with consistently positive feedback float higher in results.

**How it works:**

1. Every time you submit feedback on a search result, the system records whether it was `useful` or `not_useful`.
2. Each memory accumulates a net feedback score: `useful_count - not_useful_count`.
3. During hybrid search, memories with positive net scores receive a ranking boost proportional to the `feedback_weight` parameter.
4. Memories with zero or negative feedback are unaffected -- they receive no penalty, just no bonus.

**Using feedback_weight in search:**

```bash
curl -s -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "query": "deployment configuration",
    "k": 5,
    "hybrid": true,
    "feedback_weight": 0.1
  }' | jq .
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `feedback_weight` | `0.0` | Weight for feedback signal in RRF fusion (0.0 = disabled, up to 1.0) |

When `feedback_weight` is non-zero, vector and BM25 weights are scaled down proportionally so all four signals (vector, BM25, recency, feedback) sum to 1.0. Setting `feedback_weight` to 0 gives you the exact same ranking behavior as before R2.

**Via MCP:**

The `memory_search` tool accepts `feedback_weight` as an optional parameter (default 0.1 when used through MCP). You do not need to change your MCP configuration -- the parameter is available automatically after upgrading.

---

### Smarter Query Construction

The `memory-query.sh` hook, which fires on every user prompt in Claude Code, now enriches the search query it sends to the Memories API. You do not need to configure anything -- the improvements apply automatically.

**What the hook now does:**

1. **File context extraction** -- The hook scans the last 20 lines of your conversation transcript for `Read`, `Edit`, and `Write` tool calls. If it finds file paths, it extracts the filenames and adds them to the query as `Files: app.py, config.ts`. This helps the search engine find memories related to the files you are actively working on.

2. **Key term extraction** -- The hook identifies identifiers in your prompt -- `CamelCase` names, `snake_case` variables, `SCREAMING_CASE` constants, and quoted strings. These are prepended as `Terms: MyComponent, fetch_data, API_KEY`. This improves keyword matching for technical queries.

3. **Intent-based prefix biasing** -- If your prompt starts with words like `fix`, `debug`, `error`, or `bug`, the hook adds `learning/` and `bug-fix/` source prefixes to the search. If it starts with `how`, `setup`, or `configure`, it adds `decision/` and `learning/` prefixes. This steers the search toward the most relevant memory categories for your current task.

**Example enriched query:**

Before R2, a prompt like "fix the auth timeout" would search with:
```
Project: my-app
Recent conversation: ...
Current prompt: fix the auth timeout
```

After R2, the same prompt produces:
```
Project: my-app
Files: auth_handler.py, middleware.ts
Terms: auth, timeout
Recent conversation: ...
Current prompt: fix the auth timeout
```

And the search also checks `learning/my-app` and `bug-fix/my-app` source prefixes in addition to the standard ones.

---

### Deferred Work Surfacing

When you start a new Claude Code session, the `memory-recall.sh` hook now checks for incomplete work items stored under the `wip/{project}` source prefix. If any are found, they appear in your session context under a **Deferred Work** heading.

**How to use it:**

1. During a session, store a deferred item by adding a memory with a `wip/` source prefix:

```bash
curl -s -X POST http://localhost:8900/memory \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "text": "Auth refactor blocked on upstream API changes — revisit when v2 endpoint is available",
    "source": "wip/my-app"
  }'
```

2. The next time you start a session in that project, the recall hook picks it up automatically and shows it in the session context:

```
## Deferred Work
- [wip/my-app] Auth refactor blocked on upstream API changes — revisit when v2 endpoint is available
```

3. Once you complete the work, delete or re-source the memory to remove it from future deferred work lists.

The hook searches for up to 5 deferred items per session using keywords like "deferred", "incomplete", "blocked", "todo", and "revisit".

---

### memory_deferred MCP Tool

You can query deferred work programmatically using the new `memory_deferred` MCP tool. This is useful when you want to check on incomplete work mid-session rather than waiting for session start.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project` | string | (required) | Project name to search `wip/` prefix for |
| `k` | integer | 5 | Number of results to return (1--20) |

**Example usage in an agent:**

```
Use memory_deferred with project "my-app" to see if there are any blocked items.
```

The tool returns a formatted list of deferred items with their source and full text, or a message indicating no deferred work was found.

---

### Problem Queries (Health Page)

The Health page now includes a **Problem Queries** section that shows search queries receiving consistently negative feedback. This helps you identify patterns where the memory store is failing to serve useful results.

**How to use it:**

1. Navigate to the Health page in the web UI.
2. Scroll to the **Problem Queries** section.
3. Review the table:

| Column | Description |
|--------|-------------|
| **Query** | The search query text |
| **Total Feedback** | Number of feedback entries for this query |
| **Negative %** | Percentage of feedback that was `not_useful` |
| **Replay** | Link that navigates to the Memories page and re-runs the query |

4. Click **Replay** on any query to see its current results. After you fix the underlying issue (editing or archiving problematic memories, adjusting source prefixes), replay the query to verify improvement.

**API access:**

```bash
curl -s "http://localhost:8900/metrics/problem-queries?min_feedback=2&limit=20" \
  -H "X-API-Key: $ADMIN_API_KEY" | jq .
```

This endpoint requires admin access. It returns queries where at least `min_feedback` entries exist and at least 50% are negative.

---

### Stale Memories (Health Page)

The Health page also gains a **Stale Memories** section that surfaces memories retrieved frequently but never marked useful. These are candidates for revision or archival -- they keep appearing in results but no one finds them helpful.

**How to use it:**

1. Navigate to the Health page in the web UI.
2. Scroll to the **Stale Memories** section.
3. Review the table:

| Column | Description |
|--------|-------------|
| **Memory ID** | The memory's identifier |
| **Source** | The memory's source prefix |
| **Retrievals** | How many times it has been retrieved |
| **Useful** | Always 0 for stale memories |

4. For each stale memory, you can:
   - Click **View** to navigate to the memory detail panel and review its content.
   - Click **Archive** to soft-delete it. Archived memories can be restored later if the decision turns out to be wrong.

**API access:**

```bash
curl -s "http://localhost:8900/metrics/stale-memories?min_retrievals=3&limit=20" \
  -H "X-API-Key: $ADMIN_API_KEY" | jq .
```

This endpoint requires admin access. It returns memories with at least `min_retrievals` retrieval events and zero useful feedback.

---

### Feedback History and Retraction

Every memory's detail panel now shows its complete feedback history in the **Lifecycle** tab. You can review past feedback and retract entries that were submitted by mistake.

**How to use it:**

1. Open any memory's detail panel by clicking on it in the list.
2. Switch to the **Lifecycle** tab.
3. Scroll to the **Feedback** section. Each entry shows:
   - A signal badge (`useful` or `not_useful`)
   - The query that produced the feedback (truncated)
   - A relative timestamp
   - A **Retract** button
4. Click **Retract** to delete a feedback entry. The entry is removed immediately and a confirmation toast appears.

Retracting feedback recalculates the memory's net feedback score. If a memory was being boosted by a mistaken "useful" signal, retracting that entry adjusts its ranking accordingly.

**API access:**

```bash
# View feedback history for a memory
curl -s "http://localhost:8900/search/feedback/history?memory_id=42&limit=50" \
  -H "X-API-Key: $API_KEY" | jq .

# Retract a specific feedback entry
curl -s -X DELETE "http://localhost:8900/search/feedback/17" \
  -H "X-API-Key: $API_KEY"
```

Feedback history is scoped by your API key's access -- you can only see feedback for memories your key can read. Admin keys can see and retract all feedback.

---

## Tips and Best Practices

- **Start with the default feedback_weight.** A value of 0.1 gives feedback a gentle influence without overpowering vector and keyword signals. Increase it only after you have accumulated meaningful feedback data.
- **Leave feedback consistently.** The feedback loop works best when you mark results as useful or not useful regularly. Even a few signals per day compound over time into noticeably better rankings.
- **Use `wip/` prefixes for real incomplete work.** Reserve the `wip/{project}` source prefix for genuinely deferred tasks -- blocked items, half-finished refactors, questions to revisit. If you store general notes under `wip/`, the deferred work section becomes noisy.
- **Review the Health page periodically.** Problem queries and stale memories are leading indicators of search quality issues. Spending a few minutes each week reviewing and acting on them keeps your memory store healthy.
- **Retract bad feedback, don't counter it.** If you accidentally marked a good memory as "not useful", retract that entry rather than adding a compensating "useful" signal. Retraction removes the mistake cleanly; countering it leaves both entries in the history.
- **Replay problem queries after fixing them.** When you archive or edit a memory to address a problem query, use the Replay link to verify the fix. The query runs against the current state of the store, so you can confirm improvement immediately.
- **Archive stale memories rather than deleting them.** Archival is reversible. If a stale memory turns out to be useful after you rewrite it or adjust its source prefix, you can restore it from the Archive view.
- **Let query enrichment work for you.** The smarter query construction in hooks requires no configuration. If you notice recall improving for queries involving specific files or code patterns, that is the file context and key term extraction doing its job.
