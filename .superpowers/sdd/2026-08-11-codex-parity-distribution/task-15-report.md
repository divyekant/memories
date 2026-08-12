# Task 15 report: ignore TOML multiline strings during root insertion

Base commit: `aab9803`.

## RED capture before production edits

Added two direct `insertMarkedBlockAtRoot` regressions in
`mcp-server/test/toml.test.mjs`. Each fixture has a root-level multiline
string containing a line beginning `# BEGIN ...` and a leading `[` line before
an actual `[profiles.a]` table. The tests require insertion after the closing
triple delimiter, before the real table, with the unmanaged bytes preserved
exactly. Existing malformed ownership-marker coverage remains in place.

Before changing production code:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Result: **RED**, 49 tests total, 47 passed, 2 failed. Both new tests failed on
`insertion must be after the closing delimiter`, demonstrating that the raw
`firstSection`/`firstMarkedBlock` scans selected lines inside the strings.

## Implementation

Exported the existing line-preserving `maskTomlMultilineStrings` helper from
`mcp-server/cli/lib/toml.mjs`, imported it into `codex.mjs` for the explicit
root-boolean status reader, and used masked lines for both root insertion scans
while retaining the original lines for slicing/insertion. Strict ownership
validation, earlier managed/foreign block ordering, idempotence, and existing
status behavior remain unchanged.

## GREEN and full validation

Focused JavaScript:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Result: **GREEN**, 49 passed, 0 failed.

Package suite:

```text
cd mcp-server && npm test
```

Result: **GREEN**, 262 passed, 0 failed. npm emitted only its existing
`minimum-release-age` configuration deprecation warning.

Relevant Python checks:

```text
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
```

Result: **GREEN**, 22 passed.

Full Python suite:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
```

Result: **GREEN**, 1807 passed, 1 pre-existing local-Qdrant payload-index
warning.

`git diff --check`: passed.

Hook syntax, project-hook rendering, and `npm pack --dry-run` were not run
because Task 15 changes only JavaScript TOML helpers/tests and does not modify
hook assets or packaging inputs; the package suite's existing pack contract
still passed.

## Residual risk

The helper intentionally masks until a closing delimiter on malformed or
exotic multiline input, preferring a false-negative structural scan over
treating prose as configuration. It is not a TOML validator; strict ownership
marker validation remains the separate fail-closed path.
