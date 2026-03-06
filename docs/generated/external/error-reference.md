---
title: "Error Reference: Authentication and Authorization"
slug: error-reference-auth
type: reference
version: 1.5.0
date: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
status: released
audience: external
---

# Error Reference: Authentication and Authorization

This page documents all auth-related errors you may encounter when using the Memories API. Each entry includes the HTTP status code, the error message, what causes it, and how to fix it.

---

## 401 Unauthorized

### Missing API Key

**Response:**

```json
{
  "detail": "Missing API key"
}
```

**Cause:** You sent a request without the `X-API-Key` header, and the server has `API_KEY` configured (auth is required).

**Fix:** Add the `X-API-Key` header to your request:

```bash
curl -s http://localhost:8900/health \
  -H "X-API-Key: your-api-key"
```

---

### Invalid API Key

**Response:**

```json
{
  "detail": "Invalid API key"
}
```

**Cause:** The key you provided does not match the env-based `API_KEY` and does not match any managed key in the database.

**Fix:**
1. Double-check that you copied the full key, including the `mem_` prefix (for managed keys)
2. Verify the key has not been revoked — ask an admin to check `GET /api/keys`
3. Confirm you are connecting to the correct Memories instance

**Reproduce:**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8900/api/keys/me \
  -H "X-API-Key: mem_0000000000000000000000000000000000"
```

**Expected output:** `401`

---

### Revoked API Key

**Response:**

```json
{
  "detail": "Invalid API key"
}
```

**Cause:** The key was valid but has been revoked by an admin via `DELETE /api/keys/{id}`. Revoked keys return the same error as invalid keys (intentionally — to avoid leaking information about which keys exist).

**Fix:**
1. Ask an admin to create a new key for you
2. Update your configuration with the new key

**How to check if a key was revoked (admin only):**

```bash
curl -s http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

Look for entries where `"revoked": true`.

---

## 403 Forbidden

### Insufficient Permissions (Role)

**Response:**

```json
{
  "detail": "Insufficient permissions"
}
```

**Cause:** Your key's role does not allow the operation you attempted. For example:
- A `read-only` key tried to call `POST /memory/add`
- A `read-only` key tried to call `DELETE /memory/{id}`
- A `read-only` key tried to call `POST /memory/extract`
- A non-admin key tried to call `POST /api/keys` or any key management endpoint

**Fix:** Use a key with the appropriate role:

| Operation | Minimum Role Required |
|-----------|----------------------|
| Search, list, count, get | `read-only` |
| Add, delete, update, extract | `read-write` |
| Create/list/update/revoke keys | `admin` |

**Check your current role:**

```bash
curl -s http://localhost:8900/api/keys/me \
  -H "X-API-Key: your-api-key" | python3 -m json.tool
```

**Expected output:**

```json
{
    "type": "managed",
    "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
    "name": "my-key",
    "role": "read-only",
    "prefixes": ["claude-code/*"]
}
```

If you need a higher role, ask an admin to either:
- Update your key: `PATCH /api/keys/{id}` with `{"role": "read-write"}`
- Create a new key with the needed role

---

### Source Prefix Not Allowed

**Response:**

```json
{
  "detail": "Source prefix not allowed"
}
```

**Cause:** Your key is scoped to specific source prefixes, and the operation you attempted involves a source outside your allowed prefixes. For example:
- Your key has prefix `claude-code/*` but you tried to add a memory with source `learning/python/tips`
- Your key has prefix `team/alice/*` but you tried to delete a memory whose source is `team/bob/notes`

**Fix:**
1. Check which prefixes your key is allowed:

```bash
curl -s http://localhost:8900/api/keys/me \
  -H "X-API-Key: your-api-key" | python3 -m json.tool
```

2. Ensure the `source` field in your request starts with one of the listed prefixes
3. If you need access to additional prefixes, ask an admin to update your key:

```bash
curl -s -X PATCH http://localhost:8900/api/keys/YOUR_KEY_ID \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "prefixes": ["claude-code/*", "learning/*"]
  }' | python3 -m json.tool
```

---

### Admin Required

**Response:**

```json
{
  "detail": "Insufficient permissions"
}
```

**Cause:** You called a key management endpoint (`POST/GET/PATCH/DELETE /api/keys`) with a non-admin key. Only admin keys can manage other keys.

**Fix:** Use the env-based admin key or a managed key with the `admin` role:

```bash
curl -s http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

---

## 404 Not Found

### Key Not Found

**Response:**

```json
{
  "detail": "Key not found"
}
```

**Cause:** You called `PATCH /api/keys/{id}` or `DELETE /api/keys/{id}` with a key ID that does not exist.

**Fix:**
1. List all keys to find the correct ID:

```bash
curl -s http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

2. Use the `id` field from the key you want to update or revoke

---

## Troubleshooting Checklist

If you are getting auth errors and are not sure why, work through this checklist:

1. **Is auth enabled?** Check that `API_KEY` is set in your `.env` or `docker-compose.yml`. If it is not set, auth is disabled entirely.

2. **Is the header correct?** The header name is `X-API-Key` (capital X, capital A, capital K).

```bash
# Correct
curl -H "X-API-Key: your-key" http://localhost:8900/api/keys/me

# Wrong (will be ignored)
curl -H "Authorization: Bearer your-key" http://localhost:8900/api/keys/me
curl -H "x-api-key: your-key" http://localhost:8900/api/keys/me
```

3. **Is the key complete?** Managed keys are 36 characters: `mem_` + 32 hex characters. Check for accidental truncation.

4. **Is the key revoked?** If you are an admin, list keys and check the `revoked` field. If you are not an admin, ask someone who is.

5. **Are you hitting the right server?** If you run multiple Memories instances, verify the port in your request matches the instance where the key was created.

6. **Is the prefix correct?** Remember that prefix matching is case-sensitive and uses exact string prefix comparison. `claude-code/` and `Claude-Code/` are different prefixes.
