---
id: fh-002
type: feature-handoff
audience: internal
topic: CLI
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Feature Handoff: CLI (Full-Coverage Command-Line Interface)

## Summary

The Memories CLI is a full-coverage command-line interface wrapping all ~38 REST API endpoints. It is built with Click (Python) and designed agent-first: when stdout is piped (non-TTY), all output is JSON with a structured envelope; when run interactively in a terminal, output is human-readable with color. Installation is `pip install memories` and the entry point is `memories --help`.

## Architecture

### Data Flow

```
CLI flags / config file / env vars
        |
        v
  resolve_config()  -->  { url, api_key, _sources }
        |
        v
  Click app skeleton (cli/__init__.py)
        |
        v
  Shared Context object  { client: MemoriesClient, fmt: OutputFormatter }
        |
        v
  9 command modules (cli/commands/*.py)
        |
        v
  MemoriesClient (httpx)  -->  HTTP to Memories server
        |
        v
  OutputFormatter  -->  JSON envelope or human-readable output
```

The `app()` group function in `cli/__init__.py` is the root Click group. On every invocation it calls `resolve_config()` to merge all config sources, constructs a `MemoriesClient` (the httpx-based HTTP client) and an `OutputFormatter`, then stores them in a `Context` object passed to all subcommands via Click's `pass_decorator`.

### Key Files

| File | Responsibility |
|------|---------------|
| `cli/__init__.py` | Root Click group (`app`), `Context` dataclass (holds `client` + `fmt`), imports all command modules |
| `cli/config.py` | `resolve_config()` for layered config resolution, `write_config()` for persisting settings |
| `cli/client.py` | `MemoriesClient` class -- httpx-based HTTP client with typed error classes |
| `cli/output.py` | `OutputFormatter` class -- TTY auto-detection, JSON envelope formatting, `echo()` and `echo_error()` |
| `cli/commands/core.py` | Top-level commands: `search`, `add`, `get`, `list`, `delete`, `count`, `upsert`, `is-novel`, `folders`. Also defines the `handle_errors` decorator and `_read_text_or_stdin` helper. |
| `cli/commands/batch.py` | `batch` group: `add`, `get`, `delete`, `search`, `upsert`. Accepts JSON/JSONL from file or stdin. |
| `cli/commands/delete_by.py` | `delete-by` group: `source`, `prefix`. Destructive operations with confirmation prompts. |
| `cli/commands/admin.py` | `admin` group: `stats`, `health`, `metrics`, `usage`, `deduplicate`, `consolidate`, `prune`, `reload-embedder` |
| `cli/commands/backup.py` | `backup` group: `create`, `list`, `restore` |
| `cli/commands/sync.py` | `sync` group: `status`, `upload`, `download`, `snapshots`, `restore` |
| `cli/commands/extract.py` | `extract` group: `submit`, `status`, `poll` |
| `cli/commands/auth.py` | `auth` group: `chatgpt`, `status` |
| `cli/commands/config_cmd.py` | `config` group: `show`, `set` |

### Command Inventory

**Top-level commands (9):** `search`, `add`, `get`, `list`, `delete`, `count`, `upsert`, `is-novel`, `folders`

**Groups (9) with subcommands:**

| Group | Subcommands |
|-------|-------------|
| `batch` | `add`, `get`, `delete`, `search`, `upsert` |
| `delete-by` | `source`, `prefix` |
| `admin` | `stats`, `health`, `metrics`, `usage`, `deduplicate`, `consolidate`, `prune`, `reload-embedder` |
| `backup` | `create`, `list`, `restore` |
| `sync` | `status`, `upload`, `download`, `snapshots`, `restore` |
| `extract` | `submit`, `status`, `poll` |
| `auth` | `chatgpt`, `status` |
| `config` | `show`, `set` |

Total: 9 top-level commands + 28 subcommands = 37 commands.

## Config Resolution

The `resolve_config()` function in `cli/config.py` merges configuration from four layers with strict precedence:

```
1. CLI flags (--url, --api-key)        highest priority
2. Config file (~/.config/memories/config.json)
3. Environment variables (MEMORIES_URL, MEMORIES_API_KEY)
4. Defaults (url: http://localhost:8900, api_key: None)   lowest priority
```

The returned dict includes a `_sources` key that records where each value came from (one of `"flag"`, `"file"`, `"env"`, `"default"`). The `config show` command displays these attributions.

The `config set` command writes to `~/.config/memories/config.json`. Valid keys are: `url`, `api_key`, `default_source`.

## Output Modes

The `OutputFormatter` class in `cli/output.py` controls output format:

| Mode | Trigger | Behavior |
|------|---------|----------|
| JSON | `--json` flag, or stdout is not a TTY (piped) | Emits `{"ok": true, "data": {...}}` envelope |
| Human | `--pretty` flag, or stdout is a TTY (interactive) | Emits colored, formatted text via Click's `secho`/`echo` |
| Auto | Neither flag set | TTY detection via `sys.stdout.isatty()` |

The `is_json` property determines the mode at runtime: `force_json` takes precedence, then `force_pretty`, then TTY auto-detection.

### JSON Envelope Contract

**Success:**
```json
{"ok": true, "data": {"id": 42, "text": "...", "source": "cli"}}
```

**Error:**
```json
{"ok": false, "error": "Cannot connect to server: ...", "code": "CONNECTION_ERROR"}
```

Error codes used: `GENERAL_ERROR`, `CONNECTION_ERROR`, `AUTH_REQUIRED`, `NOT_FOUND`.

Errors are written to stderr (both in JSON mode and human mode).

## Exit Codes

| Code | Meaning | Trigger |
|------|---------|---------|
| 0 | Success | Command completed without error |
| 1 | General error | Unhandled exception, validation error (`ValueError`), `CliServerError` |
| 2 | Not found | `CliNotFoundError` (HTTP 404 from server) |
| 3 | Connection error | `CliConnectionError` (httpx `ConnectError` or `TimeoutException`) |
| 4 | Auth error | `CliAuthError` (HTTP 401 or 403 from server) |

The `handle_errors` decorator in `cli/commands/core.py` catches typed exceptions from `MemoriesClient` and maps them to the correct exit code and error envelope. Every command applies this decorator.

## Error Mapping

The `MemoriesClient._request()` method translates HTTP responses into typed exceptions:

| HTTP Status | Exception | Exit Code | Error Code |
|-------------|-----------|-----------|------------|
| Connection refused / timeout | `CliConnectionError` | 3 | `CONNECTION_ERROR` |
| 401, 403 | `CliAuthError` | 4 | `AUTH_REQUIRED` |
| 404 | `CliNotFoundError` | 2 | `NOT_FOUND` |
| 422 | `ValueError` | 1 | `GENERAL_ERROR` |
| 5xx | `CliServerError` | 1 | `GENERAL_ERROR` |

## HTTP Client

`MemoriesClient` in `cli/client.py` uses `httpx.Client` (synchronous). Key details:

- Base URL is set from resolved config, trailing slashes stripped.
- Authentication via `X-API-Key` header, set if `api_key` is not None.
- Timeout: 30 seconds.
- HTTP 204 responses return an empty dict `{}`.
- All other successful responses are parsed as JSON.
- The `extract_poll()` method polls with configurable timeout and interval, using `time.monotonic()` for deadline tracking.

## Stdin Support

Several commands accept `-` as a text argument or read from stdin when the terminal is not interactive:

- `add`: text argument defaults to `-`, reads from stdin via `_read_text_or_stdin()`.
- `upsert`: same behavior.
- `is-novel`: same behavior.
- `batch add/search/upsert`: reads JSON/JSONL from file or stdin via `read_input()`.
- `extract submit`: reads transcript from file or stdin via `read_file_or_stdin()`.

This enables piping: `echo "some text" | memories add -s cli` or `cat transcript.md | memories extract submit -s agent`.

## Confirmation Prompts

Destructive commands require confirmation in interactive mode:

- `delete-by source PATTERN` -- prompts unless `--yes`/`-y` is passed.
- `delete-by prefix PREFIX` -- prompts unless `--yes`/`-y` is passed.
- `backup restore NAME` -- prompts unless `--yes`/`-y` is passed.

In piped/non-interactive mode, agents should always pass `--yes` to skip the prompt.

## Common Questions

### Q: How do agents parse CLI output?

Agents pipe the command and parse the JSON envelope. When stdout is not a TTY (i.e., output is captured or piped), the CLI automatically emits JSON. Agents can also force JSON with `--json`. Parse the `ok` field first: if `true`, read `data`; if `false`, read `error` and `code`. Check the process exit code for coarse-grained error classification (0/1/2/3/4).

### Q: How does config resolution work?

Four layers with strict precedence: CLI flags > config file (`~/.config/memories/config.json`) > env vars (`MEMORIES_URL`, `MEMORIES_API_KEY`) > defaults (`http://localhost:8900`, no key). Run `memories config show` to see the resolved values and which layer each came from.

### Q: What if the server is down?

The CLI exits with code 3 and emits a `CONNECTION_ERROR` envelope. The error message includes the underlying httpx exception detail (e.g., "Cannot connect to server: [Errno 61] Connection refused"). The same exit code 3 applies to timeout errors (30-second timeout).

### Q: Can multiple CLI instances run concurrently?

Yes. The CLI is stateless -- each invocation creates its own `httpx.Client` and makes independent HTTP requests. There is no local lock file or shared state between invocations. The config file at `~/.config/memories/config.json` is read-only during normal operation (`config set` is the only writer). Concurrent `config set` calls could race, but this is an uncommon scenario.

### Q: How does stdin support work?

Commands that accept text (`add`, `upsert`, `is-novel`) default the text argument to `-`. The `_read_text_or_stdin()` function checks if the argument is `-` or if stdin is not a TTY, and reads `sys.stdin.read().strip()`. Batch commands (`batch add`, `batch search`, `batch upsert`) and extract (`extract submit`) use similar helpers that also accept file paths. This allows piping: `echo "fact" | memories add -s cli` or `cat data.jsonl | memories batch add -`.

### Q: What happens with HTTP 422 (validation error)?

The `MemoriesClient` parses the response body and raises a `ValueError` with the detail message from the server. This maps to exit code 1 and error code `GENERAL_ERROR`. Common causes: missing required fields, invalid JSON body, wrong argument types. The error message from the server is passed through to the user.

### Q: How do batch commands handle input format?

The `parse_jsonl()` function in `cli/commands/batch.py` accepts both JSON arrays (`[{...}, {...}]`) and JSONL (one JSON object per line). It detects the format by checking whether the input starts with `[`. Input can come from a file path argument or from stdin.

### Q: What is the `--version` flag?

`memories --version` reads the package version from the installed `memories` package metadata via Click's `version_option(package_name="memories")`.

## Backward Compatibility

The CLI is a new feature. It does not change any existing server behavior. All server API endpoints remain unchanged. The CLI is a client-side tool that wraps the existing REST API.

## Dependencies

- `click` -- command framework
- `httpx` -- HTTP client
- Standard library: `json`, `sys`, `os`, `time`, `functools`, `pathlib`
