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

### Remote MCP front door (`mcp-server/remote/`)

Two ways to reach the same `memory_*` tools (`mcp-server/lib-tools.mjs`), built for different exposure models:

- **stdio transport** (`mcp-server/index.js`) — the default for local MCP clients (Claude Code, Claude Desktop, Cursor, etc.). One process per client, no network listener, no auth layer needed — the client already controls the process.
- **Remote/HTTP transport** (`mcp-server/remote/server.mjs`) — a stateless `StreamableHTTPServerTransport` behind Express, for the claude.ai (Claude web) custom-connector flow, where the client is a browser and there is no local process to trust implicitly.

Both call `buildServer()` and register the identical tool set, so the two transports never drift in capability. The remote entry point pins its backend explicitly (`skipFileConfig: true`) so a host-level `.memories/backends.yaml` can't silently redirect a deployed remote server to the wrong backend.

For Codex, the published installer keeps these paths distinct: local setup
uses `npx -y memories-mcp@latest init --codex` and registers a local stdio
server whose backend is selected by `--url`/`MEMORIES_URL`; direct remote setup
uses `npx -y memories-mcp@latest init --codex --mcp-url https://... --yes` and
then `codex mcp login memories` for OAuth. The remote path does not copy a
backend API key into Codex configuration.

The remote front door adds a single-user OAuth 2.1 layer (`oauth.mjs`) in front of the tools: authorization-code + mandatory PKCE (S256), Dynamic Client Registration (rate-limited, with bounds on redirect-uri count and client-name length), refresh-token rotation, and an HMAC-signed bearer access token — no `client_credentials`, since there is exactly one user. `REMOTE_MCP_AUTH` must be exactly `oauth` or `none` (anything else refuses to start); `none` is for local testing only and logs a loud warning on every startup. In `oauth` mode, `REMOTE_MCP_ISSUER` must be set and must be `https:` unless the host is `localhost`/`127.0.0.1`, so the authorization code and bearer token are never sent over plaintext to a real host.

Deployment is a profile-gated `remote-mcp` service in `docker-compose.yml`, sitting behind a reverse proxy/tunnel (Caddy, Cloudflare Tunnel) and the `memories` service — see the "Claude web (claude.ai) connector" section in the README for setup, including `REMOTE_MCP_TRUST_PROXY` for correct per-client rate-limit buckets behind that proxy hop.

---

## 8) Efficacy Eval Harness

The `eval/` package provides a benchmark framework to measure how much Memories improves AI assistant performance. Baseline results: **+0.86 delta** (with=1.00, without=0.14) across 11 scenarios.

### Architecture

```text
eval/__main__.py (CLI) / eval/run.sh (wrapper)
  -> setup_validation.py                         — reject unsafe targets before eval work
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

Isolation operates at four levels:

1. **Setup validation** — `eval.setup_validation` rejects the normal local production target (`localhost:8900`) by default, requires an existing MCP server path, and the wrapper requires `/health/ready` before scenario execution
2. **MCP isolation** — `--strict-mcp-config` ensures Claude loads **only** the provided MCP config, ignoring global `~/.claude/settings.json` and project `.mcp.json` files
3. **Project isolation** — Fresh temp directory per run with no CLAUDE.md, no `.claude/`, no conversation history
4. **Hook isolation and cleanup** — `CCExecutor` writes an eval-scoped hook env file that points with-memory runs at the eval backend and sets `MEMORIES_DISABLED=1` for without-memory runs; `cleanup_stale_auto_memory()` removes `~/.claude/projects/` dirs matching `cc_eval` or `cc-eval` (Claude Code mangles underscores to hyphens in path names)

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

The automatic memory layer uses 12 shell hooks across 10 Claude Code / Cursor
lifecycle events, a version-aware five- or ten-event Codex profile layered
with MCP + developer instructions, and an OpenCode plugin + MCP lifecycle
distinct from shell hooks:

| Event | Hook | Purpose |
|---|---|---|
| `SessionStart` | `memory-recall.sh` | Inject scoped memory pointers and sync MEMORY.md pointers |
| `SubagentStart` | `memory-subagent-recall.sh` | Inject project memories into spawned subagents |
| `UserPromptSubmit` | `memory-query.sh` | Inject relevant memories into prompt context |
| `Stop` | `memory-extract.sh` | Extract and store learnings from conversation |
| `PreCompact` | `memory-flush.sh` | Flush pending memories before compaction |
| `PostCompact` | `memory-rehydrate.sh` | Claude/Cursor rehydration; Codex's event is silent (`suppressOutput` only) |
| `PostToolUse` | `memory-observe.sh` | Observability for memory MCP tool calls |
| `PostToolUse` | `memory-tool-observe.sh` | Record write/edit/bash context for richer extraction |
| `PreToolUse` | `memory-guard.sh` | Guard MEMORY.md from direct Write/Edit |
| `SubagentStop` | `memory-subagent-capture.sh` | Capture learnings from Plan/Explore subagents |
| `ConfigChange` | `memory-config-guard.sh` | Watchdog for user settings changes |
| `SessionEnd` | `memory-commit.sh` | Final extraction and cleanup |

The published `memories-mcp` npm installer checks `codex --version` and owns
Codex hook/MCP wiring. Codex `>= 0.146.0` receives all ten Codex events in the
table above; older or unparseable clients receive only `SessionStart`,
`UserPromptSubmit`, `Stop`, `PostToolUse`, and `PreToolUse`. `PostCompact` is a
silent `suppressOutput`-only hook; `SessionStart(source=compact)` performs the
recall after compaction. `SessionEnd` performs one first-routed extract POST
with a `curl --max-time 2` cap, never polls, and is configured with a manifest
timeout exactly 3 seconds.

The installer auto-approves six read-only MCP tools. `memory_is_useful` is a
persistent feedback write and remains prompt-gated. External Memories is the
durable, searchable cross-client authority; native Codex Memories is an
optional local derived cache. The installer never sets either. Users may
optionally set `memories.disable_on_external_context = true` in the exact root
`[memories]` table to avoid duplicate context; this is a recommendation only.

The v5.10-v5.12 reliability parity applies to Codex hooks as well: activation
and configuration gates honor payload cwd and resolved backend files,
routed reachability keeps per-backend breaker isolation, end-to-end deadlines
preserve partial results, and 401 responses provide credential guidance.
Materially short timeout budgets are inconclusive rather than breaker trips.

### Shared project memory boundary

Phase 1 adds two structured source families:

- `person/<principal>/<project>/<kind>` is private to one managed principal.
- `project/<project>/<kind>` is shared by managed principals whose key is
  authorized for that exact project prefix.

`kind` is exactly `decisions`, `knowledge`, `state`, or `operations`. A strict
`.memories/project.yaml` declares only `project_id` and `shared_memory: true`;
it grants no access. Collaborative routing activates only with one configured
backend and a managed `/api/keys/me` identity containing a valid stable
`principal_id`. Missing declarations preserve legacy behavior. Present but
malformed declarations, unresolved identities, and multiple backends fail
closed without an unscoped search or write.

Automatic extraction writes to the current person's `knowledge` source. A
shared write is always deliberate through an add-like tool and passes the same
project-context gate. The API strips caller-supplied authorship fields and
stamps the authenticated principal and origin client. Search, novelty,
replacement, conflict resolution, and scheduled consolidation use the same
policy domains: structured records never cross principals or projects, while
ordinary legacy sources retain cross-client deduplication and consolidation.

The write engine re-reads replacement targets while holding their source
domain locks. This prevents a concurrent source move from changing the policy
domain between validation and archive/link mutation. Moving only between kinds
of the same person/project preserves provenance; changing the owner or project
is an authored replacement and receives fresh trusted attribution.

Historical malformed `project/` or `person/` records are grandfathered for
read, export, and delete only. They cannot be edited in place because their
owner/project boundary is ambiguous. An authenticated operator must explicitly
move one to an ordinary legacy source first, then rewrite it into a strict
structured source if sharing is intended. This is a security decision, not an
automatic data migration; see the
[decision record](decisions/2026-08-13-shared-project-memory-boundary.md) and
[playbook](memory-playbook.md).

OpenCode does not use Claude Code or Codex shell hooks. The installer merges `mcp.memories` and the repo-local plugin path into `~/.config/opencode/opencode.json`; the MCP server runs as a local OpenCode server through `zsh -lc`, sourcing `~/.config/memories/env` before executing `mcp-server/index.js`. The plugin injects prompt-time recall context, searches exact project prefixes first (`opencode/{project}`, `claude-code/{project}`, `codex/{project}`, `learning/{project}`, `wip/{project}`), and logs active-search telemetry for memory tool calls with `client=opencode`. OpenCode-authored extracted memories should use `opencode/{project}` when extraction is added, but automatic extraction is not enabled by default until reliable OpenCode end-of-turn transcript access is proven.

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
