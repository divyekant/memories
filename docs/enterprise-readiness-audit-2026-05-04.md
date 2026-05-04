# Memories Enterprise Readiness Audit - 2026-05-04

Scope: previous 30 days of local Codex and Claude Code sessions, with Memories-specific turn-level usefulness classification for user asks, agent searches, retrieved memories, answer use, passive-hook-only behavior, and next-user correction signals.

Transcript handling: this audit summarizes local session evidence and cites session ids only. It does not copy private transcript content.

## Session Evidence

- Local session files scanned: 41 Codex JSONL files, 2,195 Claude Code JSONL files, and filtered Claude local-app session descriptors since 2026-04-04.
- Direct Memories MCP calls found: 243 Codex calls across 16 sessions and 758 Claude Code calls across 104 sessions.
- Turn-level parent direct-read classifications: 180 useful/direct, 5 useful after fallback, 22 empty or missed/no data, 18 harmful/error, 15 retrieved but not obviously used, and 2 likely not useful or wrong.
- Passive-hook-only behavior dominated: 2,126 parent turns had hook-injected memory context but no direct memory read. The highest-priority product gap was active-search reinforcement, not raw search transport.
- Historical write-path gap: one Claude Code fplguru worktree session showed 7 MCP write/read failures (`memory_add`/`memory_extract` 422s and one `memory_search` 405), which requires isolated MCP write-path regression coverage.
- Cross-client namespace gap: Codex and Claude Code defaulted to separate `codex/{project}` and `claude-code/{project}` search families, causing useful fallback behavior but missed first-pass recall when history lived under the sibling client prefix.

## Gaps Closed In This Branch

- Temporal MCP anchoring: `memory_search` and `memory_evidence` now accept and forward `reference_date`, and system eval prompts tell agents to use it for relative temporal questions.
- System eval proof: LongMemEval system artifacts now retain setup validation, ready-before/after status, agent answer excerpts, answer length, error kind, raw recall top sessions, and parsed Claude stream-json tool-call traces.
- Eval target safety: eval URL resolution ignores ambient `MEMORIES_URL` and defaults to the isolated eval target unless `EVAL_MEMORIES_URL` is explicitly set. Non-local eval targets are rejected by default.
- Eval credential safety: trusted eval entry points require `MEMORIES_API_KEY` and record only key presence, never the key value.
- Eval judge proof: setup validation can require judge credentials and records judge-provider presence without leaking secrets.
- Eval auth isolation: Claude Code system eval subprocesses strip judge/model-provider API keys while keeping eval-scoped Memories credentials, preventing external API key contamination.
- Eval flake resistance: add-batch seeding retries retryable 5xx/transport failures, and system-agent infrastructure failures get one bounded retry before scoring.
- MCP progressive disclosure: generic MCP clients can call `memory_search` with `compact=true`, then call `memory_get` for full text by id.
- MCP temporal timeline: generic MCP clients can call `memory_timeline` for chronological evidence. It supports user-fact filtering and travel/event query expansion so agents can separate user-confirmed events from assistant-only plans.
- Scoped conflict safety: `CONFLICT` extraction actions now verify `old_id` is inside allowed source prefixes before creating conflict metadata.
- Generic MCP compatibility: stdio smoke now validates `memory_search`, `memory_get`, `memory_evidence`, `memory_timeline`, `memory_count`, and read-only behavior.
- Generic MCP write compatibility: isolated fake-backend stdio smoke now validates `memory_add` and `memory_extract` routing, payload shape, document timestamps, extraction polling, and Codex-authored source hygiene without touching production.
- Active-search behavior eval: `eval/run_active_search_eval.py` runs realistic prompts that do not say `memory_search`, installs this worktree's read hooks/instructions into isolated Claude Code projects and isolated Codex homes, captures stream-json tool traces, scores active search, source-prefix quality, answer use, passive-hook-only failures, and no-memory controls.
- Active-search hook reinforcement: `SessionStart`, `UserPromptSubmit`, and post-compaction MEMORY.md sync now use candidate pointers instead of full memory text for prior-context prompts so agents must call `memory_search` for details.
- Active-search source hygiene: hook playbooks, Codex developer instructions, and installer output now tell agents to search exact project-scoped prefixes from candidate pointers before broad family prefixes or unscoped search.
- Cross-client source defaults: Claude Code now searches `claude-code/{project},codex/{project},learning/{project},wip/{project}` by default; Codex searches `codex/{project},claude-code/{project},learning/{project},wip/{project}` by default. Extraction still writes to the active client prefix.

## Why Prior Evals Missed Active-Search Failures

- Retrieval/tool evals call `/search` directly, so they measure whether the engine can retrieve evidence after search is invoked. They do not test whether an agent chooses to search.
- LongMemEval system mode explicitly tells the agent to use Memories tools and gives the source prefix. That evaluates answer quality and temporal reasoning after the search gate is already opened.
- Real product failures happen earlier: a user asks a context-dependent question, hooks inject relevant memory text, and the agent answers from passive context without making an auditable `memory_search` call.
- The active-search eval now targets that missing gate by using realistic prompts that do not mention `memory_search`, installing this worktree's read hooks/instructions into isolated Claude Code projects and isolated Codex homes, and failing when required-search turns produce passive-hook-only answers or broad/unscoped source-prefix use.

## Verification Evidence

- Full Python suite: `.venv/bin/pytest -q` -> 1346 passed, 1 local-Qdrant warning.
- MCP syntax: `node --check mcp-server/index.js` -> exit 0.
- MCP generic smoke: `npm run smoke` -> `generic_mcp_stdio_smoke=ok`.
- MCP generic write smoke: `npm run smoke:write` -> `generic_mcp_write_smoke=ok`.
- Claude Code active-search behavior eval: `eval/results/active-search-enterprise-20260504-claude-after-source-guidance.json` (local ignored artifact).
  - `active_search_rate`: 1.0 over 3 required-search cases
  - `passive_hook_only_failures`: 0
  - `wrong_source_prefix_failures`: 0
  - `unnecessary_memory_searches`: 0 for the no-memory control
  - `overall_active_search_score`: 1.0
  - `ready_before`: `qdrant_count=0`, `metadata_count=0`
  - `ready_after`: `qdrant_count=0`, `metadata_count=0`
  - `setup_validation`: target URL, API key presence, MCP path, and Claude CLI presence recorded as OK
- Codex active-search behavior eval: `eval/results/active-search-enterprise-20260504-codex-after-source-guidance.json` (local ignored artifact).
  - `active_search_rate`: 1.0 over 3 required-search cases
  - `passive_hook_only_failures`: 0
  - `wrong_source_prefix_failures`: 0
  - `unnecessary_memory_searches`: 0 for the no-memory control
  - `overall_active_search_score`: 1.0
  - `ready_before`: `qdrant_count=0`, `metadata_count=0`
  - `ready_after`: `qdrant_count=0`, `metadata_count=0`
  - `setup_validation`: target URL, API key presence, and MCP path recorded as OK
- Dependency audit: `npm audit --json` -> 0 vulnerabilities.
- Whitespace: `git diff --check` -> exit 0.
- Trusted temporal system eval: `eval/results/longmemeval-enterprise-temporal-20q-system-timeline-env-isolated.json`.
  - `overall`: 0.9515
  - `recall_any_at_5`: 1.0
  - `questions_run`: 20
  - `workers`: 2
  - `agent_timeout_seconds`: 180
  - `error_counts`: 20 with no agent error kind
  - `low_scores`: none below 0.95
  - `memory_timeline` tool calls observed: 17
  - `setup_validation`: target URL, API key presence, MCP path, and judge provider all recorded as OK
  - `eval_ready_before`: `qdrant_count=0`, `metadata_count=0`
  - `eval_ready_after`: `qdrant_count=0`, `metadata_count=0`
  - service log check: no `ERROR`, `500 Internal`, `Traceback`, `Search failed`, `unexpected keyword`, or `Server disconnected` entries during the final trusted run window.

## Remaining Release Note

Do not merge this branch to main/master until release review. The current branch is intended to stay as draft PR work until final release approval.
