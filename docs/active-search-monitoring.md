# Active Search Monitoring

Use this after the enterprise-readiness rollout to check whether agents are
actually using Memories the way the active-search eval expects.

## What Gets Logged

Claude Code and Codex hooks, plus the OpenCode plugin, append local JSONL telemetry to:

```bash
~/.config/memories/active-search.jsonl
```

Override the path with:

```bash
MEMORIES_ACTIVE_SEARCH_LOG=/path/to/active-search.jsonl
```

Disable local telemetry with:

```bash
MEMORIES_ACTIVE_SEARCH_METRICS=0
```

The log is metadata-only. Claude Code and Codex hooks can store prompt and tool metadata:

- timestamp
- client (`claude-code`, `codex`, or `opencode`)
- session id
- project basename
- SHA-256 prompt hash
- whether the hook classified the prompt as requiring active search
- candidate count and candidate source prefixes
- candidate memory ids surfaced for the prompt (`candidate_ids`, numeric ids only)
- memory tool name
- memory tool source prefix
- source-prefix quality (`exact_project`, `broad_or_unscoped`, or `other`)
- memory ids touched by a memory tool call (`memory_ids`, numeric ids only)

`prompt_evaluated` events are emitted for every active-search-required prompt
and for every prompt where the hook injected candidate memories, so
surfaced-vs-subsequently-used is derivable per session. Candidate ids appear
in the local telemetry log only — active-search-required hook context still
never exposes memory ids to the model.

OpenCode currently logs memory tool-call telemetry only (`event: "tool_call"`, including `memory_ids` derived from tool arguments) through its plugin. It does not emit `prompt_evaluated` events yet, so prompt classification, candidate-count, follow-up-rate, passive-risk, and surfaced-vs-used metrics are Claude/Codex hook metrics only for now.

Note on the playbook gate: the UserPromptSubmit hooks inject the full directive
playbook only when retrieval returned at least one candidate memory or the
prompt is prior-work-shaped; other prompts get at most a 1-2 line reminder.
This does not change what gets logged. `prompt_evaluated` events are still
emitted for every active-search-required prompt, and a required prompt with
`candidate_count: 0` / `hook_results_injected: false` now still carries the
full mandate (without a Retrieved Memories block), so its follow-up
`memory_search` is expected as before.

It does not store prompt text, memory text, retrieved snippets, or API keys.

## Check Active Search Health

Run:

```bash
.venv/bin/python scripts/active_search_metrics.py \
  --log ~/.config/memories/active-search.jsonl \
  --followup-window-seconds 300
```

Key fields:

- `active_search_followup_rate`: fraction of required Claude/Codex hook prompts followed by `memory_search` within the window. OpenCode is excluded until it emits `prompt_evaluated` events.
- `passive_risk_prompts`: required Claude/Codex hook prompts with no observed follow-up `memory_search`. OpenCode is excluded until it emits `prompt_evaluated` events.
- `exact_project_searches`: `memory_search` calls scoped to the active project
  family, such as `codex/memories` or `learning/memories`.
- `broad_or_unscoped_searches`: broad family or unscoped searches, such as
  `codex/` or an empty source prefix.
- `by_client`: split between Codex, Claude Code, and OpenCode where event types are available. OpenCode currently contributes `tool_call` events only.

Expected enterprise-ready behavior for Claude/Codex hook clients:

- `active_search_followup_rate` should be close to 1.0.
- `passive_risk_prompts` should stay near 0.
- `broad_or_unscoped_searches` should be rare and should generally appear only
  after exact project prefixes were tried.

## Recall Feedback Loop

Recalled-but-never-used memories should lose rank over time. The loop has
three stages, all derived from the same JSONL telemetry:

1. **Capture** — `memory-query.sh` logs which candidate memory ids were
   surfaced per prompt (`prompt_evaluated.candidate_ids`); the PostToolUse
   observer logs which ids each memory tool call touched
   (`tool_call.memory_ids`, from tool input ids plus the unambiguous `id=N` /
   `(id: N)` markers in tool responses).
2. **Judge** — a surfaced candidate counts as *used* when a follow-up memory
   tool call in the same session touches its id within the follow-up window
   (default 300s; delete tools never count). It counts as *ignored* once the
   window closes without use. Prompts whose window is still open are never
   judged.
3. **Apply** — `scripts/apply_memory_feedback.py` turns per-memory
   used/ignored tallies into relevance feedback through the existing
   `POST /search/feedback` mechanism (the same endpoint `memory_is_useful`
   uses). Search ranking already consumes this signal via `feedback_weight`
   (default 0.1, only positive net scores boost), so used memories climb and
   chronically ignored ones stop getting boosted.

### Apply feedback (dry-run by default)

```bash
.venv/bin/python scripts/apply_memory_feedback.py \
  --log ~/.config/memories/active-search.jsonl \
  --window-seconds 300 \
  --min-ignored 2

# apply for real (writes feedback + advances the cursor)
.venv/bin/python scripts/apply_memory_feedback.py --execute
```

Behavior:

- **Dry-run by default.** Nothing is written until `--execute` is passed.
- **One aggregated signal per memory per run**: any use in the judged window
  posts one `useful`; `--min-ignored` (default 2) closed-window ignores with
  zero uses post one `not_useful`. Below-threshold tallies are reported under
  `no_action`.
- **Idempotent.** An event cursor file
  (`~/.config/memories/feedback-cursor.json`, override with `--cursor`)
  records the last judged prompt event; re-running over the same log never
  double-applies. The cursor only advances on successful `--execute` runs, so
  failed windows are retried.
- **Bounded.** `--max-actions` (default 200) aborts the run without posting
  if a window would generate more feedback than expected.
- Backend target comes from `MEMORIES_URL` / `MEMORIES_API_KEY` (or `--url` /
  `--api-key`). Feedback rows carry `search_id=feedback-loop:<run-ts>` so they
  can be audited or retracted via `/search/feedback/history`.

### Prune report (review only)

```bash
.venv/bin/python scripts/active_search_metrics.py \
  --log ~/.config/memories/active-search.jsonl \
  --prune-report --prune-min-surfaced 3 --prune-limit 20
```

Lists the top chronically-surfaced-never-used memories as REVIEW candidates
(`memory_id`, surfaced/ignored counts, last surfaced timestamp, projects).
This is a report only — nothing is archived or deleted automatically. Inspect
each id with `memory_get` before acting. The default summary also reports
`candidate_surfacings`, `candidates_used`, and `candidates_ignored` so you
can watch overall hook-recall precision.

## Generic MCP Clients

Generic MCP clients do not have prompt/tool telemetry hooks, so the server
cannot know which prompts should have searched. OpenCode is not generic MCP-only
after plugin setup because its plugin provides prompt-time recall context and
memory tool-call telemetry, but it does not yet log prompt classification events.
For clients without comparable prompt telemetry, use server metrics to check
actual memory tool usage:

```bash
curl -s -H "X-API-Key: $MEMORIES_API_KEY" \
  "$MEMORIES_URL/usage?period=7d" | jq '.operations.search'

curl -s -H "X-API-Key: $MEMORIES_API_KEY" \
  "$MEMORIES_URL/metrics/search-quality?period=7d" | jq

curl -s -H "X-API-Key: $MEMORIES_API_KEY" \
  "$MEMORIES_URL/metrics/temporal-search?period=7d" | jq
```

For generic MCP adoption, watch search volume, search quality feedback,
temporal-search usage, and any application-level user corrections.
