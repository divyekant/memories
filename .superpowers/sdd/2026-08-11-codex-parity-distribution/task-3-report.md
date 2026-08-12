# Task 3 report: current Codex MCP approvals and server instructions

## Base and result

- Task 3 base: `82bb175db6b86248190224079008c0bf76abfca5`.
- Resulting task commit: this report is included in the commit carrying
  `feat(codex): use current MCP approvals and instructions`.

## TDD evidence

Marked-block, approval migration, and instructions tests were written before
the production edits.

RED command:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

RED result: both files failed at module loading because
`upsertMarkedBlock` and `READONLY_MCP_TOOL_NAMES` were not yet exported.

Instructions RED command:

```text
node --test mcp-server/test/lib-tools.test.mjs
```

RED result: the file failed at module loading because
`MEMORIES_MCP_INSTRUCTIONS` was not yet exported.

## GREEN verification

- `node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/lib-tools.test.mjs`: **32 passed**.
- `node --test` from `mcp-server/`: **223 passed, 0 failed**.
- `git diff --check`: passed.

## Files changed

- `mcp-server/cli/lib/toml.mjs`
- `mcp-server/cli/adapters/codex.mjs`
- `mcp-server/cli/lib/hooks.mjs`
- `mcp-server/test/toml.test.mjs`
- `mcp-server/test/adapter-codex.test.mjs`
- `mcp-server/lib-tools.mjs`
- `mcp-server/test/lib-tools.test.mjs`
- `.superpowers/sdd/2026-08-11-codex-parity-distribution/task-3-report.md`

## Behavior and risks

- Installer-owned Codex MCP blocks now refresh in place; unmanaged
  `[mcp_servers.memories]` sections are left byte-for-byte unchanged.
- Current Codex approval policy defaults to `prompt` and explicitly approves
  only the seven read-only Memories tools. Legacy settings rules are removed
  only when recorded in install-state, after the TOML write; provenance is
  cleared last and remains available when cleanup fails.
- Codex uninstall now removes only recorded settings rules; unrecorded rules
  are preserved because their ownership cannot be inferred safely.
