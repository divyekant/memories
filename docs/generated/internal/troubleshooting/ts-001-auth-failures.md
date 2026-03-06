---
doc-id: ts-001
type: troubleshooting
title: "Troubleshooting: Authentication Failures"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# Troubleshooting: Authentication Failures

## Quick Diagnosis

| HTTP Status | Meaning | Most Likely Cause |
|-------------|---------|-------------------|
| 401 | Invalid or missing API key | Wrong key, missing header, revoked key, typo |
| 403 | Forbidden | Key lacks required role or prefix for the operation |
| 429 | Too many requests | Rate limit exceeded (10 failed auth attempts/min/IP) |

## Problem: 401 — "Invalid or missing API key"

### Check 1: Is the header present and correct?

The key must be sent in the `X-API-Key` header (case-sensitive header name). Verify:

```bash
curl -v http://localhost:8900/api/search \
  -H "X-API-Key: mem_a3f8b2c1d4e5f6071829304a5b6c7d8e" \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}'
```

Common mistakes:
- Using `Authorization: Bearer ...` instead of `X-API-Key: ...`.
- Whitespace or newline characters in the key value.
- Quoting issues in shell commands or config files.

### Check 2: Is the key correct?

Verify by checking `/api/keys/me`:

```bash
curl http://localhost:8900/api/keys/me \
  -H "X-API-Key: <your-key>"
```

If this returns 401, the key itself is wrong.

### Check 3: Has the key been revoked?

Revoked keys return 401 (not 403). Ask an admin to check `GET /api/keys` -- the key's record will show `"revoked": 1`. Revoked keys cannot be un-revoked; a new key must be created.

### Check 4: Is auth configured at all?

If the server has no `API_KEY` env var and no managed keys have been created, all requests are unrestricted. If a 401 is returned, the server must have auth configured. Verify the `API_KEY` env var is set in the container.

### Check 5: Is the key store initialized?

The `keys.db` file is created at startup in `DATA_DIR`. If the file is missing or corrupted, managed key lookups will fail. Check the server logs for "Key store initialized" at startup.

## Problem: 403 — Forbidden

A 403 means the key authenticated successfully but lacks permission for the requested operation.

### Scenario: "Key does not have write access to source: X"

The key is either `read-only` (cannot write at all) or the source does not match the key's prefix scope.

**Diagnosis:**

```bash
# Check your role and prefixes
curl http://localhost:8900/api/keys/me -H "X-API-Key: <your-key>"
```

If the response shows `"role": "read-only"`, the key cannot perform write operations (add, delete, extract, supersede). Ask an admin to update the role to `read-write`.

If the role is `read-write` but the source does not match any prefix, the operation is out of scope. For example, a key with `prefixes: ["frontend/*"]` cannot write to `source: "backend/config"`. Ask an admin to add the needed prefix.

### Scenario: "Admin key required"

The endpoint requires admin access. This applies to:
- Key management endpoints (`POST/GET/PATCH/DELETE /api/keys`)
- Stats and metrics (`GET /api/stats`, `GET /api/metrics`)
- Maintenance (reload embedder, consolidate, prune)
- Data operations (backup, restore, rebuild index, deduplicate)
- Cloud sync (all sync endpoints)
- Folder rename (`POST /api/folders/rename`)

Non-admin keys cannot access these endpoints. Use the env key or a managed admin key.

### Scenario: "Access denied to this memory's source"

Returned when calling `GET /api/memory/{id}` for a specific memory whose source falls outside the key's prefix scope. The memory exists but the caller is not allowed to read it.

### Scenario: Writes succeed but reads return empty results

This is expected behavior, not an error. The `filter_results()` method strips out memories outside the key's prefix scope from search, list, and batch-get responses. If the search matches memories from other prefixes, those are silently excluded. The response will contain only memories within the key's scope.

Check that the source used when writing matches the source pattern expected during reads. For example, writing with `source: "claude-code/decisions"` and searching with a key scoped to `claude-code/*` will work. But writing with `source: "decisions"` (no prefix) will not be readable by a key scoped to `claude-code/*`.

## Problem: 429 — Too Many Failed Attempts

The server rate-limits failed authentication to 10 attempts per IP address per 60-second sliding window. This is a security measure against brute-force attacks.

**Resolution:** Wait 60 seconds for the window to reset, then retry with the correct key. If the problem persists, verify the key is valid (it may be revoked or incorrect).

**Note:** Rate limiting tracks failed attempts only. Successful authentications do not count toward the limit.

## Problem: Path Traversal Rejection

If a source path contains `..` as a component (e.g., `claude-code/../kai/secrets`), the prefix matching logic rejects it unconditionally. This is a security feature, not a bug. Use clean, forward-only paths.

## Problem: Key Works in curl but Not in MCP Client

Check these MCP client configuration details:
- The header name must be exactly `X-API-Key` (case-sensitive).
- The key value must not have trailing whitespace or newlines.
- If the MCP client uses environment variable interpolation, ensure the variable is set in the client's process environment, not just the shell.
- Some MCP client frameworks URL-encode header values. The `mem_` prefix and hex characters should not be affected, but verify the raw HTTP request if in doubt.

## Problem: Web UI Shows "Access Denied" on API Keys Page

This is expected for non-admin keys. The API Keys page checks `callerInfo.role` from the `/api/keys/me` endpoint. Only admin keys see the key management interface. Non-admin users can still access other pages (memories, search, extractions) within their scope.

## Diagnostic Checklist

1. What HTTP status code is returned? (401 vs 403 vs 429)
2. What is the exact error message in the response body?
3. What does `GET /api/keys/me` return for this key?
4. Is the key in the `X-API-Key` header (not `Authorization`)?
5. Has the key been revoked? (Admin checks `GET /api/keys`)
6. Does the source path match the key's prefix scope?
7. Is the operation within the key's role permissions?
8. Are there rate-limit blocks from previous failed attempts?
