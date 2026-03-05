# Multi-Auth Design: Prefix-Scoped API Keys

Date: 2026-03-05
Status: Approved

## Problem

Memories has a single `API_KEY` env var — one key with full access to everything. This doesn't support:

1. **Tool/agent isolation** — Claude Code, Kai, and other agents sharing a server can read/write each other's memories
2. **Team access** — multiple people on a shared server have identical access
3. **Least privilege** — a compromised hook or integration can wipe everything

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Prefix binding model | Explicit prefix list per key | Matches organic source prefix usage, simple and predictable |
| Permission model | Three role tiers (read-only, read-write, admin) | Covers all use cases without config surface explosion |
| Key creation/management | API + env fallback | Backward compatible; env var = implicit admin, DB keys are additive |
| Key format/storage | `mem_` prefix, SHA-256 hash stored | Industry standard (GitHub PAT pattern), no extra secrets to manage |
| Migration strategy | No migration required | Env var always works as implicit admin; multi-auth is opt-in |
| UI access | Admin-gated | Key management page only visible with admin key |

## Architecture

```
Request --> verify_api_key middleware
  |-- matches env API_KEY? --> admin, full access
  |-- matches a DB key hash? --> check role + prefix scope
  '-- no match --> 401
```

The current `API_KEY` env var is an implicit admin key with unchanged behavior. A new SQLite table stores additional keys with prefix bindings and role tiers.

## Key Model (SQLite)

Table: `api_keys`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `name` | TEXT | Human label ("claude-code-prod", "kai-reader") |
| `key_hash` | TEXT UNIQUE | SHA-256 of the full key |
| `key_prefix` | TEXT | First 8 chars for display (`mem_a3f8...`) |
| `role` | TEXT | `read-only`, `read-write`, `admin` |
| `prefixes` | TEXT (JSON) | `["claude-code/*", "learning/*"]` -- ignored for admin role |
| `created_at` | TEXT | ISO timestamp |
| `last_used_at` | TEXT | Updated on each successful auth |
| `usage_count` | INTEGER | Incremented on each successful auth |
| `revoked` | INTEGER | 0/1 soft-delete |

## Key Format

- Generated: `mem_` + 32 random hex chars
- Example: `mem_a3f8b2c1d4e5f6071829304a5b6c7d8e`
- Shown exactly once at creation, never retrievable again
- Stored as SHA-256 hash in SQLite

## Role Permissions

| Role | Read | Write/Delete | Extract | Manage Keys | Prefix Bound |
|------|------|-------------|---------|-------------|-------------|
| `read-only` | Yes | No | No | No | Yes |
| `read-write` | Yes | Yes | Yes | No | Yes |
| `admin` | Yes | Yes | Yes | Yes | No (all access) |

## Prefix Enforcement

For non-admin keys, every API operation is scope-checked:

- **Search/list**: results filtered to memories whose `source` starts with an allowed prefix
- **Add**: `source` must start with an allowed prefix, else 403
- **Delete**: target memory's `source` must match an allowed prefix, else 403
- **Extract**: extracted memories are source-checked before write

Prefix matching: a key with prefix `claude-code/*` can access any memory whose source starts with `claude-code/`. The `/*` is a convention for clarity but the match is a simple `startswith("claude-code/")`.

## API Endpoints

All key management endpoints require admin auth.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/keys/me` | Returns caller's role and allowed prefixes |
| `POST` | `/api/keys` | Create key (returns plaintext once) |
| `GET` | `/api/keys` | List all keys (masked, with usage stats) |
| `PATCH` | `/api/keys/{id}` | Update name, role, or prefixes |
| `DELETE` | `/api/keys/{id}` | Revoke key (soft-delete, sets `revoked=1`) |

### POST /api/keys — Request

```json
{
  "name": "claude-code-prod",
  "role": "read-write",
  "prefixes": ["claude-code/*", "learning/*"]
}
```

### POST /api/keys — Response (200)

```json
{
  "id": "uuid",
  "name": "claude-code-prod",
  "key": "mem_a3f8b2c1d4e5f6071829304a5b6c7d8e",
  "key_prefix": "mem_a3f8",
  "role": "read-write",
  "prefixes": ["claude-code/*", "learning/*"],
  "created_at": "2026-03-05T10:00:00Z"
}
```

The `key` field is only returned on creation. Subsequent `GET /api/keys` returns `key_prefix` only.

### GET /api/keys/me — Response

```json
{
  "type": "env",
  "role": "admin",
  "prefixes": null
}
```

Or for a DB-managed key:

```json
{
  "type": "managed",
  "id": "uuid",
  "name": "claude-code-prod",
  "role": "read-write",
  "prefixes": ["claude-code/*", "learning/*"]
}
```

## Backward Compatibility

- **`API_KEY` env var set**: works as implicit admin with full access (unchanged)
- **No env var set**: no auth / local-only mode (unchanged)
- **Existing MCP clients**: keep using same key, zero config change
- **New keys**: opt-in, created via API or Web UI

## Web UI Changes

### API Keys Page (admin-gated)

- Key table: name, masked prefix, role, prefixes, created date, last used, usage count
- Create: name + role + prefix selector, shows key once in a copy-to-clipboard modal
- Actions: rename, change role/prefixes, revoke with confirmation
- Revoked keys shown as struck-through with "revoked" badge

### UI Auth Gate

- On page load, call `GET /api/keys/me` to determine caller role
- **Admin key**: API Keys page visible in sidebar, full CRUD
- **Non-admin key**: API Keys page hidden from sidebar
- **No key / local-only**: no key management (nothing to manage)

### Future (not MVP)

- Toolbar key switcher dropdown for scoped browsing
- Per-key usage analytics charts

## Auth Flow Detail

```
1. Extract key from X-API-Key header
2. If empty and API_KEY not configured --> allow (local-only mode)
3. If empty and API_KEY configured --> 401
4. Compare against env API_KEY (constant-time)
   --> match: role=admin, prefixes=None (unrestricted)
5. Hash the provided key (SHA-256)
6. Look up hash in api_keys table
   --> not found: 401
   --> found but revoked=1: 401
7. Update last_used_at and usage_count
8. Attach role and prefixes to request state
9. Per-endpoint middleware checks role and prefix scope
```

## Testing Strategy

- Unit: key generation, hash verification, prefix matching, role checks
- Integration: CRUD endpoints, scoped search filtering, admin gate
- Backward compat: existing API_KEY env var behavior unchanged
- Security: timing-safe comparison, rate limiting on failures, revoked key rejection
