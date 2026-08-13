# Task 13 report: retain current Codex install provenance

Base commit: `464f575` (`fix(codex): address integration review findings`).

## RED capture before production edits

Added behavioral regressions for:

- a fresh current Codex install writing `permissions.codex = []` and retaining
  that sentinel across a second install/update;
- repeated current installs preserving user-added exact seven legacy-looking
  rules and unrelated rules on uninstall;
- evidence-gated pre-manifest migration removing exactly the seven legacy rules,
  establishing the empty sentinel for later updates, and clearing it only after
  successful uninstall while preserving unrelated install-state data.

Command run before production changes:

```text
node --test mcp-server/test/adapter-codex.test.mjs
```

Result: **RED**, 31 tests total, 28 passed, 3 failed:

- fresh current install had no `permissions.codex` sentinel;
- repeated current install followed by uninstall removed the user-added seven
  legacy-looking rules instead of preserving them;
- pre-manifest migration had no persisted `permissions.codex` sentinel for the
  later update.

## GREEN validation

The provenance-retention fix always writes `permissions.codex = []` after a
successful current install/migration, preserving other install-state fields;
uninstall still clears the entry only after cleanup succeeds.

```text
node --test mcp-server/test/adapter-codex.test.mjs
31 passed, 0 failed

node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs
60 passed, 0 failed

cd mcp-server && npm test
259 passed, 0 failed

uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
22 passed, 0 failed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
1807 passed, 1 warning (Qdrant local payload-index warning), 0 failed

for f in mcp-server/assets/codex/hooks/*.sh integrations/codex/hooks/*.sh; do bash -n "$f"; done
ok

uv run python scripts/render_project_hooks.py --check
ok: .claude/settings.json hooks match hooks.json (10 events)

cd mcp-server && npm pack --dry-run
ok: memories-mcp@5.13.0 dry-run package, 59 files

git diff --check
ok
```

The failed first npm-pack invocation was from the repository root (which has no
package.json); the required `mcp-server/npm pack --dry-run` command passed on
the immediate rerun.
