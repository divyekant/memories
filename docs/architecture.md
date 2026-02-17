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
  -> Persistent files (/data/index.faiss, /data/metadata.json, /data/backups)
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
- Provider abstraction for Anthropic/OpenAI/Ollama

---

## 3) Data Model and Durability

### Primary state

- `index.faiss`: dense vectors for semantic retrieval
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

- API key auth is optional (`API_KEY`)
- Local-only deployment is the default recommendation
- If exposed publicly, use HTTPS + strong API key + network controls + rate limiting upstream

---

## 8) Non-Goals (Current Scope)

- Distributed multi-writer consistency across replicas
- Tenant isolation inside one process
- ACID transaction semantics across vector + metadata operations

Those can be addressed later with a different persistence/distribution architecture.
