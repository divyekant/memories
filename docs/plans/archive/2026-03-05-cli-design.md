# CLI Design

**Date:** 2026-03-05
**Status:** Approved

## Summary

Add a full-coverage CLI to the Memories project. The CLI wraps all ~30+ REST API endpoints as Click commands, with an agent-first design (JSON by default when piped, human-friendly when interactive).

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Audience | All users (shipped with project) | CLI is a first-class interface, not internal tooling |
| Coverage | Full API surface | Every endpoint gets a command |
| Top-level command | `memories` | Matches project name |
| Framework | Click | Already a dependency, handles groups/completion natively |
| Config resolution | Flags > config file > env vars > defaults | Layered precedence, most flexible |
| Output mode | Auto-detect TTY (human) vs pipe (JSON) | Agent-first: zero-config JSON when piped, `--json`/`--pretty` overrides |
| Entry points | `memories` console_scripts + `python -m memories` | Both installed and dev workflows |

## Command Structure

```
memories
  search <query>                  # Semantic/hybrid search
  add <text> --source <src>       # Add a memory (also accepts stdin)
  get <id>                        # Fetch by ID
  list [--source <prefix>]        # Browse with pagination
  delete <id>                     # Delete by ID
  count [--source <prefix>]       # Count memories
  upsert <text> --source --key    # Idempotent create/update
  is-novel <text>                 # Novelty check before adding
  folders                         # List source folders + counts

  batch
    add <file|->                  # Add from JSON/JSONL file or stdin
    get <ids...>                  # Fetch multiple by ID
    delete <ids...>               # Delete multiple by ID
    search <file|->               # Multiple queries from file or stdin
    upsert <file|->               # Batch upsert from file or stdin

  delete-by
    source <pattern>              # Delete by source substring
    prefix <prefix>               # Delete by source prefix

  admin
    stats                         # Index statistics
    health                        # Health + readiness
    metrics                       # Operational metrics
    usage [--period 7d]           # Usage analytics
    deduplicate [--dry-run]       # Find/remove duplicates
    consolidate                   # Compact IDs, rebuild vectors
    prune                         # Remove stale memories
    reload-embedder               # Reload embedder runtime

  backup
    create [--prefix manual]      # Create backup
    list                          # List backups
    restore <name>                # Restore from backup

  sync
    status                        # Cloud sync status
    upload                        # Push to cloud
    download                      # Pull from cloud
    snapshots                     # List cloud snapshots
    restore <name>                # Restore cloud backup

  extract
    submit <file|-> --source      # Submit transcript (stdin-friendly)
    status [job_id]               # Job or subsystem status
    poll <job_id> [--wait]        # Poll, optionally block until done

  auth                            # Migrated from argparse
    chatgpt --client-id           # ChatGPT OAuth flow
    status                        # Provider config

  config
    show                          # Display resolved config (with source attribution)
    set <key> <value>             # Update config file value
```

## Agent-First Output Contract

### JSON Envelope

Every command returns a consistent shape when in JSON mode:

```json
{"ok": true, "data": { ... }}
{"ok": false, "error": "message", "code": "NOT_FOUND"}
```

Error codes: `NOT_FOUND`, `AUTH_REQUIRED`, `VALIDATION_ERROR`, `SERVER_ERROR`, `CONNECTION_ERROR`, `CONFIG_ERROR`.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Not found (memory ID, backup name, etc.) |
| 3 | Config/connection error (bad URL, missing key) |
| 4 | Auth error (invalid/insufficient API key) |

### TTY Auto-Detection

- stdout is TTY: human-formatted (tables, colors)
- stdout is pipe: JSON (JSONL for streaming commands)
- `--json` forces JSON regardless
- `--pretty` forces human-formatted regardless

## Config Resolution

Layered precedence (highest wins):

```
CLI flags (--url, --api-key)
  |
Config file (~/.config/memories/config.json)
  |
Environment variables (MEMORIES_URL, MEMORIES_API_KEY)
  |
Defaults (url=http://localhost:8900, key=none)
```

Config file format:

```json
{
  "url": "http://localhost:8900",
  "api_key": "...",
  "default_source": "cli"
}
```

`memories config show` displays the resolved config with source attribution (which layer provided each value).

## Project Structure

```
cli/
  __init__.py          # Click app, global options, config resolution
  client.py            # Sync HTTP client (httpx) wrapping all endpoints
  output.py            # TTY detection, JSON envelope, human formatters
  commands/
    __init__.py
    core.py            # search, add, get, list, delete, count, upsert, is_novel, folders
    batch.py           # batch add/get/delete/search/upsert
    delete_by.py       # delete-by source/prefix
    admin.py           # stats, health, metrics, usage, deduplicate, consolidate, prune, reload
    backup.py          # backup create/list/restore
    sync.py            # sync status/upload/download/snapshots/restore
    extract.py         # extract submit/status/poll
    auth.py            # migrated from memories_auth.py
    config.py          # config show/set
  formatters/
    __init__.py
    table.py           # Human-readable table/card formatting
    json.py            # JSON envelope + JSONL streaming
```

## HTTP Client

Thin synchronous wrapper using httpx (already a dependency). Each method maps 1:1 to an API endpoint. Returns raw dicts. Connection errors raise `click.ClickException` with exit code 3, auth errors with exit code 4.

## Stdin Support

Commands that accept `-` or detect non-TTY stdin:
- `memories add - --source <src>` (pipe text)
- `memories batch add -` (pipe JSON/JSONL)
- `memories batch search -` (pipe queries)
- `memories batch upsert -` (pipe records)
- `memories extract submit - --source <src>` (pipe transcript)

## Shell Completion

Click's built-in completion, enabled via:

```bash
eval "$(_MEMORIES_COMPLETE=zsh_source memories)"
```

## Migration

The existing `memories_auth.py` + `__main__.py` argparse code migrates into `cli/commands/auth.py` as a Click command group. `__main__.py` is updated to delegate to the Click app.
