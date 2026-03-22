# Feature Handoff: R2 Retrieval Confidence

**Release:** v3.2.0
**Date:** 2026-03-22
**Branch:** `feat/r2-retrieval-confidence`
**Batches:** B1 (Feedback Engine), B2 (Smart Queries), B3 (Operator Views)

---

## Overview

R2 closes the feedback loop in Memories. Prior to this release, search feedback (`useful` / `not_useful` signals) was collected but never influenced search ranking. With R2, feedback becomes a live ranking signal: memories that operators mark as useful rank higher in future searches. The release also makes hook-side query construction smarter (extracting file context and code identifiers from conversations) and gives operators two new diagnostic views on the Health page for monitoring search quality.

All changes are additive. No existing API contracts are broken. No Qdrant schema changes were made -- feedback data lives entirely in SQLite (`usage.db`).

---

## B1: Feedback Engine

### Feedback as a Ranking Signal

Feedback is now the 4th signal in Reciprocal Rank Fusion (RRF) hybrid search, alongside vector similarity, BM25 keyword matching, and recency decay.

**How it works:**

1. When a hybrid search runs with `feedback_weight > 0`, the system batch-fetches net feedback scores (count of `useful` minus count of `not_useful`) for all candidate memories from `search_feedback` in SQLite.
2. Only memories with a **net positive** score participate in the feedback RRF ranking. Memories with zero or negative net feedback receive no feedback contribution -- they are neutral, not penalized.
3. Positive-feedback memories are ranked by net score descending and contribute to the final RRF score: `feedback_weight * (1.0 / (rank + rrf_k))`.
4. All signal weights are scaled to sum to 1.0. The scaling formula:
   - `total_auxiliary = min(feedback_weight + confidence_weight, 1.0)`
   - `total_core = 1.0 - total_auxiliary`
   - Vector and BM25 weights are derived from `vector_weight * total_core * (1.0 - recency_weight)`

**Default behavior:**

| Surface | Default `feedback_weight` | Effect |
|---------|--------------------------|--------|
| API (`POST /search`) | `0.0` | Feedback ranking **disabled** by default on the API |
| MCP (`memory_search` tool) | `0.1` | Feedback ranking **enabled** at 10% weight for agent callers |
| Hooks | Whatever the API call specifies | Hooks call `POST /search` directly |

This means existing direct API consumers see no ranking change unless they explicitly opt in. MCP-connected agents (Claude Desktop, Claude Code via MCP) get feedback ranking out of the box.

### Feedback History Endpoint

Operators can inspect the feedback trail for any memory.

- **Endpoint:** `GET /search/feedback/history?memory_id={id}&limit=50`
- **Auth:** Scoped keys can only view feedback for memories within their source prefix. Admin keys see all.
- **Response:** `{"entries": [{id, ts, memory_id, query, signal, search_id}, ...], "count": N}`

### Feedback Retraction

Operators can remove incorrect or accidental feedback entries.

- **Endpoint:** `DELETE /search/feedback/{feedback_id}`
- **Auth:** Admin only. Scoped keys receive 403.
- **Response:** `{"status": "retracted", "id": feedback_id}`
- **Audit:** Every retraction is logged as a `feedback.retracted` audit event with the feedback ID as the resource.
- **404:** Returned if the feedback ID does not exist.

### Feedback in the Lifecycle Tab (UI)

The Lifecycle tab in the memory detail panel now includes a "Feedback" section showing:

- Each feedback entry with a badge (`useful` in green, `not_useful` in red)
- The triggering query (truncated to 60 characters)
- Relative timestamp
- A "Retract" button per entry (calls `DELETE /search/feedback/{id}`, refreshes the section, shows a toast)

If no feedback exists, the section shows "No feedback yet". If the endpoint is unreachable, it shows "Feedback unavailable".

---

## B2: Smart Queries

### Hook Query Enrichment

The `memory-query.sh` hook (fired on `UserPromptSubmit` in Claude Code) now constructs richer search queries using three new extraction steps. All processing happens in bash -- no LLM calls, no backend changes.

**1. File context extraction**

The hook reads the last 20 lines of the conversation transcript, extracts filenames from `Read`, `Edit`, and `Write` tool calls, and prepends up to 5 unique filenames as `Files: auth.py, config.ts, ...` to the query. This helps the search engine surface memories related to the files being actively worked on.

**2. Key term extraction**

The hook scans the user's prompt for code identifiers:
- `CamelCase` patterns (e.g., `SearchRequest`, `UsageTracker`)
- `snake_case` patterns (e.g., `get_feedback_scores`, `memory_id`)
- `SCREAMING_CASE` patterns (e.g., `API_KEY`, `MEMORIES_URL`)

Up to 10 unique terms are prepended as `Terms: SearchRequest, get_feedback_scores, ...`.

**3. Intent-based prefix biasing**

The hook detects the intent from the first word of the prompt and adds extra source prefixes to search:

| Prompt starts with | Extra prefixes searched |
|-------------------|----------------------|
| `fix`, `debug`, `error`, `bug`, `broken`, `crash` | `learning/{project}`, `bug-fix/{project}` |
| `how`, `setup`, `configure`, `install` | `decision/{project}`, `learning/{project}` |

These are searched **in addition to** the standard prefix list, not instead of it.

### Proactive Deferred-Work Surfacing

The `memory-recall.sh` hook (fired on `SessionStart` in Claude Code) now runs an additional search for deferred/WIP items at the start of every session.

**How it works:**

1. After the standard per-prefix recall searches, the hook searches `wip/{project}` with the query `"deferred incomplete blocked todo revisit wip"` (k=5, threshold=0.3).
2. If results are found, a `## Deferred Work` section is injected into the session context before the Memory Playbook, formatted as a bulleted list with source and 150-character text preview.
3. If no WIP items exist for the project, the section is silently omitted.

### `memory_deferred` MCP Tool

A new MCP tool lets agents explicitly query for deferred work items.

| Property | Value |
|----------|-------|
| **Tool name** | `memory_deferred` |
| **Description** | List deferred/WIP memories for a project. Surfaces incomplete threads from `wip/{project}` source prefix. |
| **Parameters** | `project` (string, required): Project name. `k` (int, 1-20, default 5): Number of results. |
| **Behavior** | Searches `POST /search` with `source_prefix: "wip/{project}"`, hybrid mode, query `"deferred incomplete blocked todo revisit wip"`. |
| **Output** | Formatted text listing each result with index, source, and full text. Returns "No deferred work found" if empty. |

---

## B3: Operator Views

### Problem Queries View (Health Page)

A new section on the Health page surfaces queries that consistently produce unhelpful results.

**Endpoint:** `GET /metrics/problem-queries`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_feedback` | int | 2 | Minimum total feedback entries for a query to qualify |
| `limit` | int | 20 | Maximum results |

**Auth:** Admin only.

**Logic:** Groups `search_feedback` rows by query text. A query is "problematic" if it has at least `min_feedback` entries AND at least 50% of those entries are `not_useful`. Results are sorted by `not_useful` count descending.

**Response:** `{"queries": [{"query": "...", "total": N, "not_useful": N, "ratio": 0.XX}, ...]}`

**UI:** Rendered as a table on the Health page with columns for the query text, negative ratio (as a badge), and a "Re-search" link. The Re-search link navigates to `#/memories?q={query}` so operators can see what results the query produces now, after making corrections (archiving bad memories, editing content, etc.).

If no problem queries exist, an empty state shows "No problem queries -- All queries are performing well."

### Stale Memories View (Health Page)

A new section on the Health page surfaces memories that are frequently retrieved but never marked useful.

**Endpoint:** `GET /metrics/stale-memories`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_retrievals` | int | 3 | Minimum retrieval count to qualify |
| `limit` | int | 20 | Maximum results |

**Auth:** Admin only.

**Logic:** Joins `retrieval_log` with `search_feedback` by `memory_id`. A memory is "stale" if it has at least `min_retrievals` retrievals AND zero `useful` feedback entries. Results are sorted by retrieval count descending.

**Response:** `{"memories": [{"memory_id": N, "retrievals": N, "useful": 0, "not_useful": N}, ...]}`

**UI:** Rendered as a table on the Health page with memory ID, retrieval count, and feedback counts. Each row has:
- **Archive** button: Archives the memory (PATCH to archived state)
- **View** button: Navigates to the memory detail view

If no stale memories exist, an empty state shows "No stale memories -- All frequently retrieved memories have positive feedback or no feedback yet."

### Search URL Parameter Support

The Memories page now supports a `?q=` parameter in the URL hash for replay navigation.

- **Format:** `#/memories?q={encoded_query}`
- **Behavior:** When the Memories page loads with a `?q=` parameter, it automatically populates the search box and executes the search.
- **Primary use case:** The "Re-search" links on the Problem Queries view use this to let operators quickly replay a problematic query and verify that changes have improved results.

---

## API Endpoints Reference

All R2 endpoints in one table:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/search` | Any valid key | Search memories. New `feedback_weight` param (float, 0.0-1.0, default 0.0). |
| `POST` | `/search/explain` | Admin | Search with scoring breakdown. Same new `feedback_weight` param. |
| `POST` | `/search/feedback` | Scoped to memory source | Record `useful`/`not_useful` feedback. Pre-existing, unchanged. |
| `GET` | `/search/feedback/history?memory_id={id}&limit=50` | Scoped to memory source | List feedback entries for a memory. **New in R2.** |
| `DELETE` | `/search/feedback/{feedback_id}` | Admin only | Retract a feedback entry. **New in R2.** |
| `GET` | `/metrics/problem-queries?min_feedback=2&limit=20` | Admin only | Queries with consistently negative feedback. **New in R2.** |
| `GET` | `/metrics/stale-memories?min_retrievals=3&limit=20` | Admin only | Frequently retrieved, never-useful memories. **New in R2.** |

### MCP Tools Reference

| Tool | Parameters | Description |
|------|-----------|-------------|
| `memory_search` | `feedback_weight` (float, 0-1, default 0.1) added | Existing tool, new parameter. |
| `memory_deferred` | `project` (string, required), `k` (int, 1-20, default 5) | **New in R2.** Query WIP items for a project. |

---

## Common Questions

### 1. Does feedback ranking change results for existing API consumers?

No. The `feedback_weight` defaults to `0.0` on the API, which means feedback has zero influence on ranking for any caller that does not explicitly set this parameter. Existing integrations see identical results. Only the MCP server defaults to `0.1`, so agent callers via MCP will see feedback-influenced results by default.

### 2. Can negative feedback push a memory lower in results?

No. The design is strictly "boost only." Memories with zero or negative net feedback receive no feedback RRF contribution -- they rank exactly as they would without feedback. There is no penalty mechanism. The rationale: at the current scale (~9,500 memories), a few negative signals should not suppress a memory that might be useful in a different context.

### 3. Who can retract feedback? Can a scoped key retract its own feedback?

Only admin keys can retract feedback (`DELETE /search/feedback/{id}`). Scoped keys cannot retract feedback, even for memories within their scope. This is a deliberate design choice to prevent feedback manipulation. If a scoped-key user reports incorrect feedback, an admin must retract it through the UI (Lifecycle tab > Feedback section > Retract button).

### 4. What happens if `feedback_weight + recency_weight` is very high (e.g., 0.9)?

The vector and BM25 signals will be compressed to just 10% of the total score. The system allows this -- it is treated as a deliberate operator choice. However, in practice this means search becomes almost entirely feedback-and-recency driven, with very little semantic matching. There is no guard rail against this combination. If operators report poor search quality, check these weight settings first.

### 5. How does the query enrichment in hooks affect search quality?

The hook enrichments (file context, key terms, intent prefixes) add extra context to the search query that gets embedded and matched against memory embeddings. This should improve recall for context-specific memories (e.g., finding a bug fix related to the file you are currently editing). However, the enrichments can also add noise if the extracted terms are not relevant. The hook limits extraction to 5 files and 10 terms to mitigate this. If operators notice irrelevant results, the enrichment logic in `memory-query.sh` can be tuned.

### 6. When does the deferred-work surfacing happen?

Only at `SessionStart` -- the very beginning of a Claude Code session. It does not fire on every prompt. The `memory-recall.sh` hook runs a single extra search against `wip/{project}` and injects any results into the session context. Operators can also query deferred work on demand via the `memory_deferred` MCP tool.

### 7. Are the Health page views (Problem Queries, Stale Memories) visible to scoped keys?

No. Both endpoints (`/metrics/problem-queries`, `/metrics/stale-memories`) require admin authentication. Scoped-key users will not see these sections on the Health page, and direct API calls will return 403.

### 8. What does the "Re-search" link on Problem Queries do?

It navigates to the Memories page with the query pre-filled in the search box: `#/memories?q={encoded_query}`. The search executes automatically on page load. This lets operators see current results for a problematic query after they have made corrections (archiving bad memories, editing content) and verify improvement without manually copying and pasting the query.

---

## Troubleshooting

### Feedback does not seem to affect search ranking

1. **Check `feedback_weight` value.** The API default is `0.0` (disabled). The caller must explicitly pass `feedback_weight > 0`. MCP defaults to `0.1`.
2. **Verify feedback exists.** Call `GET /search/feedback/history?memory_id={id}` to confirm feedback entries are recorded for the memory in question.
3. **Check net score.** Only memories with net positive feedback (more `useful` than `not_useful`) get a ranking boost. A memory with 2 useful and 2 not_useful has a net score of 0 and receives no boost.
4. **Verify hybrid mode.** Feedback ranking only applies to hybrid search (`hybrid: true`). Pure vector search ignores feedback entirely.

### Problem Queries shows unexpected results

1. **Threshold is 2 entries.** A query needs at least 2 feedback entries (default `min_feedback=2`) to appear. Queries with only 1 feedback entry, even if negative, will not show up.
2. **Ratio threshold is 50%.** A query needs at least 50% negative feedback to qualify. A query with 3 useful and 2 not_useful (40% negative) will not appear.
3. **Queries are grouped by exact text.** Slight variations in query wording ("fix auth" vs "fix authentication") are treated as separate queries. The system does not normalize or cluster similar queries.

### Stale Memories shows memories that seem fine

1. **"Never useful" is strict.** A memory appears as stale if it has zero `useful` feedback entries, even if it also has zero `not_useful` entries. Any memory retrieved 3+ times without a single `useful` signal qualifies.
2. **Retrievals are cumulative.** The count includes all-time retrievals, not just recent ones. A memory retrieved 5 times a year ago and never since will still show up.
3. **Archiving removes from future searches.** Using the Archive button on a stale memory moves it to archived state. It will no longer appear in search results (unless `include_archived: true` is passed).

### Deferred work not surfacing at session start

1. **Check source prefix.** Deferred items must have a source starting with `wip/{project}`. If the project uses a different prefix convention, the hook search will miss them.
2. **Check threshold.** The hook uses a similarity threshold of 0.3, which is intentionally low to be permissive. If results still do not appear, verify that WIP memories exist by searching directly: `POST /search` with `source_prefix: "wip/{project}"`.
3. **Check hook installation.** Verify that `memory-recall.sh` is registered as a `SessionStart` hook in `.claude/hooks.json` and that it executes without errors.

### Feedback retraction returns 403

Feedback retraction requires an admin API key. Scoped keys receive 403 regardless of whether the feedback is on a memory within their scope. Use the operator workbench (UI) with admin credentials, or call the endpoint with the admin `X-API-Key`.

---

## Files Changed in R2

| File | Changes |
|------|---------|
| `memory_engine.py` | `feedback_weight` param added to `hybrid_search()` and `hybrid_search_explain()`. Feedback as 4th RRF signal. 5-signal weight scaling. |
| `usage_tracker.py` | New methods: `get_feedback_scores()`, `get_feedback_history()`, `delete_feedback()`, `get_problem_queries()`, `get_stale_memories()`. SQLite index on `search_feedback(memory_id)`. |
| `app.py` | `SearchRequest.feedback_weight` field. New endpoints: feedback history, feedback retraction, problem queries, stale memories. |
| `mcp-server/index.js` | `feedback_weight` param on `memory_search` tool (default 0.1). New `memory_deferred` tool. |
| `webui/app.js` | Feedback section in lifecycle tab. Problem queries and stale memories sections on Health page. Search URL param support (`?q=`). |
| `webui/styles.css` | Styles for feedback history rows, problem query rows, stale memory rows, replay links. |
| `integrations/claude-code/hooks/memory-query.sh` | File context extraction, key term extraction, intent-based prefix biasing. |
| `integrations/claude-code/hooks/memory-recall.sh` | Deferred-work surfacing via `wip/{project}` search at session start. |

---

## Out of Scope (deferred to R3+)

- Auto-archive based on confidence thresholds
- Confidence as a search ranking signal (shipped in v3.4.0 as R3 Wave 4)
- LLM-powered query rewriting (too expensive for per-prompt hooks)
- Background materialization of feedback scores
- Qdrant payload changes for feedback data
- Query normalization or clustering for problem queries
