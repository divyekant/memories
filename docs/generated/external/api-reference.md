---
title: "API Reference: Key Management Endpoints"
slug: api-reference-key-management
type: api-reference
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# API Reference: Key Management Endpoints

These endpoints manage API keys for Memories multi-auth. All key management endpoints (except `GET /api/keys/me`) require admin authentication.

**Base URL:** `http://localhost:8900`

**Authentication:** Pass your API key in the `X-API-Key` header on every request.

For the full Memories API reference (search, add, delete, etc.), see the interactive documentation at `http://localhost:8900/docs`.

---

## GET /api/keys/me

Check the role and scope of the key you are currently using. Available to any authenticated key.

### Request

```bash
curl -s http://localhost:8900/api/keys/me \
  -H "X-API-Key: your-api-key"
```

### Response (env-based admin key)

```json
{
  "type": "env",
  "role": "admin"
}
```

### Response (managed key)

```json
{
  "type": "managed",
  "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "name": "claude-code-prod",
  "role": "read-write",
  "prefixes": ["claude-code/*"]
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `"env"` for the environment variable key, `"managed"` for keys created via the API |
| `role` | string | One of `read-only`, `read-write`, `admin` |
| `id` | string | UUID of the managed key (only present for managed keys) |
| `name` | string | Human-readable label (only present for managed keys) |
| `prefixes` | array or null | List of allowed source prefixes (null for admin/env keys) |

---

## POST /api/keys

Create a new API key. **Admin only.**

### Request

```bash
curl -s -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-code-prod",
    "role": "read-write",
    "prefixes": ["claude-code/*", "learning/*"]
  }'
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Human-readable label for the key |
| `role` | string | Yes | One of `read-only`, `read-write`, `admin` |
| `prefixes` | array of strings | Yes (for non-admin) | Source prefixes the key can access. Each entry should end with `/*` by convention. |

### Response (200)

```json
{
  "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "key": "mem_a3f8b2c1d4e5f6071829304a5b6c7d8e",
  "key_prefix": "mem_a3f8",
  "name": "claude-code-prod",
  "role": "read-write",
  "prefixes": ["claude-code/*", "learning/*"],
  "created_at": "2026-03-05T10:00:00Z"
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID for the key (use this for update/delete operations) |
| `key` | string | The full API key. **Only returned on creation. Save it immediately.** |
| `key_prefix` | string | First 8 characters of the key, for identification in logs and lists |
| `name` | string | The human-readable label you provided |
| `role` | string | The role assigned to this key |
| `prefixes` | array | The source prefixes this key can access |
| `created_at` | string | ISO 8601 timestamp of when the key was created |

### Errors

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid API key |
| 403 | Caller is not an admin |

---

## GET /api/keys

List all API keys. Keys are masked (only `key_prefix` is shown). **Admin only.**

### Request

```bash
curl -s http://localhost:8900/api/keys \
  -H "X-API-Key: your-admin-key"
```

### Response (200)

```json
[
  {
    "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
    "key_prefix": "mem_a3f8",
    "name": "claude-code-prod",
    "role": "read-write",
    "prefixes": ["claude-code/*", "learning/*"],
    "created_at": "2026-03-05T10:00:00Z",
    "last_used_at": "2026-03-05T14:32:00Z",
    "usage_count": 847,
    "revoked": false
  },
  {
    "id": "e4a1c3b7-89d2-4f6e-a1b5-3c7d9e2f4a6b",
    "key_prefix": "mem_7b2e",
    "name": "kai-reader",
    "role": "read-only",
    "prefixes": ["kai/*"],
    "created_at": "2026-03-05T10:05:00Z",
    "last_used_at": null,
    "usage_count": 0,
    "revoked": false
  }
]
```

### Response Fields (per key)

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID |
| `key_prefix` | string | First 8 characters (the full key is never shown after creation) |
| `name` | string | Human-readable label |
| `role` | string | `read-only`, `read-write`, or `admin` |
| `prefixes` | array | Allowed source prefixes |
| `created_at` | string | ISO 8601 creation timestamp |
| `last_used_at` | string or null | ISO 8601 timestamp of last successful authentication |
| `usage_count` | integer | Number of times the key has been used |
| `revoked` | boolean | Whether the key has been revoked |

### Errors

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid API key |
| 403 | Caller is not an admin |

---

## PATCH /api/keys/{id}

Update a key's name, role, or prefix bindings. **Admin only.**

### Request

```bash
curl -s -X PATCH http://localhost:8900/api/keys/d290f1ee-6c54-4b01-90e6-d701748f0851 \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-code-prod-v2",
    "role": "read-only",
    "prefixes": ["claude-code/*"]
  }'
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | UUID of the key to update |

### Request Body

All fields are optional. Only include the fields you want to change.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | New human-readable label |
| `role` | string | New role (`read-only`, `read-write`, `admin`) |
| `prefixes` | array of strings | New list of allowed source prefixes (replaces existing list) |

### Response (200)

```json
{
  "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "key_prefix": "mem_a3f8",
  "name": "claude-code-prod-v2",
  "role": "read-only",
  "prefixes": ["claude-code/*"],
  "created_at": "2026-03-05T10:00:00Z",
  "last_used_at": "2026-03-05T14:32:00Z",
  "usage_count": 847,
  "revoked": false
}
```

### Errors

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid API key |
| 403 | Caller is not an admin |
| 404 | Key ID not found |

---

## DELETE /api/keys/{id}

Revoke a key. This is a **soft-delete** — the key record remains in the database but is immediately unusable. Any request using a revoked key returns 401. **Admin only.**

### Request

```bash
curl -s -X DELETE http://localhost:8900/api/keys/d290f1ee-6c54-4b01-90e6-d701748f0851 \
  -H "X-API-Key: your-admin-key"
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | UUID of the key to revoke |

### Response (200)

```json
{
  "message": "Key revoked",
  "id": "d290f1ee-6c54-4b01-90e6-d701748f0851"
}
```

### Errors

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid API key |
| 403 | Caller is not an admin |
| 404 | Key ID not found |

---

## Authentication Header

All requests must include the `X-API-Key` header:

```
X-API-Key: your-api-key-here
```

This applies to both the env-based admin key and managed keys created via `POST /api/keys`.

## Role Summary

| Role | /api/keys/me | /api/keys (CRUD) | Search/Add/Delete (within scope) |
|------|-------------|------------------|----------------------------------|
| `read-only` | Yes | No (403) | Search only |
| `read-write` | Yes | No (403) | Search + Add + Delete |
| `admin` | Yes | Yes | Full access, no prefix restrictions |

---

## Export/Import

### GET /export

Export memories as streaming NDJSON.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| source | string | No | Filter by source prefix |
| since | string | No | Only memories created after this ISO8601 datetime |
| until | string | No | Only memories created before this ISO8601 datetime |

**Response:** Streaming `application/x-ndjson`. First line is a header, remaining lines are memory records.

**Example:**
```bash
curl -H "X-API-Key: YOUR_KEY" \
  "http://localhost:8900/export?source=claude-code/&since=2026-01-01" \
  -o export.jsonl
```

**Response format:**
```jsonl
{"_header": true, "count": 253, "version": "2.0.0", "exported_at": "2026-03-05T10:30:00Z", "source_filter": "claude-code/", "since": "2026-01-01", "until": null}
{"text": "Always use strict mode", "source": "claude-code/myapp", "created_at": "2026-01-15T...", "updated_at": "2026-01-15T...", "custom_fields": {}}
```

**Status Codes:**
| Code | Meaning |
|------|---------|
| 200 | Success — streaming NDJSON body |
| 401 | Authentication required |

---

### POST /import

Import memories from NDJSON body.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| strategy | string | No | `add`, `smart`, or `smart+extract` (default: `add`) |
| source_remap | string | No | Rewrite source prefixes (format: `old=new`) |
| no_backup | boolean | No | Skip auto-backup (default: false) |

**Request Body:** NDJSON (same format as export output). Content-Type: `application/x-ndjson`.

**Response:**
```json
{
  "imported": 840,
  "skipped": 5,
  "updated": 2,
  "errors": [
    {"line": 42, "error": "source prefix not authorized"}
  ],
  "backup": "pre-import_20260305_103000"
}
```

**Status Codes:**
| Code | Meaning |
|------|---------|
| 200 | Import completed (check errors array for per-line failures) |
| 400 | Invalid strategy |
| 401 | Authentication required |
| 500 | Server error during import |

**Example:**
```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/x-ndjson" \
  "http://localhost:8900/import?strategy=smart" \
  --data-binary @export.jsonl
```
