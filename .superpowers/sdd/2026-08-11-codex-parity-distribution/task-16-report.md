# Task 16 Report: Claude Markdown Marker Idempotence

## Base and scope

- Base: `cd1301feb6218308759312583d0ab7fcd6b9b176`
- Review thread: `PRRT_kwDORQCPYs6YzGZ9`
- Scope: restore the format boundary between generic text marker appends and
  TOML-aware ownership handling; add a real Claude Code adapter regression.

## Root cause

`appendMarkedBlock` is shared with the Claude Code adapter, which writes
Markdown to `~/.claude/CLAUDE.md`. Task 15 changed that generic helper's marker
check to scan `maskTomlMultilineStrings(text)`. An apostrophe in ordinary
Markdown prose opened TOML string state and masked every following line,
including the existing Memories begin marker. Each repeat install therefore
appended another rules block.

## TDD evidence

RED on the unmodified production code at `cd1301f`:

```text
node --test mcp-server/test/adapter-claude-code.test.mjs mcp-server/test/toml.test.mjs
44 tests: 43 passed, 1 failed
FAIL install stays idempotent when existing CLAUDE.md prose contains an apostrophe
```

The failing assertion showed the second real adapter install appended a second
full `# BEGIN Memories Claude rules` block.

GREEN after the minimal format-boundary fix:

```text
node --test mcp-server/test/adapter-claude-code.test.mjs \
  mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
76 passed, 0 failed
```

`appendMarkedBlock` again uses a plain exact-line marker check. TOML-specific
`upsertMarkedBlock` performs masked validation and appends directly when no real
owned TOML block exists, so exact marker-looking text inside TOML multiline
strings remains ignored without parsing Markdown as TOML.

## Full verification

```text
cd mcp-server && npm test
272 passed, 0 failed

uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
22 passed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
1807 passed, 1 warning in 155.00s
```

The warning is the existing local-Qdrant payload-index warning from
`tests/test_benchmarks.py::TestSearchBenchmarks::test_hybrid_search_latency`.

Hook syntax, project-hook rendering, and package dry-run were not rerun because
this change touches only the shared marker helper and Node tests; the full npm
suite includes the package contract.

## Residual risk

The generic helper intentionally recognizes exact marker lines in any text,
including code examples. This is its pre-existing format-agnostic contract and
avoids interpreting Markdown syntax. TOML callers that need in-string marker
distinction continue to use strict masked validation before direct append.
