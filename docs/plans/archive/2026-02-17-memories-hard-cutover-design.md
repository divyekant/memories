# Memories Hard Cutover Design

**Date:** 2026-02-17  
**Status:** Approved for implementation  
**Owner:** Codex + dk

## Context

The codebase still contains legacy naming across runtime identity strings, environment variables, deployment labels, and integration docs/scripts. This cycle standardizes all external naming to **Memories**.

## Goals

1. Standardize user-facing and operational identity to **Memories** across runtime, MCP, Docker, integrations, scripts, and docs.
2. Use `MEMORIES_*` environment variables as the only documented and supported env naming.
3. Align cloud-backup defaults to `memories*` naming.
4. Keep existing API routes and payloads stable except identity strings.
5. Ship as PR-only work (no merge in this session).

## Non-Goals

1. No backend engine replacement in this phase.
2. No scale architecture changes in this phase (queueing, sharding, stable IDs).
3. No compatibility aliases for legacy names.

## Design Summary

### Runtime/API

Files:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/app.py`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/memory_engine.py`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/runtime_memory.py`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/cloud_sync.py`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/onnx_embedder.py`

Changes:
- logger namespaces standardized to `memories*`
- API title standardized to `Memories API`
- `/health` service value standardized to `memories`
- cloud sync default prefix standardized to `memories/`

### MCP and package identity

Files:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/index.js`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package.json`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package-lock.json`

Changes:
- MCP server identity standardized to `memories`
- MCP env names standardized to `MEMORIES_URL` and `MEMORIES_API_KEY`
- MCP package name standardized to `memories-mcp`

### Deployment and ops scripts

Files:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.yml`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.snippet.yml`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/scripts/backup.sh`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/scripts/install-cron.sh`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/scripts/backup-gdrive.sh`

Changes:
- service/container/image standardized to `memories`
- compose knobs standardized to `MEMORIES_IMAGE_TARGET` and `MEMORIES_MEM_LIMIT`
- backup script env names standardized to `MEMORIES_*`
- backup directory/folder defaults standardized to `memories*`

### Docs and integrations

Files:
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/README.md`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/PROJECT.md`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/GETTING_STARTED.md`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/CLOUD_SYNC_README.md`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/CLOUD_SYNC_DESIGN.md`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docs/`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/`
- `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/webui/index.html`

Changes:
- branding standardized to `Memories`
- examples standardized to `MEMORIES_*` env names
- commands standardized to `memories` service/image/container references

## Risks

1. External configs still using legacy names will fail until updated.
2. Operators using legacy backup prefixes/folder names must update settings.
3. Large text substitutions can introduce accidental wording regressions.

## Risk Controls

1. Keep API routes unchanged.
2. Run full test suite after rename.
3. Perform grep audit for legacy naming leftovers and manually review key docs/scripts.

## Validation Plan

1. Run targeted red/green tests for compose/health/cloud naming.
2. Run full suite: `python -m pytest -q`.
3. Grep audit ensures no remaining legacy naming references.

## Implementation Sequence

1. Write failing tests for new naming expectations.
2. Update runtime, MCP, compose, and cloud defaults to new names.
3. Rename integrations/docs/scripts/UI references.
4. Run full verification and prepare PR summary.
