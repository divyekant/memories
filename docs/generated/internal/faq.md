---
doc-id: faq-001
type: faq
title: "FAQ: Multi-Auth (v1.5.0)"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# FAQ: Multi-Auth

## General

### Q: Does this replace the API_KEY environment variable?

No. The `API_KEY` env var continues to work exactly as before. It acts as an implicit admin key with full unrestricted access. Managed keys are an additional layer on top.

### Q: Do I need to create managed keys?

Only if you need multiple clients with different access levels. If a single admin key is sufficient, the env var alone works fine.

### Q: What happens if I set no API_KEY and create no managed keys?

The system runs in unrestricted mode -- all requests get admin access with no authentication. This is the local-only mode, same as previous versions.

### Q: Are there any new environment variables?

No. The multi-auth feature requires no new configuration. The existing `API_KEY` env var behavior is unchanged. The `keys.db` database is created automatically.

## Keys

### Q: Can I see a key after it's created?

No. The raw key is returned exactly once in the creation response. The system stores only the SHA-256 hash. If the key is lost, revoke it and create a new one.

### Q: What does the key look like?

Format: `mem_` followed by 32 hex characters. Example: `mem_a3f8b2c1d4e5f6071829304a5b6c7d8e`. Total length: 36 characters.

### Q: Can I change a key's value (rotate it)?

No. The key value is immutable. To rotate, create a new key with the same settings, update the client, then revoke the old key. Both keys can be active during the transition.

### Q: Can I un-revoke a key?

No. Revocation is permanent (soft-delete -- the record stays for audit, but the key cannot authenticate again). Create a new key instead.

### Q: Can two keys have the same name?

Yes. Names are not unique. Keys are identified by their UUID (`id` field) and can be distinguished by their `key_prefix` (first 8 characters of the raw key).

### Q: Is there a limit on how many keys I can create?

No hard limit. Keys are stored in a SQLite table. Practical limits depend on database size and operational needs.

### Q: What header do I use?

`X-API-Key`. Not `Authorization`, not `Bearer`. The header name is case-sensitive.

## Roles

### Q: What can each role do?

| Role | Read | Write | Extract | Delete | Key Mgmt | Maintenance |
|------|------|-------|---------|--------|----------|-------------|
| read-only | Scoped | No | No | No | No | No |
| read-write | Scoped | Scoped | Scoped | Scoped | No | No |
| admin | All | All | All | All | Yes | Yes |

### Q: Can a read-only key use the extract endpoint?

No. Extraction creates new memories, which is a write operation. Read-only keys cannot extract.

### Q: Can a non-admin key see stats or metrics?

No. Stats, metrics, and all maintenance/data-management endpoints require admin access.

## Prefixes

### Q: How does prefix scoping work?

A prefix like `claude-code/*` is normalized to the base `claude-code`. A memory's source is then checked: does it equal the base, or does it start with `base + "/"`? If yes, access is allowed.

### Q: Can I use nested prefixes?

Yes. A prefix of `team/frontend/*` would match sources like `team/frontend/components` and `team/frontend/hooks/auth`.

### Q: Can a non-admin key have no prefixes?

No. The API rejects the creation of non-admin keys with an empty prefix list (HTTP 400: "Non-admin keys must have at least one prefix"). Admin keys have their prefixes silently cleared since they are unrestricted.

### Q: Do prefixes affect admin keys?

No. Admin keys (both env key and managed admin keys) have `prefixes=None`, which bypasses all prefix checks.

### Q: What if two keys share a prefix?

Both keys see the same data under that prefix. Isolation is prefix-based, not key-based. Two keys with `shared/*` access will see identical results when querying that namespace.

### Q: Can I use wildcards beyond the trailing /*?

No. The system only supports the trailing `/*` glob pattern. There is no regex or mid-path wildcard support. The `/*` is normalized away -- it simply means "this prefix and all sub-paths."

### Q: What about memories with no source?

Memories with an empty source string do not match any prefix. Only admin/unrestricted keys can read or write memories without a source.

## Security

### Q: How is the env key compared?

Using `hmac.compare_digest` for constant-time comparison, preventing timing attacks.

### Q: How are managed keys stored?

As SHA-256 hex digests. The raw key is never written to disk.

### Q: What prevents brute-force attacks?

Rate limiting: 10 failed auth attempts per IP per 60-second sliding window. After the limit, requests return HTTP 429 until the window resets.

### Q: Can someone use path traversal to escape prefix scope?

No. Any source path containing `..` as a component is rejected before prefix matching occurs.

### Q: Are revoked keys logged?

Revoked key records remain in the database with `revoked=1`. They appear in `GET /api/keys` listings. The last_used_at timestamp reflects the last time the key was successfully used before revocation.

## Web UI

### Q: Why can't I see the API Keys page?

The API Keys page content is gated to admin keys. Non-admin keys see an empty "access denied" state. Use an admin key (env key or managed admin key) to access the key management interface.

### Q: Can I manage keys only through the API?

No, both the API and the Web UI are available. The Web UI provides a visual interface for the same operations (create, edit, revoke). Both require admin access.

### Q: Does the Web UI work without auth?

The Web UI shell and static files are served without authentication. However, API calls made by the UI (fetching memories, searching, managing keys) require authentication. The UI picks up the API key from its configuration or prompts the user.
