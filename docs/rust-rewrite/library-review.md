# Library review for the clients and tools (task P0-5)

This document records the library check of task P0-5 of the Rust rewrite.
The check date is 2026-11-16. The reference server is the Python server
v5.19.0.

## Scope

The task updates the libraries that stay after the rewrite:

- The Node MCP (Model Context Protocol) server in `mcp-server/`.
- The Python CLI (command line interface) and the eval tools.
- The GitHub Actions.
- The base images of the clients.

The task does not update the libraries of the Python server (`fastapi`,
`uvicorn`, `pydantic`, `onnxruntime`, `qdrant-client`, `rank-bm25`, `openai`,
`anthropic`, `boto3`). The Python server is the parity reference, and the
Rust server replaces it.

## Supply-chain rules

| Rule | How this change obeys the rule |
|---|---|
| Exact versions | `mcp-server/package.json` has exact versions, with no `^` range. `zod`, which `lib-tools.mjs` imports, is now a direct dependency. The publish workflow installs `npm@12.2.2`, not `npm@latest`. The GitHub Actions are pinned to a full commit SHA, with the version in a comment. The base image of the remote MCP server has an exact version. `uv.lock` holds the exact Python versions. |
| No install scripts | `npm ci` runs with `--ignore-scripts` in CI, in the publish workflow and in `Dockerfile.remote`. No package in the lock file has an install script. |
| A committed lock file | `mcp-server/package-lock.json` and `uv.lock` are in the commit. |
| No release that is less than 2 days old | A script read the publish time of each of the 94 packages in `package-lock.json` from the npm registry on 2026-11-16. No package was less than 2 days old. The two Python updates are `click` 8.5.1 (2026-11-06) and `pytest` 9.2.0 (2026-10-27). The newest Action tag is from 2026-10-28. The image tag is from 2026-11-11. |

`npm audit` found 11 vulnerabilities (1 critical, 7 high, 3 moderate) before
the change, and 0 after the change. The critical one was in `proxy-addr`,
which the remote MCP server uses for `REMOTE_MCP_TRUST_PROXY`.

## Node MCP server

| Library | In use | New | Changes that break | What helps Memories |
|---|---|---|---|---|
| `@modelcontextprotocol/sdk` | 1.29.0 | 1.32.2 | See "MCP SDK changes" below. No API that Memories uses was removed. | Security fixes in the transitive libraries (`hono`, `@hono/node-server`, `zod`, `fast-uri`, `ip-address`). A size limit on HTTP request bodies. SSE keep-alive frames. |
| `express` | 4.22.2 | 5.3.0 | See "Express 5 changes" below. | The fix for `proxy-addr` (critical) and `qs`. Async handlers send a rejected promise to the error handler. The SDK already needs `express ^5.2.1`, so the project now has one copy of Express, not two. |
| `zod` | 4.3.6 (from the SDK, not declared) | 4.6.7 | None found. The 322 tests pass. | A fix for memory use with large constrained collections. |
| `js-yaml` | 4.1.1 | 4.3.2 | `1_000` is now a string, not a number. Memories reads no number from YAML. | Security limits `maxDepth` and `maxTotalMergeKeys`. |
| Node base image of `Dockerfile.remote` | `node:24-alpine` | `node:26.13.1-alpine` | Node 26 is the active LTS (long-term support) line since 2026-10. The 322 tests pass on Node 22 here, and CI runs them on Node 26. | Support for a longer time. |

### Why js-yaml stays on the 4.x line

`js-yaml` 5.5.0 is the newest release, but it changes the result of `load()`:

- Version 5 has no ESM default export. `import yaml from "js-yaml"` fails.
- `load('')` throws an error. Version 4 returns `undefined`. The error text of
  a project declaration changes.
- The default schema is the YAML 1.2 core schema. Merge keys (`<<`) and
  timestamps are not read as before.

A change of the parse result is a change of client behavior. Thus the update
uses 4.3.2, the newest release of the maintained `v4-legacy` line. Version 5
is on the list of later ideas.

### Express 5 changes

| Change | Effect on the remote MCP server |
|---|---|
| `req.body` is `undefined` when no body is read. Express 4 gave `{}`. | No effect. `register` and `token` use `body?.`, and `validateAuthorizeRequest` accepts `undefined`. The SDK transport answers 415 when the content type is not JSON, as before. |
| The default query parser is `simple`, not `extended`. | No effect for flat OAuth parameters. `client_id[a]=b` was an object and is now a key that does not match. The reply is `400 Unknown client_id.` in the two versions. |
| `res.status()` accepts only an integer from 100 to 999. | No effect. Each status in the code is a number literal or a status from `oauth.mjs`. |
| `res.redirect(url, status)` was removed. | No effect. The code uses `res.redirect(302, url)`. |
| 5.3.0: the body of a redirect is a full HTML page when the client accepts HTML. | The `Location` header does not change. A browser follows the header. |
| Path syntax (`path-to-regexp` 8). | No effect. All paths are literal. |
| `app.listen()` gives a listen error, for example `EADDRINUSE`, to its callback. Express 4 threw the error. | Without a fix, the server logs "listening" and exits with code 0 when the port is in use. `remote/server.mjs` now throws the error again, so the server exits with code 1, as before. A test covers this. |

The new test file `mcp-server/test/remote-http-compat.test.mjs` records the
replies of Express 4 for an empty body, a wrong content type and odd query
strings. The tests passed on Express 4 before the update and pass on
Express 5 after the update.

### MCP SDK changes (1.29.0 to 1.32.2)

| Release | Change | Effect on Memories |
|---|---|---|
| 1.30.0 | The stdio transport refuses one message that is larger than 10 MiB. | A request to the stdio server that is larger than 10 MiB fails. A Memories tool request is much smaller. |
| 1.30.0 | The transport reads the media type of `Content-Type`. It no longer looks for `application/json` as a substring. | A `/mcp` request with `text/plain; x=application/json` or `application/jsonl` got 400 (-32700) before and gets 415 now. A real client sends `application/json`. |
| 1.30.0 | The text of a tool input error (-32602) is a short sentence, not a JSON list of zod issues. For example: `Invalid input: expected string, received undefined at query`. | A model that reads the error sees new words. The error code does not change. The `tools/list` output does not change. |
| 1.30.0 | The Streamable HTTP transport sends a keep-alive comment (`: keepalive`) every 15 seconds on an SSE stream. | A comment line is ignored by SSE clients. |
| 1.30.1 | The Streamable HTTP transport refuses a body that is larger than 4 MiB with 413. | No effect. Express reads the body first (limit 1 MB) and gives it to the transport, and the SDK does not apply its limit to a body that it gets. |
| 1.32.0 | `tools/call` without `arguments` is accepted. | A request that failed before can now succeed. |
| 1.32.0 | The SDK client follows a redirect only within the same origin. | Only the smoke scripts use the SDK client. |
| 1.32.2 | `McpServer` sets the request handlers at construction when the capabilities are declared. | No effect. |

SDK v2 (`@modelcontextprotocol/server` and `@modelcontextprotocol/client`) is
stable since 2026-07-27. It is a new set of packages with a new API, and it
adds the MCP spec of 2026-07-28. The v1.x line gets fixes for at least six
months after 2026-07-27. The move to v2 is on the list of later ideas.

## Python CLI, eval tools and tests

The CLI, the eval tools and the Python server share one `pyproject.toml` and
one `uv.lock`. Thus an update of a CLI library also goes into the image of the
Python server.

| Library | Used by | In use | New | Changes that break | Decision |
|---|---|---|---|---|---|
| `click` | CLI, and the `uvicorn` command of the server image | 8.3.1 | 8.5.1 | 8.3.3: `default=True` on a boolean flag returns `True`. 8.4.0: `Parameter` is an abstract class. 8.5.0: the help option has the internal name `_click_default_help`, and `colorama` is not a dependency. 8.5.1: `Argument.get_help_record` returns a tuple. | Update. The CLI tests pass, and the server starts with `uvicorn`. See "CLI output check" below. |
| `httpx` | CLI, eval tools, server | 0.28.1 | 0.28.1 | None. Version 1.0 is a pre-release only (`1.0.dev6`). | No change. |
| `pyyaml` | eval tools, tests | 6.0.3 | 6.0.3 | None. | No change. |
| `pytest` | tests | 9.0.2 | 9.2.0 | None found for this suite. | Update. |

### CLI output check

The same 37 CLI commands ran with `click` 8.3.1 and with `click` 8.5.1
against the Python server v5.19.0. The commands were the help text of each
command, `count`, `search`, `get`, `list`, `folders`, `is-novel` and some
usage errors. The output was the same, with one exception:

| Command | `click` 8.3.1 | `click` 8.5.1 |
|---|---|---|
| `memories --bogus` | `Error: No such option: --bogus` | `Error: No such option '--bogus'.` |

The text goes to stderr, and the exit code stays 2. No hook or client reads
this text.

## GitHub Actions

| Action | In use | New | Changes that break | Effect on Memories |
|---|---|---|---|---|
| `actions/checkout` | v4 | v7.0.1 | v5: runtime Node 24. v6: the credentials go to a file in `$RUNNER_TEMP`. v7: no checkout of fork PR code on `pull_request_target` or `workflow_run`. | The workflows do not use these events. The release workflow pushes a tag with the credentials of checkout. Git commands on the runner can still use these credentials. Only a Docker container action needs runner v2.329.0 or later. The first release after the merge is the test of this step. |
| `actions/setup-node` | v4 | v7.1.0 | v5: cache on by default when a package manager is found. v6: `always-auth` removed. v7: no dummy `NODE_AUTH_TOKEN`. | No effect. The publish job sets `NODE_AUTH_TOKEN`. |
| `astral-sh/setup-uv` | v5 | v10.3.3 | v8: no major or minor tags. v9: `prune-cache` default `false`. v10: no automatic cache for the `release`, `pull_request_target` and `workflow_run` events. | No effect. CI sets `enable-cache: true`. A full SHA pin is necessary, because the tag `v10` does not exist. |

The publish workflow now uses Node 26. CI now also runs the tests of the MCP
server (`npm test`) on Node 26. Before this change, CI ran only
`node --check index.js`.

## Base images

| Image | Used by | Decision |
|---|---|---|
| `node:26.13.1-alpine` | `mcp-server/Dockerfile.remote` | Updated from `node:24-alpine`. |
| `python:3.11-slim`, `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` | `Dockerfile` (Python server) | No change. These images belong to the Python server. Task P0-4 makes the image of the Rust server. |

## Ideas for the time after the cutover

These ideas change results or client behavior. They are not in release
v6.0.0.

1. Move the MCP server to SDK v2 and the MCP spec of 2026-07-28.
2. Move to `js-yaml` 5. Decide the result of an empty project declaration,
   and of merge keys and timestamps.
3. Move to `httpx` 1.0 when it is stable.
4. Remove the `ah()` async wrapper of the remote MCP server. Express 5 sends a
   rejected promise to the error handler.
5. Test a newer embedding model than `all-MiniLM-L6-v2`. A new model changes
   the search results, and each install must embed its memories again.
6. Test a reranker after the hybrid search.
7. Review the extraction prompts with the eval suite.
8. Add `lockfile-lint` as a `preinstall` check of `mcp-server/`.
9. Remove the old `node >=20` floor from `mcp-server/package.json`. Node 20 is
   at end of life.
