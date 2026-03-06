---
doc-id: rn-v1.5.0
type: release-notes
title: "Release Notes: v1.5.0 — Multi-Auth"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# Release Notes: v1.5.0

## Headline

**Multi-Auth: Prefix-Scoped API Keys** -- Multiple clients can now share a single Memories server with isolated, role-based access to different source prefixes.

## What Changed

### New: Managed API Keys

The system now supports creating, listing, updating, and revoking API keys through a dedicated set of admin-only endpoints and a Web UI page. Each key has a role and an optional set of source-prefix scopes.

**Key format:** `mem_` followed by 32 hex characters. Generated once, stored as a SHA-256 hash. The raw key is shown exactly once at creation time.

**Roles:**

| Role | Capabilities |
|------|-------------|
| `read-only` | Search and read memories within scoped prefixes |
| `read-write` | Read + write + extract within scoped prefixes |
| `admin` | Full access including key management and maintenance endpoints |

**Prefix scoping:** Non-admin keys are restricted to memories whose `source` field matches one of the key's declared prefixes. A prefix like `claude-code/*` grants access to `claude-code/decisions`, `claude-code/learnings`, etc. Path traversal (`..`) is rejected.

### New: Key Management API

| Endpoint | Method | Access | Purpose |
|----------|--------|--------|---------|
| `/api/keys/me` | GET | Any key | Caller's own role and scope |
| `/api/keys` | POST | Admin | Create a key |
| `/api/keys` | GET | Admin | List all keys (masked) |
| `/api/keys/{id}` | PATCH | Admin | Update name/role/prefixes |
| `/api/keys/{id}` | DELETE | Admin | Revoke (soft-delete) |

### New: Admin Gating

13+ endpoints that perform privileged operations (maintenance, backup, restore, sync, index, deduplicate, stats, metrics, folders/rename) now require admin-level access. Non-admin keys receive a 403.

### New: Auth Rate Limiting

Failed authentication attempts are rate-limited to 10 per IP per 60-second sliding window. Exceeding the limit returns HTTP 429.

### New: Web UI -- API Keys Page

The sidebar now includes an "API Keys" page. Visible to all callers but content-gated to admins. Features:
- Key table with name, masked prefix, role, source prefixes, timestamps, usage count
- Create modal with one-time key display and clipboard copy
- Edit and revoke modals

### New Files

| File | Purpose |
|------|---------|
| `auth_context.py` | `AuthContext` dataclass for per-request role/prefix enforcement |
| `key_store.py` | `KeyStore` class -- SQLite-backed key lifecycle management |

### Modified Files

| File | Changes |
|------|---------|
| `app.py` | Auth middleware, key management endpoints, admin gating on privileged endpoints, prefix enforcement on read/write operations |
| `webui/app.js` | API Keys page with create/edit/revoke UI |
| `webui/styles.css` | Styling for keys page and modals |

## Backward Compatibility

- **No breaking changes.** The `API_KEY` env var continues to work as an implicit admin key. No new env vars required.
- Existing MCP clients using the env key are unaffected.
- When no auth is configured (no `API_KEY`, no managed keys), the system remains in unrestricted local-only mode.
- The `keys.db` SQLite database is created automatically at startup in `DATA_DIR`.

## Security Notes

- Env key comparison uses `hmac.compare_digest` (constant-time).
- Managed keys are stored as SHA-256 hashes; raw keys are never persisted.
- Rate limiting on failed auth: 10 attempts/min/IP.
- Path traversal prevention on source prefix matching.
- Non-admin keys must have at least one prefix scope.

## Commits

10 commits on `feat/multi-auth` branch, `00dbfd6` through `1a9e843`.
