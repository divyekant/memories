# Memories Hard Cutover Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hard-cut over all external naming to Memories naming across runtime, deployment, MCP, integrations, docs, and ops scripts.

**Architecture:** Keep API behavior stable while standardizing naming surfaces to `Memories` and `MEMORIES_*`. Use test-first verification for behavior assertions and controlled rename passes for docs/scripts/integrations.

**Tech Stack:** Python (FastAPI, pytest), Node (MCP server), Docker Compose, shell scripts, Markdown docs.

---

### Task 1: Add Failing Tests for Naming Expectations

**Files:**
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/tests/test_container_config.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/tests/test_metrics_api.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/tests/test_cloud_sync.py`

**Steps:**
1. Update tests to require `memories` compose identity and `MEMORIES_*` config names.
2. Add health assertion for `service == "memories"`.
3. Update cloud prefix default expectation to `memories/`.
4. Run target tests and confirm RED.

### Task 2: Implement Runtime/MCP/Compose/Cloud Renames

**Files:**
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/app.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/memory_engine.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/runtime_memory.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/cloud_sync.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/onnx_embedder.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/index.js`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package.json`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package-lock.json`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.yml`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.snippet.yml`

**Steps:**
1. Standardize runtime identity strings/loggers.
2. Standardize MCP env names and server/package identity.
3. Standardize compose service/image/container and resource vars.
4. Re-run target tests and confirm GREEN.

### Task 3: Rename Integrations, Docs, Scripts, and UI Labels

**Files:**
- Modify docs and integrations under:
  - `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/README.md`
  - `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/PROJECT.md`
  - `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docs/`
  - `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/`
  - `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/scripts/`
  - `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/webui/index.html`

**Steps:**
1. Standardize all user-facing labels to `Memories`.
2. Standardize env examples to `MEMORIES_*`.
3. Standardize command examples to `memories` service names.
4. Run grep audit for leftover legacy naming tokens.

### Task 4: Verification and PR Preparation

**Steps:**
1. Run full test suite and capture output.
2. Review git diff/stat for consistency.
3. Keep branch in PR-ready state.
4. Do not merge.
