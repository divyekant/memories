# PR 67 Review Closure: 25 Logic Items

Date: 2026-05-04
Branch: `enterprise/memories-enterprise-ready`
PR: #67

This note tracks the 25 review items from the PR logic review and how each was closed.

| # | Review Item | Closure |
|---|---|---|
| 1 | `older_conflicting_memories` did not detect conflicts | Fixed. Added honest `older_evidence` output and made MCP prefer it; retained legacy key as a compatibility alias. |
| 2 | Undated current candidate collapsed bucketing | Fixed. Dated memories are no longer labeled supporting when the current candidate is undated; they are separated as `dated_unranked`. |
| 3 | `is_latest` outranked recency | Fixed. Latest/current queries now rank dated recency before stale `is_latest`; non-temporal ranking does not let `is_latest` hide newer evidence. |
| 4 | Follow-up queries duplicated words | Fixed. Follow-up queries are normalized and de-duplicated. |
| 5 | `memory_timeline` travel broadener was brittle | Fixed. Query expansion now always preserves the original query and adds a generic user-confirmed dated-event variant; travel synonyms are query-preserving only. |
| 6 | Undated memories sorted to end of chronological output | Fixed. Undated memories sort to the top as an explicit unknown-date group instead of looking most recent at the bottom. |
| 7 | `user_facts_only` only matched raw `user:` transcript text | Fixed. Cleaned extracted memories without assistant transcript markers are accepted; empty text and assistant-only transcript text are excluded. |
| 8 | Timeline multi-variant fan-out was sequential | Fixed. Timeline search variants use `Promise.all`. |
| 9 | Passive-hook-only detection required all terms | Fixed. Passive failure detection fires when any expected term leaks without `memory_search`. |
| 10 | Empty expected answer terms were double-handled | Fixed. Empty expected terms now consistently mean answer-term proof is not required. |
| 11 | Codex tool-name matching was substring-based | Fixed. Codex trace parsing uses the same exact/`__memory_search` matcher as the scorer. |
| 12 | Active-search trigger regex was narrow and eval-tuned | Fixed. Trigger patterns were broadened for remember/recall/how-did-we/plan/follow-up/prior-work phrasing, and eval cases were expanded with less-leading prompts. |
| 13 | Hook breadcrumb allowed `memory_get` bypass | Fixed. Active-search-required hook output no longer exposes memory IDs and explicitly says `memory_get` is not a substitute; scorer keeps `memory_get` non-compliant. |
| 14 | Hook fan-out could exceed 3s timeout | Fixed. UserPromptSubmit timeout is now 10s in product configs and eval installers; `memory-query.sh` runs unscoped, scoped, and intent-biased searches concurrently. |
| 15 | Telemetry over-credited batched prompts | Fixed. Metrics correlate each memory search to at most one recent unmatched prompt. |
| 16 | Telemetry write failures were silent | Fixed. Metrics logging writes warnings to `MEMORIES_LOG` when the metrics log cannot be created or written. |
| 17 | System eval could silently run without MCP | Fixed. Claude Code and Codex eval runners now fail loudly when memories are required but MCP config is missing. |
| 18 | Eval hook env file could leak key on disk | Fixed. Claude Code eval hook env file is unlinked in `finally`; temp project cleanup remains the outer cleanup. |
| 19 | Agent retries were not surfaced in LongMemEval output | Fixed. Per-question records include `retried` and `first_error_kind`. |
| 20 | Single-mode retry skipped project reset | Fixed. Single-mode system eval now owns an isolated project, resets it before retry, and cleans it up after completion. |
| 21 | Local production ports were hardcoded | Fixed. `EVAL_LOCAL_PRODUCTION_PORTS` can add local production ports to reject. |
| 22 | Unknown judge providers passed preflight | Fixed. Unknown judge providers are rejected with supported-provider guidance. |
| 23 | Codex eval env was not stripped like Claude Code | Fixed. Codex eval runner uses the shared `AGENT_ENV_BLOCKLIST`. |
| 24 | Bash indirect expansion required newer bash | Fixed. YAML env reference fallback now uses `printenv`, avoiding bash 4 indirect expansion. |
| 25 | Active-search eval set was tiny and leading | Fixed. Case set expanded from 4 to 7 with additional remember/plan/cross-client prompts and a stricter unnecessary-search control gate. |

No review item is left as intentionally unfixed. Compatibility aliases remain where removing existing response keys would break clients.
