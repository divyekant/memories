# Enterprise CC + Codex Eval Isolation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first enterprise milestone: contamination-resistant eval isolation for Claude Code and Codex, with run manifests and hard prod guards.

**Architecture:** Add a small eval isolation layer that centralizes target validation, run identity, temp config roots, and cleanup scope. Extend the existing Claude Code eval path to use that layer, add a Codex smoke path for parity, and emit a manifest proving what ran and what was cleaned.

**Tech Stack:** Python, pytest, Docker Compose eval stack, Claude Code CLI, Codex CLI/config files, Memories REST API, MCP config JSON.

---

## Scope

This plan implements only Milestone 1 from:

- `docs/enterprise/2026-05-04-enterprise-cc-codex-memory-design.md`

It must not change retrieval ranking, extraction prompts, production config, deployed containers, or model selection.

## Files

- Create: `eval/isolation.py`
  - Owns eval target validation, run IDs, safe source prefixes, safe collection names, temp HOME roots, and manifest structure.
- Modify: `eval/memories_client.py`
  - Add guarded cleanup helpers that require exact run prefixes.
- Modify: `eval/cc_executor.py`
  - Accept an isolation context and run with temp `HOME`, strict MCP config, and forced single backend.
- Create: `eval/codex_executor.py`
  - Minimal Codex smoke executor using temp `HOME`, temp `.codex` config, and optional hooks disabled/enabled by test mode.
- Create: `eval/enterprise_smoke.py`
  - Orchestrates one Claude Code and one Codex smoke run against an eval target.
- Modify: `cli/commands/eval_cmd.py`
  - Add `memories eval enterprise-smoke --url http://localhost:8901 --dry-run`.
- Create: `tests/eval/test_isolation.py`
- Create: `tests/eval/test_enterprise_smoke.py`
- Modify: `eval/tests/test_cc_executor.py`
- Create: `eval/tests/test_codex_executor.py`
- Update docs only after behavior is implemented:
  - `docs/architecture.md`
  - `docs/deployment.md`

## Chunk 1: Isolation Core

### Task 1: Add eval target validation

**Files:**
- Create: `eval/isolation.py`
- Test: `tests/eval/test_isolation.py`

- [ ] **Step 1: Write failing tests for prod URL rejection**

```python
import pytest

from eval.isolation import EvalIsolationError, EvalTarget


@pytest.mark.parametrize("url", [
    "http://localhost:8900",
    "http://127.0.0.1:8900",
    "http://0.0.0.0:8900",
])
def test_eval_target_rejects_prod_urls_by_default(url):
    with pytest.raises(EvalIsolationError):
        EvalTarget(url=url, source_prefix="eval/enterprise/run-1", collection="memories_eval_run_1").validate()
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
uv run --group dev pytest tests/eval/test_isolation.py -q
```

Expected: fails because `eval.isolation` does not exist.

- [ ] **Step 3: Implement minimal target validation**

Create `eval/isolation.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


class EvalIsolationError(ValueError):
    pass


PROD_PORTS = {"8900"}
SAFE_PREFIX_ROOT = "eval/enterprise/"


@dataclass(frozen=True)
class EvalTarget:
    url: str
    source_prefix: str
    collection: str
    allow_prod: bool = False

    def validate(self) -> "EvalTarget":
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        port = str(parsed.port or "")
        if not self.allow_prod and host in {"localhost", "127.0.0.1", "0.0.0.0"} and port in PROD_PORTS:
            raise EvalIsolationError(f"refusing prod-like Memories URL: {self.url}")
        if not self.source_prefix.startswith(SAFE_PREFIX_ROOT):
            raise EvalIsolationError(f"unsafe eval source prefix: {self.source_prefix}")
        if self.source_prefix in {"", "eval/", SAFE_PREFIX_ROOT}:
            raise EvalIsolationError("source prefix must include a run id")
        if not self.collection.startswith("memories_eval_"):
            raise EvalIsolationError(f"unsafe eval collection: {self.collection}")
        return self
```

- [ ] **Step 4: Run tests and confirm pass**

Run:

```bash
uv run --group dev pytest tests/eval/test_isolation.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add eval/isolation.py tests/eval/test_isolation.py
git commit -m "feat(eval): add enterprise target isolation guard"
```

### Task 2: Add run context and manifest model

**Files:**
- Modify: `eval/isolation.py`
- Test: `tests/eval/test_isolation.py`

- [ ] **Step 1: Write failing test for manifest fields**

```python
from eval.isolation import EvalRunContext, EvalTarget


def test_run_context_manifest_contains_safety_fields(tmp_path):
    target = EvalTarget(
        url="http://localhost:8901",
        source_prefix="eval/enterprise/run-abc",
        collection="memories_eval_run_abc",
    ).validate()
    ctx = EvalRunContext.create(target=target, root=tmp_path, branch="enterprise/test", commit="abc123")
    manifest = ctx.manifest(client="claude-code", cleanup={"deleted_count": 0})

    assert manifest["target"]["url"] == "http://localhost:8901"
    assert manifest["target"]["source_prefix"] == "eval/enterprise/run-abc"
    assert manifest["target"]["collection"] == "memories_eval_run_abc"
    assert manifest["git"]["branch"] == "enterprise/test"
    assert manifest["git"]["commit"] == "abc123"
    assert manifest["client"] == "claude-code"
    assert "temp_home" in manifest["paths"]
```

- [ ] **Step 2: Implement `EvalRunContext`**

Add:

```python
import json
import tempfile
from pathlib import Path


@dataclass(frozen=True)
class EvalRunContext:
    target: EvalTarget
    run_id: str
    root: Path
    temp_home: Path
    branch: str
    commit: str

    @classmethod
    def create(cls, target: EvalTarget, root: Path | None = None, branch: str = "", commit: str = "") -> "EvalRunContext":
        safe = target.validate()
        base = root or Path(tempfile.mkdtemp(prefix="memories-enterprise-eval-"))
        temp_home = base / "home"
        temp_home.mkdir(parents=True, exist_ok=True)
        run_id = safe.source_prefix.rstrip("/").split("/")[-1]
        return cls(target=safe, run_id=run_id, root=base, temp_home=temp_home, branch=branch, commit=commit)

    def manifest(self, client: str, cleanup: dict) -> dict:
        return {
            "run_id": self.run_id,
            "client": client,
            "target": {
                "url": self.target.url,
                "source_prefix": self.target.source_prefix,
                "collection": self.target.collection,
            },
            "git": {"branch": self.branch, "commit": self.commit},
            "paths": {"root": str(self.root), "temp_home": str(self.temp_home)},
            "cleanup": cleanup,
        }

    def write_manifest(self, path: Path, client: str, cleanup: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.manifest(client=client, cleanup=cleanup), indent=2) + "\n")
```

- [ ] **Step 3: Run tests**

```bash
uv run --group dev pytest tests/eval/test_isolation.py -q
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add eval/isolation.py tests/eval/test_isolation.py
git commit -m "feat(eval): add enterprise run manifest context"
```

## Chunk 2: Cleanup Safety

### Task 3: Guard eval cleanup by exact run prefix

**Files:**
- Modify: `eval/memories_client.py`
- Test: `tests/eval/test_isolation.py`

- [ ] **Step 1: Write failing tests for unsafe cleanup prefixes**

```python
import pytest

from eval.isolation import assert_safe_cleanup_prefix, EvalIsolationError


@pytest.mark.parametrize("prefix", ["", "eval/", "eval/enterprise/", "codex/", "claude-code/", "learning/", "wip/"])
def test_rejects_broad_cleanup_prefixes(prefix):
    with pytest.raises(EvalIsolationError):
        assert_safe_cleanup_prefix(prefix)


def test_accepts_exact_enterprise_run_prefix():
    assert_safe_cleanup_prefix("eval/enterprise/run-123")
```

- [ ] **Step 2: Implement cleanup prefix assertion**

Add to `eval/isolation.py`:

```python
UNSAFE_CLEANUP_PREFIXES = {"", "eval/", "eval/enterprise/", "codex/", "claude-code/", "learning/", "wip/"}


def assert_safe_cleanup_prefix(prefix: str) -> str:
    normalized = prefix.strip()
    if normalized in UNSAFE_CLEANUP_PREFIXES:
        raise EvalIsolationError(f"refusing broad cleanup prefix: {prefix!r}")
    if not normalized.startswith(SAFE_PREFIX_ROOT):
        raise EvalIsolationError(f"cleanup prefix must start with {SAFE_PREFIX_ROOT}")
    if len(normalized.split("/")) < 3:
        raise EvalIsolationError("cleanup prefix must include a run id")
    return normalized
```

- [ ] **Step 3: Add guarded cleanup method**

Modify `eval/memories_client.py`:

```python
from eval.isolation import assert_safe_cleanup_prefix


def clear_enterprise_run(self, prefix: str, *, skip_snapshot: bool = True) -> int:
    safe_prefix = assert_safe_cleanup_prefix(prefix)
    return self.clear_by_prefix(safe_prefix, skip_snapshot=skip_snapshot)
```

- [ ] **Step 4: Run tests**

```bash
uv run --group dev pytest tests/eval/test_isolation.py eval/tests/test_memories_client.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add eval/isolation.py eval/memories_client.py tests/eval/test_isolation.py
git commit -m "feat(eval): guard enterprise cleanup prefixes"
```

## Chunk 3: Claude Code Isolation

### Task 4: Run Claude Code with temp HOME from eval context

**Files:**
- Modify: `eval/cc_executor.py`
- Modify: `eval/tests/test_cc_executor.py`

- [ ] **Step 1: Write failing test that env HOME is the temp home**

Mock `subprocess.run` and assert the `env["HOME"]` passed into Claude Code equals `ctx.temp_home`.

- [ ] **Step 2: Add optional `run_context` to `CCExecutor`**

Implementation shape:

```python
def __init__(..., run_context: EvalRunContext | None = None):
    ...
    self.run_context = run_context
```

In `run_prompt`:

```python
home = str(self.run_context.temp_home) if self.run_context else os.environ.get("HOME", "")
env["HOME"] = home
env["MEMORIES_BACKENDS_FILE"] = "__eval_single_backend__"
```

Keep `--strict-mcp-config`.

- [ ] **Step 3: Run CC executor tests**

```bash
uv run --group dev pytest eval/tests/test_cc_executor.py -q
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add eval/cc_executor.py eval/tests/test_cc_executor.py
git commit -m "feat(eval): isolate Claude Code HOME during enterprise eval"
```

## Chunk 4: Codex Smoke Isolation

### Task 5: Add minimal Codex executor

**Files:**
- Create: `eval/codex_executor.py`
- Create: `eval/tests/test_codex_executor.py`

- [ ] **Step 1: Write failing test for temp Codex config**

Assert executor creates:

- `<temp_home>/.codex/config.toml`
- `<temp_home>/.codex/hooks.json`
- MCP server pointing to eval URL
- no reference to real `~/.codex`

- [ ] **Step 2: Implement `CodexExecutor.create_config()`**

Use `EvalRunContext.temp_home` as `HOME`.

Minimal config should include:

```toml
[mcp_servers.memories]
command = "node"
args = ["<repo>/mcp-server/index.js"]

[mcp_servers.memories.env]
MEMORIES_URL = "http://localhost:8901"
MEMORIES_API_KEY = "<eval key>"
MEMORIES_BACKENDS_FILE = "__eval_single_backend__"
```

For smoke mode, hooks can be disabled unless explicitly testing hook behavior. That keeps first proof focused on isolation and MCP reachability.

- [ ] **Step 3: Add `run_prompt()` wrapper**

Command shape:

```python
cmd = ["codex", "exec", "--config", str(config_path), prompt]
```

If actual Codex CLI flags differ, adapt after checking `codex --help`, and capture that in the test.

- [ ] **Step 4: Run tests**

```bash
uv run --group dev pytest eval/tests/test_codex_executor.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add eval/codex_executor.py eval/tests/test_codex_executor.py
git commit -m "feat(eval): add isolated Codex smoke executor"
```

## Chunk 5: Enterprise Smoke Orchestration

### Task 6: Add dry-run enterprise smoke command

**Files:**
- Create: `eval/enterprise_smoke.py`
- Modify: `cli/commands/eval_cmd.py`
- Create: `tests/eval/test_enterprise_smoke.py`

- [ ] **Step 1: Write failing test for dry-run output**

The dry run should return a structure with:

- target URL
- source prefix
- collection
- temp home path
- clients planned: `claude-code`, `codex`
- cleanup scope
- `mutates: false`

- [ ] **Step 2: Implement dry-run planner**

No HTTP calls in dry-run.

- [ ] **Step 3: Add CLI command**

Command:

```bash
memories eval enterprise-smoke --url http://localhost:8901 --run-id smoke-001 --dry-run
```

Expected output includes:

```text
Enterprise eval smoke dry run
URL: http://localhost:8901
Source prefix: eval/enterprise/smoke-001
Clients: claude-code, codex
Mutates: false
```

- [ ] **Step 4: Run tests**

```bash
uv run --group dev pytest tests/eval/test_enterprise_smoke.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add eval/enterprise_smoke.py cli/commands/eval_cmd.py tests/eval/test_enterprise_smoke.py
git commit -m "feat(eval): add enterprise smoke dry run"
```

### Task 7: Add mutating smoke run against eval only

**Files:**
- Modify: `eval/enterprise_smoke.py`
- Modify: `tests/eval/test_enterprise_smoke.py`

- [ ] **Step 1: Write test that prod URL refuses mutating run**

Use `http://localhost:8900`; expect `EvalIsolationError`.

- [ ] **Step 2: Implement mutating smoke orchestration**

Flow:

1. Validate target.
2. Health check eval URL.
3. Clear exact run prefix only.
4. Seed one arbitrary non-derivable memory under `eval/enterprise/<run-id>/seed`.
5. Run Claude Code smoke if CLI available; otherwise record skipped with reason.
6. Run Codex smoke if CLI available; otherwise record skipped with reason.
7. Cleanup exact run prefix only.
8. Write manifest.

- [ ] **Step 3: Run tests**

```bash
uv run --group dev pytest tests/eval/test_enterprise_smoke.py tests/eval/test_isolation.py -q
```

Expected: pass.

- [ ] **Step 4: Manual smoke only after eval stack is up**

Commands:

```bash
docker compose -f docker-compose.eval.yml up -d
memories eval enterprise-smoke --url http://localhost:8901 --run-id smoke-local-001
```

Expected:

- refuses if URL is `http://localhost:8900`
- uses source prefix `eval/enterprise/smoke-local-001`
- writes manifest under `eval/results/enterprise-smoke-local-001.json`
- cleans only that prefix

- [ ] **Step 5: Commit**

```bash
git add eval/enterprise_smoke.py tests/eval/test_enterprise_smoke.py eval/results/.gitkeep
git commit -m "feat(eval): run enterprise smoke against isolated eval target"
```

## Chunk 6: Documentation and Completion Gate

### Task 8: Document safe enterprise eval workflow

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/deployment.md`

- [ ] **Step 1: Add architecture section**

Document:

- eval-only URL/port
- source-prefix guard
- temp HOME isolation
- manifest fields
- prod refusal behavior

- [ ] **Step 2: Add deployment/operator section**

Document:

```bash
docker compose -f docker-compose.eval.yml up -d
memories eval enterprise-smoke --url http://localhost:8901 --run-id <id> --dry-run
memories eval enterprise-smoke --url http://localhost:8901 --run-id <id>
```

- [ ] **Step 3: Run targeted tests**

```bash
uv run --group dev pytest \
  tests/eval/test_isolation.py \
  tests/eval/test_enterprise_smoke.py \
  eval/tests/test_cc_executor.py \
  eval/tests/test_codex_executor.py \
  eval/tests/test_memories_client.py \
  -q
```

Expected: pass.

- [ ] **Step 4: Run existing quick regression tests**

```bash
uv run --group dev pytest tests/test_codex_plugin.py tests/test_container_config.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md docs/deployment.md
git commit -m "docs(eval): document enterprise isolation workflow"
```

## Completion Audit

Before claiming Milestone 1 complete, verify:

- [ ] Current branch is not `main`.
- [ ] Worktree path is not `/Users/dk/projects/memories`.
- [ ] `git status --short` shows only intended changes.
- [ ] Prod containers `memories` and `qdrant` were not restarted or modified.
- [ ] Mutating smoke refuses `http://localhost:8900`.
- [ ] Mutating smoke accepts `http://localhost:8901`.
- [ ] Manifest exists and includes URL, prefix, collection, client, temp HOME, branch, commit, and cleanup.
- [ ] Cleanup deleted only `eval/enterprise/<run-id>`.
- [ ] Targeted tests pass.

## Build Approval Gate

Do not execute this plan until the user explicitly approves:

```text
Approved: build Milestone 1 eval isolation.
```
