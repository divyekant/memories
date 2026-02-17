# Memories Hard Cutover Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hard-cut over external identity from FAISS naming to Memories naming across runtime, deployment, MCP, integrations, and docs.

**Architecture:** Keep API route behavior stable while changing external naming surfaces (`FAISS_*` -> `MEMORIES_*`, `faiss-memory` -> `memories`, `FAISS Memory` -> `Memories`). Use test-first edits for behavioral assertions and a controlled rename pass for docs/integration surfaces.

**Tech Stack:** Python (FastAPI, pytest), Node (MCP server), Docker Compose, shell hooks, Markdown docs.

---

### Task 1: Add Failing Tests for New Naming Surface

**Files:**
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/tests/test_container_config.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/tests/test_metrics_api.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/tests/test_cloud_sync.py`

**Step 1: Write failing test expectations for Memories naming**
- Update assertions to require `memories` service/image naming and `MEMORIES_*` compose vars.
- Add/adjust health assertion to require `/health` response `service == "memories"`.
- Update cloud sync default-prefix expectations from `faiss-memory/` to `memories/`.

**Step 2: Run tests to verify RED state**
Run:
```bash
/Users/dk/projects/memories/.venv/bin/python -m pytest -q tests/test_container_config.py tests/test_metrics_api.py tests/test_cloud_sync.py
```
Expected:
- Failures showing old FAISS naming still present.

**Step 3: Commit test-only RED state (optional if policy allows local red commit)**
```bash
git add tests/test_container_config.py tests/test_metrics_api.py tests/test_cloud_sync.py
git commit -m "test: require Memories naming across config and health"
```

### Task 2: Implement Runtime/MCP/Compose Renames (Green)

**Files:**
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/app.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/memory_engine.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/runtime_memory.py`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/index.js`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package.json`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/mcp-server/package-lock.json`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.yml`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docker-compose.snippet.yml`

**Step 1: Update runtime identity fields and loggers**
- `FAISS Memory API` -> `Memories API`
- health payload `service: "memories"`
- logger namespace strings `faiss-memory*` -> `memories*`

**Step 2: Update MCP surface**
- env vars in MCP server to `MEMORIES_URL` and `MEMORIES_API_KEY`
- server name and package metadata to `memories`

**Step 3: Update Docker compose naming**
- service/image/container `memories`
- `MEMORIES_IMAGE_TARGET` and `MEMORIES_MEM_LIMIT` variables

**Step 4: Re-run target tests (Green)**
Run:
```bash
/Users/dk/projects/memories/.venv/bin/python -m pytest -q tests/test_container_config.py tests/test_metrics_api.py tests/test_cloud_sync.py
```
Expected:
- All targeted tests pass.

**Step 5: Commit implementation**
```bash
git add app.py memory_engine.py runtime_memory.py mcp-server/index.js mcp-server/package.json mcp-server/package-lock.json docker-compose.yml docker-compose.snippet.yml tests/test_container_config.py tests/test_metrics_api.py tests/test_cloud_sync.py
git commit -m "feat: hard-cutover runtime and deploy naming to Memories"
```

### Task 3: Rename Integrations and Docs, then Full Verification

**Files:**
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/README.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/PROJECT.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docs/architecture.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docs/decisions.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/docs/benchmarks/2026-02-17-memory-reclamation.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/QUICKSTART-LLM.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/openclaw-skill.md`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code/install.sh`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code/hooks/memory-recall.sh`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code/hooks/memory-query.sh`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code/hooks/memory-extract.sh`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code/hooks/memory-flush.sh`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code/hooks/memory-commit.sh`
- Modify: `/Users/dk/projects/memories/.worktrees/memories-hard-cutover/integrations/claude-code/hooks/hooks.json`

**Step 1: Rename all user-facing FAISS naming references to Memories naming**
- Product label strings.
- Env var names and shell variable defaults.
- Docker service names and command snippets.
- OpenClaw helper names suffixed `_memories` where practical.

**Step 2: Grep audit for stale external naming**
Run:
```bash
rg -n "FAISS_|FAISS Memory|faiss-memory" README.md PROJECT.md docs integrations mcp-server docker-compose.yml docker-compose.snippet.yml app.py
```
Expected:
- No external surface leftovers except intentionally retained low-level technical internals (e.g. `import faiss`, `index.faiss`).

**Step 3: Full verification**
Run:
```bash
/Users/dk/projects/memories/.venv/bin/python -m pytest -q
```
Expected:
- Full test suite passes.

**Step 4: Commit docs/integration cutover**
```bash
git add README.md PROJECT.md docs integrations
git commit -m "docs: rename user-facing identity and env examples to Memories"
```

### Task 4: PR Preparation (No Merge)

**Files:**
- No code changes required; may update notes in plan/design docs if needed.

**Step 1: Show branch diff summary**
Run:
```bash
git status --short
git log --oneline --decorate -n 8
git diff --stat origin/codex/memory-rss-hardening...HEAD
```

**Step 2: Request code review**
- Use superpowers `requesting-code-review` workflow against the branch range.

**Step 3: Prepare PR text**
Include:
- Summary of hard cutover scope
- Explicit no-compatibility decision
- Verification commands run + outcomes
- Known operator actions (update `MEMORIES_*`, service names, cloud prefix defaults)

**Step 4: Stop at PR-ready state**
- Do not merge.
