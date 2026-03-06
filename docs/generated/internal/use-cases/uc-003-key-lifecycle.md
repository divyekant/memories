---
doc-id: uc-003
type: use-case
title: "Key Lifecycle: Create, Use, Update, Revoke"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# Use Case: Key Lifecycle (Create, Use, Update, Revoke)

## Scenario

An admin needs to manage the full lifecycle of a managed API key: creation, distribution, day-to-day use, modification, and eventual revocation.

## Phase 1: Creation

### Via API

```bash
curl -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "data-pipeline", "role": "read-write", "prefixes": ["pipeline/*"]}'
```

Response:
```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "key": "mem_a3f8b2c1d4e5f6071829304a5b6c7d8e",
  "key_prefix": "mem_a3f8",
  "name": "data-pipeline",
  "role": "read-write",
  "prefixes": ["pipeline/*"],
  "created_at": "2026-03-05T10:30:00Z"
}
```

The `key` field contains the raw API key. This is the only time it is ever returned. The system stores only the SHA-256 hash.

### Via Web UI

1. Navigate to the API Keys page in the sidebar (admin keys only).
2. Click "Create Key".
3. Fill in name, select role, enter comma-separated prefixes.
4. Submit. The modal displays the raw key with a clipboard-copy button.
5. Copy the key before closing the modal. It cannot be retrieved again.

### Validation rules

- `name`: required, 1-200 characters.
- `role`: must be `read-only`, `read-write`, or `admin`.
- `prefixes`: non-admin keys must have at least one prefix. Admin keys have prefixes silently cleared.

## Phase 2: Distribution

The raw key must be securely transmitted to the intended client. Common patterns:
- Set it as an environment variable in the client's configuration.
- Store it in a secrets manager and inject at runtime.
- For MCP clients, add it to the MCP client config under the `X-API-Key` header.

The key prefix (first 8 characters, e.g., `mem_a3f8`) can be used to identify keys in logs and the admin UI without exposing the full key.

## Phase 3: Day-to-Day Use

### Authentication

The client includes the key in every request:

```bash
curl http://localhost:8900/api/search \
  -H "X-API-Key: mem_a3f8b2c1d4e5f6071829304a5b6c7d8e" \
  -H "Content-Type: application/json" \
  -d '{"query": "deployment config", "n": 5}'
```

### What happens on each request

1. The middleware hashes the raw key with SHA-256.
2. Looks up the hash in `api_keys` table (indexed).
3. If found and not revoked: updates `last_used_at` and increments `usage_count`, then attaches an `AuthContext` with the key's role and prefixes.
4. If not found or revoked: returns 401.

### Self-inspection

Any key can check its own scope:

```bash
curl http://localhost:8900/api/keys/me \
  -H "X-API-Key: mem_a3f8b2c1d4e5f6071829304a5b6c7d8e"
```

### Monitoring usage

Admins can view usage statistics for all keys:

```bash
curl http://localhost:8900/api/keys \
  -H "X-API-Key: $ADMIN_KEY"
```

Each key record includes `usage_count` and `last_used_at`. This is visible in the Web UI keys table as well.

## Phase 4: Modification

### Renaming a key

```bash
curl -X PATCH http://localhost:8900/api/keys/f47ac10b-58cc-4372-a567-0e02b2c3d479 \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "data-pipeline-v2"}'
```

### Changing scope

Expand or restrict a key's prefix access:

```bash
curl -X PATCH http://localhost:8900/api/keys/f47ac10b-58cc-4372-a567-0e02b2c3d479 \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prefixes": ["pipeline/*", "etl/*"]}'
```

The change takes effect immediately. The next request using this key will be evaluated against the new prefixes.

### Changing role

Promote or restrict a key's role:

```bash
curl -X PATCH http://localhost:8900/api/keys/f47ac10b-58cc-4372-a567-0e02b2c3d479 \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"role": "read-only"}'
```

### Constraints

- Only `name`, `role`, and `prefixes` can be updated.
- Revoked keys cannot be updated (returns 404).
- The key's raw value and hash cannot be changed. To rotate a key, revoke the old one and create a new one.

## Phase 5: Revocation

### Via API

```bash
curl -X DELETE http://localhost:8900/api/keys/f47ac10b-58cc-4372-a567-0e02b2c3d479 \
  -H "X-API-Key: $ADMIN_KEY"
```

### Via Web UI

Click the "Revoke" action on the key row in the keys table. Confirm in the modal.

### What happens on revocation

1. The `revoked` column is set to 1 (soft-delete).
2. The key immediately stops authenticating. Any request using it returns 401.
3. The key record remains in the database. It appears in `GET /api/keys` with `"revoked": 1`.
4. The key cannot be un-revoked. To restore access, create a new key.

### Already-revoked keys

Attempting to revoke an already-revoked key returns an error (`ValueError`: "Key is already revoked").

## Key Rotation

There is no built-in key rotation endpoint. The recommended pattern:

1. Create a new key with the same name, role, and prefixes.
2. Update the client configuration to use the new key.
3. Verify the client is working with the new key (check `last_used_at`).
4. Revoke the old key.

Both keys can be active simultaneously during the transition window.

## Edge Cases

- **Lost key**: The raw key cannot be recovered. The admin must revoke the lost key and create a new one.
- **Duplicate names**: Multiple keys can share the same name. The `id` and `key_prefix` fields distinguish them.
- **Admin key creation**: Admin-role managed keys have their `prefixes` field silently set to `[]`. They operate without prefix restrictions.
