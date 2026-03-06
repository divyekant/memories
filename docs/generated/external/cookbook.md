---
title: "Cookbook: Recipes"
slug: cookbook
type: cookbook
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# Cookbook

Practical patterns for common scenarios. Each recipe is self-contained — pick the ones that match your setup.

---

# Multi-Auth Recipes

Patterns for using prefix-scoped API keys.

---

## Recipe 1: Isolate Two AI Agents on One Server

**Scenario:** You run Claude Code and Cursor on the same Memories instance. You want each agent to have its own namespace so they cannot read or modify each other's memories.

### Create the Keys

```bash
# Claude Code key
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-code",
    "role": "read-write",
    "prefixes": ["claude-code/*"]
  }' | python3 -m json.tool
```

```bash
# Cursor key
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cursor",
    "role": "read-write",
    "prefixes": ["cursor/*"]
  }' | python3 -m json.tool
```

### Result

- Claude Code adds memories with source `claude-code/myapp/decisions` — visible only to Claude Code
- Cursor adds memories with source `cursor/myapp/context` — visible only to Cursor
- Neither agent can search or modify the other's memories

---

## Recipe 2: Read-Only Key for Monitoring

**Scenario:** You want to build a dashboard or script that reads memory stats without the risk of accidentally modifying data.

### Create the Key

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "monitoring",
    "role": "read-only",
    "prefixes": ["claude-code/*", "learning/*", "wip/*"]
  }' | python3 -m json.tool
```

### Use It

```bash
# Search is allowed
curl -s -X POST http://localhost:8900/search \
  -H "X-API-Key: mem_MONITORING_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"query": "recent architecture decisions", "k": 10}' | python3 -m json.tool

# Add is blocked (403)
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8900/memory/add \
  -H "X-API-Key: mem_MONITORING_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"text": "test", "source": "claude-code/test"}'
# Output: 403
```

---

## Recipe 3: CI/CD Pipeline Integration

**Scenario:** Your CI pipeline checks Memories during code review to see if a proposed pattern was previously decided against. The pipeline should never write.

### Create the Key

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ci-pipeline",
    "role": "read-only",
    "prefixes": ["claude-code/*", "learning/*"]
  }' | python3 -m json.tool
```

### Use in a CI Script

```bash
#!/bin/bash
# ci-check-decisions.sh
# Check if a pattern was decided against before merging

MEMORIES_KEY="mem_YOUR_CI_KEY_HERE"
PATTERN="$1"

result=$(curl -s -X POST http://localhost:8900/search \
  -H "X-API-Key: $MEMORIES_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"decision against $PATTERN\", \"k\": 3, \"threshold\": 0.7}")

count=$(echo "$result" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))")

if [ "$count" -gt "0" ]; then
  echo "WARNING: Found $count relevant decisions about '$PATTERN'"
  echo "$result" | python3 -m json.tool
  exit 1
fi

echo "No conflicting decisions found for '$PATTERN'"
```

---

## Recipe 4: Team Shared Knowledge Base

**Scenario:** Three team members share a Memories instance. Each has a personal namespace, plus a shared namespace everyone can read from. Only designated writers can add to shared.

### Create the Keys

```bash
# Alice: personal read-write + shared read-write
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "alice",
    "role": "read-write",
    "prefixes": ["team/alice/*", "team/shared/*"]
  }' | python3 -m json.tool

# Bob: personal read-write + shared read-only
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bob-personal",
    "role": "read-write",
    "prefixes": ["team/bob/*"]
  }' | python3 -m json.tool

curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bob-reader",
    "role": "read-only",
    "prefixes": ["team/shared/*"]
  }' | python3 -m json.tool
```

**Note:** Bob has two keys — one for his personal namespace (read-write) and one for the shared namespace (read-only). This gives you fine-grained control without overloading a single key.

Alternatively, if you want Bob to have one key for everything, you can create a single read-write key with both prefixes. The tradeoff is that Bob could then write to `team/shared/*` as well.

---

## Recipe 5: Temporary Key for a Guest or Contractor

**Scenario:** A contractor needs to search your memories for a week-long engagement. You want to give them the minimum necessary access and revoke it when they are done.

### Create the Key

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "contractor-jane-2026-03",
    "role": "read-only",
    "prefixes": ["projects/frontend/*"]
  }' | python3 -m json.tool
```

### Revoke When Done

```bash
curl -s -X DELETE http://localhost:8900/api/keys/THE_KEY_UUID \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

**Expected output:**

```json
{
    "message": "Key revoked",
    "id": "THE_KEY_UUID"
}
```

The key is immediately unusable. No grace period, no expiry delay.

---

## Recipe 6: Key Rotation Without Downtime

**Scenario:** You want to rotate a key periodically for security. You need to do it without any downtime for the agent using the key.

### Steps

1. Create the new key with the same scope:

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-code-v2",
    "role": "read-write",
    "prefixes": ["claude-code/*"]
  }' | python3 -m json.tool
```

2. Update your agent's configuration with the new key.

3. Verify the new key works:

```bash
curl -s http://localhost:8900/api/keys/me \
  -H "X-API-Key: mem_NEW_KEY_HERE" | python3 -m json.tool
```

4. Revoke the old key:

```bash
curl -s -X DELETE http://localhost:8900/api/keys/OLD_KEY_UUID \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

Both keys work simultaneously between steps 1 and 4, so there is no window where the agent loses access.

---

## Recipe 7: Expand a Key's Scope

**Scenario:** Your Claude Code agent now needs to also read the `learning/*` namespace.

### Update the Key

```bash
curl -s -X PATCH http://localhost:8900/api/keys/CLAUDE_CODE_KEY_UUID \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prefixes": ["claude-code/*", "learning/*"]
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": "CLAUDE_CODE_KEY_UUID",
    "key_prefix": "mem_a3f8",
    "name": "claude-code",
    "role": "read-write",
    "prefixes": [
        "claude-code/*",
        "learning/*"
    ],
    "created_at": "2026-03-05T10:00:00Z",
    "last_used_at": "2026-03-05T14:32:00Z",
    "usage_count": 847,
    "revoked": false
}
```

The change takes effect immediately. The next request with that key can access `learning/*` memories. No agent restart is needed.

---

## Recipe 8: Downgrade a Key from Read-Write to Read-Only

**Scenario:** An integration that used to write memories no longer needs write access. You want to reduce its permissions without revoking and recreating the key.

### Update the Role

```bash
curl -s -X PATCH http://localhost:8900/api/keys/INTEGRATION_KEY_UUID \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "read-only"
  }' | python3 -m json.tool
```

The key continues to work for searches, but any add or delete attempts will return 403 immediately.

---

## Recipe 9: Audit Key Usage

**Scenario:** You want to see which keys are active, which are unused, and how much traffic each generates.

### List All Keys with Usage Stats

```bash
curl -s http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" | python3 -c "
import sys, json
keys = json.load(sys.stdin)
print(f'{'Name':<25} {'Role':<12} {'Uses':>8} {'Last Used':<22} {'Status':<8}')
print('-' * 80)
for k in keys:
    status = 'REVOKED' if k['revoked'] else 'active'
    last = k.get('last_used_at') or 'never'
    print(f'{k[\"name\"]:<25} {k[\"role\"]:<12} {k[\"usage_count\"]:>8} {last:<22} {status:<8}')
"
```

**Example output:**

```
Name                      Role            Uses Last Used              Status
--------------------------------------------------------------------------------
claude-code               read-write       847 2026-03-05T14:32:00Z   active
ci-reader                 read-only         23 2026-03-05T12:00:00Z   active
old-integration           read-write         0 never                  REVOKED
```

---

## Recipe 10: Web UI Key Management

**Scenario:** You prefer a visual interface over curl commands.

### Steps

1. Open `http://localhost:8900/ui` in your browser
2. The Web UI uses the API key from your browser's session (configured at first login)
3. If you are using an admin key, you will see **API Keys** in the sidebar
4. From the API Keys page you can:
   - View all keys with their roles, prefixes, creation dates, and usage counts
   - Click **Create Key** to open a form with name, role selector, and prefix input
   - The new key is shown once in a copy-to-clipboard modal — save it immediately
   - Click a key row to edit its name, role, or prefixes
   - Click **Revoke** to soft-delete a key (with confirmation dialog)
   - Revoked keys appear with a "Revoked" badge and strikethrough styling

---

# CLI Recipes

Patterns for using the Memories CLI in scripts, agents, and day-to-day workflows.

---

## Recipe 11: Search and Format Results

**Scenario:** You want to search for memories and display them in a custom format.

```bash
memories --json search "architecture decisions" -k 5 \
  | jq -r '.data.results[] | "[\(.id)] (\(.similarity * 100 | floor)%) \(.source)\n  \(.text)\n"'
```

**Example output:**

```
[12] (92%) claude-code/myapp/decisions
  Use repository pattern for data access layer

[8] (87%) claude-code/myapp/architecture
  PostgreSQL for primary datastore, Redis for caching
```

---

## Recipe 12: Add a Memory From the Command Line

**Scenario:** You quickly want to capture a fact without leaving the terminal.

```bash
memories add -s learning/python/ "asyncio.TaskGroup replaces gather() in Python 3.11+"
```

**Expected output:**

```
Added memory #51
```

If you want confirmation with the full JSON response:

```bash
memories --json add -s learning/python/ "asyncio.TaskGroup replaces gather() in Python 3.11+" | jq .
```

---

## Recipe 13: Pipe Stdin to Add

**Scenario:** You want to add text from another command's output or from a file.

```bash
# From a command
git log --oneline -1 | memories add -s project/commits/ -

# From a file
cat meeting-notes.txt | memories add -s team/meetings/ -

# From a heredoc
memories add -s decisions/api/ - <<'EOF'
We decided to use GraphQL for the public API because our clients
need flexible queries and we want to avoid over-fetching.
EOF
```

---

## Recipe 14: Batch Add From JSONL

**Scenario:** You have a batch of facts to load into Memories at once.

Create a file called `facts.jsonl`:

```json
{"text": "Rate limit: 100 req/min per API key", "source": "infra/limits/"}
{"text": "Deploy window: Tue-Thu 10am-4pm ET", "source": "process/deploy/"}
{"text": "Staging resets nightly at 02:00 UTC", "source": "infra/staging/"}
```

```bash
memories batch add facts.jsonl
```

**Expected output:**

```
Added 3 memories
```

You can also pipe JSONL from another process:

```bash
your-export-script --format jsonl | memories batch add -
```

---

## Recipe 15: Check Novelty Before Adding

**Scenario:** Your agent wants to avoid adding duplicate or near-duplicate information.

```bash
memories --json is-novel "Use repository pattern for data access" | jq '.data'
```

**If novel:**

```json
{
  "is_novel": true
}
```

**If not novel:**

```json
{
  "is_novel": false,
  "most_similar": {
    "id": 12,
    "text": "Use repository pattern for data access layer",
    "similarity": 0.96
  }
}
```

**In a script:**

```bash
#!/bin/bash
TEXT="Use repository pattern for data access"
is_novel=$(memories --json is-novel "$TEXT" | jq -r '.data.is_novel')

if [ "$is_novel" = "true" ]; then
  memories add -s decisions/arch/ "$TEXT"
  echo "Added."
else
  echo "Already known -- skipping."
fi
```

---

## Recipe 16: Export Memories to JSON

**Scenario:** You want to export all memories (or a subset) for analysis, migration, or backup.

```bash
# Export all memories as JSON
memories --json list --limit 1000 | jq '.data.memories' > export.json

# Export only memories from a specific source
memories --json list --source claude-code/ --limit 500 | jq '.data.memories' > claude-code-export.json

# Export just the text (one per line)
memories --json list --limit 1000 | jq -r '.data.memories[].text' > memories.txt
```

---

## Recipe 17: Backup Before Maintenance

**Scenario:** You are about to run deduplication or pruning and want a safety net.

```bash
# Create a backup
memories backup create --prefix pre-dedup

# Verify it exists
memories backup list

# Run deduplication (dry run first)
memories admin deduplicate --threshold 0.90 --dry-run

# If it looks right, execute
memories admin deduplicate --threshold 0.90 --execute

# If something went wrong, restore
memories backup restore pre-dedup-2026-03-05T10-00-00 -y
```

---

## Recipe 18: Extract Memories From a Transcript

**Scenario:** You have a conversation transcript and want to automatically extract memorable facts.

```bash
# Submit the transcript for extraction
cat session-log.txt | memories --json extract submit -s agent/session-123/ -

# Note the job_id from the output, then poll until complete
memories extract poll ext-abc123 --wait

# Or check status manually
memories extract status ext-abc123
```

**For a fully automated pipeline:**

```bash
#!/bin/bash
# Submit and capture the job ID
job_id=$(cat transcript.txt \
  | memories --json extract submit -s agent/daily/ - \
  | jq -r '.data.job_id')

echo "Submitted extraction job: $job_id"

# Wait for completion (up to 2 minutes)
result=$(memories --json extract poll "$job_id" --wait --timeout 120)
status=$(echo "$result" | jq -r '.data.status')

if [ "$status" = "completed" ]; then
  count=$(echo "$result" | jq '.data.memories | length')
  echo "Extracted $count memories"
else
  echo "Extraction failed: $(echo "$result" | jq -r '.data.error')"
fi
```

---

## Recipe 19: Delete by Source Prefix

**Scenario:** You want to clean up all memories from a retired project or test run.

```bash
# Preview what will be deleted (check count first)
memories count --source old-project/

# Delete all memories with that prefix
memories delete-by prefix "old-project/" -y
```

**Expected output:**

```
Deleted 47 memories with prefix 'old-project/'
```

Without `-y`, the CLI prompts for confirmation:

```
Delete all memories matching source pattern 'old-project/'? [y/N]:
```

---

## Recipe 20: Monitor Server Health

**Scenario:** You want a quick health check in your monitoring script or cron job.

```bash
#!/bin/bash
# Quick health check
if memories --json admin health | jq -e '.data.status == "ok"' > /dev/null 2>&1; then
  echo "Memories server: healthy"
else
  echo "Memories server: DOWN" >&2
  exit 1
fi
```

**Full monitoring script:**

```bash
#!/bin/bash
echo "=== Memories Server Status ==="
memories admin health
echo ""
echo "=== Memory Count ==="
memories count
echo ""
echo "=== Folder Breakdown ==="
memories folders
echo ""
echo "=== Usage (7 days) ==="
memories admin usage --period 7d
```

---

## Export/Import

### Export All Memories to File

**Goal:** Create a portable backup of all memories.

```bash
memories export -o all-memories.jsonl
```

**Notes:**
- Output is NDJSON — one JSON object per line
- First line is a header with metadata (count, version, timestamp)
- [Feature doc: Export/Import](features/feat-003-export-import.md)

### Export Filtered by Source

**Goal:** Export only memories from a specific project.

```bash
memories export --source "claude-code/myapp" -o project.jsonl
```

**Notes:**
- Source filter uses prefix matching
- Combine with `--since` and `--until` for date range filtering

### Import with Smart Dedup

**Goal:** Import memories without creating duplicates.

```bash
memories import data.jsonl --strategy smart
```

**Notes:**
- Skips exact duplicates (>=0.95 similarity)
- For near-duplicates, keeps the newer version
- Creates an automatic backup before importing

### Import with Source Remapping

**Goal:** Import memories and change their source namespace.

```bash
memories import data.jsonl --source-remap "old-project/=new-project/"
```

**Notes:**
- Remapping applies to all records during import
- Useful when consolidating multiple projects into one namespace

### Export via API (curl)

**Goal:** Export memories programmatically.

```bash
curl -H "X-API-Key: YOUR_KEY" "http://localhost:8900/export?source=claude-code/" -o export.jsonl
```

**Notes:**
- Response is streaming NDJSON with `application/x-ndjson` content type
- Add `since` and `until` query params for date filtering

### Import via API (curl)

**Goal:** Import memories programmatically.

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/x-ndjson" \
  "http://localhost:8900/import?strategy=smart" \
  --data-binary @export.jsonl
```

**Notes:**
- Use `--data-binary` (not `-d`) to preserve newlines in NDJSON
- Response includes imported/skipped/updated counts
