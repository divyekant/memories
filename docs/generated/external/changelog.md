---
title: "Changelog"
slug: changelog
type: changelog
date: 2026-03-18
source-tier: direct
hermes-version: 1.0.0
status: current
audience: external
---

# Changelog

## [Unreleased]

### Added
- **Search explain API** (`POST /search/explain`) — admin-only endpoint that returns detailed scoring breakdowns for hybrid search queries, including per-candidate vector scores, BM25 scores, RRF parameters, and auth filtering counts.
- **Extraction debug trace** — pass `debug: true` to `/memory/extract` to receive a `debug_trace` object in the job result containing extracted facts, AUDN decisions with similar-memory context, and an execution summary.
- **5 new Claude Code hooks**:
  - `memory-rehydrate.sh` — re-injects relevant memories after context compaction
  - `memory-subagent-capture.sh` — captures decisions from Plan/Explore subagents
  - `memory-observe.sh` — logs MCP tool invocations to `~/.config/memories/tool-usage.log`
  - `memory-guard.sh` — blocks direct writes to MEMORY.md files
  - `memory-config-guard.sh` — warns when memory hooks are removed from settings
- **Quality metrics endpoints**:
  - `GET /metrics/quality-summary` — top-level retrieval precision and extraction accuracy
  - `GET /metrics/failures` — recent low-quality results for debugging
  - `GET /metrics/search-quality` — rank distribution, feedback ratios, and volume
  - `GET /metrics/extraction-quality` — AUDN outcome ratios and per-source breakdown
- **Search feedback API** (`POST /search/feedback`) — record positive/negative relevance signals for search results
- **SSE event stream** (`GET /events/stream`) — real-time Server-Sent Events for memory lifecycle events (`memory.added`, `memory.updated`, `memory.deleted`, `memory.linked`, `extraction.completed`)
- **Webhook registration** — register callback URLs via `POST /webhooks` to receive event notifications (admin only)
- **Recent events API** (`GET /events/recent`) — fetch recent event history
- **Memory relationships** — typed directional links between memories:
  - `POST /memory/{id}/link` — create links (types: `supersedes`, `related_to`, `blocked_by`, `caused_by`, `reinforces`)
  - `GET /memory/{id}/links` — query outgoing and incoming links
  - `DELETE /memory/{id}/link/{target_id}` — remove links
- **Confidence decay** — every memory now carries a computed `confidence` score (0.0-1.0) based on exponential decay from last reinforcement, with a configurable 90-day half-life. Memories are automatically reinforced on retrieval.
- **Compact and consolidate** — two-phase memory deduplication:
  - `POST /maintenance/compact` — discover clusters of similar memories (read-only)
  - `POST /maintenance/consolidate` — merge clusters using an LLM (dry-run by default)
  - `POST /maintenance/prune` — remove stale unretrieved memories
- **MCP tools**: `memory_is_useful` (search feedback), `memory_conflicts` (list unresolved conflicts)
- **Audit log** — append-only trail with `GET /audit` query and `POST /audit/purge` retention

### Fixed
- Scoped extraction hardening: `/memory/extract` requires explicit `source` for scoped non-admin keys; AUDN flow scopes similar-memory context and execution to allowed prefixes
- Scoped read/write hardening: `/memory/delete-by-source` enforces per-memory authorization; `/memories` returns scope-correct totals; `/memories/count` counts only accessible memories
- Admin-only endpoint hardening: `/usage` and `/backups` now require admin privileges
- Codex notify parity: `memory-codex-notify.sh` supports transcript fallback, broader payload variants, and scoped-source overrides

---

## [2.1.0] - March 15, 2026

### Security Hardening

This release focuses on closing authorization gaps across extraction, read/write, and admin endpoints.

**Extraction scoping** — `/memory/extract` now requires an explicit `source` for scoped (non-admin) keys. The AUDN pipeline restricts similar-memory lookups and action execution to the caller's allowed prefixes. Job visibility on `/memory/extract/{job_id}` is enforced by admin, owner, or source scope.

**Read/write scoping** — `/memory/delete-by-source` enforces per-memory source authorization. `/memories` returns scope-correct `total` counts. `/memories/count` counts only accessible memories and rejects disallowed source filters.

**Admin gating** — `/usage` and `/backups` now require admin privileges.

**Codex integration** — `memory-codex-notify.sh` gains transcript fallback, broader payload variant support, and scoped-source overrides via `MEMORIES_SOURCE_PREFIX` / `MEMORIES_SOURCE`.

---

## [2.0.0] - March 5, 2026

### Full CLI

A complete command-line interface with 30+ commands covering every API endpoint.

- **Agent-first output** — auto-detects TTY for human-friendly display, outputs JSON when piped
- **Command groups** — core, batch, delete-by, admin, backup, sync, extract, auth, config
- **Layered configuration** — CLI flags > config file > env vars > defaults
- **Stdin support** — pipe input to add, upsert, batch, and extract commands
- **Shell completion** — via Click

### Export/Import

- **Streaming NDJSON export** with source prefix and date range filters
- **Multi-strategy import** — `add` (raw insert), `smart` (novelty + timestamp resolution), `smart+extract` (LLM for borderline conflicts)
- **Auto-backup before import** with `--no-backup` override
- **Source prefix remapping** during import (`--source-remap "old/=new/"`)

---

## [1.5.0] - March 5, 2026

### Multi-Auth: Prefix-Scoped API Keys

Create multiple API keys with different access levels (`read-only`, `read-write`, `admin`), each scoped to specific source prefixes. Five new endpoints under `/api/keys` plus a Web UI management page.

Your existing `API_KEY` env var continues to work as an implicit admin key. No migration needed.

---

## [1.4.0] - March 4, 2026

### Web UI v2

Complete redesign with sidebar navigation, 5 pages (Dashboard, Memories, Extractions, API Keys, Settings), Arkos-inspired dark/light theme, list+detail memory view, global semantic search, responsive mobile layout, and toast notifications.

---

## [1.3.0] - March 4, 2026

### Extraction MCP Tool

`memory_extract` MCP tool — synchronous wrapper around the async extraction API with internal polling and AUDN lifecycle management. Memories skill v2 restructured around Read, Write, and Maintain responsibilities.

---

## [1.2.0] - March 4, 2026

### Memories Skill

Claude Code skill for disciplined memory capture and proactive recall. Hard triggers for explicit requests, soft triggers for architectural decisions and deferred work. Eval results: +43.5% pass rate vs baseline.

---

## [1.1.0] - March 3, 2026

### Efficacy Eval Harness

A/B benchmarking framework measuring how Memories improves AI assistant performance. 11 YAML scenarios, deterministic rubric scoring, Claude Code executor with MCP isolation. Baseline: +0.86 overall delta.

---

## [1.0.0] - February 28, 2026

First stable release. Hybrid search (BM25 + vector), full CRUD API, MCP server, automatic extraction with AUDN, LLM provider abstraction (Anthropic, OpenAI, Ollama, ChatGPT OAuth), folder organization, ONNX embeddings, WebUI, interactive installer, cloud sync, and Docker multi-target builds.
