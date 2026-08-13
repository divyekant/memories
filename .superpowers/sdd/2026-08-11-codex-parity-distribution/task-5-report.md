# Task 5 report: Codex plugin portability, status, and documentation parity

## Base and result

- Task 5 started from `d682846` and was verified on the shared branch after
  the Task 4 canonical-URL follow-up (`c2ab2ed`).
- Commit: inspect `git log -1` for the commit carrying
  `docs(codex): ship the current integration workflow`.

## TDD evidence

The plugin/docs assertions and the explicit-root status matrix were added
before the corresponding production and documentation edits.

Initial RED commands:

```text
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
RED: 16 passed, 4 failed. The new plugin/docs assertions failed against the
checkout-based setup text and stale manifest; the existing installer test also
failed because it still expects no expanded Codex lifecycle events.

node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/pack.test.mjs
RED: 19 passed, 2 failed. The new status cases failed because status had no
explicit-root native-memory or external-context dedupe reporting.
```

## Changes

- `plugins/memories/skills/setup/SKILL.md` now presents portable local stdio
  setup first, the direct remote HTTPS/OAuth command and exact login command,
  credential-safe verification, lifecycle facts, coexistence guidance, and
  npm-installer ownership. It no longer requires a checkout, shell installer,
  local server path, or npm install from the repository.
- `plugins/memories/.codex-plugin/plugin.json` keeps version `5.12.0`, removes
  the write capability claim, and describes the plugin as a thin guide whose
  published npm installer owns wiring.
- `mcp-server/cli/adapters/codex.mjs` reports only explicit boolean assignments
  in exact root `[features]` and `[memories]` tables. It ignores comments,
  nested/profile/managed/array-of-table sections, preserves existing status
  details, and never writes `config.toml`.
- `README.md`, `GETTING_STARTED.md`, `docs/architecture.md`, and
  `CHANGELOG.md` describe the published installer, local-vs-remote setup,
  version-aware five/ten-event lifecycle, PostCompact/compact recall and
  SessionEnd timing, six read-only approvals with prompt-gated feedback,
  native-memory coexistence, and v5.10-v5.12 Codex reliability parity.
- `tests/test_codex_plugin.py` and
  `mcp-server/test/adapter-codex.test.mjs` provide plugin, docs, and status
  regression coverage.

## GREEN verification

```text
uv run pytest -q tests/test_codex_plugin.py
3 passed

node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/pack.test.mjs
22 passed, 0 failed

cd mcp-server && node --test
240 passed, 0 failed

git diff --check
passed
```

The requested combined Python command currently reports `19 passed, 1
failed`: the one failure is the pre-existing
`tests/test_installer.py::test_codex_install_writes_standalone_hooks_json`
assertion that rejects `PreCompact`, despite the accepted expanded lifecycle
already shipped by Task 2. That test is outside Task 5's owned files and was
not changed.

## Risks

- The native settings parser is intentionally conservative and status-only; it
  does not parse or infer profile/managed configuration.
- The deprecated shell installer still has an older standalone lifecycle
  expectation in its test fixture; the published npm installer and full MCP
  suite use the current version-aware Codex profile.
