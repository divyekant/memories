---
id: uc-005
type: use-case
audience: internal
topic: Human Terminal Workflow
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Use Case: Human Terminal Workflow

## Scenario

A developer uses the Memories CLI interactively in a terminal (TTY mode). The CLI detects the TTY and produces colored, human-readable output instead of JSON. This document walks through common workflows a human operator would perform.

## Setup: First-Time Configuration

### Set the server URL and API key

```bash
$ memories config set url http://localhost:8900
Set url in /Users/dev/.config/memories/config.json

$ memories config set api_key god-is-an-astronaut
Set api_key in /Users/dev/.config/memories/config.json
```

### Verify configuration

```bash
$ memories config show
            url: http://localhost:8900  (from file)
        api_key: god-is-a****  (from file)
```

The API key is masked after the first 8 characters. The `(from file)` annotation shows where each value was resolved from.

### Verify server connectivity

```bash
$ memories admin health
Status: ok
Version: 1.5.0
Memories: 247
```

## Workflow 1: Searching Memories

### Basic search

```bash
$ memories search "authentication flow"
[42] (91%) claude-code/decisions
  Multi-auth uses prefix-scoped API keys with role-based access...
[17] (84%) claude-code/learnings
  Rate limiting: 10 failed auth attempts per IP per 60-second...
[93] (78%) shared/architecture
  The verify_api_key middleware resolves credentials in order...
```

Each result shows the memory ID in brackets, similarity percentage in parentheses, the source prefix in cyan, and a truncated text preview (200 character limit).

### Search with options

```bash
$ memories search "deploy config" -k 10 --source "devops/*" --threshold 0.7
```

`-k` controls the number of results (default 5), `--source` filters by source prefix, and `--threshold` sets the minimum similarity score.

### No results

```bash
$ memories search "quantum computing applications"
No results.
```

## Workflow 2: Adding Memories

### Add with explicit text

```bash
$ memories add "The staging server requires VPN access on port 443" -s devops/notes
Added memory #248
```

The `-s`/`--source` flag is required. The confirmation line appears in green.

### Add from stdin (piping)

```bash
$ echo "CI pipeline times out after 30 minutes on large repos" | memories add -s devops/learnings
Added memory #249
```

### Add with deduplication disabled

```bash
$ memories add "Known duplicate fact for testing" -s test --no-deduplicate
Added memory #250
```

## Workflow 3: Listing and Browsing

### List memories

```bash
$ memories list --limit 5
Showing 5/247 memories
  [1] claude-code/decisions  Architecture uses event-driven pattern with...
  [2] claude-code/learnings  The SQLite WAL mode provides non-blocking co...
  [3] shared/config          Default timeout is 30 seconds for all HTTP c...
  [4] devops/notes           The staging server requires VPN access on po...
  [5] devops/learnings       CI pipeline times out after 30 minutes on la...
```

### List with source filter

```bash
$ memories list --source devops --limit 10
Showing 2/2 memories
  [4] devops/notes           The staging server requires VPN access on po...
  [5] devops/learnings       CI pipeline times out after 30 minutes on la...
```

### View source folders

```bash
$ memories folders
  142  claude-code
   58  shared
   32  devops
   15  kai
  247  (total)
```

Folders are listed with right-aligned counts and a total at the bottom.

### Count memories

```bash
$ memories count
247 memories

$ memories count --source claude-code
142 memories
```

## Workflow 4: Getting and Deleting Individual Memories

### Get a specific memory

```bash
$ memories get 42
ID: 42
Source: claude-code/decisions
Created: 2026-02-15T10:30:00Z
Text: Multi-auth uses prefix-scoped API keys with role-based access control...
```

### Delete a specific memory

```bash
$ memories delete 42
Deleted memory #42
```

The deletion confirmation appears in yellow.

## Workflow 5: Upsert and Novelty Check

### Upsert (insert or update by key)

```bash
$ memories upsert "Deploy requires Node 20+ as of March 2026" -s devops/notes -k deploy-node-version
Created memory #251
```

Running the same command with updated text:

```bash
$ memories upsert "Deploy requires Node 22+ as of March 2026" -s devops/notes -k deploy-node-version
Updated memory #251
```

### Check novelty

```bash
$ memories is-novel "Rate limiting uses 10 attempts per minute per IP"
Not novel
  Most similar (94%): Rate limiting: 10 failed auth attempts per IP per 60-second...
```

For novel content:

```bash
$ memories is-novel "GraphQL subscriptions require WebSocket transport"
Novel
```

"Novel" appears in green, "Not novel" in yellow with the most similar existing memory shown.

## Workflow 6: Admin Operations

### Server health and stats

```bash
$ memories admin health
Status: ok
Version: 1.5.0
Memories: 247

$ memories admin stats
total_memories: 247
total_sources: 4
embedding_model: all-MiniLM-L6-v2
index_size_mb: 12.4
```

### Deduplication (dry run first)

```bash
$ memories admin deduplicate --threshold 0.95
Found 3 duplicates (dry run)

$ memories admin deduplicate --threshold 0.95 --execute
Removed 3 duplicates
```

The default is `--dry-run`. Pass `--execute` to actually remove duplicates.

### Maintenance operations

```bash
$ memories admin consolidate
Consolidation complete

$ memories admin prune
Pruned 5 entries

$ memories admin reload-embedder
Reload: Embedder reloaded successfully
```

### Usage stats

```bash
$ memories admin usage --period 7d
searches: 142
adds: 38
extractions: 12
deletes: 5
```

## Workflow 7: Backup and Restore

### Create a backup

```bash
$ memories backup create --prefix manual
Backup created: manual-2026-03-05T14-30-00.zip
```

### List backups

```bash
$ memories backup list
  manual-2026-03-05T14-30-00.zip  (2026-03-05T14:30:00Z)
  auto-2026-03-04T00-00-00.zip    (2026-03-04T00:00:00Z)
```

### Restore from backup (requires confirmation)

```bash
$ memories backup restore manual-2026-03-05T14-30-00.zip
Restore from backup 'manual-2026-03-05T14-30-00.zip'? This will replace current data. [y/N]: y
Restored 247 memories from 'manual-2026-03-05T14-30-00.zip'
```

The confirmation prompt defaults to No. Pass `--yes` to skip.

## Workflow 8: Bulk Deletion

### Delete by source pattern

```bash
$ memories delete-by source "test-data"
Delete all memories matching source pattern 'test-data'? [y/N]: y
Deleted 12 memories matching 'test-data'
```

### Delete by prefix

```bash
$ memories delete-by prefix "scratch/"
Delete all memories matching source pattern 'scratch/'? [y/N]: y
Deleted 8 memories with prefix 'scratch/'
```

Both commands require confirmation. Pass `-y` to skip.

## Workflow 9: Forcing Output Modes

### Force JSON in interactive terminal

```bash
$ memories --json search "auth flow"
{"ok": true, "data": {"results": [...]}}
```

Useful for copying structured data or debugging.

### Force human-readable in a pipe

```bash
$ memories --pretty search "auth flow" | less
[42] (91%) claude-code/decisions
  Multi-auth uses prefix-scoped API keys...
```

Useful for piping human-readable output to a pager or log file.

## Error Handling in TTY Mode

### Server unreachable

```bash
$ memories search "test"
Error: Cannot connect to server: [Errno 61] Connection refused
```

The error appears in red on stderr. Exit code: 3.

### Authentication failure

```bash
$ memories search "test"
Error: Authentication failed: 401
```

Red text on stderr. Exit code: 4.

### Not found

```bash
$ memories get 99999
Error: Not found: /memory/99999
```

Red text on stderr. Exit code: 2.

## Considerations

- TTY auto-detection is based on `sys.stdout.isatty()`. Running inside `script`, `tmux`, or SSH typically preserves TTY status.
- The `--pretty` flag forces human-readable output even when piped (e.g., `memories --pretty list | less`).
- The `--json` flag forces JSON output even in an interactive terminal.
- Confirmation prompts for destructive operations only appear in interactive mode. When piped, the command aborts unless `--yes` is passed.
