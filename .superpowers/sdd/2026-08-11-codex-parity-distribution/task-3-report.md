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
  only the six read-only Memories tools; `memory_is_useful` is a persistent
  feedback write and remains prompt-gated. Legacy settings rules are removed
  only when recorded in install-state, after the TOML write; provenance is
  cleared last and remains available when cleanup fails.
- Codex uninstall now removes only recorded settings rules; unrecorded rules
  are preserved because their ownership cannot be inferred safely.

## Follow-up safety correction

The review identified two safety gaps and the follow-up closes both:

- `memory_is_useful` POSTs persistent ranking feedback, so it was removed from
  the six-tool auto-approved set while recorded legacy feedback rules remain
  eligible for provenance-based migration cleanup.
- Incomplete, reversed, or duplicate installer markers now raise a specific
  `ERR_TOML_MARKED_BLOCK` error. Codex install propagates that failure before
  writing the config or cleaning legacy settings/provenance.

RED command:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/hooks.test.mjs mcp-server/test/adapter-claude-code.test.mjs mcp-server/test/cli.test.mjs
```

RED result: `64 passed, 9 failed`; failures covered the stale seven-tool
allowlist and the missing fail-closed marker/retry behavior.

GREEN verification:

- The same affected suite: **73 passed, 0 failed**.
- `node --test` from `mcp-server/`: **225 passed, 0 failed**.
- `git diff --check`: passed.

The deprecated shell installer now derives the same six-tool approval set. Its
focused regression passed:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -o addopts='' tests/test_installer.py -k 'does_not_auto_approve_feedback_writes'
```

Result: **1 passed**. The remaining installer tests pass with the pre-existing
expanded-lifecycle assertion excluded (`16 passed, 1 deselected`); that stale
assertion predates this follow-up and expects no `PreCompact` output even though
the shipped installer emits the accepted expanded lifecycle.

Follow-up commit: this report is included in the commit carrying
`fix(codex): keep feedback writes prompt-gated`.

## Follow-up safety correction: strict uninstall markers

The uninstall path now validates all three installer-owned TOML markers
(`Memories Codex notify`, `Memories Codex MCP`, and `Memories Codex developer
instructions`) before removing hooks, settings permissions, or provenance.
Missing, reversed, and duplicate marker pairs raise
`ERR_TOML_MARKED_BLOCK`; config, settings, hooks, and install-state remain
untouched so a retry is safe. The same validation contract is shared by the
strict removal and upsert paths.

RED command:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

RED result: the new strict-removal test could not import its missing helper,
and the uninstall regression did not reject the malformed marker (`16 passed,
2 failed`).

GREEN verification:

- TOML and Codex adapter tests: **31 passed, 0 failed**.
- Affected adapter, CLI, hooks, and TOML suite: **75 passed, 0 failed**.
- Full `node --test` from `mcp-server/`: **227 passed, 0 failed**.
- `git diff --check`: passed.

Follow-up commit: this report is included in the commit carrying
`fix(codex): fail closed on malformed uninstall markers`.

## Follow-up safety correction: validate config before install mutations

Codex install now prepares and validates the MCP TOML in memory before copying
hooks, rewriting `hooks.json`, writing config/settings, or clearing install
provenance. Missing-end, reversed, and duplicate MCP marker regressions seed
foreign hooks and preserve their directory and JSON byte-for-byte when install
fails closed.

RED command:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

RED result: the expanded regression failed after the malformed marker was
reported because install had already added managed hook entries to the foreign
`hooks.json` (`30 passed, 1 failed`).

GREEN verification:

- TOML and Codex adapter tests: **31 passed, 0 failed**.
- Affected adapter, CLI, hooks, and TOML suite: **75 passed, 0 failed**.
- Full `node --test` from `mcp-server/`: **227 passed, 0 failed**.
- `git diff --check`: passed.

Follow-up commit: this report is included in the commit carrying
`fix(codex): validate config before install mutations`.
