# Task 12 report: remaining Codex distribution review findings

Base commit: `039e82d` (`fix(codex): keep query output portable across jq versions`).

## RED capture before production edits

Behavioral regressions were added first for:

- developer-instructions marker placement outside the MCP marker and preservation of an edited managed block on update;
- pre-manifest legacy seven-rule cleanup only with exact hook ownership evidence on update/uninstall;
- remote `--mcp-url` lifecycle-hook transport warning without REST health/bootstrap;
- bare HTTPS authority normalization to a trailing slash;
- stale compact-summary lifecycle wording in the shipped guides.

Commands run on `039e82d`:

```text
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs
```

Result: **RED**, 57 tests total, 51 passed, 6 failed:

- fresh install nested the developer marker inside the MCP marker;
- update with on-disk legacy ownership evidence left all seven legacy settings rules;
- uninstall with on-disk legacy ownership evidence left all seven legacy settings rules;
- remote setup emitted no lifecycle-hook REST transport prerequisite message;
- `https://memory.example.com` was rejected instead of normalized;
- `validateRemoteMcpUrl('https://memory.example.com')` was rejected instead of returning the canonical URL.

```text
uv run pytest -q tests/test_codex_plugin.py
```

Result: **RED**, 5 tests total, 4 passed, 1 failed: the stale
`Re-injects memories using compact summary` row remained in the README/GETTING_STARTED
guides.

No production implementation files were changed before this RED capture.

## GREEN validation

Focused and package checks passed after the minimal implementation:

```text
node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs
57 passed, 0 failed

uv run pytest -q tests/test_codex_plugin.py
5 passed, 0 failed

cd mcp-server && npm test
256 passed, 0 failed

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

The final diff was reviewed for marker preflight, unmanaged TOML/settings
preservation, remote no-secret/no-REST behavior, and evidence-gated legacy
cleanup before staging.
