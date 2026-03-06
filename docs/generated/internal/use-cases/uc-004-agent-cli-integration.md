---
id: uc-004
type: use-case
audience: internal
topic: Agent CLI Integration
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Use Case: Agent CLI Integration

## Scenario

An AI agent (Claude Code, a custom LLM tool, or an orchestration script) needs to interact with the Memories server programmatically. Rather than making raw HTTP calls, the agent uses the `memories` CLI as a subprocess, relying on the JSON envelope output and exit codes for structured communication.

## Why the CLI Instead of Direct HTTP

- The CLI handles config resolution (server URL, API key) automatically.
- JSON envelope output provides a consistent contract (`ok`, `data`, `error`, `code`).
- Exit codes give coarse-grained error classification without parsing.
- Batch commands accept JSONL input from stdin, simplifying bulk operations.
- The agent does not need to manage httpx sessions, headers, or error mapping.

## Flow 1: Semantic Search

### Step 1: Agent spawns the search command

```bash
memories --json search "authentication flow for multi-auth"
```

The `--json` flag forces JSON output regardless of TTY state. This is recommended for agents even though piped output auto-detects as JSON.

### Step 2: CLI returns JSON envelope on stdout

```json
{"ok": true, "data": {"results": [{"id": 42, "text": "Multi-auth uses prefix-scoped API keys...", "source": "claude-code/decisions", "similarity": 0.91}, {"id": 17, "text": "Rate limiting: 10 failed auth attempts per IP...", "source": "claude-code/learnings", "similarity": 0.84}]}}
```

Exit code: `0`

### Step 3: Agent parses the response

The agent reads stdout, parses JSON, checks `ok == true`, then iterates over `data.results`. Each result has `id`, `text`, `source`, and a similarity score.

### Error case: Server unreachable

```json
{"ok": false, "error": "Cannot connect to server: [Errno 61] Connection refused", "code": "CONNECTION_ERROR"}
```

Exit code: `3`

The agent detects `ok == false`, reads the `code` field (`CONNECTION_ERROR`), and either retries or reports the failure.

### Error case: Authentication failure

```json
{"ok": false, "error": "Authentication failed: 401", "code": "AUTH_REQUIRED"}
```

Exit code: `4`

The agent should not retry -- this indicates a misconfigured API key.

## Flow 2: Adding a Memory

### Step 1: Agent pipes text via stdin

```bash
echo "The deploy script requires Node 18+ and fails silently on Node 16" | memories --json add -s "agent/learnings"
```

Or with an explicit argument:

```bash
memories --json add "The deploy script requires Node 18+" -s "agent/learnings"
```

### Step 2: CLI returns the created memory

```json
{"ok": true, "data": {"id": 108, "text": "The deploy script requires Node 18+...", "source": "agent/learnings", "deduplicated": false}}
```

Exit code: `0`

### Step 3: Agent confirms creation

The agent reads `data.id` to record the memory ID for future reference (e.g., for supersede or delete operations).

## Flow 3: Batch Operations for Bulk Ingestion

### Step 1: Agent prepares JSONL input

```
{"text": "React 19 uses automatic memoization", "source": "agent/learnings"}
{"text": "PostgreSQL 17 adds JSON_TABLE support", "source": "agent/learnings"}
{"text": "Python 3.13 removes GIL optionally", "source": "agent/learnings"}
```

### Step 2: Agent pipes JSONL to batch add

```bash
cat facts.jsonl | memories --json batch add -
```

Or from a file:

```bash
memories --json batch add /tmp/facts.jsonl
```

### Step 3: CLI returns batch result

```json
{"ok": true, "data": {"added": 3, "ids": [109, 110, 111]}}
```

Exit code: `0`

The agent can also use `batch upsert` for idempotent inserts, or `batch search` to run multiple queries in a single call.

## Flow 4: Extract Submit for Conversation Processing

### Step 1: Agent submits a conversation transcript

```bash
cat conversation.md | memories --json extract submit -s "agent/session-42" --context session_end -
```

### Step 2: CLI returns the job ID

```json
{"ok": true, "data": {"job_id": "ext-abc123", "status": "submitted"}}
```

Exit code: `0`

### Step 3: Agent polls for completion

```bash
memories --json extract poll ext-abc123 --wait --timeout 120
```

The `--wait` flag blocks until the job completes or the timeout is reached. Without `--wait`, poll returns the current status immediately.

### Step 4: CLI returns the completed extraction

```json
{"ok": true, "data": {"job_id": "ext-abc123", "status": "completed", "result": [{"text": "User prefers dark mode for all tools", "source": "agent/session-42"}]}}
```

Exit code: `0`

### Error case: Extraction timeout

If the job does not complete within the timeout, the CLI raises a `TimeoutError`, which maps to exit code `1` and error code `GENERAL_ERROR`.

## Flow 5: Novelty Check Before Adding

### Step 1: Agent checks if a fact is novel

```bash
memories --json is-novel "PostgreSQL 17 adds JSON_TABLE support" --threshold 0.88
```

### Step 2: CLI returns the novelty assessment

```json
{"ok": true, "data": {"is_novel": false, "most_similar": {"id": 110, "text": "PostgreSQL 17 adds JSON_TABLE support", "similarity": 0.99}}}
```

Exit code: `0`

The agent reads `data.is_novel`. If `false`, the fact already exists and the agent skips the add. If `true`, the agent proceeds to add.

## Exit Code Branching (Pseudocode)

```python
import subprocess, json

result = subprocess.run(
    ["memories", "--json", "search", "deploy requirements"],
    capture_output=True, text=True,
)

if result.returncode == 0:
    envelope = json.loads(result.stdout)
    memories = envelope["data"]["results"]
    # use memories
elif result.returncode == 3:
    # server unreachable, retry or fail gracefully
    pass
elif result.returncode == 4:
    # auth error, check API key config
    pass
elif result.returncode == 2:
    # not found (uncommon for search, more relevant for get)
    pass
else:
    # general error (exit code 1)
    envelope = json.loads(result.stderr)
    error_msg = envelope.get("error", "Unknown error")
```

## Agent Configuration

Agents should configure the CLI via one of:

1. **Environment variables** (recommended for agents):
   ```bash
   export MEMORIES_URL=http://localhost:8900
   export MEMORIES_API_KEY=mem_a3f8b2c1d4e5f6071829304a5b6c7d8e
   ```

2. **Config file** (`~/.config/memories/config.json`):
   ```json
   {"url": "http://localhost:8900", "api_key": "mem_a3f8..."}
   ```

3. **CLI flags** (per invocation):
   ```bash
   memories --url http://localhost:8900 --api-key mem_a3f8... search "query"
   ```

Agents should verify configuration is working with:
```bash
memories --json admin health
```

## Considerations

- Always use `--json` for agent invocations to guarantee JSON output regardless of environment.
- Always pass `--yes` (or `-y`) to destructive commands (`delete-by source`, `delete-by prefix`, `backup restore`) to skip interactive confirmation prompts.
- Error envelopes are written to stderr, not stdout. Agents should capture both streams.
- The CLI timeout is 30 seconds per HTTP request. For long-running operations like `extract poll`, use `--wait --timeout N`.
- Concurrent CLI invocations are safe -- there is no shared state between processes.
