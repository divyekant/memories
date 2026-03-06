---
title: "Tutorial: Setting Up Multi-Auth"
slug: tut-001-setup-multi-auth
type: tutorial
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# Tutorial: Setting Up Multi-Auth

In this tutorial, you will enable multi-auth on your Memories instance, create scoped keys for different use cases, and verify that prefix isolation works correctly.

**Time:** 10 minutes

**Prerequisites:**
- Memories v1.5.0 running at `http://localhost:8900`
- An `API_KEY` configured in your `.env` file (this is your admin key)
- `curl` and `python3` available in your terminal

## Step 1: Verify Your Admin Access

First, confirm that your existing key works as admin.

```bash
curl -s http://localhost:8900/api/keys/me \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

**Expected output:**

```json
{
    "type": "env",
    "role": "admin"
}
```

If you see this, your existing key is recognized as admin and you can manage other keys.

## Step 2: Create a Read-Write Key

Create a key that can read and write memories, but only within the `claude-code/*` prefix.

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-code-rw",
    "role": "read-write",
    "prefixes": ["claude-code/*"]
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
    "key": "mem_a3f8b2c1d4e5f6071829304a5b6c7d8e",
    "key_prefix": "mem_a3f8",
    "name": "claude-code-rw",
    "role": "read-write",
    "prefixes": [
        "claude-code/*"
    ],
    "created_at": "2026-03-05T10:00:00Z"
}
```

**Important:** Copy the `key` value and save it somewhere safe. This is the only time it will be shown. In the examples below, replace `YOUR_RW_KEY` with this value.

## Step 3: Create a Read-Only Key

Create a key that can only search memories within the `learning/*` prefix.

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "learning-reader",
    "role": "read-only",
    "prefixes": ["learning/*"]
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": "e4a1c3b7-89d2-4f6e-a1b5-3c7d9e2f4a6b",
    "key": "mem_7b2ef1c3d4a5b6071829304e5f6c8d9a",
    "key_prefix": "mem_7b2e",
    "name": "learning-reader",
    "role": "read-only",
    "prefixes": [
        "learning/*"
    ],
    "created_at": "2026-03-05T10:01:00Z"
}
```

Save this key as well. In the examples below, replace `YOUR_RO_KEY` with this value.

## Step 4: Test the Read-Write Key

### Add a memory within scope

```bash
curl -s -X POST http://localhost:8900/memory/add \
  -H "X-API-Key: YOUR_RW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The auth module uses SHA-256 for key hashing",
    "source": "claude-code/myapp/architecture"
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": 1,
    "message": "Memory added",
    "deduplicated": false
}
```

### Try to add a memory outside scope

```bash
curl -s -X POST http://localhost:8900/memory/add \
  -H "X-API-Key: YOUR_RW_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Python async pitfall: forgetting to await",
    "source": "learning/python/async"
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "detail": "Source prefix not allowed"
}
```

The response status will be **403 Forbidden**. The key scoped to `claude-code/*` cannot write to `learning/`.

## Step 5: Test the Read-Only Key

### Search within scope

```bash
curl -s -X POST http://localhost:8900/search \
  -H "X-API-Key: YOUR_RO_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "python async"}' | python3 -m json.tool
```

This will return search results filtered to only memories with sources starting with `learning/`. Memories from other prefixes will not appear.

### Try to add a memory

```bash
curl -s -X POST http://localhost:8900/memory/add \
  -H "X-API-Key: YOUR_RO_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "This should fail",
    "source": "learning/test"
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "detail": "Insufficient permissions"
}
```

The response status will be **403 Forbidden**. Read-only keys cannot add or delete memories.

## Step 6: List Your Keys

View all managed keys from your admin key:

```bash
curl -s http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

**Expected output:**

```json
[
    {
        "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
        "key_prefix": "mem_a3f8",
        "name": "claude-code-rw",
        "role": "read-write",
        "prefixes": ["claude-code/*"],
        "created_at": "2026-03-05T10:00:00Z",
        "last_used_at": "2026-03-05T10:02:00Z",
        "usage_count": 2,
        "revoked": false
    },
    {
        "id": "e4a1c3b7-89d2-4f6e-a1b5-3c7d9e2f4a6b",
        "key_prefix": "mem_7b2e",
        "name": "learning-reader",
        "role": "read-only",
        "prefixes": ["learning/*"],
        "created_at": "2026-03-05T10:01:00Z",
        "last_used_at": "2026-03-05T10:03:00Z",
        "usage_count": 2,
        "revoked": false
    }
]
```

Notice that the full key is not shown — only the `key_prefix` for identification.

## Step 7: Revoke a Key

If a key is compromised or no longer needed, revoke it:

```bash
curl -s -X DELETE http://localhost:8900/api/keys/e4a1c3b7-89d2-4f6e-a1b5-3c7d9e2f4a6b \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

**Expected output:**

```json
{
    "message": "Key revoked",
    "id": "e4a1c3b7-89d2-4f6e-a1b5-3c7d9e2f4a6b"
}
```

Any subsequent request using the revoked key will return **401 Unauthorized**.

## What You Have Now

After completing this tutorial:

- Your existing `API_KEY` continues to work as admin with full access
- You have a `claude-code-rw` key that can read and write within `claude-code/*`
- You saw that prefix enforcement blocks access to out-of-scope memories
- You saw that role enforcement prevents read-only keys from writing
- You know how to revoke a key instantly

## Next Steps

- [Create Scoped Keys for AI Agents](tut-002-scoped-keys-for-agents.md) — configure Claude Code, Kai, or other agents with dedicated keys
- [Multi-Auth Recipes](../cookbook.md) — common patterns for team setups, CI/CD, and monitoring
- [Error Reference](../error-reference.md) — full list of auth-related error codes
