---
doc-id: uc-001
type: use-case
title: "Agent Isolation: Claude Code + Kai on a Shared Server"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# Use Case: Agent Isolation (Claude Code + Kai Sharing a Server)

## Scenario

A developer runs two AI agents -- Claude Code and Kai -- that both connect to the same Memories server. Each agent should only see and write to its own memories. Neither agent should be able to read, modify, or delete the other's data.

## Setup

### 1. Ensure admin access

The developer's `API_KEY` env var is already set (e.g., `god-is-an-astronaut`). This key is the admin key and has unrestricted access.

### 2. Create scoped keys

Using the admin key, create two managed keys:

```bash
# Key for Claude Code
curl -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: god-is-an-astronaut" \
  -H "Content-Type: application/json" \
  -d '{"name": "claude-code", "role": "read-write", "prefixes": ["claude-code/*"]}'

# Key for Kai
curl -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: god-is-an-astronaut" \
  -H "Content-Type: application/json" \
  -d '{"name": "kai-agent", "role": "read-write", "prefixes": ["kai/*"]}'
```

Each response includes a `"key"` field with the raw key (e.g., `mem_a3f8b2c1d4e5f6071829304a5b6c7d8e`). Save it immediately -- it is shown once and never stored.

### 3. Configure each agent

Set the `X-API-Key` header in each agent's MCP client configuration to its respective managed key.

## How Isolation Works

### Writes

When Claude Code adds a memory with `source: "claude-code/decisions"`, the middleware checks `auth.can_write("claude-code/decisions")`. The key's prefix `claude-code/*` normalizes to base `claude-code`, and `"claude-code/decisions".startswith("claude-code/")` is true -- write allowed.

If Claude Code attempts to write with `source: "kai/notes"`, `can_write` returns false because `"kai/notes"` does not match the `claude-code` base. The system returns HTTP 403.

### Reads

When Kai searches for memories, the results pass through `auth.filter_results()`, which strips out any memory whose `source` does not match `kai/*`. Even if the vector search returns Claude Code memories as semantically similar, they are filtered out before the response.

### Path traversal

If an agent attempts `source: "claude-code/../kai/notes"`, the `_matches_prefix` method detects `..` as a path component and rejects the request. No prefix matching is even attempted.

### Cross-agent visibility

Neither agent can see the other's data:
- Search results are filtered by prefix.
- Single-memory GET checks `can_read(source)`.
- List and batch-get results are filtered.
- Folder listing only includes folders the caller can read.

### Admin override

The developer, using the env key, retains full visibility across both agents' data. The admin can search, list, and manage all memories regardless of source prefix.

## Verification

After setup, each agent can verify its own scope:

```bash
curl http://localhost:8900/api/keys/me \
  -H "X-API-Key: mem_<claude-code-key>"
```

Expected response:
```json
{
  "type": "managed",
  "role": "read-write",
  "prefixes": ["claude-code/*"],
  "id": "...",
  "name": "claude-code"
}
```

## Considerations

- **Prefix granularity**: The prefix `claude-code/*` covers all sub-paths. For finer control, multiple specific prefixes can be assigned (e.g., `["claude-code/decisions", "claude-code/learnings"]`), but each must be listed explicitly.
- **Extract operations**: Both agents have `read-write` role, which includes extract access. If an agent should only read, use `read-only` instead.
- **Shared knowledge**: If the agents need access to a common pool of memories, add a shared prefix to both keys (e.g., `["claude-code/*", "shared/*"]` and `["kai/*", "shared/*"]`).
