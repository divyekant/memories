# Changelog

## [5.4.0] - 2026-05-04

### Added
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
