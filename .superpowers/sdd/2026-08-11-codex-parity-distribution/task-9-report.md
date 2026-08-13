# Task 9 report: Codex expanded-hook environment precedence

Base under test: `d369f8b5b74650e4a703687d2285af1d640831b4` (`d369f8b`).

## RED

After adding the five behavioral regressions and before changing production
hooks, this command failed as required:

```text
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and expanded and environment'
5 failed, 101 deselected in 0.33s
```

Each expanded hook was disabled by the conflicting env-file value, proving that
the process-exported configuration did not yet win.

## Changes

The five expanded Codex hooks now use the v5.13 environment-over-file snapshot,
source, and restore preamble. Their lifecycle payload/output contracts are
unchanged. Regression coverage asserts process URL, enabled state, API key, and
source precedence, with PostCompact remaining schema-valid and returning exactly
`{"suppressOutput":true}`.

The npm README and pack test document and assert local stdio setup, remote
`--mcp-url` OAuth plus `codex mcp login`, `--no-persist-api-key`, and the
`codex-cli >=0.146.0` ten-event versus older/unparseable five-event profiles.

## Validation

```text
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and expanded and environment'
5 passed, 101 deselected in 1.22s

uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex'
45 passed, 61 deselected in 28.30s

uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex or memory_hooks'
106 passed in 66.28s

node --test mcp-server/test/pack.test.mjs
2 passed, 0 failed

bash -n mcp-server/assets/codex/hooks/*.sh
exit 0

cd mcp-server && npm test
247 passed, 0 failed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
1807 passed, 1 warning in 158.87s

uv run python scripts/render_project_hooks.py --check
ok: .claude/settings.json hooks match hooks.json (10 events)

git diff --check
exit 0
```

The full Python suite warning is the existing local-Qdrant payload-index
warning; it is non-failing and unrelated to these hook changes.

Commit message: `fix(codex): preserve environment across expanded hooks`

The hash is intentionally recorded by the branch history rather than embedded
here, because this report is part of that commit and amending it changes the
commit hash.
(`fix(codex): preserve environment across expanded hooks`).
