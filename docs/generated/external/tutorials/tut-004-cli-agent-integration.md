---
id: tut-004
type: tutorial
audience: external
topic: Agent CLI Integration
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Tutorial: Agent CLI Integration

In this tutorial, you will use the Memories CLI as the interface between an AI agent (or any automated script) and your Memories server. You will configure environment-based auth, parse JSON output, handle errors with exit codes, run batch operations, and extract memories from conversation transcripts.

**Time:** 15 minutes

**What you'll build:** An AI agent workflow that uses the CLI to store, retrieve, and extract memories -- with proper error handling and structured output parsing.

**Prerequisites:**
- Memories CLI installed (`pip install memories`)
- A Memories server running at `http://localhost:8900`
- An API key configured
- `jq` installed (for JSON parsing; install with `brew install jq` or your package manager)

## Step 1: Set Up Environment Variables

For agent and script use, environment variables are the cleanest configuration method. They avoid putting secrets in command arguments (which appear in process listings).

```bash
export MEMORIES_URL="http://localhost:8900"
export MEMORIES_API_KEY="your-api-key"
```

Verify the configuration:

```bash
memories config show
```

**Expected output:**

```
            url: http://localhost:8900  (from env)
        api_key: your-api****  (from env)
```

Notice the source says `(from env)` instead of `(from file)`. The CLI always tells you where each setting originates.

## Step 2: Verify JSON Output

When the CLI output is piped to another program, it automatically switches to JSON. Verify this:

```bash
memories search "test" | cat
```

**Expected output (JSON envelope):**

```json
{"ok": true, "data": {"results": []}}
```

You can also force JSON mode explicitly with the `--json` flag:

```bash
memories --json search "test"
```

**Expected output:**

```json
{"ok": true, "data": {"results": []}}
```

Now pipe to `jq` for formatted output:

```bash
memories --json search "test" | jq .
```

**Expected output:**

```json
{
  "ok": true,
  "data": {
    "results": []
  }
}
```

The JSON envelope always has this structure:
- **Success:** `{"ok": true, "data": {...}}`
- **Error:** `{"ok": false, "error": "message", "code": "ERROR_CODE"}`

## Step 3: Add Memories From a Script

Your agent can add memories by passing text as an argument or piping it via stdin.

**Direct argument:**

```bash
memories --json add -s agent/mybot/ "User prefers dark mode and compact layouts"
```

**Expected output:**

```json
{"ok": true, "data": {"id": 44, "message": "Memory added", "deduplicated": false}}
```

**Via stdin (useful when the text contains special characters or is very long):**

```bash
echo "The deployment pipeline takes 12 minutes on average" | memories --json add -s agent/mybot/ -
```

**Expected output:**

```json
{"ok": true, "data": {"id": 45, "message": "Memory added", "deduplicated": false}}
```

The `-` at the end tells the CLI to read from stdin instead of treating the next token as the text argument.

**Parse the result in a script:**

```bash
#!/bin/bash
result=$(memories --json add -s agent/mybot/ "CI builds break on Node 22")
id=$(echo "$result" | jq -r '.data.id')
echo "Stored memory #$id"
```

## Step 4: Search and Parse Results

Search for memories and extract just the text:

```bash
memories --json search "deployment pipeline" | jq '.data.results[].text'
```

**Expected output:**

```
"The deployment pipeline takes 12 minutes on average"
```

Get IDs and similarity scores:

```bash
memories --json search "user preferences" -k 3 | jq '.data.results[] | {id, text, similarity}'
```

**Expected output:**

```json
{
  "id": 44,
  "text": "User prefers dark mode and compact layouts",
  "similarity": 0.89
}
```

**Use in a conditional script:**

```bash
#!/bin/bash
# Check if we already know about a topic before adding
result=$(memories --json search "deployment pipeline" -k 1 --threshold 0.8)
count=$(echo "$result" | jq '.data.results | length')

if [ "$count" -eq 0 ]; then
  echo "No existing knowledge, adding..."
  memories --json add -s agent/mybot/ "The deployment pipeline takes 12 minutes"
else
  echo "Already know about this topic"
fi
```

## Step 5: Batch Operations

For adding multiple memories at once, create a JSONL file (one JSON object per line). Save the following as `agent_memories.jsonl`:

```json
{"text": "The API rate limit is 100 requests per minute", "source": "agent/mybot/"}
{"text": "Database backups run at 02:00 UTC daily", "source": "agent/mybot/"}
{"text": "The staging environment mirrors production with a 1-hour delay", "source": "agent/mybot/"}
{"text": "Error logs are stored in /var/log/app/errors.log", "source": "agent/mybot/"}
```

Add them all in one call:

```bash
memories --json batch add agent_memories.jsonl | jq .
```

**Expected output:**

```json
{
  "ok": true,
  "data": {
    "added": 4
  }
}
```

You can also pipe JSONL directly from your agent:

```bash
echo '{"text": "Redis cache TTL is 300 seconds", "source": "agent/mybot/"}' | memories --json batch add -
```

**Batch search with multiple queries.** Save the following as `queries.jsonl`:

```json
{"query": "rate limits", "k": 2}
{"query": "backup schedule", "k": 2}
{"query": "staging environment", "k": 2}
```

```bash
memories --json batch search queries.jsonl | jq '.data.results | length'
```

**Expected output:**

```
3
```

## Step 6: Extract Memories From a Transcript

If your agent has conversation transcripts, you can use the extract command to automatically pull out memorable facts.

Save a sample transcript as `transcript.txt` with a conversation between a human and an assistant discussing technical decisions. Then submit it for extraction:

```bash
cat transcript.txt | memories --json extract submit -s agent/session-042/ -
```

**Expected output:**

```json
{"ok": true, "data": {"job_id": "ext-a1b2c3", "status": "submitted"}}
```

The extraction runs asynchronously. Poll for completion:

```bash
memories --json extract poll ext-a1b2c3 --wait --timeout 120 | jq .
```

**Expected output (when complete):**

```json
{
  "ok": true,
  "data": {
    "job_id": "ext-a1b2c3",
    "status": "completed",
    "memories": [
      {"text": "Team decided to use PostgreSQL for the new user service", "source": "agent/session-042/"},
      {"text": "Connection pooling should use PgBouncer with max 20 connections", "source": "agent/session-042/"}
    ]
  }
}
```

You can also check status without waiting:

```bash
memories --json extract status ext-a1b2c3 | jq '.data.status'
```

**Expected output:**

```
"completed"
```

## Step 7: Error Handling With Exit Codes

The CLI uses consistent exit codes that your scripts can check:

| Exit Code | Constant | Meaning |
|-----------|----------|---------|
| 0 | Success | Command completed successfully |
| 1 | GENERAL_ERROR | Validation error or server rejection |
| 2 | NOT_FOUND | Requested resource does not exist |
| 3 | CONNECTION_ERROR | Cannot reach the Memories server |
| 4 | AUTH_REQUIRED | API key is missing or invalid |

**Example: robust agent script with error handling:**

```bash
#!/bin/bash
set -euo pipefail

add_memory() {
  local text="$1"
  local source="$2"
  local result exit_code

  result=$(memories --json add -s "$source" "$text" 2>&1) || exit_code=$?
  exit_code=${exit_code:-0}

  case $exit_code in
    0)
      echo "OK: $(echo "$result" | jq -r '.data.id')"
      ;;
    3)
      echo "ERROR: Server unreachable. Is the Memories server running?" >&2
      return 1
      ;;
    4)
      echo "ERROR: Authentication failed. Check MEMORIES_API_KEY." >&2
      return 1
      ;;
    *)
      echo "ERROR: $(echo "$result" | jq -r '.error // "Unknown error"')" >&2
      return 1
      ;;
  esac
}

# Usage
add_memory "User prefers TypeScript over JavaScript" "agent/mybot/prefs"
```

## What You Have Now

After completing this tutorial:

- Your agent connects to Memories via environment variables (no secrets in command args)
- You can add, search, and parse memories in structured JSON
- You can run batch operations from JSONL files or piped input
- You can extract memories from conversation transcripts
- Your scripts handle errors gracefully using exit codes

## Troubleshooting

### jq reports a parse error

Make sure you are using the `--json` flag or piping the output. In a terminal without piping, the CLI outputs human-readable text, not JSON.

```bash
# Wrong -- human output in terminal, not parseable JSON
memories search "test" | jq .

# Correct -- force JSON output
memories --json search "test" | jq .
```

### "Cannot connect to server" in CI/CD

In CI/CD environments, the Memories server may not be at `localhost:8900`. Set the URL explicitly:

```bash
export MEMORIES_URL="http://memories-server:8900"
```

### Stdin reads hang

If the CLI appears to hang, it may be waiting for stdin input. Make sure you either:
- Pass the text as a direct argument: `memories add -s src/ "the text"`
- Pipe input explicitly: `echo "the text" | memories add -s src/ -`

Do not pass `-` as the text argument without providing stdin data.

### Batch add rejects my file

The batch add command expects either:
- A JSON array: `[{"text": "...", "source": "..."}, ...]`
- JSONL (one object per line): each line is `{"text": "...", "source": "..."}`

Each object must have at least a `text` field. The `source` field is required.

## Next Steps

- [CLI Feature Documentation](../features/feat-002-cli.md) -- full command reference and options
- [CLI Quick Start](tut-003-cli-quickstart.md) -- basic setup if you have not done it yet
- [CLI Recipes](../cookbook.md#cli-recipes) -- copy-paste patterns for common tasks
- [CLI Errors](../error-reference.md#cli-errors) -- detailed error reference
