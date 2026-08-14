# Shared project memory playbook (Phase 1)

This playbook is the safe onboarding path for two collaborators (for example,
dk and Darshan) using one Memories host. It is intentionally generic: replace
`fplguru`, `dk`, and `darshan` with the reviewed project slug and stable
principal IDs for the project at hand.

Phase 1 is a namespace and prefix-ACL policy over one existing Memories
service. It does not add a membership database, a second memory store,
automatic promotion, or federation.

## 1. Declare the project, without granting access

Commit this exact, two-field declaration at the repository boundary:

```yaml
# .memories/project.yaml
project_id: fplguru
shared_memory: true
```

`project_id` must be a lowercase path-safe slug. `shared_memory` must be the
YAML boolean `true`; unknown fields are rejected. Do not put a backend URL, API
key, member list, role, or secret in this file. The declaration names the
project only. It never grants access or chooses a principal.

Supported hooks and the MCP bridge resolve the declaration at the main Git
repository boundary, including worktrees. A missing declaration keeps the
existing legacy behavior. A declaration that exists but is invalid fails
closed without unscoped reads or writes.

Collaborative mode also requires exactly one configured backend. Configure the
same URL for every local and cloud client, for example with
`MEMORIES_URL=https://memory.example` and that client's `MEMORIES_API_KEY`.
Do not configure a multi-backend `backends.yaml` for this project: when more
than one backend is configured, collaborative mode fails closed. All sessions
must reach this one host; Phase 1 does not replicate or federate memories.

## 2. Mint two managed keys (administrator only)

An existing admin key (the deployment's `API_KEY` or an admin managed key) is
required for key management. Run these calls against the one shared host and
save each returned raw `key` out-of-band; it is shown only when the key is
created. Never commit it to the repository or `.memories/project.yaml`.

For dk (stable principal ID `dk`):

```bash
DK_KEY_JSON=$(curl -sS -X POST "$MEMORIES_URL/api/keys" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dk-fplguru",
    "principal_id": "dk",
    "role": "read-write",
    "prefixes": ["person/dk/fplguru", "project/fplguru"]
  }')
DK_KEY=$(printf '%s' "$DK_KEY_JSON" | jq -r .key)
DK_KEY_ID=$(printf '%s' "$DK_KEY_JSON" | jq -r .id)
```

For Darshan (stable principal ID `darshan`):

```bash
DARSHAN_KEY_JSON=$(curl -sS -X POST "$MEMORIES_URL/api/keys" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "darshan-fplguru",
    "principal_id": "darshan",
    "role": "read-write",
    "prefixes": ["person/darshan/fplguru", "project/fplguru"]
  }')
DARSHAN_KEY=$(printf '%s' "$DARSHAN_KEY_JSON" | jq -r .key)
DARSHAN_KEY_ID=$(printf '%s' "$DARSHAN_KEY_JSON" | jq -r .id)
```

The `name` is a display label; `principal_id` is the stable identity used in
private sources and authorship. `read-write` is required for an explicit
project `memory_add`; use `read-only` for a collaborator who must only read.
Every non-admin key must have at least one prefix. The two keys intentionally
share only `project/fplguru` and have separate private roots:

```text
person/dk/fplguru/...
person/darshan/fplguru/...
project/fplguru/...
```

Check each key before giving it to a client:

```bash
curl -sS "$MEMORIES_URL/api/keys/me" \
  -H "X-API-Key: $DK_KEY" | jq .
curl -sS "$MEMORIES_URL/api/keys/me" \
  -H "X-API-Key: $DARSHAN_KEY" | jq .
```

Each response must include `type: "managed"`, its stable `principal_id`, the
expected `role`, and exactly the expected prefixes. If it reports `type:
"env"`, `type: "none"`, or no principal, do not use that key for
collaborative mode: repository configuration cannot turn an admin/environment
key into a person identity.

## 3. Namespaces, kinds, and writing rules

Phase 1 recognizes only these exact source shapes:

```text
person/<principal_id>/<project_id>/<kind>
project/<project_id>/<kind>
```

The four project kinds are:

| Kind | Use it for |
| --- | --- |
| `decisions` | A choice between alternatives, with rationale and boundaries |
| `knowledge` | Facts, constraints, conventions, and references |
| `state` | Current work, handoffs, and blockers |
| `operations` | Runbooks, release/deploy procedures, and recurring operations |

Before sharing, apply the durable-sharing test: **will another contributor
need this fact without the current session?** If yes, call the existing
`memory_add` tool exactly once with one `project/<project>/<kind>` source. For
example:

```text
memory_add({
  text: "The release checklist requires a dry-run before the production tag.",
  source: "project/fplguru/operations",
  on_duplicate: "add"
})
```

The equivalent REST request is:

```bash
curl -sS -X POST "$MEMORIES_URL/memory/add" \
  -H "X-API-Key: $DK_KEY" \
  -H "X-Memories-Client: manual" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The release checklist requires a dry-run before the production tag.",
    "source": "project/fplguru/operations",
    "on_duplicate": "add"
  }'
```

Use an authenticated managed key for a project write. The server stamps
`author` from that key's principal and normalizes the `X-Memories-Client`
header into `origin_client` (`codex`, `claude-code`, `hook`, `manual`, or
`other`). Caller metadata cannot spoof these fields. Automatic extraction
remains private in collaborative mode: it writes only to
`person/<principal>/<project>/knowledge` and never infers `project/...`.

## 4. Fresh-session isolation verification

Perform this check with fresh local or cloud sessions after both clients have
the same host URL and their own managed key. Use unique synthetic probe text;
do not use production FPLGuru content during implementation. The REST calls
below make the same authorization contract visible when an MCP client is not
available.

1. With the dk key, add one private probe and one project probe. Record both
   returned IDs. Include `X-Memories-Client: manual` (or let the MCP bridge
   send `codex`/`claude-code`).

   ```bash
   DK_PRIVATE_JSON=$(curl -sS -X POST "$MEMORIES_URL/memory/add" \
     -H "X-API-Key: $DK_KEY" -H "X-Memories-Client: manual" \
     -H "Content-Type: application/json" \
     -d '{"text":"synthetic dk private isolation probe","source":"person/dk/fplguru/knowledge","on_duplicate":"add"}')
   DK_PROJECT_JSON=$(curl -sS -X POST "$MEMORIES_URL/memory/add" \
     -H "X-API-Key: $DK_KEY" -H "X-Memories-Client: manual" \
     -H "Content-Type: application/json" \
     -d '{"text":"synthetic shared project isolation probe","source":"project/fplguru/knowledge","on_duplicate":"add"}')
   DK_PRIVATE_ID=$(printf '%s' "$DK_PRIVATE_JSON" | jq -r .id)
   DK_PROJECT_ID=$(printf '%s' "$DK_PROJECT_JSON" | jq -r .id)
   ```

2. With the Darshan key, add a private probe at
   `person/darshan/fplguru/knowledge` and record `DARSHAN_PRIVATE_ID`.

3. From a fresh session using each key, `POST /search` with
   `source_prefix: "project/fplguru"` and a query matching the shared probe.
   Both responses must contain the project result. For example:

   ```bash
   curl -sS -X POST "$MEMORIES_URL/search" \
     -H "X-API-Key: $DARSHAN_KEY" -H "Content-Type: application/json" \
     -d '{"query":"synthetic shared project isolation probe","source_prefix":"project/fplguru","k":10}' | jq .
   ```

4. Fetch the shared result with each key. Both `GET
   /memory/$DK_PROJECT_ID` calls must succeed. Inspect the response and
   confirm `source` is `project/fplguru/knowledge`, `author` is `dk`, and
   `origin_client` is `manual` (or the actual client label used).

5. Verify private isolation in both directions:

   ```bash
   # Darshan must not read dk's private memory.
   curl -sS -o /dev/null -w '%{http_code}\n' \
     "$MEMORIES_URL/memory/$DK_PRIVATE_ID" -H "X-API-Key: $DARSHAN_KEY"
   # dk must not read Darshan's private memory.
   curl -sS -o /dev/null -w '%{http_code}\n' \
     "$MEMORIES_URL/memory/$DARSHAN_PRIVATE_ID" -H "X-API-Key: $DK_KEY"
   ```

   Both status codes must be `403`. Searches constrained to the other
   person's `person/<principal>/<project>` prefix must likewise return no
   visible results. A project result visible to both people must not be used
   as evidence that either private prefix is readable.

6. Confirm each client starts a new session with the same host and key, then
   repeat `/api/keys/me` and the project search. A declaration alone is not a
   successful verification.

Only after these checks pass should an administrator perform a reviewed,
manual seed: use one deliberate `memory_add` call with reviewed project text,
then repeat the shared search and metadata check. This step is post-deployment
and manual; it is not part of code implementation, CI, or automatic extraction.

## 5. Narrow or revoke access

Prefix changes take effect on subsequent requests. To narrow Darshan to their
private namespace while retaining the same stable identity:

```bash
curl -sS -X PATCH "$MEMORIES_URL/api/keys/$DARSHAN_KEY_ID" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prefixes":["person/darshan/fplguru"]}'
```

After opening a fresh session, `/api/keys/me` must show only that prefix;
project reads should return no visible results and project writes should be
denied (`403`). To remove the key completely, use the soft-revoke endpoint:

```bash
curl -sS -X DELETE "$MEMORIES_URL/api/keys/$DARSHAN_KEY_ID" \
  -H "X-API-Key: $ADMIN_API_KEY"
```

The response is `{"id":"...","revoked":true}`. A subsequent
`GET /api/keys/me` with the old raw key must return `401`, and no read or write
may succeed. Revoke or narrow the key before distributing a replacement; do
not widen a key to compensate for a failed isolation check.

## 6. Legacy migration and explicit non-goals

Legacy sources such as `codex/fplguru`, `claude-code/fplguru`,
`learning/fplguru`, and `wip/fplguru` are not renamed or copied. During
collaborative recall they remain readable only when that principal's key
explicitly includes the legacy project prefix or a narrower descendant such
as `codex/fplguru/knowledge`. A new collaborator is never
given another person's legacy prefixes automatically. Review a legacy memory
and write a new, explicit `project/fplguru/<kind>` memory if it truly needs to
be shared.

Pre-upgrade records whose source begins with `project/` or `person/` but does
not match the strict shapes above are intentionally read/export/delete-only.
Their ownership is ambiguous, so they cannot be repaired in place or silently
treated as ordinary legacy data. Using a managed key, explicitly move the
record to a non-reserved source such as `legacy/project-decisions`, inspect it,
then create a new strict project memory only if it passes the durable-sharing
test. Imports report each malformed reserved record as an individual error so
the rest of a batch remains auditable.

There is no automatic promotion, no implicit membership inferred from Git or
`.memories/project.yaml`, no cross-backend federation, and no server-to-server
replication in Phase 1. If the project has multiple configured backends,
shared mode is disabled rather than guessing which host is authoritative.
