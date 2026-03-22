# Changelog

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
