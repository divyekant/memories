# PROJECT.md - Memories Maintainer Notes

High-level maintainer-facing snapshot for the `memories` repository.

---

## What This Project Is

Memories is a local-first memory service for AI assistants. It provides:

- semantic and hybrid retrieval over stored memories
- HTTP API for broad client compatibility
- MCP adapter for native tool-based assistants
- optional LLM extraction pipeline for automatic memory capture

Primary user profile: single-user to small-team workflows that value local control, low operational overhead, and straightforward recovery.

---

## Runtime Components

- `app.py`: FastAPI API surface and lifecycle
- `memory_engine.py`: core index/metadata operations
- `onnx_embedder.py`: local embedding inference (ONNX Runtime)
- `llm_extract.py`: extraction + AUDN pipeline
- `llm_provider.py`: extraction provider abstraction
- `mcp-server/index.js`: MCP wrapper exposing memory tools

Detailed architecture: `docs/architecture.md`  
Decision rationale: `docs/decisions.md`

---

## Storage Model

Persistent files under `DATA_DIR` (default `/data`):

- `vector_index.bin`
- `metadata.json`
- `config.json`
- `backups/` (rolling backups; controlled by `MAX_BACKUPS`)

Key invariant: Memories vector count must match metadata entry count.

---

## Operational Defaults

- API bind: `0.0.0.0:8000` inside container
- Recommended host mapping: `8900:8000`
- Auth: optional API key via `API_KEY`
- Embedding model: `all-MiniLM-L6-v2` (ONNX)
- Backup retention: `MAX_BACKUPS=10`

Extraction is optional and disabled by default unless `EXTRACT_PROVIDER` is configured.

---

## Performance Envelope (Guidance, Not SLA)

- Search latency: typically sub-50ms for small/medium local indexes
- Write latency: higher due to persistence + backup behavior
- Baseline memory: typically ~180-260MiB container RSS
- Extraction bursts: can temporarily exceed baseline depending transcript/provider payload size and concurrency

Memory controls:

- `MAX_EXTRACT_MESSAGE_CHARS`
- `EXTRACT_MAX_INFLIGHT`
- `MEMORY_TRIM_ENABLED`
- `MEMORY_TRIM_COOLDOWN_SEC`

---

## Development Workflow

- Sync deps: `uv sync` (add `--extra extract` and/or `--extra cloud` as needed)
- Tests: `uv run pytest -q`
- Local run: `uv run uvicorn app:app --reload`
- Docker build (core): `docker build --target core -t memories:core .`
- Docker build (extract): `docker build --target extract -t memories:extract .`

When changing memory/index behavior:

1. Add or update tests.
2. Validate backup/restore still works.
3. Validate extraction behavior if touching extraction paths.
4. Update `README.md` and/or `docs/architecture.md` if behavior changed.

---

## Integration Surface

Client entry points:

- REST API (all clients)
- MCP tools (Claude Code/Desktop, Codex, MCP-capable clients)
- Hook scripts (Claude Code 5-hook flow + Codex notify extraction)

Integration docs:

- `README.md` (primary entry)
- `integrations/claude-code.md`
- `integrations/openclaw-skill.md`
- `integrations/QUICKSTART-LLM.md`

---

## Release Hygiene Checklist

- No hardcoded credentials in docs/examples
- Public docs avoid product-specific assumptions unless the file is intentionally integration-specific
- Benchmarks describe workload profile and caveats
- Versioned behavior changes documented in `README.md`

---

## Roadmap / TODO

### Immediate (Seamless + Efficiency)

- [x] Remove `curl` from runtime image and switch to Python healthcheck to shrink image.
- [x] Publish two Docker targets/images:
  - core (`search/add/list`, no extraction SDKs)
  - extract (includes extraction provider SDKs)
- [x] Optionally move model download to first-run volume cache for smaller image pulls.
- [x] Make `/memory/extract` async-first (accept + queue, return `202`).
- [x] Add extraction backpressure with `429` and retry hints when queue is full.
- [x] Ship one-command installer that auto-detects Claude Code/Codex/OpenClaw config targets.
- [x] Add `/metrics` endpoint (latency, queue depth, error rates, memory trend).

### v1.1 (Next)

- [x] Web UI for browsing memories.
- [ ] Auto-rebuild on file changes (watch mode).
- [x] Memory deduplication tool (`/memory/deduplicate`) is implemented.
- [ ] Export formats (JSON, Markdown, CSV).
- [x] MCP server implementation is shipped (`mcp-server/index.js`).
- [x] OpenClaw `memory_search` integration is available (`integrations/openclaw-skill.md`).

### v1.2 (Future)

- [ ] Multi-index support (different projects).
- [x] Hybrid search (semantic + keyword) is implemented (Memories + BM25 + RRF).
- [ ] Memory tagging system.
- [ ] Search filters by source/date/type (source exists; date/type pending).
- [ ] Scheduled index rebuilds via cron.
- [ ] Memory analytics dashboard.
