---
doc-id: fh-001
type: feature-handoff
title: "Multi-Auth: Prefix-Scoped API Keys"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# Feature Handoff: Multi-Auth (Prefix-Scoped API Keys)

## Summary

v1.5.0 introduces managed API keys with role-based access control and source-prefix scoping. Multiple clients can share a single Memories server, each restricted to its own slice of the data. The existing `API_KEY` environment variable continues to work unchanged as an implicit admin credential.

## Architecture

### Authentication Flow

Every request passes through the `verify_api_key` middleware (registered as a FastAPI dependency on the app). The middleware resolves credentials in this order:

1. **No auth configured** (no `API_KEY` env var, no key store) -- request gets unrestricted admin access.
2. **Health/UI paths** (`/health`, `/health/ready`, `/ui`, `/ui/*`) -- always unrestricted; no key required.
3. **Env key match** -- the raw `X-API-Key` header is compared to the `API_KEY` env var using `hmac.compare_digest` (constant-time). On match, the request gets an `AuthContext` with `role="admin"`, `prefixes=None` (unrestricted), `key_type="env"`.
4. **Managed key match** -- the raw key is SHA-256 hashed and looked up in the `api_keys` SQLite table. On match (and not revoked), the request gets an `AuthContext` with the key's stored role, prefixes, and `key_type="managed"`.
5. **No match** -- 401 response. The failure is recorded for rate limiting.

Rate limiting: 10 failed auth attempts per IP per 60-second sliding window. Exceeding the limit returns 429.

### Components

| File | Responsibility |
|------|---------------|
| `auth_context.py` | `AuthContext` dataclass -- role checks (`can_read`, `can_write`, `can_manage_keys`), prefix matching (`_matches_prefix`), result filtering (`filter_results`), introspection (`to_me_response`) |
| `key_store.py` | `KeyStore` class -- SQLite-backed key lifecycle: generate, hash, create, lookup, list, update, revoke. WAL mode, thread-local connections. |
| `app.py` | Middleware (`verify_api_key`), helper functions (`_get_auth`, `_require_write`, `_require_admin`), key management endpoints, prefix enforcement on memory operations. |
| `webui/app.js` | API Keys page -- admin-gated UI for creating, editing, and revoking keys. |
| `webui/styles.css` | Styling for the keys page, modals, and role badges. |

### Data Model

SQLite table `api_keys` (stored in `{DATA_DIR}/keys.db`):

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID v4 |
| `name` | TEXT NOT NULL | Human-readable label |
| `key_hash` | TEXT UNIQUE NOT NULL | SHA-256 hex digest of the raw key |
| `key_prefix` | TEXT NOT NULL | First 8 characters of the raw key (for display/identification) |
| `role` | TEXT NOT NULL | One of: `read-only`, `read-write`, `admin` |
| `prefixes` | TEXT NOT NULL | JSON array of source prefixes (e.g., `["claude-code/*", "kai/*"]`) |
| `created_at` | TEXT NOT NULL | ISO 8601 UTC timestamp |
| `last_used_at` | TEXT | ISO 8601 UTC, updated on every successful lookup |
| `usage_count` | INTEGER | Incremented on every successful lookup |
| `revoked` | INTEGER | 0 = active, 1 = revoked (soft-delete) |

Index: `idx_api_keys_hash` on `key_hash` for fast lookup.

### Key Format

Keys follow the pattern `mem_` + 32 hex characters (e.g., `mem_a3f8b2c1d4e5f6071829304a5b6c7d8e`). Generated using `secrets.token_hex(16)`. The raw key is returned exactly once at creation time. Only the SHA-256 hash is stored.

### Roles

| Role | Read | Write/Extract | Key Management | Admin Endpoints |
|------|------|---------------|----------------|-----------------|
| `read-only` | Yes (scoped) | No | No | No |
| `read-write` | Yes (scoped) | Yes (scoped) | No | No |
| `admin` | Yes (all) | Yes (all) | Yes | Yes |

### Prefix Enforcement

Non-admin keys have a `prefixes` list (e.g., `["claude-code/*"]`). The `AuthContext._matches_prefix` method normalizes each prefix by stripping trailing `/*` and `/`, then checks whether the source string equals the base or starts with `base + "/"`.

Path traversal is blocked: any source containing `..` as a path component is rejected.

Prefix enforcement applies to:
- **Writes**: `_require_write()` checks `auth.can_write(source)` before add/extract/delete/supersede operations.
- **Reads**: `auth.filter_results()` filters search, list, and batch-get results. Single-memory GET checks `auth.can_read(source)`.
- **Folders**: The folders endpoint filters by `auth.can_read(source)` per memory.

Admin keys (`prefixes=None`) bypass all prefix checks.

### Admin-Gated Endpoints

The `_require_admin()` helper gates these endpoints (returns 403 for non-admin keys):

- Key management: `POST /api/keys`, `GET /api/keys`, `PATCH /api/keys/{id}`, `DELETE /api/keys/{id}`
- Maintenance: reload embedder, consolidate, prune
- Data operations: backup, restore, rebuild index, deduplicate
- Cloud sync: status, push, pull, list snapshots, pull-and-restore
- Stats/metrics: `GET /api/stats`, `GET /api/metrics`
- Folders: `POST /api/folders/rename`

The `GET /api/keys/me` endpoint is available to all authenticated callers.

## API Endpoints

### GET /api/keys/me

Returns the caller's own auth context. Available to any authenticated key.

**Response:**
```json
{
  "type": "managed",
  "role": "read-write",
  "prefixes": ["claude-code/*"],
  "id": "uuid",
  "name": "claude-code-agent"
}
```

### POST /api/keys (admin only)

Creates a new managed API key. Returns the raw key exactly once.

**Request:**
```json
{
  "name": "claude-code-agent",
  "role": "read-write",
  "prefixes": ["claude-code/*"]
}
```

Validation: non-admin keys must have at least one prefix. Admin keys have their prefixes silently cleared (admins are unrestricted).

**Response:** includes `"key": "mem_..."` -- the raw key shown once.

### GET /api/keys (admin only)

Lists all keys (including revoked). Never exposes the raw key or hash.

### PATCH /api/keys/{id} (admin only)

Updates `name`, `role`, or `prefixes` on a non-revoked key. Partial updates allowed.

### DELETE /api/keys/{id} (admin only)

Soft-revokes a key (sets `revoked=1`). The key remains in the database for audit purposes but no longer authenticates.

## Web UI

The API Keys page appears in the sidebar navigation for all users, but its content is gated: on load, the page calls `GET /api/keys/me` and checks `callerInfo.role !== "admin"`. Non-admin callers see an "access denied" empty state.

Admin callers see:
- A table listing all keys with columns: name, key prefix (masked), role, source prefixes, created date, last used, usage count, and revoked status.
- A "Create Key" button opening a modal with name, role, and prefixes fields. On success, the raw key is displayed once with a clipboard-copy button.
- Edit and Revoke actions per key row.

## Backward Compatibility

- The `API_KEY` environment variable is unchanged. It continues to grant full admin access via constant-time comparison. Existing MCP clients using this key require no changes.
- No new environment variables were introduced.
- When no `API_KEY` is set and no managed keys exist, the system remains in local-only unrestricted mode.
- The `keys.db` database file is created automatically on startup.

## Security Considerations

1. **Constant-time comparison** for the env key via `hmac.compare_digest` prevents timing attacks.
2. **SHA-256 hashing** for managed keys -- raw keys are never stored.
3. **Rate limiting** on auth failures (10/min/IP) mitigates brute-force attempts.
4. **Path traversal prevention** -- `..` in source paths is rejected.
5. **Empty prefix validation** -- non-admin keys must declare at least one prefix scope.
6. **Soft-delete revocation** -- revoked keys persist for audit trails but cannot authenticate.

## Source Commits

10 commits on `feat/multi-auth` branch, from `00dbfd6` to `1a9e843`.
