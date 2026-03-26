# Memories Architecture

This document describes the runtime architecture of `memories` and the reasoning behind the main component boundaries.

---

## 1) System Overview

Memories is a local-first semantic memory service for AI assistants. It exposes:

- HTTP API (`app.py`) for direct integration
- MCP wrapper (`mcp-server/index.js`) for MCP-capable clients

Core storage and retrieval are handled by `MemoryEngine` (`memory_engine.py`) using:

- vector similarity search via Qdrant (`qdrant_store.py`)
- lexical ranking (BM25)
- reciprocal-rank fusion (RRF) for hybrid search
- recency-weighted scoring with configurable half-life decay (prefers `document_at` over `created_at`)
- confidence weighting with decay and reinforcement-based refresh (`last_reinforced_at`)
- graph-aware expansion via Personalized PageRank (PPR) on `related_to` link adjacency
- temporal filtering with `since`/`until` date-range parameters
- payload filtering for source prefix, archived status, and metadata

Supporting subsystems:

- **Event bus** (`event_bus.py`) — in-process pub/sub with SSE streaming and webhook dispatch
- **Audit log** (`audit_log.py`) — append-only SQLite trail for multi-user operation tracking
- **Usage tracker** (`usage_tracker.py`) — search quality metrics, extraction outcome tracking, and efficacy measurement

### High-level request path

```text
Client (HTTP or MCP)
  -> FastAPI API (app.py)
  -> MemoryEngine (memory_engine.py)
  -> ONNX Embedder (onnx_embedder.py) + QdrantStore (qdrant_store.py) + BM25
  -> Qdrant vector DB + /data/metadata.json + /data/backups
  -> EventBus (event_bus.py) — SSE subscribers + webhook dispatch
  -> AuditLog (audit_log.py) — SQLite append-only trail
  -> UsageTracker (usage_tracker.py) — SQLite analytics
```

---

## 2) Component Responsibilities

### `app.py` (API boundary)

- Auth, request validation, routing, and lifecycle
- Minimal orchestration around `MemoryEngine`
- Extraction endpoints (`/memory/extract`, `/extract/status`) with debug trace mode
- Event streaming (`/events/stream`) and webhook management
- Audit log endpoint (`/audit/log`) with query and retention
- Search explainability (`/search/explain`) and quality feedback (`/search/feedback`)
- Quality efficacy endpoints (`/metrics/quality-summary`, `/metrics/failures`, `/metrics/search-quality`)
- Maintenance endpoints (`/maintenance/reembed`, `/maintenance/compact`, `/maintenance/consolidate`)

### `memory_engine.py` (stateful core)

- Qdrant-backed vector index and metadata lifecycle
- CRUD operations with memory relationships (graph edges via metadata)
- Hybrid search (vector + BM25 + RRF) with recency boosting and confidence weighting
- Confidence system: exponential decay over time, reinforcement on search access
- Memory linking: `add_link`, `get_links`, `delete_link` for lightweight graph edges
- Backup/restore and optional cloud sync hooks

### `qdrant_store.py` (vector storage)

- Qdrant API adapter isolating vector backend specifics
- Collection management with configurable consistency levels
- Payload filtering for source prefix and metadata-based queries
- Supports both remote Qdrant server and local embedded mode

### `event_bus.py` (event system)

- Thread-safe in-process event bus for memory lifecycle events
- Event types: `memory.added`, `memory.updated`, `memory.deleted`, `memory.linked`, `extraction.completed`
- SSE subscriber management with async queue-based delivery
- Webhook registration with retry logic via `httpx`
- Event history ring buffer for late-joining subscribers

### `audit_log.py` (audit trail)

- SQLite-backed append-only audit log (enabled via `AUDIT_LOG=true`)
- Records action, key identity, resource ID, source prefix, and IP
- Query with time-range, action, and key filters
- Configurable retention with `purge()` for age-based cleanup
- `NullAuditLog` no-op when disabled

### `usage_tracker.py` (analytics)

- SQLite-backed usage tracking (enabled via `USAGE_TRACKING=true`)
- API event logging, extraction token costs with per-model pricing
- Retrieval stats and search feedback (relevance signals)
- Search quality metrics: rank distribution, feedback aggregation
- `NullTracker` no-op when disabled

### `onnx_embedder.py` (embedding runtime)

- Local text embedding generation via ONNX Runtime
- Model/tokenizer loading from Hugging Face cache
- Drop-in SentenceTransformer-compatible API

### `llm_extract.py` + `llm_provider.py` (extraction layer)

- LLM-assisted fact extraction from conversation transcripts
- AUDN decisioning with 5 actions: Add, Update, Delete, Noop, Conflict
- Conflict detection flags direct contradictions between new and existing memories
- Debug trace mode (`debug=true`) for extraction pipeline introspection
- Source-scoped auth: extraction scoped to caller's allowed prefixes
- Provider abstraction for Anthropic/OpenAI/ChatGPT Subscription/Ollama

---

## 3) Data Model and Durability

### Primary state

- **Qdrant collection**: dense vectors for semantic retrieval, with payload filtering
- `metadata.json`: memory text, source, timestamp, confidence, links, and optional metadata
- `config.json`: model + index metadata
- `audit.db`: append-only audit trail (when `AUDIT_LOG=true`)
- `usage.db`: usage analytics and search quality metrics (when `USAGE_TRACKING=true`)

IDs are positional and compact (0..N-1). Deletes trigger rebuild/reindex for consistency.

### Memory relationships

Lightweight graph edges stored as metadata on source and target memories. Each link has a type (e.g., `related`, `supersedes`, `contradicts`) and is bidirectional in queries. Managed via `POST /memory/{id}/link`, `GET /memory/{id}/links`, `DELETE /memory/{id}/link/{link_id}`.

### Confidence system

Each memory carries a confidence score that decays exponentially over time (configurable half-life). Accessing a memory via search triggers reinforcement, boosting its confidence. Confidence is factored into search ranking.

### Durability strategy

- Every write operation persists updated index + metadata
- Pre-change backups are created for destructive/high-impact operations
- Retention policy keeps recent backups (`MAX_BACKUPS`, default 10)

This prioritizes recoverability and correctness over maximal write throughput.

---

## 4) Query and Write Flows

### Search (`POST /search`)

1. Embed query via ONNX model
2. Vector search over Qdrant with optional payload filtering (source prefix, metadata)
3. BM25 rank over tokenized corpus (source-prefix aware)
4. Reciprocal Rank Fusion (RRF) combining vector and BM25 scores
5. Optional recency boosting (exponential decay with configurable half-life, blended as third RRF signal)
6. Confidence weighting via `_enrich_with_confidence`
7. Reinforce accessed memories (boost confidence on retrieval)
8. Return top-k results

### Search explain (`POST /search/explain`)

Returns full scoring breakdown for a query: per-result vector score, BM25 score, recency score, confidence, and final RRF score with weight contributions.

### Search feedback (`POST /search/feedback`)

Accepts explicit relevance signals (relevant/irrelevant) tied to a query and memory. Feeds into `/metrics/search-quality` for rank distribution and feedback aggregation.

### Add/update/delete

1. Acquire write lock
2. Optional deduplication check
3. Mutate vector index + metadata
4. Persist files + rebuild BM25 index
5. Release lock

This lock-based model keeps index/metadata integrity simple and predictable.

### Extraction (`POST /memory/extract`)

1. Validate and bound transcript size
2. Run extraction pipeline (provider call + AUDN decisioning)
3. Execute AUDN actions: ADD, UPDATE, DELETE, NOOP, or CONFLICT
4. CONFLICT action flags contradictions and stores both versions for resolution via `GET /memory/conflicts`
5. Emit `extraction.completed` event to event bus
6. Record audit trail entry (when enabled)
7. Apply post-extract memory reclamation (`gc.collect`, `malloc_trim` where available)

When `debug=true` is passed, extraction returns a debug trace with LLM prompt, raw response, parsed actions, and execution results.

Extraction is source-scoped: scoped API keys can only extract to their allowed prefixes.

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

- Multi-auth with prefix-scoped API keys and three role tiers: `read-only`, `read-write`, `admin`
- Legacy `API_KEY` env var continues to work as implicit admin (backward compatible)
- SQLite-backed key store (`key_store.py`) with SHA-256 hashing
- Request-scoped auth context (`auth_context.py`) for role and prefix enforcement
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

## 9) Event System

### Event bus (`event_bus.py`)

The event bus provides real-time observability into memory lifecycle operations:

- **SSE streaming** — `GET /events/stream` delivers events to long-lived HTTP clients
- **Webhook dispatch** — registered URLs receive POST callbacks with retry logic
- **Event types**: `memory.added`, `memory.updated`, `memory.deleted`, `memory.linked`, `extraction.completed`
- Events include source metadata for scoped filtering

Events are emitted non-blocking from the calling thread. The bus maintains a bounded history ring buffer for late-joining SSE subscribers.

---

## 10) Hook System

Ten shell hooks across eight Claude Code lifecycle events provide automatic memory capture and recall:

| Event | Hook | Purpose |
|---|---|---|
| `SessionStart` | `memory-recall.sh` | Hydrate MEMORY.md from stored memories |
| `UserPromptSubmit` | `memory-query.sh` | Inject relevant memories into prompt context |
| `Stop` | `memory-extract.sh` | Extract and store learnings from conversation |
| `PreCompact` | `memory-flush.sh` | Flush pending memories before compaction |
| `PostCompact` | `memory-rehydrate.sh` | Rehydrate MEMORY.md after compaction |
| `PostToolUse` | `memory-observe.sh` | Observability for memory MCP tool calls |
| `PreToolUse` | `memory-guard.sh` | Guard MEMORY.md from direct Write/Edit |
| `SubagentStop` | `memory-subagent-capture.sh` | Capture learnings from Plan/Explore subagents |
| `ConfigChange` | `memory-config-guard.sh` | Watchdog for user settings changes |
| `SessionEnd` | `memory-commit.sh` | Final extraction and cleanup |

Hooks share a common library (`_lib.sh`) with logging, health checks, and log rotation. All hooks use guarded `_lib.sh` sourcing with no-op fallbacks for backward compatibility. Response hints use a JSON lookup table (`response-hints.json`) rather than shell case/esac. Hook behavior is configurable via 10 environment variables.

---

## 11) Explainability

### Search explain (`POST /search/explain`)

Returns a full scoring breakdown for each search result: vector similarity score, BM25 score, recency score, confidence value, RRF contribution per signal, and final fused score. Useful for debugging search quality and tuning weights.

### Extraction debug trace

When `debug=true` is passed to `/memory/extract`, the response includes: the LLM prompt sent, the raw provider response, parsed AUDN actions, and per-action execution results. Enables inspection of why specific extraction decisions were made.

### Quality metrics

- `GET /metrics/quality-summary` — aggregated search and extraction quality overview
- `GET /metrics/failures` — recent extraction failures with error details
- `GET /metrics/search-quality` — rank distribution and feedback signal aggregation

---

## 12) Non-Goals (Current Scope)

- Distributed multi-writer consistency across replicas
- Tenant isolation inside one process
- ACID transaction semantics across vector + metadata operations

Those can be addressed later with a different persistence/distribution architecture.
