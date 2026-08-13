# Task 14 report: preserve pre-manifest cleanup across uninstall retries

Base commit: `552b965` (`fix(codex): retain current install provenance`).

## RED capture before production edits

Added a regression that seeds all exact legacy hook assets, seven legacy rules,
an unrelated rule, no install-state record, and malformed `.codex/hooks.json`.
The first uninstall must fail without deleting the evidence; after repairing
the JSON, retry must remove exactly the seven rules while preserving the
unrelated rule and leaving no inferred provenance state. Added documentation
assertions for the generic Claude Code/Cursor lifecycle row to distinguish its
compact-summary `MEMORY.md` synchronization from the Codex no-op contract.

Commands run before production changes:

```text
node --test mcp-server/test/adapter-codex.test.mjs
```

Result: **RED**, 32 tests total, 31 passed, 1 failed: malformed `hooks.json`
was parsed after `rm -r ~/.codex/hooks/memory`, so ownership evidence was lost.

```text
uv run pytest -q tests/test_codex_plugin.py
```

Result: **RED**, 5 tests total, 4 passed, 1 failed: the generic lifecycle
documentation did not contain the required compact-summary/MEMORY.md wording.

No production implementation or documentation files were changed before this
RED capture; only the new tests, assertions, plan entry, and this report were
added.

## GREEN validation

`codex.mjs` now parses every mutable JSON artifact before removing the owned
hooks directory. A malformed `hooks.json` therefore fails before any ownership
evidence or settings are changed; a repaired retry can still infer the exact
legacy ownership and remove only its seven rules. Existing strict TOML
preflight and durable recorded-state cleanup remain unchanged. The generic
README and GETTING_STARTED lifecycle rows now describe compact-summary search
and `MEMORY.md` synchronization; Codex-specific lifecycle text remains
unchanged.

Focused checks:

```text
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs
```

Result: **GREEN**, 61 tests passed, 0 failed.

```text
uv run pytest -q tests/test_codex_plugin.py
```

Result: **GREEN**, 5 passed.

Full/package checks:

- `cd mcp-server && npm test`: **260 passed**, 0 failed.
- `uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py`:
  **22 passed**.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q`: **1807 passed**, 1
  existing Qdrant local payload-index warning.
- `bash -n mcp-server/assets/codex/hooks/*.sh integrations/codex/hooks/*.sh`:
  **passed**.
- `uv run python scripts/render_project_hooks.py --check`: **passed**.
- `cd mcp-server && npm pack --dry-run`: **passed**, 59 files.
- `git diff --check`: **passed**.
