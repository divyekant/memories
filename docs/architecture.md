# Memories Architecture

This document describes the runtime architecture of `memories` and the reasoning behind the main component boundaries.

---

## 1) System Overview

Memories is a local-first semantic memory service for AI assistants. It exposes:

- HTTP API (`app.py`) for direct integration
- MCP wrapper (`mcp-server/index.js`) for MCP-capable clients

Core storage and retrieval are handled by `MemoryEngine` (`memory_engine.py`) using:

- vector similarity search (Memories `IndexFlatIP`)
- lexical ranking (BM25)
- reciprocal-rank fusion (RRF) for hybrid search

### High-level request path

```text
Client (HTTP or MCP)
  -> FastAPI API (app.py)
  -> MemoryEngine (memory_engine.py)
  -> ONNX Embedder (onnx_embedder.py) + Memories + BM25
  -> Persistent files (/data/vector_index.bin, /data/metadata.json, /data/backups)
```

---

## 2) Component Responsibilities

### `app.py` (API boundary)

- Auth, request validation, routing, and lifecycle
- Minimal orchestration around `MemoryEngine`
- Optional extraction endpoints (`/memory/extract`, `/extract/status`)

### `memory_engine.py` (stateful core)

- In-memory Memories index and metadata lifecycle
- CRUD operations and index rebuilds
- Hybrid search (vector + BM25)
- Backup/restore and optional cloud sync hooks

### `onnx_embedder.py` (embedding runtime)

- Local text embedding generation via ONNX Runtime
- Model/tokenizer loading from Hugging Face cache
- Drop-in SentenceTransformer-compatible API

### `llm_extract.py` + `llm_provider.py` (optional learning layer)

- LLM-assisted fact extraction from conversation transcripts
- AUDN decisioning (Add/Update/Delete/Noop)
- Provider abstraction for Anthropic/OpenAI/ChatGPT Subscription/Ollama

---

## 3) Data Model and Durability

### Primary state

- `vector_index.bin`: dense vectors for semantic retrieval
- `metadata.json`: memory text, source, timestamp, and optional metadata
- `config.json`: model + index metadata

IDs are positional and compact (0..N-1). Deletes trigger rebuild/reindex for consistency.

### Durability strategy

- Every write operation persists updated index + metadata
- Pre-change backups are created for destructive/high-impact operations
- Retention policy keeps recent backups (`MAX_BACKUPS`, default 10)

This prioritizes recoverability and correctness over maximal write throughput.

---

## 4) Query and Write Flows

### Search (`POST /search`)

1. Embed query via ONNX model
2. Vector search over Memories index
3. Optional BM25 rank over tokenized corpus
4. Fuse with RRF and return top-k results

### Add/update/delete

1. Acquire write lock
2. Optional deduplication check
3. Mutate vector index + metadata
4. Persist files + rebuild BM25 index
5. Release lock

This lock-based model keeps index/metadata integrity simple and predictable.

### Extraction (`POST /memory/extract`)

1. Validate and bound transcript size
2. Run extraction pipeline (provider call + AUDN + storage actions)
3. Apply post-extract memory reclamation (`gc.collect`, `malloc_trim` where available)

Extraction is optional and isolated from core CRUD/search behavior.

---

## 5) Concurrency and Scaling Model

### Current model

- Single-process API instance
- In-process mutable index
- Thread-safe write lock for mutating operations
- Bounded concurrent extraction jobs (`EXTRACT_MAX_INFLIGHT`)

### Practical scaling guidance

- Single-user/small-team: run one container, local volume, default settings
- Higher read throughput: add API replicas only if each replica has isolated data, or introduce external shared storage/coordination
- Higher write throughput: current design intentionally favors correctness and local durability over distributed write scaling

---

## 6) Memory Behavior Under Burst Load

Transient spikes can occur when extraction handles:

- large transcripts
- large provider responses
- concurrent extraction calls

Mitigations:

- request size limits (`MAX_EXTRACT_MESSAGE_CHARS`)
- extraction concurrency limits (`EXTRACT_MAX_INFLIGHT`)
- bounded extraction payload shaping (`EXTRACT_MAX_FACTS`, `EXTRACT_MAX_FACT_CHARS`, `EXTRACT_SIMILAR_TEXT_CHARS`)
- post-extract reclamation (`MEMORY_TRIM_ENABLED`, `MEMORY_TRIM_COOLDOWN_SEC`)

This keeps steady-state usage near baseline while allowing occasional burst capacity.

---

## 7) Security and Exposure

### Authentication

- API key auth is optional (`API_KEY` env var); omitting it disables auth entirely (suitable for local-only)
- Constant-time comparison (`hmac.compare_digest`) prevents timing-based key extraction
- Per-IP rate limiting on failed auth attempts (10 failures per minute per IP before 429)

### Input validation

- Path traversal prevention on all filesystem-facing inputs: `/index/build` sources, `/restore` backup names, `/sync/download` and `/sync/restore` backup names, S3 object keys during cloud download
- Traversal checks use both character-level rejection (`..`, `/`, `\\`) and `Path.resolve().is_relative_to()` containment
- Reserved metadata fields (`id`, `text`, `source`, `timestamp`, `entity_key`) are protected from overwrite via `PATCH`

### Network exposure

- CORS restricted to localhost origins (ports 8000, 8900) rather than wildcard
- Qdrant ports bound to `127.0.0.1` in Docker Compose (not exposed to host network)
- Health endpoint returns minimal info for unauthenticated callers (no stats leakage)
- Internal error details are logged server-side only; clients receive generic messages

### Runtime

- Docker container runs as non-root user (`memories`)
- Web UI stores API key in `sessionStorage` (cleared on tab close) rather than `localStorage`
- Hook scripts use `jq -nc` for safe JSON construction (no shell interpolation)
- OAuth callback server has explicit timeout (120s)
- Credential files written with `0600` permissions, parent directories with `0700`
- Ollama URL validated for `http`/`https` scheme only (SSRF prevention)

### Deployment recommendation

Local-only is the default. If exposed publicly, additionally use HTTPS + strong API key + network controls + WAF/rate-limiting upstream.

---

## 8) Efficacy Eval Harness

The `eval/` package provides a benchmark framework to measure how much Memories improves AI assistant performance. Baseline results: **+0.86 delta** (with=1.00, without=0.14) across 11 scenarios.

### Architecture

```text
eval/__main__.py (CLI) / eval/run.sh (wrapper)
  -> EvalRunner (runner.py)
     -> CCExecutor.cleanup_stale_auto_memory()  — purge prior run artifacts
     -> MemoriesClient (memories_client.py)      — seed/clear test memories
     -> CCExecutor (cc_executor.py)              — run prompts via `claude -p`
     -> scorer.py                                — deterministic rubric scoring
     -> LLMJudge (judge.py)                     — optional LLM-judged scoring
  -> reporter.py                                 — JSON + summary output
```

### Per-scenario flow

1. **Purge** stale auto-memory dirs (`~/.claude/projects/cc_eval*`) at startup
2. **Clear** eval memories (`eval/` prefix)
3. **Create isolated project** — temp dir, no CLAUDE.md, no `.claude/`, empty MCP config
4. **Run prompt without Memories** via `claude -p --strict-mcp-config` (empty MCP)
5. **Score** output against rubrics
6. **Clear** again, **seed** scenario-specific memories
7. **Create isolated project** — same pattern but `.mcp.json` pointing to Memories MCP
8. **Run prompt with Memories** via `claude -p --strict-mcp-config` (Memories MCP only)
9. **Score** again, optionally resolve LLM-judged rubrics
10. Compute **efficacy delta** = score_with - score_without

### Isolation strategy

Isolation operates at three levels:

1. **MCP isolation** — `--strict-mcp-config` ensures Claude loads **only** the provided MCP config, ignoring global `~/.claude/settings.json` and project `.mcp.json` files
2. **Project isolation** — Fresh temp directory per run with no CLAUDE.md, no `.claude/`, no conversation history
3. **Auto-memory cleanup** — `cleanup_stale_auto_memory()` removes `~/.claude/projects/` dirs matching `cc_eval` or `cc-eval` (Claude Code mangles underscores to hyphens in path names)

### Scenario design

Test scenarios use a fictional project context ("Voltis") with **arbitrary, non-derivable facts** that Claude cannot guess from naming conventions or training data:

- **Arbitrary values**: port `7443`, prefix `Vx`, error codes `VTIS-`, threshold `73%`
- **Fictional tools**: `vcheck` library, `vtctl deploy-gate`, `hvt_client` fixture
- **Non-standard names**: `VTX_LEGACY_DSN` (not `DATABASE_URL`), `shed mode` (not `throttle`)

This design principle ensures zero-delta scenarios are true negatives — if Claude scores well without memories, the scenario is too easy and needs tightening.

### Scoring

- **Deterministic rubrics**: `contains` with per-rubric weights — scored programmatically
- **LLM-judged rubrics**: optional, scored by LLM-as-judge with structured JSON output
- **Aggregation**: weighted average per category, category-weighted overall score

### Baseline results

| Category | With | Without | Delta | Scenarios |
|---|---|---|---|---|
| Coding | 1.00 | 0.00 | +1.00 | 4 |
| Recall | 1.00 | 0.20 | +0.80 | 4 |
| Compounding | 1.00 | 0.27 | +0.73 | 3 |
| **Overall** | **1.00** | **0.14** | **+0.86** | **11** |

---

## 9) Non-Goals (Current Scope)

- Distributed multi-writer consistency across replicas
- Tenant isolation inside one process
- ACID transaction semantics across vector + metadata operations

Those can be addressed later with a different persistence/distribution architecture.
