---
title: "Cookbook: Multi-Auth Recipes"
slug: cookbook-multi-auth
type: cookbook
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# Cookbook: Multi-Auth Recipes

Practical patterns for using prefix-scoped API keys in common scenarios. Each recipe is self-contained — pick the ones that match your setup.

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
