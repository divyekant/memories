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

## 7. Phase 2 promotion activation gate (off by default)

Phase 2 adds a private-first, model-reviewed promotion path, but this
repository remains inert until the release evidence gates below are met. The
host cap defaults to off and is an operator setting, not an authorization
grant:

```bash
PROJECT_PROMOTION_MODE=off
```

Do not change the host cap while installing or validating the release. `off`
means no new review and no new shared target. It does not delete existing
shared memories and it still permits idempotent finalization of a target that
was already created before a crash, plus linkage verification for a previously
promoted candidate.

### Upgrade order and isolation prerequisite

Upgrade the backend first. The pruning-safety prerequisite is a deployed
v5.15.1 (or newer) backend with the standalone `project/` pruning hotfix; test
that every `project/` source, including malformed legacy paths, is excluded
from both scheduled and manual pruning/consolidation. Keep the host at
`PROJECT_PROMOTION_MODE=off` while this check is performed.

Only after the backend health, version, and pruning checks pass should the
client hooks/MCP packages be upgraded. Restart each client and verify that it
still reaches the same one backend. Client configuration cannot compensate for
an unsafe or stale backend.

The managed-key isolation check is mandatory before any shadow run:

- configure exactly one backend for the repository;
- use one server-issued key per stable principal, and confirm
  `GET /api/keys/me` reports `type: "managed"`, the expected `principal_id`,
  role, and only the reviewed private plus project prefixes;
- from fresh sessions, confirm each principal can read the shared project
  prefix but receives `403` for the other principal's private memory;
- confirm a narrowed or revoked key cannot read or write the shared project;
- record no private text, credentials, prompts, or evidence excerpts in the
  operator report.

An environment/admin key, a key without a stable principal, or a multi-backend
configuration fails closed. Do not proceed by widening a key after an
isolation failure.

### Explicit repository shadow

Shadow is explicit and project-scoped. After backend and key isolation pass,
an operator may configure the reviewed repository declaration and set:

```yaml
# .memories/project.yaml (later rollout change, not this implementation PR)
project_id: fplguru
shared_memory: true
promotion:
  mode: shadow
```

Set the host cap and the threshold selected from the fixture routing report:

```bash
PROJECT_PROMOTION_MODE=shadow
PROJECT_PROMOTION_RELEVANCE_THRESHOLD=<measured-threshold>
```

Do not add .memories/project.yaml to this implementation PR, do not
activate FPLGuru, and do not treat Git repository membership as authorization.
Shadow may record a reviewer decision and a would-promote outcome, but it must
never write a new `project/...` target. Keep `PROJECT_PROMOTION_MODE=off` for
all non-shadow repositories.

Run the versioned offline fixture gate from the repository root:

```bash
uv run python eval/run_promotion_eval.py \
  --fixtures eval/fixtures/project_promotion_v1.jsonl \
  --threshold <candidate-threshold> \
  --output /tmp/promotion-eval.json
```

The exact fixture invocation is also:
`run_promotion_eval.py --fixtures eval/fixtures/project_promotion_v1.jsonl --threshold <candidate-threshold>`.

The report is deterministic and machine-readable. It includes weighted
precision and recall, high-risk unsafe count, route and decision counts,
provider/model/policy versions, per-risk-class confusion, and routing rates at
`0.30`, `0.40`, `0.50`, `0.70`, and higher thresholds. It never includes
conversation text. The fixture gate requires at least 100 weighted fixtures,
precision of at least 95%, recall of at least 85%, and zero unsafe high-risk
promotions. A nonzero command exit or a machine-readable failure keeps the
host off.

### Live evidence gates and reset rules

The fixture gate is necessary but does not authorize production. A separate
live shadow evidence record must show all of the following before any reviewed
change to `auto`:

- two weeks of uninterrupted, real dual-principal FPLGuru shadow activity;
- 50 total reviewed candidates;
- 30 manually inspected would-promote outcomes;
- five would-promote outcomes from each principal;
- zero unsafe live would-promote outcomes.

Fewer than 30 would-promote outcomes extends shadow. Fewer than roughly ten
after two weeks is a routing-threshold review, not safety evidence. A change to
the classifier/reviewer prompt or policy, provider, or model invalidates prior
shadow approvals and resets the two-week and volume gates. Re-run the fixture
command, accept the new policy identity, and begin a fresh live evidence
record. An old approval is never reused merely because its text is unchanged.

The implementation PR does not create or satisfy the separate FPLGuru shadow
evidence record; the live record is not created by this PR. That record must contain only reviewed aggregate outcomes and
the exact backend/client/policy versions; it is a later operator artifact.

Moving the host cap and repository declaration to `auto` does not publish the
existing shadow backlog. Existing `shadow_approved` candidates stay private
until an owner or administrator approves them individually; release them in
small, observed cohorts. Only new candidates captured after the current
authenticated `auto` declaration is accepted can follow the automatic path.

The backend accepts the current declaration only from an authenticated managed
key on a valid project extraction request. After a backend restart, delayed
promotion stays private until that declaration is observed again. An
authenticated `off` declaration is still reported to the backend so the
repository kill switch stops pending review and new shared-target creation.

### Alerts and rollback

Monitor the unreviewable signals without exposing candidate text:

- page on at least five newly `unreviewable` candidates within one hour;
- raise an informational aged-backlog signal when any unresolved item reaches
  seven days.

For either a privacy concern, unsafe would-promote outcome, provider/policy
drift, key isolation failure, or alert investigation, roll back by setting the
host cap to `PROJECT_PROMOTION_MODE=off` and verify the setting on the backend.
This stops new review and shared-target creation immediately. Allow only the
idempotent finalization/linkage repair of already-created targets, preserve the
private candidate, and investigate with the audit identifiers.

Phase 2 has no bulk dismissal API, no project consolidation or semantic merge,
and no automatic seed. Do not seed or manually promote project history until
the fixture, isolation, live shadow, and rollback gates are independently
reviewed. A reviewed manual seed, if later authorized, is a separate
post-deployment action and is not evidence that the implementation PR passed.
