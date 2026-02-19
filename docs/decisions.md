# Engineering Decisions and Tradeoffs

This document captures key architecture decisions for Memories and the tradeoffs behind each one.

---

## D1: Use ONNX Runtime Instead of PyTorch for Embeddings

- Status: accepted
- Decision: run `all-MiniLM-L6-v2` through ONNX Runtime (`onnx_embedder.py`) rather than `sentence-transformers` + PyTorch runtime.
- Why:
  - significantly smaller runtime/image footprint
  - faster cold start for local container workflows
  - same embedding space/model family retained
- Tradeoff:
  - custom embedder wrapper adds maintenance surface
  - less out-of-the-box flexibility than full PyTorch stack

## D2: Use Memories `IndexFlatIP` for Vector Search

- Status: accepted
- Decision: use exact inner-product search with normalized embeddings.
- Why:
  - deterministic and simple behavior
  - no training/build phase for IVF/HNSW graph parameters
  - easier correctness/debugging for local-first usage
- Tradeoff:
  - linear scan cost grows with index size
  - not optimized for very large multi-tenant datasets

## D3: Use Hybrid Retrieval (Vector + BM25) with RRF

- Status: accepted
- Decision: combine semantic similarity with lexical ranking and fuse via reciprocal-rank fusion.
- Why:
  - improves precision on exact-term queries
  - better robustness across short/long queries and naming-heavy content
  - avoids overfitting to either dense or sparse retrieval alone
- Tradeoff:
  - extra compute and index maintenance (BM25 rebuilds on writes)
  - introduces fusion parameters to tune

## D4: Keep Metadata in JSON Alongside the Vector Index

- Status: accepted
- Decision: persist metadata in `metadata.json` and vectors in `vector_index.bin`.
- Why:
  - transparent, debuggable local files
  - easy backup/restore mechanics
  - no external database dependency
- Tradeoff:
  - full-file rewrites on update-heavy workloads
  - not ideal for high-write concurrent scenarios

## D5: Favor Integrity and Recovery Over Max Write Throughput

- Status: accepted
- Decision: use lock-guarded writes, pre-change backups for destructive operations, and strict index/metadata integrity checks.
- Why:
  - predictable behavior for single-user and small-team workflows
  - safer recovery from interruptions/corruption
  - simpler operational model
- Tradeoff:
  - slower write paths under heavy mutation
  - limited horizontal write scaling

## D6: Keep LLM Extraction Optional and Provider-Agnostic

- Status: accepted
- Decision: extraction lives behind explicit env configuration (`EXTRACT_PROVIDER`), with provider abstraction for Anthropic/OpenAI/ChatGPT Subscription/Ollama.
- Why:
  - base memory features remain fully local and zero-cost
  - users can choose accuracy/cost/privacy profile
  - avoids coupling core service availability to an external LLM
- Tradeoff:
  - more configuration combinations to test
  - behavior differences across providers (e.g., AUDN support)

## D7: Add Post-Extraction Memory Reclamation Controls

- Status: accepted
- Decision: bound extraction payload sizes/concurrency and run post-extract GC + allocator trim (`MEMORY_TRIM_*`).
- Why:
  - extraction bursts can create transient allocation spikes
  - long-running containers should return toward baseline after heavy workloads
  - keeps memory behavior more predictable in shared hosts
- Tradeoff:
  - small CPU overhead after extraction requests
  - allocator trim effectiveness depends on runtime/platform allocator behavior

## D8: Embed OAuth Logic Directly Instead of Gateway Services

- Status: accepted
- Decision: port ChatGPT OAuth2+PKCE token exchange as pure-stdlib Python helpers (`chatgpt_oauth.py`) rather than running separate gateway services.
- Why:
  - zero new pip dependencies (all `urllib.request`, `hashlib`, `secrets`)
  - no runtime services to manage — token exchange happens at provider init
  - same pattern as existing Anthropic OAuth auto-detection
  - CLI tool (`python -m memories auth chatgpt`) handles one-time browser-based setup
- Tradeoff:
  - eager token exchange in `__init__` blocks startup on network call (other providers defer to first `complete()`)
  - refresh token is a long-lived secret stored in `~/.config/memories/env`

## D9: Enable Full AUDN for Ollama via JSON Format Constraint

- Status: accepted
- Decision: set `supports_audn = True` on Ollama and add `"format": "json"` to the Ollama API payload.
- Why:
  - modern local models (Llama 3, Mistral, Qwen) reliably produce structured JSON when constrained
  - `"format": "json"` is natively supported by Ollama and dramatically reduces parse failures
  - eliminates the ADD/NOOP-only limitation for local-model users
- Tradeoff:
  - smaller/older local models may still produce malformed AUDN decisions
  - fallback behavior on parse failure is ADD-all (same as before)

## D10: Defense-in-Depth Input Validation on All Filesystem Paths

- Status: accepted
- Decision: validate all user-controlled strings that reach the filesystem with both character-level rejection and `Path.resolve().is_relative_to()` containment checks.
- Why:
  - multiple API endpoints accept user-supplied names that become filesystem paths (backup names, index build sources, S3 object keys)
  - character-level checks (`..`, `/`, `\`) catch obvious traversal attempts
  - `is_relative_to()` catches edge cases (symlinks, Unicode normalization) that character checks miss
  - defense-in-depth: both layers must pass
- Tradeoff:
  - slightly more code per endpoint
  - legitimate backup names containing `/` or `\` are rejected (acceptable constraint)

## D11: Restrict CORS to Localhost Origins

- Status: accepted
- Decision: hardcode CORS `allow_origins` to `localhost:8000`, `localhost:8900`, `127.0.0.1:8000`, `127.0.0.1:8900` instead of `["*"]`.
- Why:
  - wildcard CORS exposes the authenticated API to any browser origin
  - all current legitimate clients (web UI, MCP server) run on localhost
  - reduces cross-origin attack surface when API key is available in the browser
- Tradeoff:
  - custom deployments on non-standard ports must update the origin list (via code change, not env var — intentional friction)
  - remote web UIs would need a proxy or origin list extension

## D12: Constant-Time Auth with Per-IP Rate Limiting

- Status: accepted
- Decision: use `hmac.compare_digest` for API key comparison and track per-IP failure counts (10/min limit before 429).
- Why:
  - timing attacks on string comparison can leak key bytes
  - rate limiting prevents brute-force key guessing
  - simple in-memory tracking avoids external dependencies
- Tradeoff:
  - in-memory failure tracking resets on restart
  - no distributed rate limiting across replicas (acceptable for single-instance deployment)

## D13: Qdrant Named Docker Volume

- Status: accepted
- Decision: mount Qdrant storage as a named Docker volume (`qdrant-storage`) rather than a host bind mount (`./data/qdrant:/qdrant/storage`).
- Why:
  - Qdrant v1.15+ uses memory-mapped files (`mmap`) for its HNSW index, which requires full POSIX filesystem semantics
  - Docker Desktop on macOS and Windows uses VirtioFS for bind mounts, which is not fully POSIX-compliant
  - This combination causes Qdrant to panic with `OutputTooSmall` (and similar) errors on vector search/upsert operations — data is silently lost or corrupted
  - Named volumes live inside the Linux VM managed by Docker Desktop, giving Qdrant a compliant filesystem
- Tradeoff:
  - Volume data is managed by Docker rather than visible as host-side files — `docker volume inspect` / `docker cp` required to access raw files
  - Existing bind-mount data must be manually migrated (see README migration note)
  - On Linux hosts (where bind mounts are POSIX-compliant) the change is neutral — named volumes behave identically

---

## Revisit Triggers

Re-evaluate these decisions when:

- index size or query QPS outgrows exact-search economics
- multi-writer or multi-tenant requirements become first-class
- durability requirements exceed file + backup semantics
- extraction latency/cost dominates end-to-end workflow performance
