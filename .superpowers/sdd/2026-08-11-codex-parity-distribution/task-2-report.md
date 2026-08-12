# Task 2 report: version-aware expanded Codex lifecycle

## Base and head

- Task 2 base: `8cc255c1ef90ac2aab2485ab9e4d907f9eaaecba`.
- Branch head before Task 2: `8cc255c` (`fix(codex): propagate search reachability state`).
- Resulting task commit: this report is included in the commit carrying
  `feat(codex): expand the supported hook lifecycle` (inspect `git log -1`).

## TDD evidence

Profile/manifest tests were written before the implementation.

```text
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/hooks.test.mjs mcp-server/test/pack.test.mjs
```

RED result: `6 failed, 15 passed`; failures covered the missing version export,
expanded profile selection/status, lifecycle assets, and package entries.

Lifecycle payload tests were written before the five scripts.

```text
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (precompact or postcompact or subagent or session_end)'
```

RED result: `5 failed, 89 deselected`; the new lifecycle scripts were absent.

## GREEN verification

- `node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/hooks.test.mjs mcp-server/test/pack.test.mjs`: `22 passed`.
- `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (precompact or postcompact or subagent or session_end)'`: `5 passed, 89 deselected`.
- `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex'`: `39 passed, 55 deselected`.
- `bash -n mcp-server/assets/codex/hooks/*.sh`: passed.
- `git diff --check`: passed.

## Files changed

- `mcp-server/assets/codex/hooks/hooks.json`
- `mcp-server/assets/codex/hooks/hooks.legacy.json`
- `mcp-server/assets/codex/hooks/memory-flush.sh`
- `mcp-server/assets/codex/hooks/memory-rehydrate.sh`
- `mcp-server/assets/codex/hooks/memory-subagent-recall.sh`
- `mcp-server/assets/codex/hooks/memory-subagent-capture.sh`
- `mcp-server/assets/codex/hooks/memory-commit.sh`
- `mcp-server/cli/adapters/codex.mjs`
- `mcp-server/cli/lib/hooks.mjs`
- `mcp-server/test/adapter-codex.test.mjs`
- `mcp-server/test/hooks.test.mjs`
- `mcp-server/test/pack.test.mjs`
- `tests/test_claude_memory_hooks.py`
- `.superpowers/sdd/2026-08-11-codex-parity-distribution/task-2-report.md`

## Risks and deviations

- Codex detection is fail-closed: an unavailable or unparseable `codex --version`
  selects the legacy manifest. Tests can inject `ctx.codexVersion` or
  `ctx.execFileImpl`.
- SessionEnd performs the single `_extract_multi` enqueue operation and does not
  poll or sleep; the Codex manifest owns the three-second timeout.
- PostCompact and SubagentStart inject scoped candidate pointers directly into
  Codex `hookSpecificOutput.additionalContext`; Codex extraction defaults remain
  `codex/{project}`.

## Follow-up contract correction

- Follow-up base: `6c506e3dc74cddc5c87e3f05c81d52e7acb3b14d` (`feat(codex): expand the supported hook lifecycle`).
- Follow-up head: the commit carrying `fix(codex): honor lifecycle hook contracts` (inspect `git log -1`).

### RED evidence

The review regressions were added before the production corrections.

```text
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/hooks.test.mjs mcp-server/test/pack.test.mjs
RED result: 1 failed, 23 passed (foreign PreCompact was reported as expanded).

uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (precompact or postcompact or subagent or session_end)'
RED result: 3 failed, 2 passed, 90 deselected (invalid PostCompact output, parent transcript selection, and the multi-backend SessionEnd timeout).
```

### GREEN evidence

```text
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/hooks.test.mjs mcp-server/test/pack.test.mjs
24 passed.

uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (precompact or postcompact or subagent or session_end)'
5 passed, 90 deselected.

uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex'
40 passed, 55 deselected.

bash -n mcp-server/assets/codex/hooks/*.sh
passed.

git diff --check
passed.
```

### Corrections

- `memory-rehydrate.sh` is now a silent, schema-valid PostCompact hook that
  emits only `{ "suppressOutput": true }`; compact rehydration remains on
  `SessionStart(source=compact)`.
- `memory-subagent-capture.sh` prefers `agent_transcript_path` and falls back
  to `transcript_path` only when the child path is absent.
- `memory-commit.sh` selects the first routed extract backend and performs one
  direct enqueue POST with `curl --max-time 2`; it does not call `_extract_multi`,
  poll, or sleep.
- Codex status derives `expanded` only when every expanded lifecycle command is
  owned under the current hooks directory; a foreign `PreCompact` does not
  change an installed legacy profile.

Follow-up files: `mcp-server/assets/codex/hooks/memory-commit.sh`,
`mcp-server/assets/codex/hooks/memory-rehydrate.sh`,
`mcp-server/assets/codex/hooks/memory-subagent-capture.sh`,
`mcp-server/cli/adapters/codex.mjs`, `mcp-server/test/adapter-codex.test.mjs`,
`tests/test_claude_memory_hooks.py`, and this report.

The earlier risk notes about PostCompact context injection and `_extract_multi`
are superseded by these corrections.
