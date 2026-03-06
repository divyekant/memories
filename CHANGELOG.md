# Changelog

## [Unreleased]

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
