---
id: feat-002
type: feature-doc
audience: external
topic: CLI
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Memories CLI

The Memories CLI is a full-coverage command-line interface for your Memories semantic memory server. It gives you direct access to every server capability from your terminal -- searching, adding, deleting, extracting, backing up, and administering your memory store.

## Who Is It For?

- **Developers** who want to manage memories without writing curl commands or navigating the Web UI
- **AI agent builders** who need a scriptable interface for storing and retrieving memories programmatically
- **DevOps and SREs** who manage Memories instances and need health checks, backups, and maintenance from the command line

## What It Does

The CLI exposes 30+ commands organized into logical groups:

| Group | Commands | What They Do |
|-------|----------|-------------|
| Top-level | `search`, `add`, `get`, `list`, `delete`, `count`, `upsert`, `is-novel`, `folders` | Core memory operations |
| `batch` | `add`, `get`, `delete`, `search`, `upsert` | Bulk operations via JSON/JSONL files |
| `delete-by` | `source`, `prefix` | Delete memories by source pattern or prefix |
| `admin` | `stats`, `health`, `metrics`, `usage`, `deduplicate`, `consolidate`, `prune`, `reload-embedder` | Server administration and maintenance |
| `backup` | `create`, `list`, `restore` | Local backup management |
| `sync` | `status`, `upload`, `download`, `snapshots`, `restore` | Remote sync operations |
| `extract` | `submit`, `status`, `poll` | Memory extraction from conversation transcripts |
| `auth` | `chatgpt`, `status` | Extraction provider authentication |
| `config` | `show`, `set` | CLI configuration management |

### Agent-First Output

The CLI automatically detects whether it is running in an interactive terminal or being piped to another program:

- **In a terminal:** You see human-readable, color-coded output
- **Piped or redirected:** Output switches to a structured JSON envelope

You can override this behavior with `--json` (always JSON) or `--pretty` (always human-readable).

**JSON envelope format:**

```json
{"ok": true, "data": {"results": [...]}}
```

```json
{"ok": false, "error": "Cannot connect to server", "code": "CONNECTION_ERROR"}
```

This makes the CLI a natural fit for AI agents, shell scripts, and CI/CD pipelines that parse structured output.

## How to Use It

### Install

```bash
pip install memories
```

Verify the installation:

```bash
memories --help
```

### Configure

The CLI uses layered configuration with clear precedence:

1. **CLI flags** (highest priority)
2. **Config file** (`~/.config/memories/config.json`)
3. **Environment variables** (`MEMORIES_URL`, `MEMORIES_API_KEY`)
4. **Defaults** (`http://localhost:8900`, no API key)

Set up your connection using the built-in config command:

```bash
memories config set url http://localhost:8900
memories config set api_key YOUR_API_KEY
```

Or set environment variables:

```bash
export MEMORIES_URL=http://localhost:8900
export MEMORIES_API_KEY=your-api-key
```

Or pass flags directly:

```bash
memories --url http://myserver:8900 --api-key my-key search "query"
```

To see where each setting is coming from:

```bash
memories config show
```

```
            url: http://localhost:8900  (from file)
        api_key: god-is-a****  (from file)
```

### Basic Usage

**Search for memories:**

```bash
memories search "architecture decisions"
```

**Add a memory:**

```bash
memories add -s claude-code/myapp/ "Use repository pattern for data access"
```

**Pipe text from stdin:**

```bash
echo "Important fact about the project" | memories add -s notes/project/ -
```

**List all folders:**

```bash
memories folders
```

**Check server health:**

```bash
memories admin health
```

## Configuration Options

| Option | CLI Flag | Config File Key | Env Var | Default |
|--------|----------|-----------------|---------|---------|
| Server URL | `--url` | `url` | `MEMORIES_URL` | `http://localhost:8900` |
| API Key | `--api-key` | `api_key` | `MEMORIES_API_KEY` | None |
| JSON output | `--json` | -- | -- | Auto-detect (JSON when piped) |
| Human output | `--pretty` | -- | -- | Auto-detect (pretty in terminal) |

**Config file location:** `~/.config/memories/config.json`

```json
{
  "url": "http://localhost:8900",
  "api_key": "your-api-key"
}
```

## Examples

### Search and filter results

```bash
memories search "python async patterns" -k 10 --threshold 0.7
```

### Add a memory with deduplication disabled

```bash
memories add -s learning/python/ --no-deduplicate "Always use asyncio.gather for concurrent tasks"
```

### Batch add from a JSONL file

```bash
cat <<'EOF' > memories.jsonl
{"text": "Use connection pooling for database access", "source": "decisions/db/"}
{"text": "Prefer composition over inheritance", "source": "decisions/design/"}
{"text": "Cache embedding vectors for 24 hours", "source": "decisions/perf/"}
EOF

memories batch add memories.jsonl
```

### Pipe CLI output to jq

```bash
memories --json search "architecture" | jq '.data.results[].text'
```

### Extract memories from a conversation transcript

```bash
cat transcript.txt | memories extract submit -s agent/session-042/ -
```

### Check if a fact is novel before adding

```bash
memories is-novel "Use repository pattern for data access"
```

### Delete all memories under a prefix

```bash
memories delete-by prefix "old-project/"
```

### Create a backup before maintenance

```bash
memories backup create --prefix pre-maintenance
```

## Exit Codes

| Code | Name | Meaning |
|------|------|---------|
| 0 | Success | Command completed successfully |
| 1 | General Error | Validation error, server rejected request, or unexpected failure |
| 2 | Not Found | The requested memory ID or resource does not exist |
| 3 | Connection Error | Cannot connect to the Memories server (is it running?) |
| 4 | Auth Error | Authentication failed (check your API key) |

These exit codes are consistent across all commands and can be used in scripts for error handling:

```bash
memories admin health
if [ $? -eq 3 ]; then
  echo "Server is down!"
fi
```

## Limitations

- **Requires a running Memories server.** The CLI is a client that communicates with your Memories server over HTTP. It does not operate on local data directly.
- **Python 3.10 or later.** The CLI uses modern Python features including type union syntax (`str | None`).
- **Network dependent.** All operations require a network connection to the server. Timeouts default to 30 seconds.
- **No offline mode.** You cannot queue operations for later execution.
- **Destructive operations prompt for confirmation.** Commands like `delete-by source`, `delete-by prefix`, and `backup restore` ask for confirmation in interactive terminals. Use `--yes` or `-y` to skip the prompt in scripts.

## Related

- [CLI Quick Start Tutorial](../tutorials/tut-003-cli-quickstart.md) -- get up and running in 5 minutes
- [Agent CLI Integration Tutorial](../tutorials/tut-004-cli-agent-integration.md) -- use the CLI from AI agents and scripts
- [CLI Recipes](../cookbook.md#cli-recipes) -- copy-paste patterns for common tasks
- [CLI Errors](../error-reference.md#cli-errors) -- troubleshoot CLI-specific errors
- [Multi-Auth: Prefix-Scoped API Keys](feat-001-multi-auth.md) -- secure your CLI access with scoped keys
