# Task 4 report: direct remote MCP OAuth and attribution

## Base and result

- Task 4 base: `2697ab5bb4e2f7a8588cb4419ff6f48db87a9c69`.
- Resulting task commit: this report is included in the commit carrying
  `feat(codex): support remote MCP OAuth setup`.

## TDD evidence

The CLI, Codex adapter, and remote attribution tests were written before the
implementation edits.

RED command:

```text
node --test mcp-server/test/cli.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/remote-server.test.mjs
```

RED result: the new CLI tests rejected the unknown `--mcp-url` flag, the
adapter test still rendered the local stdio block, and the remote test could
not import the missing `detectRemoteClient` export.

## GREEN verification

Focused Task 4 suite:

```text
node --test mcp-server/test/cli.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/remote-server.test.mjs
```

Result: **84 passed, 0 failed**.

Full MCP server suite:

```text
cd mcp-server && node --test
```

Result: **234 passed, 0 failed**.

`git diff --check`: passed.

## Follow-up safety correction: require canonical remote MCP URLs

The follow-up rejects URL-parser-normalized authority forms such as
`https:memory.example/mcp`, `https:///memory.example/mcp`, and
`https:\\memory.example\\mcp`. A remote URL must begin literally with
`https://`, have a parsed hostname, and serialize identically to the supplied
value; this preserves canonical percent-encoded paths and query strings while
preventing ambiguous normalization. Value validation now runs immediately
after command validation and before the Windows restriction log; target
combination checks remain after target resolution. The platform is injectable
for the ordering regression test.

RED command:

```text
node --test mcp-server/test/cli.test.mjs
```

RED result: test-file module loading failed because the new
`validateRemoteMcpUrl` export was not yet implemented.

GREEN verification:

```text
node --test mcp-server/test/cli.test.mjs
```

Result: **25 passed, 0 failed**.

```text
node --test mcp-server/test/cli.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/remote-server.test.mjs
```

Result: **89 passed, 0 failed**.

```text
cd mcp-server && node --test
```

Result: **239 passed, 0 failed**.

`git diff --check`: passed.

## Behavior

- `--mcp-url` is parsed separately from the REST `--url`, is accepted only
  for Codex init/update, and conflicts explicitly with `--url` and
  `--api-key` before dry-run or backend health/bootstrap work.
- Direct remote setup writes an installer-owned Codex URL/OAuth MCP block with
  the six current read-only approvals, omits backend API credentials, skips
  REST health/bootstrap, and prints the exact `codex mcp login memories`
  follow-up. Local stdio behavior and strict unmanaged/atomic marker handling
  remain unchanged.
- `detectRemoteClient(req)` uses case-insensitive Codex User-Agent precedence,
  then an allowed Claude origin or Claude User-Agent, with a neutral
  `remote-mcp` fallback. Attribution is passed only to the backend telemetry
  header; origin, bearer/OAuth authorization, and rate limiting do not consult
  it.

## Scope and risks

Only the six Task 4 implementation/test files and this report are changed.
No push, merge, release, deployment, or service mutation was performed.

## Follow-up safety correction: validate remote MCP URLs before setup

Review reproduced that a raw newline in `--mcp-url` was accepted and emitted
an invalid TOML string after hooks, config, and install state had already been
written. The follow-up validates absolute HTTPS URLs before any prompts, logs,
health/bootstrap calls, adapter calls, or filesystem mutation. Whitespace and
control characters, malformed/non-absolute URLs, non-HTTPS schemes,
credentials, and fragments are rejected; no localhost HTTP exception is
allowed for direct remote OAuth setup.

RED command:

```text
node --test mcp-server/test/cli.test.mjs
```

RED result: **22 passed, 1 failed**. The first newline/control case reached
setup instead of rejecting, so the matrix stopped at that assertion; the
failure demonstrated the missing preflight guard. The reproduction also
showed hooks, `config.toml`, and `state.json` were created for the malformed
URL; the remaining malformed/non-HTTPS/credential/fragment cases are covered
by the same matrix once the preflight is present.

GREEN verification:

```text
node --test mcp-server/test/cli.test.mjs mcp-server/test/adapter-codex.test.mjs mcp-server/test/remote-server.test.mjs
```

Result: **85 passed, 0 failed**.

```text
cd mcp-server && node --test
```

Result: **235 passed, 0 failed**.

`git diff --check`: passed.
