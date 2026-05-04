# Memories Enterprise Readiness Audit - 2026-05-04

Scope: previous 30 days of local Codex and Claude Code sessions related to `/Users/dk/projects/memories`.

Transcript handling: this audit summarizes local session evidence and cites session ids only. It does not copy private transcript content.

## Session Evidence

- Codex sessions inspected: 10 Memories-related sessions from the last 30 days.
- Claude Code sessions inspected: 74 Memories-related JSONL files under Claude project storage, including subagent traces.
- Relevant Codex session ids included `019d56af-a4d8-7f92-96dd-d95aaa028339`, `019d700b-ef21-7a81-ad3b-29ddf4f95073`, `019dd193-2565-74d0-b019-49bd04fd27a8`, `019df094-5e32-78c3-831a-e733e73940b4`, and `019df0b8-c431-7433-a0fe-abd88203612b`.
- Relevant Claude session ids included sessions covering temporal reasoning, graph/temporal decisioning, Codex plugin setup, extraction auth, progressive disclosure, scoped conflicts, and shadow extraction evaluation.

## Gaps Closed In This Branch

- Temporal MCP anchoring: `memory_search` and `memory_evidence` now accept and forward `reference_date`, and system eval prompts tell agents to use it for relative temporal questions.
- System eval proof: LongMemEval system artifacts now retain setup validation, ready-before/after status, agent answer excerpts, answer length, error kind, raw recall top sessions, and parsed Claude stream-json tool-call traces.
- Eval target safety: eval URL resolution ignores ambient `MEMORIES_URL` and defaults to the isolated eval target unless `EVAL_MEMORIES_URL` is explicitly set. Non-local eval targets are rejected by default.
- Eval credential safety: trusted eval entry points require `MEMORIES_API_KEY` and record only key presence, never the key value.
- Eval flake resistance: add-batch seeding retries retryable 5xx/transport failures, and system-agent infrastructure failures get one bounded retry before scoring.
- MCP progressive disclosure: generic MCP clients can call `memory_search` with `compact=true`, then call `memory_get` for full text by id.
- Scoped conflict safety: `CONFLICT` extraction actions now verify `old_id` is inside allowed source prefixes before creating conflict metadata.
- Generic MCP compatibility: stdio smoke now validates `memory_search`, `memory_get`, `memory_evidence`, `memory_count`, and read-only behavior.

## Verification Evidence

- Full Python suite: `uv run pytest -q` -> 1323 passed, 1 local-Qdrant warning.
- MCP syntax: `node --check mcp-server/index.js` -> exit 0.
- MCP generic smoke: `npm run smoke` -> `generic_mcp_stdio_smoke=ok`.
- Dependency audit: `npm audit --json` -> 0 vulnerabilities.
- Whitespace: `git diff --check` -> exit 0.
- Trusted temporal system eval: `eval/results/longmemeval-enterprise-temporal-20q-system-proof-trace-retry.json`.
  - `overall`: 0.855
  - `recall_any_at_5`: 1.0
  - `questions_run`: 20
  - `workers`: 2
  - `eval_ready_before`: `qdrant_count=0`, `metadata_count=0`
  - `eval_ready_after`: `qdrant_count=0`, `metadata_count=0`
  - service log check: no `ERROR`, `500 Internal`, `Traceback`, `Search failed`, `unexpected keyword`, or `Server disconnected` entries during the final trusted run window.

## Remaining Release Note

Do not merge this branch to main/master until release review. The current branch is intended to stay as draft PR work until final release approval.
