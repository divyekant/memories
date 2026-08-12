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

## Task 15 follow-up: delimiter-context regression and validation

Base commit: `f9dd1c2`.

### RED capture before production edits

Added two valid-TOML regressions in `mcp-server/test/toml.test.mjs`: a comment
containing `developer_instructions = """` and an ordinary basic string
containing `'''`, each before `[profiles.a]`. Both assert root insertion before
the real table and exact preservation of the original bytes. The existing
triple-double fixture also includes an escaped-quote sequence to retain the
multiline basic-string close behavior.

Before changing the scanner:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Result: **RED**, 51 tests total, 49 passed, 2 failed. Both new tests failed on
`insertion must precede the real table`, because the old scanner entered a
multiline mode from delimiter-looking text and masked the actual table to EOF.

### Implementation and GREEN validation

`maskTomlMultilineStrings` now tracks comment state and ordinary single-line
basic/literal strings before recognizing triple delimiters. It retains
line-preserving masking for multiline bodies, escaped basic-string quote
handling, and conservative masking after an unterminated ordinary string
encounters a raw newline. No full TOML parser was introduced.

Focused JavaScript:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Result: **GREEN**, 51 passed, 0 failed.

Package suite:

```text
cd mcp-server && npm test
```

Result: **GREEN**, 264 passed, 0 failed. npm emitted only its existing
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

`git diff --check`: passed. Hook syntax, project-hook rendering, and package
dry-run checks were not rerun because this follow-up changes only the TOML
scanner/tests and does not touch hook assets or packaging inputs.

## Task 15 second follow-up: in-string ownership-marker regression and validation

Base commit: `6ca7488`.

### RED capture before production edits

Added triple-basic and triple-literal insertion regressions containing exact
`# BEGIN Owned` and `# END Owned` lines plus a fake table before `[profiles.a]`.
The tests require the in-string marker text and all unmanaged bytes to remain
unchanged while a real owned block is inserted at the root before the actual
table. Expanded insertion coverage to assert incomplete, duplicate, and
reversed external markers remain fail-closed. Added an append regression for
the in-string marker case.

Before changing production code:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Result: **RED**, 53 tests total, 51 passed, 2 failed. Both new multiline
ownership tests failed because raw `validateMarkedBlock` treated the in-string
markers as real ownership and insertion returned unchanged text.

### Implementation and GREEN validation

`validateMarkedBlock` now counts exact markers on masked lines while returning
the original line array and indexes for edits. `appendMarkedBlock` uses the
masked exact-begin-line check, preserving its legacy no-op behavior for an
actual incomplete begin marker while avoiding in-string short-circuits. Strict
upsert/removal and external malformed-marker behavior remain fail-closed.

Focused JavaScript:

```text
node --test mcp-server/test/toml.test.mjs mcp-server/test/adapter-codex.test.mjs
```

Result: **GREEN**, 54 passed, 0 failed.

The first full npm run exposed four idempotence failures in Claude/CLI tests:
the initial implementation had routed `appendMarkedBlock` through strict
validation, changing its established behavior for incomplete external markers.
After restoring the masked exact-begin check, the required package validation
was rerun successfully:

```text
cd mcp-server && npm test
```

Result: **GREEN**, 267 passed, 0 failed. npm emitted only its existing
`minimum-release-age` configuration deprecation warning.

Relevant Python checks:

```text
uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py
```

Result: **GREEN**, 22 passed.

Full Python validation was also run:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
```

Result: **GREEN**, 1807 passed, 1 pre-existing local-Qdrant payload-index
warning.

`git diff --check`: passed. Hook syntax, project-hook rendering, and package
dry-run checks were not rerun because no hook assets or packaging inputs
changed.
