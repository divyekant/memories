---
doc-id: uc-002
type: use-case
title: "Team Access: Shared Server with Different Visibility"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# Use Case: Team Access (Shared Server, Different Visibility)

## Scenario

A team shares a single Memories server. Different members need different levels of access:

- **Admin** (server operator): full access to all data plus key management, maintenance, backup/restore.
- **Contributor** (developer/agent): can read and write memories within assigned project prefixes.
- **Viewer** (stakeholder/dashboard): can search and read memories from specific projects but cannot write.

## Setup

### 1. Admin key

The server operator uses the `API_KEY` env var. This is the implicit admin key. Alternatively, the operator can create a managed admin key:

```bash
curl -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "ops-admin", "role": "admin", "prefixes": []}'
```

Admin keys ignore prefixes -- the `prefixes` field is silently cleared on creation.

### 2. Contributor keys

Create keys for developers or agents who need to add and retrieve memories within their project scope:

```bash
# Frontend team -- scoped to frontend project memories
curl -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "frontend-team", "role": "read-write", "prefixes": ["frontend/*", "shared/*"]}'

# Backend team -- scoped to backend project memories
curl -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "backend-team", "role": "read-write", "prefixes": ["backend/*", "shared/*"]}'
```

Both teams share access to the `shared/*` prefix for cross-team knowledge.

### 3. Viewer keys

Create read-only keys for stakeholders who need visibility but should not modify data:

```bash
curl -X POST http://localhost:8900/api/keys \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "pm-readonly", "role": "read-only", "prefixes": ["frontend/*", "backend/*", "shared/*"]}'
```

This key can search and read across all project prefixes but cannot add, delete, or modify any memory.

## Access Matrix

| Caller | Role | Prefixes | Read | Write | Extract | Key Mgmt | Maintenance |
|--------|------|----------|------|-------|---------|----------|-------------|
| Server operator | admin (env) | unrestricted | All | All | All | Yes | Yes |
| frontend-team | read-write | `frontend/*`, `shared/*` | Scoped | Scoped | Scoped | No | No |
| backend-team | read-write | `backend/*`, `shared/*` | Scoped | Scoped | Scoped | No | No |
| pm-readonly | read-only | `frontend/*`, `backend/*`, `shared/*` | Scoped | No | No | No | No |

## Behavior Details

### Search isolation

When `frontend-team` searches for "authentication flow", the vector search runs over all memories but `filter_results()` removes anything outside `frontend/*` and `shared/*`. The frontend team never sees backend-only memories in results.

### Write scoping

If `frontend-team` tries to add a memory with `source: "backend/api-design"`, the request is rejected with 403. The key can only write to sources matching `frontend/*` or `shared/*`.

### Read-only enforcement

The `pm-readonly` key has `role: read-only`. Even though it has broad prefix coverage, `can_write()` returns false for any source. Add, delete, extract, and supersede operations all fail with 403.

### Admin endpoint protection

Non-admin keys cannot access stats, metrics, backup/restore, consolidation, pruning, index rebuild, deduplication, folder rename, or cloud sync endpoints. These all return 403 for non-admin callers.

## Adjusting Access Over Time

### Adding a prefix

If the frontend team takes ownership of a new project area, update their key:

```bash
curl -X PATCH http://localhost:8900/api/keys/<key-id> \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prefixes": ["frontend/*", "shared/*", "mobile/*"]}'
```

### Promoting a key

To give a contributor admin access:

```bash
curl -X PATCH http://localhost:8900/api/keys/<key-id> \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"role": "admin"}'
```

### Revoking access

When a team member leaves or a key is compromised:

```bash
curl -X DELETE http://localhost:8900/api/keys/<key-id> \
  -H "X-API-Key: $API_KEY"
```

The key is soft-revoked (remains in the database for audit) but immediately stops authenticating.

## Considerations

- **Prefix overlap**: Multiple keys can share the same prefix. The `shared/*` pattern is intentional for cross-team data.
- **No key-to-key isolation**: Two keys with the same prefix see the same data. Isolation is prefix-based, not key-based.
- **Viewer limitations**: Read-only keys cannot use the extract endpoint since extraction creates new memories (a write operation).
- **Web UI access**: Only admin keys can see the API Keys management page in the Web UI. Non-admin users see other pages (memories, search) filtered to their scope.
