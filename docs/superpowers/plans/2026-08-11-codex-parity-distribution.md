# Codex Parity and Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give current Codex clients reliability and lifecycle parity, current MCP configuration, direct remote OAuth setup, portable plugin bootstrap, and a documented native-memory coexistence policy.

**Architecture:** Keep the npm installer as the sole owner of user-specific Codex wiring. Extend the existing Codex assets with tested current-client and legacy profiles, publish shared MCP instructions from `buildServer`, and add a separate remote MCP URL path without changing the REST backend path. Preserve all unmanaged configuration and use install-state provenance for legacy cleanup.

**Tech Stack:** Bash hooks, Node.js ESM, Node test runner, Python 3.11/pytest, MCP SDK, Codex `config.toml`, Markdown.

## Global Constraints

- Base is Memories v5.12.0 at `f50b05d`.
- Current-client behavior targets validated `codex-cli 0.146.0`; older or unparseable versions retain the five-hook profile.
- Every production behavior change follows red-green-refactor; record the failing command before implementation.
- Do not change backend data models, search ranking, or authentication decisions.
- Do not force native Codex Memories on or off.
- Never overwrite an unmanaged `[mcp_servers.memories]` block or remove unowned permission rules.
- `SessionEnd` timeout is exactly 3 seconds.
- Local stdio remains the default; `--url` remains the REST backend URL; `--mcp-url` means a remote Streamable HTTP MCP endpoint.
- Remote client attribution affects telemetry only and must never affect authorization.
- Do not implement Windows hooks, publish a release, merge, deploy, or push.

---

### Task 1: Codex Hook Runtime Reliability Parity

**Files:**
- Modify: `mcp-server/assets/codex/hooks/_lib.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-recall.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-query.sh`
- Modify: `tests/test_claude_memory_hooks.py`

**Interfaces:**
- Consumes: the existing Codex `_memory_client_prefix`, `_default_source_prefixes`, `_default_extract_source`, `_load_backends`, and hook JSON output shapes.
- Produces: `_memories_active`, `_resolve_backends_file`, `_hook_deadline_start`, `_hook_budget_exhausted`, `_hook_call_budget`, `_breaker_file_for`, `_should_trip_breaker`, routed `_health_check`, and deadline-aware Codex recall calls with the same semantics as the Claude hook runtime.

- [ ] **Step 1: Add failing Codex parity cases to the existing hook harness**

Add Codex variants that execute `CODEX_HOOKS_DIR / "memory-recall.sh"` for the already-covered Claude contracts. The minimum new assertions are:

```python
def test_codex_memory_hooks_unconfigured_url_is_silent_noop(tmp_path: Path) -> None:
    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": "/Users/example/memories", "source": "startup"},
        responses=[],
        extra_env={"MEMORIES_URL": ""},
    )
    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""


def test_codex_memory_hooks_enabled_false_wins_over_url(tmp_path: Path) -> None:
    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": "/Users/example/memories", "source": "startup"},
        responses=[],
        extra_env={"MEMORIES_ENABLED": "false"},
    )
    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""
```

Also add Codex coverage for explicit backends-file precedence, routed health excluding a non-routed dead backend, an already-open breaker, per-backend breaker isolation, tiny-budget clean JSON output, 401 credential wording, and materially-short timeout attribution. Reuse harness helpers; expected sources remain `codex/{project}`.

- [ ] **Step 2: Run the Codex parity cases and verify RED**

Run:

```bash
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (unconfigured or enabled_false or backends_file or routed or breaker or budget or 401 or timeout)'
```

Expected: failures showing Codex still calls localhost when unconfigured, lacks per-backend/deadline state, or emits the wrong warning.

- [ ] **Step 3: Port the minimal runtime guarantees into the Codex hook library**

Port the v5.12 Claude implementations and their required callers while retaining Codex-specific prefix and payload behavior. Required precedence and constants are:

```bash
# MEMORIES_DISABLED truthy -> off
# MEMORIES_ENABLED explicitly truthy/falsy -> obey it
# otherwise active only when MEMORIES_URL or a resolved backends file exists
_MEMORIES_HOOK_BUDGET_MS_DEFAULT=5000
_MEMORIES_BREAKER_FAIR_BUDGET_RATIO="${MEMORIES_BREAKER_FAIR_BUDGET_RATIO:-0.75}"
```

Health must probe every routed search backend in parallel, publish down-name state on every return path, and use `_breaker_file_for "$name"`. Search timeout exit 28 may trip only when `_should_trip_breaker` says the supplied budget was fair. `memory-recall.sh` must initialize the deadline before health/search, stop new calls when exhausted, and still emit valid Codex hook JSON with gathered candidates. `memory-query.sh` must use the same activation, resolver, breaker, and authenticated-search diagnostics.

- [ ] **Step 4: Run the targeted parity suite and verify GREEN**

Run:

```bash
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex or memory_hooks'
```

Expected: all selected tests pass with no new warnings from the hook scripts.

- [ ] **Step 5: Run all hook tests**

Run:

```bash
uv run pytest -q tests/test_claude_memory_hooks.py tests/test_codex_notify_hook.py
```

Expected: both files pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add mcp-server/assets/codex/hooks tests/test_claude_memory_hooks.py
git commit -m "fix(codex): match hook runtime reliability guarantees"
```

---

### Task 2: Version-Aware Expanded Codex Lifecycle

**Files:**
- Create: `mcp-server/assets/codex/hooks/hooks.legacy.json`
- Create: `mcp-server/assets/codex/hooks/memory-flush.sh`
- Create: `mcp-server/assets/codex/hooks/memory-rehydrate.sh`
- Create: `mcp-server/assets/codex/hooks/memory-subagent-recall.sh`
- Create: `mcp-server/assets/codex/hooks/memory-subagent-capture.sh`
- Create: `mcp-server/assets/codex/hooks/memory-commit.sh`
- Modify: `mcp-server/assets/codex/hooks/hooks.json`
- Modify: `mcp-server/cli/adapters/codex.mjs`
- Modify: `mcp-server/cli/lib/hooks.mjs`
- Modify: `mcp-server/test/adapter-codex.test.mjs`
- Modify: `mcp-server/test/remote-server.test.mjs`
- Modify: `mcp-server/test/hooks.test.mjs`
- Modify: `tests/test_claude_memory_hooks.py`
- Modify: `mcp-server/test/pack.test.mjs`

**Interfaces:**
- Consumes: Task 1's deadline-aware Codex `_lib.sh`; `renderHooksJson`; `copyHookScripts`; `ctx.codexVersion` test override.
- Produces: exported `supportsExpandedHooks(versionText): boolean`; expanded `hooks.json`; five-event `hooks.legacy.json`; `ctx.codexHookProfile` status detail; five Codex-native lifecycle scripts.

- [ ] **Step 1: Write failing version/profile and manifest tests**

Add pure version assertions:

```javascript
assert.equal(adapter.supportsExpandedHooks('codex-cli 0.146.0'), true);
assert.equal(adapter.supportsExpandedHooks('codex-cli 0.145.9'), false);
assert.equal(adapter.supportsExpandedHooks('unknown'), false);
```

Install with `ctx.codexVersion = 'codex-cli 0.146.0'` and assert the rendered events are exactly:

```javascript
[
  'PostCompact', 'PostToolUse', 'PreCompact', 'PreToolUse',
  'SessionEnd', 'SessionStart', 'Stop', 'SubagentStart',
  'SubagentStop', 'UserPromptSubmit',
]
```

Assert `SessionEnd` has timeout `3`. Install with `0.145.9` and assert the five legacy events remain. Assert all five new scripts are copied and packed.

- [ ] **Step 2: Run profile tests and verify RED**

Run:

```bash
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/hooks.test.mjs mcp-server/test/pack.test.mjs
```

Expected: missing export, legacy manifest, lifecycle scripts, and expanded events.

- [ ] **Step 3: Add the version-aware manifest selection**

Implement semantic numeric comparison for the first `major.minor.patch` in `versionText`. `install(ctx)` uses `ctx.codexVersion` when supplied; otherwise it invokes `codex --version` through an injectable `ctx.execFileImpl` and selects legacy on any error. Add the profile to the install/status log. Preserve foreign hooks through `mergeHookSettings`.

`hooks.legacy.json` is the current v5.12 five-event manifest. `hooks.json` adds:

```json
{
  "PreCompact": "memory-flush.sh",
  "PostCompact": "memory-rehydrate.sh",
  "SubagentStart": "memory-subagent-recall.sh",
  "SubagentStop": "memory-subagent-capture.sh",
  "SessionEnd": "memory-commit.sh"
}
```

Use existing timeouts for the first four and exactly 3 seconds for `SessionEnd`.

- [ ] **Step 4: Write failing payload tests for every new lifecycle script**

Use documented Codex fields: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `trigger`, `agent_id`, `agent_type`, and `last_assistant_message`. Assert:

- PreCompact submits extraction with `context=pre_compact`;
- PostCompact returns JSON containing `hookSpecificOutput.additionalContext` from scoped candidates;
- SubagentStart returns additional context with Codex project candidates;
- SubagentStop submits the subagent transcript/last message with `context=subagent_stop`;
- SessionEnd submits once with `context=session_end`, exits promptly, and never polls the queued job.

- [ ] **Step 5: Run lifecycle payload tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (precompact or postcompact or subagent or session_end)'
```

Expected: scripts are missing or do not yet emit Codex-compatible output.

- [ ] **Step 6: Implement the five lifecycle scripts**

Adapt the corresponding Claude scripts, but source the Codex `_lib.sh`, keep `codex/{project}` extraction defaults, and consume Codex snake_case payloads. All scripts call `_exit_if_disabled` first. SessionEnd performs one enqueue request and exits; no extraction-status polling or sleep is allowed.

- [ ] **Step 7: Run lifecycle and adapter tests and verify GREEN**

Run:

```bash
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/hooks.test.mjs mcp-server/test/pack.test.mjs
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex'
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add mcp-server/assets/codex/hooks mcp-server/cli/adapters/codex.mjs mcp-server/cli/lib/hooks.mjs mcp-server/test tests/test_claude_memory_hooks.py
git commit -m "feat(codex): expand the supported hook lifecycle"
```

---

### Task 3: Current Codex MCP Approvals and Server Instructions

**Files:**
- Modify: `mcp-server/cli/lib/toml.mjs`
- Modify: `mcp-server/cli/adapters/codex.mjs`
- Modify: `mcp-server/test/toml.test.mjs`
- Modify: `mcp-server/test/adapter-codex.test.mjs`
- Modify: `mcp-server/lib-tools.mjs`
- Modify: `mcp-server/test/lib-tools.test.mjs`

**Interfaces:**
- Consumes: installer-owned marked blocks and install-state permission provenance.
- Produces: `upsertMarkedBlock(text, marker, body)`; current Codex per-tool approvals; exported `MEMORIES_MCP_INSTRUCTIONS`; initialize responses carrying instructions.

- [ ] **Step 1: Write failing marked-block and approval migration tests**

Add:

```javascript
test('upsertMarkedBlock replaces only the owned block', () => {
  const old = appendMarkedBlock('model = "x"\n', 'Owned', 'old = true');
  const next = upsertMarkedBlock(old, 'Owned', 'new = true');
  assert.match(next, /model = "x"/);
  assert.doesNotMatch(next, /old = true/);
  assert.match(next, /new = true/);
});
```

Update adapter tests to assert the installer-owned MCP block contains:

```toml
default_tools_approval_mode = "prompt"
[mcp_servers.memories.tools.memory_search]
approval_mode = "approve"
```

Assert all six read-only tools are approved and no write/delete/update/feedback tool is approved. Seed a v5.12 managed block plus recorded legacy settings permissions, run update/install, and assert the TOML is replaced while only recorded legacy settings rules are removed. An unmanaged MCP section must remain byte-for-byte unchanged.

- [ ] **Step 2: Run TOML/adapter tests and verify RED**

Run:

```bash
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Expected: missing `upsertMarkedBlock`, approval tables, or migration cleanup.

- [ ] **Step 3: Implement owned-block refresh and current approvals**

`upsertMarkedBlock` replaces text only between the exact begin and end lines generated by `appendMarkedBlock`, appending when absent. The Codex adapter uses it only for installer-owned MCP blocks. Keep the default prompt mode and emit one nested approval table per `READONLY_MCP_TOOL_NAMES`; export that name array from `cli/lib/hooks.mjs` rather than duplicating it. After successful config write, remove only install-state-recorded legacy Codex rules and clear that permission record last.

- [ ] **Step 4: Write a failing initialize-instructions test**

Connect an in-memory MCP client and assert:

```javascript
assert.match(client.getInstructions() ?? '', /exact project-scoped/i);
```

Also assert `MEMORIES_MCP_INSTRUCTIONS.slice(0, 512)` contains the exact project-scoped read rule.

- [ ] **Step 5: Run the instructions test and verify RED**

Run:

```bash
node --test mcp-server/test/lib-tools.test.mjs
```

Expected: initialize result has no instructions.

- [ ] **Step 6: Publish concise shared MCP instructions**

Define one exported instruction string in `lib-tools.mjs` and pass it through the MCP SDK server options used by `buildServer`. The opening text must state: search exact project-scoped sources before broad search; hook candidates are pointers, not a substitute for active search. Follow with user/assistant evidence treatment, `memory_add` versus `memory_extract`, and explicit delete handling.

- [ ] **Step 7: Run Task 3 tests and verify GREEN**

Run:

```bash
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/lib-tools.test.mjs
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 3**

```bash
git add mcp-server/cli mcp-server/test mcp-server/lib-tools.mjs
git commit -m "feat(codex): use current MCP approvals and instructions"
```

---

### Task 4: Direct Remote MCP OAuth and Correct Attribution

**Files:**
- Modify: `mcp-server/cli/index.mjs`
- Modify: `mcp-server/cli/adapters/codex.mjs`
- Modify: `mcp-server/test/cli.test.mjs`
- Modify: `mcp-server/test/adapter-codex.test.mjs`
- Modify: `mcp-server/remote/server.mjs`
- Modify: `mcp-server/test/remote-server.test.mjs`

**Interfaces:**
- Consumes: Task 3's installer-owned MCP block generation and approval tables.
- Produces: parsed `mcpUrl?: string`; Codex URL/OAuth MCP blocks; exported `detectRemoteClient(req): 'codex' | 'claude-web' | 'remote-mcp'`.

- [ ] **Step 1: Write failing CLI parse and validation tests**

Assert:

```javascript
assert.equal(parseArgs(['init', '--codex', '--mcp-url', 'https://memory.example/mcp']).mcpUrl,
  'https://memory.example/mcp');
assert.throws(() => parseArgs(['init', '--mcp-url']), /Missing value/);
```

End-to-end init with `--codex --mcp-url https://memory.example/mcp --yes` must not call REST health/bootstrap, must write a URL-based block containing `auth = "oauth"`, must contain no backend API key, and must log `codex mcp login memories`. Reject `--mcp-url` combined with `--url`, `--api-key`, or a non-Codex target with a specific error.

- [ ] **Step 2: Run CLI/adapter tests and verify RED**

Run:

```bash
node --test mcp-server/test/cli.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Expected: unknown flag or local stdio configuration is written.

- [ ] **Step 3: Implement the distinct remote MCP path**

Add `--mcp-url` to help and parsing. Validate combinations before dry-run or health checks. Set `ctx.mcpUrl`; skip REST health/bootstrap when present. The adapter emits:

```toml
[mcp_servers.memories]
url = "https://memory.example/mcp"
auth = "oauth"
default_tools_approval_mode = "prompt"
```

followed by the six read-only approval tables from Task 3. Preserve local stdio behavior when absent and unmanaged-section protection in both modes.

- [ ] **Step 4: Write failing remote-attribution tests**

Using an injected backend `fetchImpl`, call `memory_stats` through `/mcp` and assert `X-Memories-Client` for:

```javascript
{ 'User-Agent': 'codex-cli/0.146.0' }          // codex
{ Origin: 'https://claude.ai' }               // claude-web
{ 'User-Agent': 'generic-mcp-client/1.0' }    // remote-mcp
```

Also assert the same authorization outcome for all three metadata variants under the same auth mode.

- [ ] **Step 5: Run remote tests and verify RED**

Run:

```bash
node --test mcp-server/test/remote-server.test.mjs
```

Expected: all calls currently carry `claude-web`.

- [ ] **Step 6: Implement conservative telemetry-only detection**

Export `detectRemoteClient(req)`. Case-insensitively detect `codex` in User-Agent first, then an allowed Claude origin or `claude` in User-Agent, else `remote-mcp`. Pass the result to `buildServer({ client })` inside the `/mcp` handler. Do not read it in origin guards, bearer auth, OAuth, rate limiting, or any authorization branch.

- [ ] **Step 7: Run Task 4 tests and verify GREEN**

Run:

```bash
node --test mcp-server/test/cli.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/remote-server.test.mjs
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add mcp-server/cli mcp-server/remote/server.mjs mcp-server/test
git commit -m "feat(codex): support remote MCP OAuth setup"
```

---

### Task 5: Portable Plugin Bootstrap, Native Coexistence, and Shipped Documentation

**Files:**
- Modify: `plugins/memories/skills/setup/SKILL.md`
- Modify: `plugins/memories/.codex-plugin/plugin.json`
- Modify: `tests/test_codex_plugin.py`
- Modify: `mcp-server/cli/adapters/codex.mjs`
- Modify: `mcp-server/test/adapter-codex.test.mjs`
- Modify: `README.md`
- Modify: `GETTING_STARTED.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: expanded/current and legacy profile status from Task 2; local and remote MCP status from Task 4.
- Produces: portable `$memories:setup`; native-memory status details; synchronized lifecycle, remote setup, and coexistence documentation.

- [ ] **Step 1: Write failing plugin and status tests**

Change `tests/test_codex_plugin.py` to require:

```python
assert "npx -y memories-mcp@latest init --codex" in setup_text
assert "--mcp-url" in setup_text
assert "codex mcp login memories" in setup_text
assert "integrations/claude-code/install.sh" not in setup_text
assert "mcp-server/index.js" not in setup_text
```

Add adapter status tests for a config containing:

```toml
[features]
memories = true
[memories]
disable_on_external_context = true
```

Expected status details include `native memories: enabled` and `external-context dedupe: enabled`. Cover explicit false and unset as `disabled` and `not explicitly configured`.

- [ ] **Step 2: Run plugin/status tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_codex_plugin.py
node --test mcp-server/test/adapter-codex.test.mjs
```

Expected: deprecated checkout assertions or missing native status.

- [ ] **Step 3: Implement portable setup and conservative native status**

Rewrite the setup skill around the published npm installer. Present local setup first and direct remote setup second. Never request or print an API key value. Update manifest descriptions/default prompts to say the plugin guides setup while the npm installer owns hooks and MCP wiring.

In `status(ctx)`, parse only explicit TOML assignments inside `[features]` and `[memories]`; do not guess inherited/profile/managed settings. Report `enabled`, `disabled`, or `not explicitly configured`. Do not write either setting.

- [ ] **Step 4: Update shipped docs and changelog**

Document:

- current Codex gets ten lifecycle events; older/unparseable versions get five;
- v5.10-v5.12 reliability guarantees now apply to Codex;
- `SessionEnd` is enqueue-only with a three-second timeout;
- `--mcp-url` versus backend `--url` and the required OAuth login;
- npm installer ownership and portable plugin bootstrap;
- external Memories is durable cross-client authority, native Codex Memories is optional local derived cache;
- `memories.disable_on_external_context = true` is optional and never set automatically.

Add an `[Unreleased]` changelog section; do not bump versions.

- [ ] **Step 5: Run Task 5 tests and documentation checks**

Run:

```bash
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/pack.test.mjs
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Commit Task 5**

```bash
git add plugins/memories tests/test_codex_plugin.py mcp-server/cli/adapters/codex.mjs mcp-server/test/adapter-codex.test.mjs README.md GETTING_STARTED.md docs/architecture.md CHANGELOG.md
git commit -m "docs(codex): ship the current integration workflow"
```

---

### Task 6: Whole-Branch Contract and Packaging Verification

**Files:**
- Modify only files required to fix failures caused by Tasks 1-5.

**Interfaces:**
- Consumes: all prior task contracts.
- Produces: a clean, packable branch with no version bump and no unrelated changes.

- [ ] **Step 1: Run the complete Node suite**

```bash
cd mcp-server && npm test
```

Expected: zero failures.

- [ ] **Step 2: Run the complete Python suite**

```bash
uv run pytest -q
```

Expected: zero failures; pre-existing third-party warnings may remain.

- [ ] **Step 3: Inspect the package contents**

```bash
cd mcp-server && npm pack --dry-run
```

Expected: expanded and legacy Codex hook manifests plus all five new scripts are included; no test fixtures, credentials, or worktree files are included.

- [ ] **Step 4: Verify release and diff hygiene**

```bash
git diff --check origin/develop...HEAD
git status --short
git diff --stat origin/develop...HEAD
```

Expected: clean checks, no version changes, and only approved Codex integration/docs/plan/spec files.

- [ ] **Step 5: Handle verification failures as a new exact task**

If Steps 1-4 expose a regression, stop Task 6 and append a new numbered correction task to this plan before editing. That task must name the exact test and production files, capture the failing command, require the covering test to fail on the pre-fix commit, and provide its exact staging command. If no correction is required, do not create an empty commit.

---

### Task 7: Align the Deprecated Installer Contract with the Expanded Codex Manifest

**Files:**
- Modify: `tests/test_installer.py`

**Failure captured:**

```bash
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
```

On commit `ed3faff`, `tests/test_installer.py::test_codex_install_writes_standalone_hooks_json`
fails because it asserts that `PreCompact` is absent even though Task 2 made
the shipped `integrations/codex/hooks/hooks.json` compatibility target the
expanded ten-event manifest.

- [ ] **Step 1: Re-run the exact failing test on the pre-fix commit**

```bash
uv run pytest -q tests/test_installer.py::test_codex_install_writes_standalone_hooks_json
```

Expected: failure at the stale `PreCompact`-absent assertion.

- [ ] **Step 2: Update only the deprecated-installer test contract**

Assert that the compatibility installer writes and reports all ten shipped
Codex events, including the five new lifecycle scripts, while retaining the
existing standalone `hooks.json`, MCP, permissions, and developer-instructions
assertions. No production file changes are required.

- [ ] **Step 3: Run focused and full verification**

```bash
uv run pytest -q tests/test_installer.py::test_codex_install_writes_standalone_hooks_json
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
uv run pytest -q
```

Expected: zero failures.

- [ ] **Step 4: Stage and commit the exact correction**

```bash
git add tests/test_installer.py docs/superpowers/plans/2026-08-11-codex-parity-distribution.md
git commit -m "test(codex): align legacy installer lifecycle coverage"
```

---

### Task 8: Close Final Task 4/5 Acceptance Gaps

**Files:**
- Modify: `mcp-server/cli/index.mjs`
- Modify: `mcp-server/test/cli.test.mjs`
- Modify: `mcp-server/cli/adapters/codex.mjs`
- Modify: `mcp-server/test/adapter-codex.test.mjs`
- Modify: `mcp-server/assets/claude-code/skills/memories/SKILL.md`
- Modify: `integrations/QUICKSTART-LLM.md`
- Replace: `plugins/memories/skills/memories` symlink with a self-contained copied skill directory
- Modify: `tests/test_codex_plugin.py`

**Failures captured on `9905c01`:**

- `run(['help', '--mcp-url', <invalid>])` logs help before raw URL validation.
- `status(ctx)` treats `[features]` and assignments inside TOML multiline strings
  as real root configuration.
- The npm-shipped Memories skill and `integrations/QUICKSTART-LLM.md` still
  describe the obsolete five-hook/settings.json/repo-checkout Codex flow.
- The repo-local plugin's Memories skill is an out-of-tree symlink, so copying
  the plugin directory alone does not produce a self-contained package.
- Remote client-attribution invariance is covered only with authentication
  disabled, not by an authenticated OAuth request matrix.

- [ ] **Step 1: Add focused failing regressions**

Add a help-path invalid-URL no-log test, a multiline-TOML status false-positive
test, and plugin tests that reject symlinks/out-of-tree dependencies and assert
the current lifecycle/setup contract in both shipped guides. Add authenticated
OAuth tool calls carrying Codex, Claude, and generic metadata; all must have the
same successful authorization outcome while forwarding only the expected
telemetry client header.

- [ ] **Step 2: Implement minimal corrections**

Validate a provided raw MCP URL before the help early return. Make the explicit
root-boolean reader ignore TOML basic/literal multiline string bodies without
becoming a general config resolver. Synchronize the shipped skill and quickstart
with the accepted Task 4/5 behavior. Replace the out-of-tree skill symlink with
a real copied directory whose bytes match the npm canonical skill.

- [ ] **Step 3: Verify focused and full contracts**

```bash
node --test mcp-server/test/cli.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/pack.test.mjs
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
cd mcp-server && npm test
uv run pytest -q
git diff --check
```

- [ ] **Step 4: Stage and commit exact files**

```bash
git add mcp-server/cli/index.mjs mcp-server/test/cli.test.mjs \
  mcp-server/cli/adapters/codex.mjs mcp-server/test/adapter-codex.test.mjs \
  mcp-server/test/remote-server.test.mjs \
  mcp-server/assets/claude-code/skills/memories/SKILL.md \
  integrations/QUICKSTART-LLM.md plugins/memories/skills/memories \
  tests/test_codex_plugin.py docs/superpowers/plans/2026-08-11-codex-parity-distribution.md
git commit -m "fix(codex): close parity acceptance gaps"
```

### Task 9: Preserve Process Environment Across Expanded Codex Hooks

**Files:**
- Modify: `mcp-server/assets/codex/hooks/memory-flush.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-rehydrate.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-subagent-recall.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-subagent-capture.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-commit.sh`
- Modify: `tests/test_claude_memory_hooks.py`
- Modify: `mcp-server/README.md`
- Modify: `mcp-server/test/pack.test.mjs`
- Create (forced-added): `.superpowers/sdd/2026-08-11-codex-parity-distribution/task-9-report.md`

**RED command:**

```bash
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and expanded and environment'
```

The new regressions must fail on base `d369f8b` because each expanded hook currently
allows conflicting `~/.config/memories/env` values to override process-exported
`MEMORIES_*` values. They must behaviorally cover URL/enabled precedence for all
five hooks, include API key/source precedence where practical, and verify that
PostCompact returns schema-valid `{"suppressOutput":true}` when the process says
enabled while the file says disabled. Package assertions must document local Codex
setup, remote `--mcp-url` OAuth setup plus `codex mcp login`, `--no-persist-api-key`,
and the >=0.146 ten-event versus older/unparseable five-event distinction.

**Implementation and validation:**

After recording RED, update only the five named scripts to use the existing
environment-over-file loading semantics without changing lifecycle output contracts.
Update the npm README truthfully and add packaging assertions. Run the focused tests,
`bash -n` for all Codex scripts, `cd mcp-server && npm test`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q`,
`uv run python scripts/render_project_hooks.py --check`, and `git diff --check`.
Inspect status and diff; the worktree must be clean after the commit.

**Exact staging and commit scope:**

```bash
git add docs/superpowers/plans/2026-08-11-codex-parity-distribution.md \
  mcp-server/assets/codex/hooks/memory-flush.sh \
  mcp-server/assets/codex/hooks/memory-rehydrate.sh \
  mcp-server/assets/codex/hooks/memory-subagent-recall.sh \
  mcp-server/assets/codex/hooks/memory-subagent-capture.sh \
  mcp-server/assets/codex/hooks/memory-commit.sh \
  tests/test_claude_memory_hooks.py mcp-server/README.md \
  mcp-server/test/pack.test.mjs \
  .superpowers/sdd/2026-08-11-codex-parity-distribution/task-9-report.md
git commit -m "fix(codex): preserve environment across expanded hooks"
```

---

### Task 10: Omit API Keys from Local Codex TOML on `--no-persist-api-key`

**Files:**
- Modify: `mcp-server/cli/adapters/codex.mjs`
- Modify: `mcp-server/test/adapter-codex.test.mjs`
- Modify: `mcp-server/test/cli.test.mjs` (if needed for CLI-level generation)
- Modify: `mcp-server/README.md` (only if wording needs precision)
- Create (forced-added): `.superpowers/sdd/2026-08-11-codex-parity-distribution/task-10-report.md`
- Modify: this plan

**Contract:**

- `--no-persist-api-key` makes the local Codex TOML `[mcp_servers.memories].env` block omit `MEMORIES_API_KEY` entirely while preserving `MEMORIES_URL` and `MEMORIES_CLIENT`.
- Default persistence remains unchanged: without the flag, local Codex TOML still contains `MEMORIES_API_KEY` when an API key is supplied.
- Remote OAuth setup remains unchanged and contains no API key.
- Hooks may read `MEMORIES_API_KEY` from the process environment; do not change hook behavior.

- [ ] **Step 1: Add the failing adapter/CLI regressions and capture RED**

Assert explicit secret absence for local `--no-persist-api-key`, plus preserved URL/client fields, default persistence, and unchanged remote OAuth output. Run the focused adapter/CLI tests on the current HEAD before production edits and record the failing command/output in the Task 10 report.

- [ ] **Step 2: Implement the smallest Codex TOML generation fix**

Follow the JSON-file/Claude adapter precedent and TOML formatting safety. Remove only the API-key assignment from the local environment block when the no-persist option is active; retain all other fields and unmanaged configuration.

- [ ] **Step 3: Verify focused, package, and repository contracts**

Run the focused adapter/CLI/pack tests, `cd mcp-server && npm test`, targeted Python Codex/plugin/installer tests if affected, `bash -n` for changed shell scripts, and `git diff --check`. Verify the final worktree contains only the owned files before commit.

- [ ] **Step 4: Commit the exact owned files (do not push)**

```bash
git add docs/superpowers/plans/2026-08-11-codex-parity-distribution.md \
  mcp-server/cli/adapters/codex.mjs mcp-server/test/adapter-codex.test.mjs \
  mcp-server/test/cli.test.mjs mcp-server/README.md \
  .superpowers/sdd/2026-08-11-codex-parity-distribution/task-10-report.md
git commit -m "fix(codex): honor non-persistent API keys"
```

---

### Task 11: Keep Codex Query Output Portable Across jq Versions

**Files:**
- Modify: `mcp-server/assets/codex/hooks/memory-query.sh`
- Modify: `integrations/codex/hooks/memory-query.sh`
- Modify: `tests/test_claude_memory_hooks.py`
- Modify: this plan
- Create (forced-added): `.superpowers/sdd/2026-08-11-codex-parity-distribution/task-11-report.md`
- Modify only if required to prevent generated-hook drift: `scripts/render_project_hooks.py` or its tests

**Observed CI failure:**

Ubuntu CI job `94000510019` in workflow run `31560110060` uses an older jq parser and rejects the object field expression at line 410:
`additionalContext: (if ... end) + (if ... end)` with `syntax error, unexpected '+', expecting '}'`.
The local jq 1.8.1 parser accepts it. The four affected cases are the minimal reminder, named-backend 401, search reachability, and configured-default 401 tests; both shipped hook copies are currently byte-identical and contain the expression.

- [ ] **Step 1: Add/execute a portable-jq regression and capture RED**

Use TDD and systematic debugging to reproduce the parser failure with jq 1.6/1.7 (an Ubuntu 24.04/Linux container or another supported older jq environment). Add a regression at the narrowest existing hook-test boundary if feasible; otherwise record the exact parser command and the four failing test command/output in the Task 11 report before changing production hooks.

- [ ] **Step 2: Identify the root cause and implement the minimal portable expression**

Parenthesize or reshape only the `additionalContext` object value so jq 1.6/1.7 and jq 1.8 accept it without changing emitted JSON or reminder/credential/search wording. Keep `mcp-server/assets/codex/hooks/memory-query.sh` and `integrations/codex/hooks/memory-query.sh` synchronized; follow the repository generation mechanism if it defines a source of truth.

- [ ] **Step 3: Verify all affected contracts**

Run the four exact CI failures, relevant Codex query tests, the full Python suite, `bash -n` for both query hooks, the project hook render check, and `git diff --check`. Confirm the two shipped copies remain byte-identical and inspect status/diff.

- [ ] **Step 4: Commit and push the exact owned files (do not merge)**

```bash
git add docs/superpowers/plans/2026-08-11-codex-parity-distribution.md \
  mcp-server/assets/codex/hooks/memory-query.sh integrations/codex/hooks/memory-query.sh \
  tests/test_claude_memory_hooks.py
git add -f .superpowers/sdd/2026-08-11-codex-parity-distribution/task-11-report.md
git commit -m "fix(codex): keep query output portable across jq versions"
git push origin codex/codex-parity-distribution
```

### Task 12: Close Remaining Codex Distribution Review Findings

**Files:**
- Modify: `mcp-server/cli/adapters/codex.mjs`
- Modify: `mcp-server/cli/index.mjs`
- Modify: `mcp-server/cli/lib/toml.mjs` only if required by the canonical URL/marker fix
- Modify: `mcp-server/test/adapter-codex.test.mjs`
- Modify: `mcp-server/test/cli.test.mjs`
- Modify: `README.md`
- Modify: `GETTING_STARTED.md`
- Modify: `integrations/QUICKSTART-LLM.md`
- Modify: `plugins/memories/skills/setup/SKILL.md`
- Modify: `mcp-server/README.md`
- Modify: `tests/test_codex_plugin.py` only if the documentation contract requires it
- Create (forced-added): `.superpowers/sdd/2026-08-11-codex-parity-distribution/task-12-report.md`

**Scope:**

Resolve the remaining PR #93 review findings without broadening installer ownership or
weakening marker/permission preflight behavior:

1. Fresh Codex initialization must place the developer-instructions marked block outside
   the MCP marked block. Updating after a user edits the managed developer instructions
   must preserve that edit, and uninstall remains strict/atomic.
2. Remote `--mcp-url` setup must preserve hook installation for users with a separately
   configured REST backend, but log an explicit actionable statement that lifecycle hooks
   require `MEMORIES_URL` or a `backends.yaml` REST configuration and are inactive without
   one. Remote mode still skips REST health/bootstrap and writes no secret. Documentation
   must distinguish remote MCP tools from REST hook transport.
3. For pre-manifest Codex installs with no recorded Codex rule ownership, exact on-disk
   installer ownership evidence (owned hooks directory/known hook assets) permits update
   and uninstall to remove only the exact legacy seven settings rules, including
   `memory_is_useful`, while preserving unrelated/user rules. Machines without ownership
   evidence must not receive a broad fallback cleanup; current recorded ownership stays
   exact.
4. `--mcp-url https://memory.example.com` is accepted and generated TOML uses the
   canonical normalized `https://memory.example.com/`. Malformed/noncanonical authority,
   credentials, fragments, whitespace/control, raw backslashes, malformed percent escapes,
   and non-HTTPS inputs remain rejected. Cover the pure validator and end-to-end atomic
   behavior.
5. Correct stale lifecycle documentation rows in `README.md` and `GETTING_STARTED.md`
   that describe `memory-rehydrate.sh` as re-injecting a compact summary; they must match
   the later `suppressOutput`/`SessionStart(source=compact)` behavior.

**TDD / RED capture before production edits:**

Add behavioral regressions first, then run each focused command on `039e82d` and record
the expected failures in Task 12's report before changing production code:

```bash
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs
uv run pytest -q tests/test_codex_plugin.py
```

The RED cases must cover marker placement and edited-instruction preservation, strict
atomic uninstall, remote hook warning plus no REST bootstrap/secret, evidence-gated
legacy seven-rule cleanup on update/uninstall, canonical URL normalization and rejection
matrix, and the stale lifecycle wording in the shipped docs.

**Implementation and validation:**

Implement the smallest adapter/CLI/TOML and documentation changes. Preserve all strict
marker preflight semantics, unmanaged blocks/rules, remote setup atomicity, and exact
ownership checks. Run:

```bash
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs
cd mcp-server && npm test
cd ..
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
for f in mcp-server/assets/codex/hooks/*.sh integrations/codex/hooks/*.sh; do bash -n "$f"; done
uv run python scripts/render_project_hooks.py --check
cd mcp-server && npm pack --dry-run
cd ..
git diff --check
git status --short
```

Review the final diff for security/ownership regressions and verify only the named files
plus the forced report are staged.

**Exact staging and commit:**

```bash
git add docs/superpowers/plans/2026-08-11-codex-parity-distribution.md \
  mcp-server/cli/adapters/codex.mjs mcp-server/cli/index.mjs \
  mcp-server/cli/lib/toml.mjs mcp-server/test/adapter-codex.test.mjs \
  mcp-server/test/cli.test.mjs README.md GETTING_STARTED.md \
  integrations/QUICKSTART-LLM.md plugins/memories/skills/setup/SKILL.md \
  mcp-server/README.md tests/test_codex_plugin.py
git add -f .superpowers/sdd/2026-08-11-codex-parity-distribution/task-12-report.md
git commit -m "fix(codex): address integration review findings"
git push origin codex/codex-parity-distribution
```
