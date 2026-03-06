---
title: "Tutorial: Creating Scoped Keys for AI Agents"
slug: tut-002-scoped-keys-for-agents
type: tutorial
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# Tutorial: Creating Scoped Keys for AI Agents

In this tutorial, you will create dedicated API keys for different AI agents so each agent can only access its own memories. You will configure Claude Code, a CI reader, and a shared learning namespace — then verify the isolation works.

**Time:** 15 minutes

**Prerequisites:**
- Memories v1.5.0 running at `http://localhost:8900`
- Admin access (your `API_KEY` env var)
- `curl` and `python3` available in your terminal

## The Scenario

You have three consumers of your Memories instance:

1. **Claude Code** — your primary AI coding assistant. It reads and writes memories about architecture decisions, project conventions, and deferred work.
2. **CI pipeline** — a read-only consumer that checks memories during code review (e.g., "was this pattern decided against?").
3. **Learning agent** — a separate agent that captures general programming learnings you want to share across projects.

Each should have its own key and prefix scope.

## Step 1: Plan Your Prefix Layout

Before creating keys, decide on your source prefix structure:

| Agent | Prefix | Role | Rationale |
|-------|--------|------|-----------|
| Claude Code | `claude-code/*` | read-write | Needs to add and search its own memories |
| CI pipeline | `claude-code/*`, `learning/*` | read-only | Reads decisions and learnings, but never writes |
| Learning agent | `learning/*` | read-write | Captures learnings across projects |

## Step 2: Create the Claude Code Key

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-code",
    "role": "read-write",
    "prefixes": ["claude-code/*"]
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "key": "mem_1a2b3c4d5e6f70819203a4b5c6d7e8f9",
    "key_prefix": "mem_1a2b",
    "name": "claude-code",
    "role": "read-write",
    "prefixes": [
        "claude-code/*"
    ],
    "created_at": "2026-03-05T10:00:00Z"
}
```

Save the `key` value. You will use it to configure Claude Code's MCP connection.

## Step 3: Create the CI Pipeline Key

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ci-reader",
    "role": "read-only",
    "prefixes": ["claude-code/*", "learning/*"]
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "key": "mem_2b3c4d5e6f7081920a3b4c5d6e7f8091",
    "key_prefix": "mem_2b3c",
    "name": "ci-reader",
    "role": "read-only",
    "prefixes": [
        "claude-code/*",
        "learning/*"
    ],
    "created_at": "2026-03-05T10:01:00Z"
}
```

This key can search across both `claude-code/` and `learning/` namespaces but cannot add or delete anything.

## Step 4: Create the Learning Agent Key

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "learning-agent",
    "role": "read-write",
    "prefixes": ["learning/*"]
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
    "key": "mem_3c4d5e6f708192a0b3c4d5e6f7081920",
    "key_prefix": "mem_3c4d",
    "name": "learning-agent",
    "role": "read-write",
    "prefixes": [
        "learning/*"
    ],
    "created_at": "2026-03-05T10:02:00Z"
}
```

## Step 5: Configure Claude Code

To use the scoped key with Claude Code's MCP server, update your MCP configuration. In your `.mcp.json` or Claude Code settings, set the API key for the Memories server:

```json
{
  "mcpServers": {
    "memories": {
      "command": "npx",
      "args": ["-y", "memories-mcp@latest"],
      "env": {
        "MEMORIES_API_URL": "http://localhost:8900",
        "MEMORIES_API_KEY": "mem_1a2b3c4d5e6f70819203a4b5c6d7e8f9"
      }
    }
  }
}
```

Claude Code will now only be able to access memories under `claude-code/*`. When it calls `memory_search`, results are automatically filtered. When it calls `memory_add`, the source must start with `claude-code/`.

## Step 6: Verify Agent Isolation

### Claude Code adds a memory

```bash
curl -s -X POST http://localhost:8900/memory/add \
  -H "X-API-Key: mem_1a2b3c4d5e6f70819203a4b5c6d7e8f9" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Use repository pattern for data access layer",
    "source": "claude-code/myapp/decisions"
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

### Learning agent adds a memory

```bash
curl -s -X POST http://localhost:8900/memory/add \
  -H "X-API-Key: mem_3c4d5e6f708192a0b3c4d5e6f7081920" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Python dataclasses with slots=True reduce memory footprint by 30%",
    "source": "learning/python/performance"
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
    "id": 2,
    "message": "Memory added",
    "deduplicated": false
}
```

### CI reader searches across both namespaces

```bash
curl -s -X POST http://localhost:8900/search \
  -H "X-API-Key: mem_2b3c4d5e6f7081920a3b4c5d6e7f8091" \
  -H "Content-Type: application/json" \
  -d '{"query": "data patterns performance"}' | python3 -m json.tool
```

The CI reader sees results from both `claude-code/` and `learning/` because its key covers both prefixes.

### Claude Code cannot see learning memories

```bash
curl -s -X POST http://localhost:8900/search \
  -H "X-API-Key: mem_1a2b3c4d5e6f70819203a4b5c6d7e8f9" \
  -H "Content-Type: application/json" \
  -d '{"query": "Python dataclasses performance"}' | python3 -m json.tool
```

**Expected behavior:** The search returns no results (or only results from `claude-code/*`), even though the learning memory exists. The Claude Code key cannot see `learning/` memories.

### Learning agent cannot write to Claude Code's namespace

```bash
curl -s -X POST http://localhost:8900/memory/add \
  -H "X-API-Key: mem_3c4d5e6f708192a0b3c4d5e6f7081920" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Trying to write to wrong namespace",
    "source": "claude-code/myapp/hack"
  }'
```

**Expected output (403):**

```json
{
    "detail": "Source prefix not allowed"
}
```

## Step 7: Monitor Key Usage

Check how your keys are being used:

```bash
curl -s http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

Look at the `usage_count` and `last_used_at` fields to see which keys are active and how frequently they are used.

## Step 8: Rotate a Key

If you need to rotate a key (for example, the Claude Code key), follow these steps:

1. Create a new key with the same name and scope:

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

2. Update your MCP configuration with the new key.

3. Revoke the old key:

```bash
curl -s -X DELETE http://localhost:8900/api/keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

The old key is immediately unusable. The new key takes effect as soon as your MCP configuration is reloaded.

## What You Have Now

After completing this tutorial:

- Three isolated agents, each with their own scoped key
- Claude Code can only access `claude-code/*` memories
- A CI pipeline can search (but not modify) both `claude-code/*` and `learning/*`
- A learning agent can only access `learning/*` memories
- No agent can read or modify another agent's data without explicit prefix overlap

## Common Prefix Layouts

Here are prefix layouts that work well for different team structures:

### Solo developer, multiple agents

| Key | Prefixes | Role |
|-----|----------|------|
| claude-code | `claude-code/*` | read-write |
| cursor | `cursor/*` | read-write |
| shared-reader | `claude-code/*`, `cursor/*` | read-only |

### Team with shared knowledge base

| Key | Prefixes | Role |
|-----|----------|------|
| alice-agent | `team/alice/*`, `team/shared/*` | read-write |
| bob-agent | `team/bob/*`, `team/shared/*` | read-write |
| ci-review | `team/*` | read-only |

### Project isolation

| Key | Prefixes | Role |
|-----|----------|------|
| project-a | `projects/alpha/*` | read-write |
| project-b | `projects/beta/*` | read-write |
| cross-project | `projects/*` | read-only |

## Next Steps

- [Multi-Auth Recipes](../cookbook.md) — more patterns including CI/CD, webhooks, and temporary keys
- [Error Reference](../error-reference.md) — troubleshoot 401 and 403 errors
- [API Reference](../api-reference.md) — full endpoint documentation for key management
