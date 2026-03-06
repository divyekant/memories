---
title: "Changelog: v1.5.0"
slug: changelog-v1.5.0
type: changelog
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# Changelog: v1.5.0

**Release date:** March 5, 2026

## Multi-Auth: Prefix-Scoped API Keys

You can now create multiple API keys with different access levels, each scoped to specific source prefixes. This lets you give each AI agent, tool, or team member exactly the access they need.

### What You Can Do Now

- **Create scoped keys** — Give Claude Code a key that only accesses `claude-code/*` memories, give a CI pipeline a read-only key, or give a teammate access to just their namespace.

- **Three role tiers** — Choose from `read-only` (search only), `read-write` (search + add/delete within scope), or `admin` (full access + key management).

- **Prefix isolation** — Keys are bound to source prefixes. A key with prefix `claude-code/*` can only see and modify memories whose source starts with `claude-code/`. Agents cannot read or modify each other's memories.

- **Key management via API** — Create, list, update, and revoke keys through five new endpoints under `/api/keys`. All management operations require admin access.

- **Key management via Web UI** — A new API Keys page in the sidebar lets you manage keys visually with usage stats, a copy-to-clipboard creation flow, and one-click revocation. The page is only visible to admin keys.

- **Check your access** — Call `GET /api/keys/me` with any key to see your role and allowed prefixes.

### What Stays the Same

- **Your existing `API_KEY` still works.** It is now recognized as an implicit admin key. No changes to your `.env`, `docker-compose.yml`, or MCP configuration are needed.

- **No-auth mode still works.** If you do not set `API_KEY`, the server runs without authentication.

- **All existing endpoints are unchanged.** Search, add, delete, extract, backup, sync — everything works the same.

- **No data migration.** Your memories are not modified in any way.

### New API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/keys/me` | Check your current key's role and scope |
| `POST /api/keys` | Create a new key (admin only) |
| `GET /api/keys` | List all keys with usage stats (admin only) |
| `PATCH /api/keys/{id}` | Update a key's name, role, or prefixes (admin only) |
| `DELETE /api/keys/{id}` | Revoke a key (admin only) |

### New Error Responses

| Status | Message | When |
|--------|---------|------|
| 403 | `"Insufficient permissions"` | Role does not allow the operation |
| 403 | `"Source prefix not allowed"` | Source is outside the key's allowed prefixes |

These errors only apply to managed keys with restricted roles or prefixes. Admin keys never receive these errors.

### Getting Started

To start using multi-auth, create your first scoped key:

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

Save the `key` from the response — it is shown only once.

For a full walkthrough, see the [Setting Up Multi-Auth tutorial](tutorials/tut-001-setup-multi-auth.md).
