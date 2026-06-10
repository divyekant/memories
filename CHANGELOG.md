# Changelog

## [Unreleased]

### Added
- **Hook circuit breaker** — after any backend call fails, a breaker file (`~/.config/memories/backend-down`, cooldown `MEMORIES_BREAKER_COOLDOWN`, default 60s) makes every subsequent hook invocation skip backend calls instantly instead of paying full curl timeouts (~8s measured per prompt with a sick backend). One half-open retry after the cooldown; success resets. While open, the UserPromptSubmit hook says "memory backend is unreachable" instead of the misleading "no stored memories matched".
- **`memory_update` MCP tool + `POST /memory/{id}/supersede`** — agents finally have an update verb: the new text replaces the memory, the old version is archived with a supersedes link (pinned memories refuse with 409 until unpinned). Previously the MCP surface was add/delete-only, so even a willing agent could not correct a stale fact deliberately.
- **Dates on search results** — MCP `memory_search` hits now render `[YYYY-MM-DD]` from `document_at`/`created_at`, so agents can discount stale facts at a glance.
- **Conflict-queue drain** — `resolve_conflicts` resolves `conflicts_with` markers under newest-wins: the newer of the pair stays live, the older is **archived** with a `supersedes` link (recoverable, never deleted). Pinned losers and undated pairs stay queued marked `needs_review`; orphaned markers (other side gone or already archived) are cleared. Exposed as `POST /memory/conflicts/resolve` (dry-run by default; unscoped key required) and as a daily scheduled maintenance job (3:30 UTC, `MAINTENANCE_CONFLICT_DRAIN`, capped by `MAINTENANCE_CONFLICT_MAX`, default 200/run). `GET /memory/conflicts` now annotates entries held for review.

### Changed
- **Write doctrine: corrections supersede instead of being eaten** — `supersede()` now ARCHIVES the original (with `superseded_by` pointer and a `supersedes` link from the new memory) instead of hard-deleting it, and adds the new memory first so a crash can never lose data. New `add_with_doctrine` write path: a colliding write (similarity ≥ dedup threshold) supersedes the blocker when the texts differ materially, skips with the blocking id surfaced when near-identical (≥ `DOCTRINE_IDENTICAL_THRESHOLD`, default 0.97), and never touches pinned blockers. `POST /memory/add` gains `on_duplicate: add|skip|supersede` (legacy behavior unchanged when omitted, but dedup skips now report `blocked_by` + a hint). MCP `memory_add` defaults to `on_duplicate=supersede` — "weight is now 79kg" finally updates "weight is 78kg" instead of being dropped as a duplicate.

### Fixed
- **webui "Export All Memories" works** — the button called `/memories?limit=10000`, which the endpoint rejects (limit caps at 5000), so the only export affordance in the UI failed with a 422 every time. It now streams `GET /export` (NDJSON, no cap) and downloads `.ndjson`.
- **Quickstart first command works** — `git clone` uses HTTPS instead of SSH (which fails for anyone without GitHub keys), and the start command uses the standalone `docker-compose.yml` instead of `docker-compose.snippet.yml` (a merge-into-your-own-compose snippet with no standalone services, broken as a first command for ~4 months).
- **`memory_conflicts` is paginated** — `GET /memory/conflicts` takes `limit` (default 50, max 500) and `offset` and reports `total`/`has_more`; the MCP tool defaults to 20 per page. A single unpaginated call used to dump the entire queue (70KB observed) into the agent's context.

## [5.6.0] - 2026-06-10

### Fixed
- **Pruner can no longer destroy pinned or archived memories** — `find_prune_candidates` excludes pinned (operator-protected) and archived (supersede-chain version history) memories; `delete_memory` refuses pinned ids without `force=true` (HTTP 409 from the API); bulk `delete_memories` silently skips pinned ids and reports them as `skipped_pinned`. This is the guard the June 7 prune incident lacked (271 memories hard-deleted, including version history).
- **Backup rotation sorts by mtime with per-prefix retention** — rotation previously sorted by NAME descending, so a `pre_delete` backup could be evicted by the very call that created it while alphabetically-later stale backups survived. Rotation now keeps the N most recent by mtime plus the 2 most recent of every prefix class.
- **Atomic metadata/config save with `.bak` fallback** — `save()` writes tmp + fsync + `os.replace` and refreshes a `.bak` of the previous good file; `load()` falls back to `.bak` on a corrupt `metadata.json` (preserving the corrupt file for inspection) instead of crash-looping.
- **Nightly consolidation actually works now — and safely** — `find_clusters` compared RRF rank-fusion scores (structurally ≤ ~0.017) against a 0.75 cosine threshold, so no cluster ever formed; it now uses vector-only cosine search. `consolidate_cluster` adds the merged memories before deleting originals (originals survive any add failure), rejects unparseable LLM responses instead of storing raw text over deleted memories, and skips clusters containing pinned/archived members.

### Added
- **CI workflow** — pytest, MCP server install + syntax, hook-script `bash -n`, and a Docker image build with an in-image `import app` check (the exact gap that caused the v5.5.1 crash-loop patch).

## [5.5.1] - 2026-06-10

### Fixed
- **Docker image missing new modules** — `embedding_space.py` and `transcript_hygiene.py` were not in the Dockerfile COPY list, so the 5.5.0 image crash-looped at boot (`ModuleNotFoundError`). Both are now shipped; an import-graph check against the COPY list caught no other gaps.

## [5.5.0] - 2026-06-10

### Added
- **`relative_score` on search results** — `/search`, `/search/batch`, `/search/evidence`, and `/search/explain` results now carry `relative_score` (`score / max(score)` of the returned set, `(0, 1]`, `1.0` = top of set). Hybrid `rrf_score` values are Reciprocal Rank Fusion sums (`weight * 1/(rank + 60)` per signal) structurally bounded near `1/60 ≈ 0.017`, so consumers rendering them as percentages showed a useless 0-2%. Ratio-to-top was chosen over min-max so near-tied results don't render as 0%. Normalization is per result set and strictly monotone — ranking order and ties are provably unchanged. Raw `rrf_score`/`similarity` fields and `threshold` semantics (raw vector similarity) are untouched.
- **Modern-embedder upgrade path** — the embedder is now fully env-selectable: `EMBED_BASE_URL`/`EMBED_API_KEY` point the `openai` provider at any OpenAI-compatible endpoint (e.g. oMLX), `EMBED_DIMENSION` declares and validates the vector size, and `EMBED_QUERY_PREFIX`/`EMBED_DOC_PREFIX` support asymmetric prefix models (nomic-embed, arctic-embed). Candidate evaluation is documented in `docs/designs/embedder-upgrade.md` — including the sampled tier-1 A/B (n=120) in which nomic-embed-text-v1.5 scored recall@5 0.942 vs MiniLM's 0.958, failing the +3pt promotion gate. **The default embedder remains all-MiniLM-L6-v2**; this release ships the migration rails, not a cutover.
- **Explicit embedding spaces** — non-default embedders resolve to collections named with model+dimension (`memories__<model>_<dim>d`), and a sidecar registry (`data/embedding_spaces.json`) records each collection's embedding signature; the engine refuses to write vectors into a collection created with a different signature (catching same-dimension model swaps that dimension checks miss). `EMBED_COLLECTION` pins an exact name; `EMBED_ALLOW_SPACE_REBIND=1` opts into in-place rebinds.
- **`scripts/reembed.py`** — blue/green re-embedding migration: builds a new Qdrant collection from existing payload text (resumable cursor state, progress/ETA logging, `--max-rps` rate limiting), `verify` samples old-vs-new top-k neighbor overlap, and `cutover` re-points `EMBED_*` config in an env file only behind `--execute` (dry-run by default, automatic backup, printed rollback values; the source collection is never modified).
- **Recall feedback loop** — surfaced-vs-used telemetry now closes the loop on hook-recalled memories:
  - `memory-query.sh` logs `candidate_ids` on `prompt_evaluated` events for every prompt with injected candidates (local telemetry only; ids still never enter model context for active-search prompts).
  - The PostToolUse observers (Claude Code, Codex) log `memory_ids` touched by each memory tool call, from tool input ids plus unambiguous `id=N` / `(id: N)` response markers; the OpenCode plugin logs argument-derived `memory_ids`.
  - `scripts/apply_memory_feedback.py` — batch applier that tallies per-memory used/ignored over closed follow-up windows and posts `useful`/`not_useful` through the existing `/search/feedback` mechanism (consumed by `feedback_weight` ranking). Dry-run by default, idempotent via an event cursor file, bounded by `--max-actions`.
  - `scripts/active_search_metrics.py --prune-report` — lists chronically-surfaced-never-used memories as REVIEW candidates (report only, never auto-deletes); the summary now includes `candidate_surfacings`, `candidates_used`, and `candidates_ignored`.
  - `memory_search` MCP results now include `id=N` per hit (previously compact/timeline modes only), so follow-up usage is id-derivable and agents can call `memory_get` / `memory_is_useful` directly.
  - Documented the loop in `docs/active-search-monitoring.md`.

- **Transcript hygiene before extraction** — new `transcript_hygiene.clean_transcript()` strips hook-injected context (`<system-reminder>` blocks, `## Retrieved Memories` / `## Relevant Memories` sections, the recall preamble, and `<HookEvent> hook additional context:` blocks) before any extraction LLM call. Applied in `run_extraction()` (two-call, single-call, and training-data capture) and the provider-less fallback path. Fixes the re-ingestion bug where recalled memories injected by hooks were re-extracted as new memories every session (redundant clusters). Transcripts that are pure injection skip the LLM call entirely (`skipped_reason: empty_after_hygiene`). The Claude Code Stop hook additionally drops `<system-reminder>` content items in jq before the per-message clip (defense in depth).
- **Extraction novelty gate** — extraction ADD/FALLBACK_ADD actions now pass an explicit `engine.is_novel()` check before storing; near-duplicates are recorded as `noop` actions with `reason: novelty_gate` and counted in `gated_count`. Controlled by `EXTRACT_NOVELTY_GATE` (default on) and `EXTRACT_NOVELTY_THRESHOLD` (default 0.85, stricter than the 0.90 engine dedup backstop). Fails open on engine errors. Human-approved dry-run commits (`/memory/extract/commit`) bypass the gate.

### Changed
- **Gated per-prompt playbook injection** — the UserPromptSubmit hooks (Claude Code and Codex) now key the full directive memory playbook on prompt SHAPE: prior-work-shaped prompts ("did we", "weren't we", "how does X work", "what version", "is X still", "last time", "resume", "continue", and similar) get the full mandate — including, new, when retrieval returned zero candidates (previously the hook stayed silent exactly when active search mattered most). Prompts that merely matched candidate memories get the Retrieved Memories block with a 2-line preamble instead of the mandate (on real telemetry ~99% of prompts have ≥1 keyword candidate, so candidate-triggered mandates would fire on effectively every prompt). Self-contained prompts with no matches get a 1-line reminder. Mandate wording when injected is unchanged; the gate is exposed as `_playbook_injection_mode` ("full" / "memories" / "minimal") in the hook `_lib.sh` for standalone testing.
- **Stronger default extraction profile** — the DEFAULT profile now ships hygiene rules (`extraction_profiles.DEFAULT_RULES`): always remember decisions + rationale (with until/unless/because boundary conditions), learnings, durable preferences, and deferred work; never remember session narration/running commentary, restated repo code, ephemeral task chatter, or recalled memory text repeated back. Rules now also reach the fact-extraction system prompt (previously AUDN/single-call only). A profile that explicitly sets `rules` (even `{}`) fully replaces the defaults.

### Fixed
- **Worktree sessions now share the repo's memories** — all hooks resolve the project name via the git common dir (`_memories_resolve_project`), so git-worktree checkouts (e.g. Claude Code's `.claude/worktrees/<name>`) recall and capture under the main repo's name instead of a throwaway worktree dir name that made them memory-blind. Non-git directories keep basename behavior.
- **Relevance display no longer shows 0-2% noise for hybrid search** — the MCP server (`memory_search` full + compact, `memory_timeline`), the CLI `search` command, and the web UI now render the set-relative score (`rel NN%`) for hybrid results instead of the raw RRF value, keep absolute percentages for vector `similarity`, and omit the tag entirely against legacy backends without `relative_score`. MCP output includes a legend clarifying that `rel %` is relative to the top result of the search, not an absolute match score.

### Internal / Experimental
- **Shadow extraction fan-out** — opt-in A/B harness that mirrors extraction calls to candidate local models (oMLX/Ollama) and logs JSONL comparisons, without touching the primary path. Inert unless `SHADOW_PROVIDERS` is set; not part of the supported user-facing feature set. Includes `scripts/shadow_compare.py` for offline agreement analysis.

## [5.4.0] - 2026-05-04

### Added
- **First-class OpenCode integration** — installer target, OpenCode MCP config merge, marker-safe Memories skill install, repo-local OpenCode plugin registration, prompt-time recall context, and active-search telemetry with `client=opencode`.
- **Enterprise eval isolation** — eval runners now validate setup before execution, require eval-scoped API keys, reject unsafe production targets by default, and record ready-before/after evidence so eval runs can prove they did not contaminate production data.
- **Active-search behavior evals** — added realistic Codex and Claude Code eval coverage that checks required `memory_search` use, exact project source prefixes, passive-hook-only failures, unnecessary control-case searches, and whether retrieved memory affected the answer.
- **Active-search monitoring** — hooks now emit privacy-safe local JSONL telemetry for required prompts and memory tool calls, with `scripts/active_search_metrics.py` summarizing follow-up rate, passive-risk prompts, exact project searches, and broad/unscoped searches.
- **Temporal evidence surfaces** — added `memory_evidence` and `memory_timeline` support for evidence packets, source/date trails, reference dates, chronological user-fact evidence, and compact MCP retrieval flows.
- **Generic MCP smoke coverage** — added read and write smoke tests for generic MCP clients, including `memory_search`, `memory_get`, `memory_evidence`, `memory_timeline`, `memory_add`, and `memory_extract`.
- **Enterprise audit artifacts** — added the 30-day session audit, active-search monitoring guide, and PR review closure matrix under `docs/`.

### Changed
- Hook recall/search behavior now prefers exact project-scoped prefixes across Claude Code, Codex, learning, and WIP sources before broad family prefixes or unscoped fallback.
- UserPromptSubmit search fan-out now runs unscoped, scoped, and intent-biased searches concurrently, with active-search hook timeout raised to 10 seconds.
- SessionStart guidance now requires active memory search only for prior-work/project-context prompts and explicitly skips self-contained prompts such as arithmetic, translation, formatting, or generic facts.
- Evidence packets now expose honest `older_evidence`, separate dated evidence when the current candidate is undated, prefer dated recency for latest/current queries, and de-duplicate follow-up queries.
- `memory_timeline` query expansion now preserves the original query, includes generic user-confirmed dated-event evidence, accepts cleaned extracted memories for `user_facts_only`, and sorts undated evidence as an explicit unknown-date group.

### Fixed
- Closed the active-search `memory_get` bypass by removing candidate memory IDs from active-search-required hook context and keeping `memory_get` non-compliant in the eval scorer.
- Fixed active-search scorer brittleness around passive-hook-only detection, empty expected answer terms, Codex tool-name parsing, and unnecessary memory searches in control cases.
- Fixed active-search metrics over-crediting by matching each memory search to at most one prompt, and made telemetry write failures visible in the hook log.
- Fixed eval contamination risks by stripping model-provider credentials from Codex and Claude Code eval subprocess environments and failing loudly when required MCP config is missing.
- Fixed LongMemEval retry accounting and single-mode isolation by recording retry metadata, resetting owned temp projects before retry, and cleaning them after completion.
- Fixed setup validation gaps around unknown judge providers and configurable local production ports.
- Replaced bash 4 indirect expansion in hook YAML parsing with `printenv` for macOS bash compatibility.

### Validated

LongMemEval system eval (agent + MCP tools, 20 questions per category, 2 workers, default Sonnet model):

| Category (n=20) | v4.0.0 baseline (full 500q) | v5.4.0 sample | Δ |
|---|---:|---:|---:|
| single-session-user | 87.6% | 91.5% | +3.9pp |
| single-session-assistant | 91.7% | 90.4% | −1.3pp |
| single-session-preference | 74.0% | 84.2% | +10.2pp |
| multi-session | 70.3% | 68.0% | −2.3pp |
| knowledge-update | 80.6% | 82.0% | +1.4pp |
| **temporal-reasoning** | **42.2%** | **85.5%** | **+43.3pp** |
| **Overall (weighted)** | **69.5%** | **83.6%** | **+14.1pp** |

R@5 across all 120 questions: **98.3%**. Retrieval is no longer the bottleneck — gains are from `memory_timeline` + `memory_evidence` + reference-date threading + the active-search hook gate. Multi-session aggregation remains the open gap.

## [5.3.0] - 2026-04-11

### Added
- **Enriched keyword-bag queries** — UserPromptSubmit hook now extracts a keyword-bag (project name, identifiers, version refs, domain nouns) from conversational prompts before searching, stripping filler words that dilute semantic similarity. Tested improvement: 6/10 to 9/10 relevance on real missed-recall prompts.
- **Dual search strategy** — hook searches use two strategies per turn: enriched unscoped (k=6, cross-project) + enriched prefix-scoped (k=3, project-specific), replacing the previous 3x prefix-scoped approach. Catches cross-project context while maintaining project precision.
- **Query intent classifier** — `/search` endpoint detects temporal, comparison, and aggregation intent in queries for smarter routing
- **Temporal intent detection** — search queries with temporal signals (dates, "recent", "last week") are automatically enriched with date-range filters

### Changed
- **Stronger Memory Playbook** — SessionStart hook now injects mandatory recall directives with anti-rationalization table, replacing soft "run memory_search first" language. Pattern matches Anthropic's own memory tool directive style (`IMPORTANT: ALWAYS ... BEFORE`).
- **Stronger CLAUDE.md recall directives** — plugin and global CLAUDE.md "Before responding" sections upgraded with `MUST NOT skip` language and explicit rationalization blockers
- Hook search flow simplified: dropped separate `wip/` and `learning/` prefix-scoped searches (0 results in 95% of cases) in favor of unscoped semantic search that catches all prefix results naturally

## [5.2.0] - 2026-04-07

### Added
- **Auto-feature metrics** — graph search, temporal queries, and auto-linking now tracked in usage analytics
  - `graph_search_events` table tracks activations, graph-influenced result counts, and average graph yield per search
  - `temporal_search_events` table tracks since-only, until-only, and range query usage
  - `links_created` column on `extraction_outcomes` with automatic DB migration
  - `GET /metrics/graph-search` and `GET /metrics/temporal-search` admin endpoints
  - `graph_search` and `temporal_search` sections added to `GET /metrics/quality-summary`
- **Health page UI** — new stat cards (Graph Searches, Temporal Queries, Links Created) and Auto-Features quality panel with detailed breakdowns
- **Extraction eval framework** — `eval/run_extraction_eval.py` and scenario runner for measuring extraction quality

### Fixed
- **Recall hook regression (v5.1.0)** — backend version check killed script with exit code 2 when service unreachable due to unguarded `curl` inside `set -euo pipefail`; now skips version check when health warning already present and guards pipeline with `|| fallback`
- Test fake curl handles GET requests (no POST body) correctly

### Changed
- Extraction training pair collection refactored with richer context (system prompts, AUDN decisions, similar memory payloads)
- Graph search metric counts both pure `graph` and `direct+graph` match types for complete picture of graph influence

## [5.1.0] - 2026-04-04

### Added
- **Claude Code Plugin** — hooks, skills, and CLAUDE.md packaged as a CC plugin in `plugin/` directory with native auto-update via dk-marketplace (#66)
  - SubagentStart recall hook (`memory-subagent-recall.sh`) — injects project memories into Plan, Explore, code-reviewer, and general-purpose subagents at spawn
  - PostToolUse tool observation hook (`memory-tool-observe.sh`) — logs Write/Edit/Bash tool usage to session file for richer extraction context
  - Setup skill (`/memories:setup`) — interactive backend provisioning with Docker, MCP config, and auto-update enforcement
  - Backend version checking in recall hook — warns when running Docker version is behind expected
  - Standalone `docker-compose.standalone.yml` for zero-clone backend deployment
  - Plugin CLAUDE.md with behavioral overrides making memory non-optional
- **Repo-local Codex Plugin** — lightweight Codex plugin at `plugins/memories` with repo marketplace entry in `.agents/plugins/marketplace.json`
  - Reuses the `memories` skill for Codex without creating a second behavior fork
  - Adds a Codex bootstrap skill (`$memories:setup`) that installs `mcp-server` deps and runs the canonical `./integrations/claude-code/install.sh --codex` flow from the local checkout
  - Keeps Codex hook, MCP, and `developer_instructions` wiring in the existing installer instead of duplicating machine-specific paths in the cached plugin copy
- Assertive injection framing — recalled memories now include "IMPORTANT: MUST be considered" prefix matching CC's native memory priority language

### Changed
- Extraction fires unconditionally — removed signal keyword filter; the extraction LLM (AUDN) decides what's worth keeping
- Extraction window widened from 2 message pairs / 4K to 4 pairs / 8K chars
- SubagentStop capture widened from Plan/Explore only to all subagent types
- Subagent capture window widened from 6 messages / 4K to 12 messages / 8K chars
- Config guard skips `settings.json` check when running as a plugin (`CLAUDE_PLUGIN_ROOT` set)
- All hook paths updated from `dirname "$0"` to `dirname "${BASH_SOURCE[0]}"` for reliable plugin resolution

### Fixed
- Compaction cluster semantics — `find_similar_clusters()` now tightens union-find clusters by removing members not similar to at least half the group, preventing chain-connected outliers (#38)
- Codex uninstall `local` keyword used outside function scope in `install.sh`
- Codex installer hook merge overwrote existing hooks instead of concatenating arrays
- Hardcoded Plan/Explore filter in `memory-subagent-capture.sh` silently dropped other subagent types despite hooks.json matching all

## [5.0.2] - 2026-03-28

### Added
- **Codex Native Hooks Integration** — full parallel to Claude Code hooks using Codex CLI's 5 hook events
  - `SessionStart`: memory recall with project-scoped search and deferred-work surfacing (no MEMORY.md hydration)
  - `UserPromptSubmit`: prompt-enriched memory search with flexible Codex transcript parsing
  - `Stop`: beefier extraction (500 lines, 10 msg pairs, 8000 chars, no signal filter) compensating for no PreCompact/SessionEnd
  - `PreToolUse`: MEMORY.md write guard
  - `PostToolUse`: memory tool usage logging
- Standalone `hooks.json` config for Codex (writes to `~/.codex/hooks.json`, not `settings.json`)
- Installer updated: copies from `integrations/codex/hooks/`, writes standalone hooks config, safe uninstall by command path

### Changed
- Installer extracts `READONLY_MCP_TOOLS` to top-level constant (DRY)

## [5.0.1] - 2026-03-27

### Fixed
- AUDN DELETE action never fired — rewrote prompt definition to "no longer true and no replacement exists" with concrete example, clearly separated from UPDATE and CONFLICT
- Silent all-ADD fallback on AUDN exception now tagged as `FALLBACK_ADD` and tracked separately through metrics pipeline (DB column, extraction-quality endpoint, quality-summary endpoint)
- `fallback_add` propagated to all downstream consumers: MCP formatter, debug trace builder, execution summary, auto-linking maintenance
- DB migration adds `fallback` column to existing `extraction_outcomes` tables

## [5.0.0] - 2026-03-26

### Added
- **Graph-Aware Search** — memories build a relationship graph automatically (#59, #61, #63)
  - Auto-linking: extraction creates `related_to` graph edges between new memories and similar existing ones
  - PPR scoring: Personalized PageRank replaces flat decay for principled multi-hop graph traversal
  - Link-expanded retrieval: search results enriched with graph-connected neighbors via `graph_weight` param
  - Reserved slot injection: graph-only results guaranteed in top-k (HopRAG-style)
  - Result annotations: `match_type`, `base_rrf_score`, `graph_support`, `graph_via` on every result
  - `graph_weight` param on MCP `memory_search` (default 0.1) and HTTP `/search` (default 0.0)
  - Bidirectional adjacency index + scope-safe subgraph filtering (no cross-prefix leakage)
  - Config: `EXTRACT_MAX_LINKS`, `EXTRACT_MIN_LINK_SCORE`, `SEARCH_PPR_ALPHA`, `SEARCH_PPR_MAX_ITERS`

- **Temporal Reasoning Engine** — stable temporal metadata + date-range search (#64)
  - `document_at` field: optional ISO 8601 date for when source content was created
  - Version preservation: UPDATE archives old memory + creates `supersedes` link (no more hard-delete)
  - `is_latest` flag: distinguishes current versions from superseded ones
  - `since`/`until` filters: date-range search across all methods (hybrid, vector, explain, batch)
  - `last_reinforced_at`: reinforcement separated from content `updated_at`
  - Qdrant payload indexes on `document_at` and `is_latest`
  - MCP `memory_search` gains `since`, `until`, `include_archived` params

- **AUDN Improvements** (#60)
  - Relevance scores in AUDN prompt (was sending 0.0 for all similar memories)
  - Compaction candidate detection flagged in extraction results

- **Eval Framework** (#55-58, #62)
  - Three-tier eval: Tool (raw API), System (agent + MCP), Scenario (conversational)
  - Scalable windowed eval runner with adapter pattern (no Qdrant crashes)
  - MuSiQue 2-hop/3-hop/4-hop benchmark (1,165 questions)
  - Voltis synthetic benchmark (2,000-5,000 memories)
  - `--agent-model` flag for model comparison, `--category` filter
  - Parallel eval workers with thread-safe project isolation

### Changed
- `reinforce()` now updates `last_reinforced_at` instead of `updated_at` (breaking)
- UPDATE action archives old memory instead of deleting (breaking)
- Confidence scoring reads `last_reinforced_at` → `updated_at` → `created_at`
- Recency scoring reads `document_at` → `created_at` → `timestamp`

### Benchmark Results
- Graph search: +20% answer hit rate on 2-hop MuSiQue (100 questions, 0 regressions)
- Support chain recall: +15.3% on 3-hop questions
- LongMemEval system eval baseline: 69.5% (vs supermemory 81.6%)

## [4.0.0] - 2026-03-23

### Added
- **R4: Multi-Backend Routing** — one agent session talks to multiple Memories instances (#53)
  - Config at `~/.config/memories/backends.yaml` with 3 tiers: scenario-based, scenario + overrides, DIY
  - Scenario routing: dev+prod (search both, extract to dev), personal+shared, single instance
  - Env var interpolation for API keys in config (`${VAR_NAME}`)
  - Parallel search fan-out with exact-text dedup and `_backend` provenance tags
  - Refactored duplicated `search_memories()` into shared `_search_memories_multi()` in `_lib.sh`
  - Multi-backend extract routing via `_extract_multi()` — all 7 hooks updated
  - MCP server proxy routing with `Promise.allSettled()` fan-out, all 14 tools updated
  - Node.js + js-yaml for YAML config parsing (no Python/PyYAML dependency)
  - Full backward compatibility — no config file = env var mode = unchanged behavior

## [3.4.0] - 2026-03-23

### Added
- **R3 Wave 4: Lifecycle Policies** — TTL retention, auto-archive with proof, confidence ranking (#52)
  - Per-prefix retention policies: `ttl_days` on extraction profiles (e.g., `wip/` expires after 30 days)
  - Confidence-based auto-archive: `confidence_threshold` + `min_age_days` (archive when confidence decays below threshold)
  - `POST /maintenance/enforce-policies` endpoint (admin only, dry_run=true default)
  - Policy evidence stored in protected `_policy_*` metadata namespace with full audit trail
  - Per-prefix `confidence_half_life_days` separate from extraction half-life (90 vs 30 default)
  - Confidence as 5th RRF signal in hybrid search (`confidence_weight` param)
  - 5-signal weight scaling with combined auxiliary weight guard
  - MCP `memory_search` gets `confidence_weight` param

## [3.3.0] - 2026-03-23

### Added
- **R3 Wave 3: Quality Proof** — LongMemEval benchmark, signal filter, recovery validation (#51)
  - LongMemEval adapter: 500-question benchmark with configurable LLM judge (Anthropic/OpenAI/Ollama)
  - CLI: `memories eval longmemeval` with regression delta tracking per release
  - Signal keyword pre-filter on extraction hooks — skips LLM calls when conversation has no decision/bug/architecture keywords
  - Snapshot round-trip validation test (create → mutate → restore → verify)
  - Import/export round-trip validation test (export → clear → import → verify, plus smart dedup)
  - 21 signal filter pattern tests, 7 import/export tests, 4 snapshot tests
  - Extended `MemoriesClient` with `search()` and `extract()` for eval framework

## [3.2.1] - 2026-03-23

### Fixed
- **R3 Wave 1: Trust Hardening** — 23 trust gaps closed (#50)
  - Auth check added to `/memory/is-novel`, fail-fast for read-only keys on delete
  - Audit trails added for consolidate, prune, index build, deduplicate operations
  - Audit action `delete` renamed to `memory.deleted` (namespace consistency)
  - 9 missing audit actions added to UI lifecycle timeline color map
  - Stale memories label now shows both useful and not_useful counts
  - "Replay" renamed to "Re-search" for honest navigation semantics
  - Version strings synchronized to 3.2.x across all surfaces

## [3.2.0] - 2026-03-22

### Added
- **R2: Retrieval Confidence** — feedback-weighted search ranking, smart queries, operator views (#49)
  - Feedback as 4th RRF signal in hybrid search (`feedback_weight` param)
  - Feedback history endpoint with retraction (`GET /search/feedback/history`, `DELETE /search/feedback/{id}`)
  - Feedback section in lifecycle tab with retract buttons
  - Smarter query construction: file context, key term extraction, intent-based prefix biasing
  - Proactive deferred-work surfacing at session start (`wip/{project}` prefix)
  - `memory_deferred` MCP tool for querying WIP items
  - Problem queries view on Health page (admin only)
  - Stale memories view on Health page (admin only)
  - Search URL parameter support (`#/memories?q=...`) for replay navigation
  - SQLite index on `search_feedback(memory_id)` for efficient feedback lookups

## [3.1.0] - 2026-03-22

### Added
- **R1 B1: Safety Foundations** — pre-delete Qdrant snapshots, pin/protect memories, soft archive (reversible), memory merge with supersedes links, CLI links command (#43)
- **R1 B2: Extraction Engine** — extraction profiles per source prefix, user-definable extraction rules (always/never remember), single-call extraction mode, dry-run with selective commit, missed memory capture flow (#44)
- **R1 B3: UI Write-Path** — 8 features adding write capabilities to operator workbench (#48):
  - Create memory from UI (+ Create button, source/category modal, empty state CTA)
  - Inline edit (click-to-edit text/source/category, pin toggle, archive with undo toast)
  - Enhanced link modal (source + text + confidence display, bidirectional links)
  - Merge memories (side-by-side comparison, editable merged result, archive originals)
  - Bulk actions (multi-select mode with archive/delete/retag/re-source/merge toolbar)
  - Extraction trigger (paste text, dry-run preview, per-fact approve/reject, commit approved)
  - Lifecycle panel (tabbed detail: Overview|Lifecycle|Links, origin block, audit timeline)
  - Conflict resolution modal (Keep A/B/Merge/Defer with soft archive — no permanent deletion)
- 7 reusable UI components in `webui/components.js` (editableField, actionBadge, approvalToggle, bulkSelectMode, memoryCard, timelineEvent, comparisonPanel)
- Shared utilities extracted to `webui/utils.js` (breaks circular ES module imports)
- `resource_id` filter on `GET /audit` endpoint with SQLite index
- Dockerfile: added missing `extraction_profiles.py` COPY

### Fixed
- Extraction mode mapping in UI (aggressive correctly maps to pre_compact prompt)
- Active memory card no longer shows gold left border in browse mode
- Button sizing in memories page toolbar (btn-sm)
- Editable fields use text cursor instead of dashed underline
- Textarea auto-sizes to content height on inline edit

## [3.0.0] - 2026-03-18

### Added
- Qdrant payload filtering with source prefix and metadata filters (#22)
- Recency-weighted search with configurable half-life decay (#22)
- Memory relationships — lightweight graph edges between memories (#23)
  - `POST /memory/{id}/link`, `GET /memory/{id}/links`, `DELETE /memory/{id}/link/{link_id}`
- Conflict detection in AUDN extraction pipeline (#24)
  - New CONFLICT action when memories directly contradict
  - `GET /memory/conflicts` endpoint and `memory_conflicts` MCP tool
- Confidence decay and reinforcement for memories (#25)
  - Automatic exponential decay with configurable half-life
  - Reinforcement on access (search hits boost confidence)
- Event-driven architecture with SSE and webhooks (#26)
  - `GET /events/stream` for real-time memory events
  - Webhook registration and delivery with retry
- Embedding model migration via `POST /maintenance/reembed` (#27)
  - Staged rollback — embeds before destroying collection
- Extended Python client SDK with links, events, and reembed (#28)
- Memory compaction — find and merge similar memory clusters (#29)
  - `POST /maintenance/compact` (dry-run discovery)
  - `POST /maintenance/consolidate` (LLM-powered merge)
- Search quality feedback loop (#30)
  - `POST /search/feedback` for explicit relevance signals
  - `GET /metrics/search-quality` for rank and feedback metrics
- Extraction quality dashboard (#31)
  - Per-source extraction metrics and outcome tracking
- Audit log for multi-user operations (#32)
  - Append-only trail with query and retention
  - `GET /audit/log` endpoint
- Load testing harness with benchmarks (#33)
- Search explainability — `POST /search/explain` with full scoring breakdown (#41)
- Extraction debug trace via `debug=true` on extract requests (#41)
- Quality efficacy endpoints — `GET /metrics/quality-summary`, `GET /metrics/failures` (#41)
- 6 benchmark scenarios for agentic memory evaluation (#41)
- 5 new Claude Code hooks: PostCompact rehydration, PostToolUse observability, PreToolUse MEMORY.md guard, SubagentStop capture, ConfigChange watchdog (#41)
- Shared hook library `_lib.sh` with logging, health check, log rotation (#41)
- Permission auto-approve for read-only memory MCP tools (#41)
- Configurable hook thresholds via 10 new env vars (#41)
- `MEMORIES_EXTRACT_SOURCE` override for scoped API keys (#41)
- Response hints refactored from case/esac to JSON lookup table (#41)
- Deployment guide (`docs/deployment.md`) (#41)
- API coverage matrix (`docs/api-coverage.md`) (#41)

### Fixed
- Include source on update/link events for scoped filtering (#34)
- Lock down search-quality feedback/metrics endpoints to caller scope (#35)
- Add real rollback to reembed after destructive migration starts (#36)
- Preserve webhook delivery for events emitted from worker threads (#37)
- Clarify compaction cluster semantics with clear docstrings (#38)
- Record real source when admin/env deletes are audited (#39)
- Fix search-quality metrics to count batch searches and honor period (#40)

### Changed
- SKILL.md updated with hook lifecycle, auto-memory hydration, and manual vs automatic extraction documentation
- Hook scripts use guarded `_lib.sh` sourcing with no-op fallbacks for backward compatibility

## [2.1.0] - 2026-03-15

### Fixed
- Scoped extraction hardening:
  - `/memory/extract` now requires explicit `source` for scoped non-admin keys
  - extraction AUDN flow now scopes similar-memory context and update/delete execution to allowed prefixes
  - `/memory/extract/{job_id}` now enforces job visibility by admin/owner/source scope
- Scoped read/write hardening:
  - `/memory/delete-by-source` now enforces per-memory source authorization for scoped keys
  - `/memories` now returns scope-correct `total` for scoped keys
  - `/memories/count` now counts only accessible memories and rejects disallowed source filters
- Admin-only endpoint hardening:
  - `/usage` now requires admin privileges
  - `/backups` now requires admin privileges
- Codex notify parity hardening:
  - `memory-codex-notify.sh` now supports transcript fallback, broader payload variants, and scoped-source overrides via `MEMORIES_SOURCE_PREFIX` / `MEMORIES_SOURCE`

### Changed
- Improved Claude memory follow-up responses to avoid meta-phrasing and sound more natural
- Deduplicated prefix matching and jq textify logic in hook scripts

### Docs
- Added quick operating rules to memories skill for faster agent onboarding
- Added memory routing directive to integration setup guides
- Cleaned up roadmap, removed completed items
- Clarified Codex setup prerequisites (`npm install` in `mcp-server`, `jq`/`curl`, running service)
- Added explicit guidance for merging Codex `notify` config when an existing `notify` entry is already present
- Added scoped-key guidance for Codex notify source overrides and client-aware source-prefix conventions in skill/setup docs

## [2.0.0] - 2026-03-05

### Added
- Full CLI with 30+ commands covering all API endpoints (`memories` command)
- Agent-first output: auto-detect TTY for human-friendly display, JSON when piped
- Command groups: core, batch, delete-by, admin, backup, sync, extract, auth, config
- Layered configuration: CLI flags > config file > env vars > defaults
- Stdin support for piped input on add, upsert, batch, and extract commands
- Shell completion support via Click
- Streaming NDJSON export with source prefix, date range filters
- Multi-strategy import: `add` (raw), `smart` (novelty + timestamp), `smart+extract` (LLM for borderline)
- Auto-backup before import with `--no-backup` override
- Source prefix remapping during import

## [1.5.0] - 2026-03-05

### Added
- **Multi-auth**: prefix-scoped API keys with three role tiers (`read-only`, `read-write`, `admin`)
  - `POST /api/keys` — create keys (admin-only, shown once)
  - `GET /api/keys` — list keys with usage stats
  - `PATCH /api/keys/{id}` — update name, role, prefixes
  - `DELETE /api/keys/{id}` — revoke keys (soft-delete)
  - `GET /api/keys/me` — caller identity and role
- Prefix enforcement on all read/write endpoints — scoped keys only see/modify their allowed prefixes
- Web UI: API Keys management page (admin-gated)
- `key_store.py` — SQLite-backed key store with SHA-256 hashing
- `auth_context.py` — request-scoped role and prefix enforcement

### Changed
- `verify_api_key` now checks both env `API_KEY` (implicit admin) and DB-managed keys
- All API endpoints now receive `AuthContext` via `request.state.auth`
- Existing `API_KEY` env var continues to work unchanged (backward compatible)

## [1.4.0] - 2026-03-04

### Changed
- **Web UI v2**: Complete redesign with sidebar navigation, 5 pages (Dashboard, Memories, Extractions, API Keys, Settings), Arkos-inspired dark/light theme with CSS custom properties, list+detail memory view with grid toggle, global semantic search, responsive mobile layout with collapsible sidebar, and toast notifications
- Usage analytics dashboard with period selector, operations breakdown, and extraction token costs
- Jump-to-page pagination with page size selector and source prefix dropdown filter
- Security hardened: XSS prevention via escHtml(), no global function pollution, encoded query params, Content-Type only on POST/PUT

## [1.3.0] - 2026-03-04

### Added
- `memory_extract` MCP tool — synchronous wrapper around async extraction API with internal polling and AUDN (Add/Update/Delete/Noop) lifecycle management

### Changed
- Memories skill v2: restructured from 2 to 3 responsibilities (Read, Write, Maintain)
  - Write now uses hybrid approach: `memory_add` for simple facts, `memory_extract` for lifecycle operations
  - New Maintain responsibility covers updates, deletes, and cleanup
  - Decision table guides tool selection based on situation and cost

## [1.2.0] - 2026-03-04

### Added
- Memories skill (`skills/memories/SKILL.md`) — Claude Code skill for disciplined memory capture and proactive recall
  - Hard triggers: explicit "remember this" requests bypass judgment gates
  - Soft triggers: architectural decisions (including implicit), deferred work, non-obvious fixes, phase transitions
  - Proactive recall: searches memories before clarifying questions and when entering domains with prior context
  - Source prefix convention: `claude-code/{project}`, `learning/{project}`, `wip/{project}`
  - Eval results: +43.5% pass rate vs baseline across 8 scenarios, ~11% token overhead

## [1.1.0] - 2026-03-03

### Added
- Efficacy eval harness (`eval/`) — A/B benchmarking framework that measures how much Memories improves AI assistant performance
- 11 YAML-defined test scenarios across coding (4), recall (4), and compounding (3) categories
- Deterministic rubric scoring (`contains` with weighted rubrics) and optional LLM-as-judge
- Claude Code executor with `--strict-mcp-config` for full MCP isolation
- Auto-memory cleanup: purges stale `~/.claude/projects/cc_eval*` dirs at startup and per-run
- Configurable category-weighted aggregation (coding 40%, recall 35%, compounding 25%)
- JSON report output and human-readable summary formatter
- CLI entrypoint: `python -m eval [--category] [--scenario] [-v]`
- Shell wrapper: `./eval/run.sh` with health checks and environment setup
- 82 tests covering all eval components
- Baseline results: overall delta **+0.86** (with=1.00, without=0.14)

## [1.0.0] - 2026-02-28

First stable release of Memories — a local-first memory layer for AI assistants.

### Added
- Hybrid search (BM25 + vector) with FAISS/Qdrant backends
- Full CRUD API with auth, chunking, and batch operations
- MCP server for Claude Code, Desktop, and Codex integration
- Automatic extraction pipeline with AUDN (Add/Update/Delete/Noop)
- LLM provider abstraction (Anthropic, OpenAI, Ollama, ChatGPT OAuth)
- Folder/namespace organization for memories
- Sparse IDs to eliminate full reindex on deletes
- BYOK embeddings via OpenAI-compatible providers
- Opt-in usage analytics with SQLite persistence
- WebUI Memory Observatory with folder sidebar, usage dashboard
- WebUI pagination: page sizes up to 500, editable offset for direct jumping
- Interactive installer for Claude Code, Codex, and Cursor
- Claude Code auto-memory MEMORY.md hydration from Memories MCP
- OpenClaw QMD bridge for unified memory_search
- Codex-native memory integration
- S3-compatible cloud sync for automatic backups
- Google Drive off-site backup scripts
- Embedder hot-reload controls and observability
- Bulk delete by source prefix and memory count endpoints
- Extraction quality overhaul with maintenance system
- Periodic memory trim loop and process RSS metrics
- Docker multi-target builds (core/extract images)

### Fixed
- macOS compatibility guard for MEMORY.md hydration
- Folder sidebar hover jitter with overlay approach
- Qdrant storage space handling
- Cursor integration (cwd fallback for missing transcript fields)
- OAuth token exchange and HTTP transport
- Embedder migration stability
- API key auth exemption for Docker healthchecks
- Bounded extraction queue with backpressure (429 retry hints)

### Performance
- Replaced PyTorch with ONNX Runtime — image size from 2GB to 649MB

### Security
- Comprehensive hardening across API, Docker, hooks, and auth
- Secrets pattern scanning before commits
