---
title: "Multi-Auth: Prefix-Scoped API Keys"
slug: feat-001-multi-auth
type: feature
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# Multi-Auth: Prefix-Scoped API Keys

Memories v1.5.0 introduces **multi-auth** — you can now create multiple API keys with different access levels, each scoped to specific source prefixes. This lets you give each AI agent, tool, or team member exactly the access they need and nothing more.

## Why Multi-Auth?

If you run a single Memories instance serving multiple agents or users, you previously had one shared `API_KEY` with full access to everything. That meant:

- Any agent could read or delete another agent's memories
- A compromised integration could wipe your entire memory store
- There was no way to give someone read-only access

Multi-auth solves all three problems.

## Key Concepts

### Roles

Every API key has one of three roles:

| Role | Can Search | Can Add/Delete | Can Manage Keys |
|------|-----------|----------------|-----------------|
| `read-only` | Yes (within scope) | No | No |
| `read-write` | Yes (within scope) | Yes (within scope) | No |
| `admin` | Yes (all) | Yes (all) | Yes |

### Prefix Scoping

Non-admin keys are bound to one or more **source prefixes**. A key with prefix `claude-code/*` can only see and modify memories whose `source` field starts with `claude-code/`.

For example, if you have memories with these sources:

- `claude-code/myapp/architecture`
- `claude-code/myapp/decisions`
- `learning/python/async`
- `wip/myapp/refactor`

A key scoped to `claude-code/*` can access the first two but not the others. A key scoped to both `claude-code/*` and `learning/*` can access the first three.

Admin keys have no prefix restrictions — they can access everything.

### Key Format

Keys follow the format `mem_` followed by 32 hexadecimal characters:

```
mem_a3f8b2c1d4e5f6071829304a5b6c7d8e
```

Keys are shown **exactly once** at creation. Memories stores only a SHA-256 hash — there is no way to recover a lost key.

## Getting Started

### Your Existing Setup Still Works

If you already have an `API_KEY` set in your `.env` or `docker-compose.yml`, it continues to work as an implicit admin key. No migration is needed. Multi-auth is purely additive.

### Check Your Current Access

```bash
curl -s http://localhost:8900/api/keys/me \
  -H "X-API-Key: your-existing-key" | python3 -m json.tool
```

If you are using the env-based admin key, you will see:

```json
{
    "type": "env",
    "role": "admin"
}
```

### Create a Scoped Key

To create a read-write key scoped to `claude-code/*`:

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-code-prod",
    "role": "read-write",
    "prefixes": ["claude-code/*"]
  }' | python3 -m json.tool
```

**Save the `key` field from the response immediately.** It will not be shown again.

### Use the Scoped Key

Pass the new key in the `X-API-Key` header to any Memories API call:

```bash
curl -s -X POST http://localhost:8900/search \
  -H "X-API-Key: mem_a3f8b2c1d4e5f6071829304a5b6c7d8e" \
  -H "Content-Type: application/json" \
  -d '{"query": "architecture decisions"}' | python3 -m json.tool
```

Search results are automatically filtered to only include memories within the key's allowed prefixes.

## Web UI

If you access the Web UI at `http://localhost:8900/ui` with an admin key, you will see an **API Keys** page in the sidebar. From there you can:

- View all keys with their roles, prefixes, and usage statistics
- Create new keys with a visual prefix selector
- Update key names, roles, or prefix bindings
- Revoke keys (soft-delete with confirmation)

The API Keys page is hidden from the sidebar for non-admin keys.

## How Prefix Enforcement Works

Every API operation is scope-checked for non-admin keys:

- **Search and list**: Results are filtered to memories whose `source` starts with an allowed prefix
- **Add**: The `source` field must start with an allowed prefix, or the request returns 403
- **Delete**: The target memory's `source` must match an allowed prefix, or the request returns 403
- **Extract**: Extracted memories are source-checked before being written

## Configuration

No new environment variables are required. Multi-auth uses your existing `API_KEY` env var as the admin key and stores additional keys in an SQLite database alongside your memory data.

| Setting | Where | Description |
|---------|-------|-------------|
| `API_KEY` | `.env` or `docker-compose.yml` | Your admin key (unchanged from previous versions) |

## Limits and Considerations

- Key names must be unique
- Keys use `mem_` prefix format and are 36 characters total
- Revoked keys return 401 immediately — there is no grace period
- Prefix matching uses simple string prefix comparison: `claude-code/*` matches any source starting with `claude-code/`
- The `/*` suffix in prefixes is a naming convention for clarity; the actual match is `startswith("claude-code/")`

## Related

- [API Reference: Key Management Endpoints](../api-reference.md)
- [Tutorial: Setting Up Multi-Auth](../tutorials/tut-001-setup-multi-auth.md)
- [Tutorial: Creating Scoped Keys for AI Agents](../tutorials/tut-002-scoped-keys-for-agents.md)
- [Migration Guide: v1.4 to v1.5](../migration/v1.4-to-v1.5.md)
- [Error Reference](../error-reference.md)
- [Cookbook: Multi-Auth Recipes](../cookbook.md)
