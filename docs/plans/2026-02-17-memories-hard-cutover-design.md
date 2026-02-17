# Memories Hard Cutover Design

**Date:** 2026-02-17  
**Status:** Approved for implementation  
**Owner:** Codex + dk

## Context

The codebase currently exposes mixed identity: product name "FAISS Memory", runtime service name `faiss-memory`, env vars `FAISS_*`, and integration docs/scripts built around those names. The project objective for this cycle is a hard cutover to a single external identity: **Memories**.

## Goals

1. Rename user-facing and operational identity to **Memories** across runtime, MCP, Docker, integrations, and docs.
2. Use `MEMORIES_*` environment variable names as the only supported names.
3. Rename deployment and cloud-backup defaults from `faiss-memory*` to `memories*`.
4. Keep existing API route paths and request/response schemas stable except identity strings.
5. Ship as PR only (no merge in this session).

## Non-Goals

1. No backend-engine replacement in this phase (FAISS internals stay as implementation detail).
2. No queueing/sharding/stable-ID architecture changes in this phase.
3. No dedicated migration/compatibility layer for old `FAISS_*` configs.

## Design Summary

### 1) Runtime/API identity

Update runtime identity strings in:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/app.py`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/memory_engine.py`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/runtime_memory.py`

Changes:
- logger namespaces: `faiss-memory*` -> `memories*`
- FastAPI title: `FAISS Memory API` -> `Memories API`
- `/health` service field: `faiss-memory` -> `memories`

### 2) MCP identity and env surface

Update:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/index.js`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package.json`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package-lock.json`

Changes:
- server name: `faiss-memory` -> `memories`
- env reads: `FAISS_URL`, `FAISS_API_KEY` -> `MEMORIES_URL`, `MEMORIES_API_KEY`
- package name `faiss-memory-mcp` -> `memories-mcp`

### 3) Deployment naming

Update:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.yml`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.snippet.yml`

Changes:
- service/container/image: `faiss-memory` -> `memories`
- compose tunables: `FAISS_IMAGE_TARGET`, `FAISS_MEM_LIMIT` -> `MEMORIES_IMAGE_TARGET`, `MEMORIES_MEM_LIMIT`
- comments/examples aligned to Memories naming

### 4) Integrations and docs

Update all docs and scripts under:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/README.md`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/PROJECT.md`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docs/`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/`

Changes:
- product references `FAISS Memory` -> `Memories`
- env references `FAISS_*` -> `MEMORIES_*`
- service key names and command examples `faiss-memory` -> `memories`
- helper function names using `_faiss` renamed to `_memories` where practical

### 5) Cloud-backup defaults

Update cloud default labels/prefix examples from `faiss-memory` to `memories`, including any tested defaults and docs references.

## Risks

1. Existing external configs using old names fail until manually updated.
2. Operators with old cloud prefix/folder names must update config to discover new snapshots.
3. Large search/replace may unintentionally alter historical/reference text if not reviewed.

## Risk Controls

1. Keep endpoint paths unchanged.
2. Run full test suite after rename.
3. Grep audit for leftover `FAISS_*`/`faiss-memory`/`FAISS Memory` references and intentionally keep only low-level technical internals (`import faiss`, `index.faiss`).

## Validation Plan

1. Unit/integration tests: `python -m pytest -q`
2. Config tests still passing with renamed compose settings.
3. Spot checks:
   - `/health` returns `service: "memories"`
   - MCP uses `MEMORIES_URL` / `MEMORIES_API_KEY`
   - integration hooks install and run with `MEMORIES_*` vars.

## Implementation Sequence

1. Add/adjust tests for rename expectations (TDD red).
2. Apply runtime + MCP + compose + integrations/doc updates (green).
3. Re-run full tests and targeted grep audits.
4. Commit on feature branch and prepare PR summary.
