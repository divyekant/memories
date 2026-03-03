# Changelog

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
