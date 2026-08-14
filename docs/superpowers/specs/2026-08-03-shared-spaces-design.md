---
shaping: true
---

# Shared Project Memory — Phase 1 Design

## Status

Revised 2026-08-11 after architectural and pull-request review.

This spec replaces the original Shared Spaces proposal. It keeps **Project** first-class in the product but implements the first useful version as a thin namespace and policy over existing Memories machinery.

Phase 1 covers explicit shared-project memory on one host. Automatic promotion, review queues, and scheduled reconciliation remain intended follow-up work, but are not sufficiently specified to implement safely in this PR's plan.

## Problem

Two people collaborate on the same project using their own agents and sessions. Each person's agents accumulate decisions, constraints, handoffs, and operational knowledge, but the other person's agents cannot reliably recall them. Decisions are re-derived, contradicted, or lost at the collaborator boundary.

For the first consumer:

- dk and Darshan both work on FPLGuru.
- Either may use Codex, Claude Code, another supported client, or a cloud session.
- They may work on the same item or different items.
- Durable project knowledge must be available to both people regardless of who or which agent captured it.
- Personal memory must remain private.

FPLGuru is the proving case for an OSS Memories capability, not a hard-coded special case.

## Phase 1 Scope

Phase 1 adds only:

1. an explicit, portable project declaration;
2. person-private and project-shared namespaces;
3. stable principal identity and server-authoritative attribution;
4. project-aware recall and explicit project writes;
5. conditional client guidance for collaborative repositories;
6. collaborator onboarding, verification, and revocation.

It adds no new service, storage authority, generic entity system, background promotion path, or federated memory identity.

## Design Principles

1. **Project is a collaboration boundary.** It is not an agent prefix and not a person's private memory.
2. **Person, Project, Visibility, and Source are separate concepts.** Authorization follows visibility; attribution follows person; provenance records the producing client.
3. **Reuse existing enforcement.** Prefix-scoped managed keys remain the authorization mechanism in this version.
4. **Explicit before inferred.** Phase 1 shares only an explicit write to a project namespace. Existing automatic extraction remains private.
5. **Fail private.** Missing context, invalid configuration, ambiguous deployment, or stale authorization must not widen visibility.
6. **One storage path.** Memories continue through the existing metadata + Qdrant path. This feature does not introduce SQLite as a second memory authority, an outbox, replication, or another server mode.
7. **One shared host first.** Local and cloud clients use the same Memories instance for the declared project.
8. **Generalize from evidence.** Stable identifiers leave an additive path to future Spaces, People, automation, and federation without building them now.

## Current State (verified against `develop` at `f50b05d`)

Memories already has most of the required mechanics:

- [auth_context.py](../../../auth_context.py) defines segment-safe prefix matching at line 9 and request-scoped `AuthContext` at line 29. `filter_results` provides a final authorization check before disclosure.
- [key_store.py](../../../key_store.py) defines the SQLite-backed managed-key store at line 21 and its current key schema at lines 29–40.
- [memory_engine.py](../../../memory_engine.py) creates memories through `add_memories` at line 649, flattens non-reserved metadata at lines 715–723, and supports ID-preserving source changes through `update_memory` and its source-only path at lines 1295–1335.
- [qdrant_store.py](../../../qdrant_store.py) provides payload filtering used by source-scoped retrieval.
- [mcp-server/lib-tools.mjs](../../../mcp-server/lib-tools.mjs) selects backends at line 152 and routes requests at line 196. ID-based manage operations still use backend-local numeric IDs.
- [mcp-server/assets/claude-code/hooks/_lib.sh](../../../mcp-server/assets/claude-code/hooks/_lib.sh) has its own backend selector at line 845 and extraction path at line 1075.
- [mcp-server/assets/codex/hooks/_lib.sh](../../../mcp-server/assets/codex/hooks/_lib.sh) has a separate backend selector at line 441 and extraction path at line 596.
- [app.py](../../../app.py) contains the existing scheduled consolidation entry point at line 966 and maintenance scheduler at line 1057. They are relevant to a future reconciliation design, not changed by Phase 1.

The confirmed gaps are:

1. Sources such as `codex/fplguru` identify a client, not a person, so they cannot be a reliable private ownership boundary on a shared host.
2. Shared project knowledge has no explicit, portable project declaration.
3. Memory creation and replacement paths do not share one server-authoritative authorship boundary.
4. Clients and injected guidance do not conditionally switch between legacy and collaborative namespace conventions.
5. Collaborator onboarding and revocation are not documented end to end.
6. Federated search results lack backend-qualified handles for safe get, update, delete, supersede, feedback, and link operations.

## Requirements

| ID | Requirement |
|---|---|
| R1 | Two or more collaborators can recall durable project knowledge from local or cloud sessions through one shared Memories host. |
| R2 | Personal memories are person-scoped and unreadable by other project members. |
| R3 | Every managed creation or replacement is attributed at a shared server boundary; clients cannot impersonate another author. |
| R4 | A repository can declare a stable project identity without credentials, member lists, or authority-bearing configuration. |
| R5 | Shared project reads require no per-result agent decision; server prefix authorization remains decisive. |
| R6 | A memory has one destination namespace: person-private or project-shared. Phase 1 does not copy or dual-write it between those namespaces. |
| R7 | Existing automatic extraction remains private; an agent or human creates shared memory through an explicit project write. |
| R8 | Existing single-user, legacy-prefix, and multi-backend behavior remains unchanged outside an activated collaborative repository. |
| R9 | Phase 1 rejects ambiguous multi-backend project activation rather than returning unsafe backend-local IDs. |
| R10 | Stable principal and project IDs leave additive paths to later entity types and automation. |

## Conceptual Model

| Concept | Example | Responsibility |
|---|---|---|
| Person | `dk`, `darshan` | Authorship and private ownership |
| Project | `fplguru` | Durable collaboration boundary |
| Visibility | `private`, `project` | Who may read the memory |
| Source | `codex`, `claude-code`, `hook`, `manual` | Provenance only |

Project is first-class conceptually. Phase 1 represents it with a stable ID, a repository declaration, and a project-qualified source namespace—not a generic entity graph.

## Project Declaration

A collaborative repository commits `.memories/project.yaml`:

```yaml
project_id: fplguru
shared_memory: true
```

- `project_id` is a stable explicit slug. A repository basename or Git remote may suggest it during setup but cannot silently establish shared identity.
- `shared_memory: true` opts the repository into the Phase 1 namespace and recall behavior.
- Unknown fields are rejected so a future `promotion` setting cannot appear to work before its behavior exists.

The declaration contains no backend URL, key, member list, or secret. Supported clients resolve it and send normalized project context; the server does not read the caller's repository filesystem.

The file never grants access. A caller still needs a server-issued key authorized for `project/<project_id>`. A missing or invalid file preserves existing behavior and never falls back to a basename for shared writes.

## Namespaces and Kinds

```text
person/<principal_id>/<project_id>/<kind>
project/<project_id>/<kind>
```

For FPLGuru:

```text
person/dk/fplguru/knowledge
person/darshan/fplguru/knowledge
project/fplguru/decisions
project/fplguru/knowledge
project/fplguru/state
project/fplguru/operations
```

Project kinds:

- `decisions` — choices between alternatives, including rationale and boundary conditions; superseded explicitly.
- `knowledge` — facts, constraints, conventions, and references that are true independently of a choice.
- `state` — current work, handoffs, blockers, and temporary coordination; freely superseded.
- `operations` — runbooks, release/deploy procedures, and recurring operational knowledge.

The deciding rule is: if someone chose it, it is a decision; if it remains true regardless of anyone's choice, it is knowledge.

The prefix is the access boundary. The producing client is metadata, not ownership, so Codex and Claude Code writes by the same person remain in the same private space.

## Identity, Authorship, and Authorization

Managed keys gain a stable `principal_id`. It may initially default to the key name for compatibility, but `AuthContext` carries it separately so multiple client keys can later map to one person without parsing names.

Authorship is applied at one shared server-side creation boundary. Every path that creates or replaces a memory supplies trusted authorship context, including add, batch add, extraction commit and fallback, supersede/update replacement, upsert, merge, missed capture, and import.

- Principal-originated memory stamps `author` from `AuthContext.principal_id`.
- Client-supplied author, contributors, or trusted context is ignored or overwritten.
- System-derived memory stamps `author: system` and preserves contributing principals and source memory IDs.
- Producing key ID stays internal audit data, not displayed person identity.
- Project-namespace creation without trusted principal or system authorship fails closed.
- OAuth may later populate the same principal field from its authenticated subject.

Project access reuses existing managed-key prefixes:

- a person's key receives `person/<principal_id>/...`;
- a collaborator receives `project/<project_id>`;
- existing read-only/read-write roles apply;
- revocation removes or narrows those prefixes.

There is no membership table or project-role model. All project read-write keys are equivalent contributors until a concrete operation requires finer governance.

Every shared retrieval is prefix-filtered before vector search where supported and checked again before disclosure. Link expansion and derived results must apply the same final filter.

## Minimal Metadata

| Field | Authority | Purpose |
|---|---|---|
| `author` | Server | Stable person or `system` attribution |
| `contributors` | Server | Principals and source IDs represented by a system-derived memory |
| `origin_client` | Client, normalized by server | Producer allowlist: `codex`, `claude-code`, `hook`, `manual`, `other`; unknown values become `other` |

Source already carries the owner/project and kind. Phase 1 does not add `entity_id`, `space_id`, promotion state, confidence, policy, or lifecycle metadata.

Existing timestamps, supersede links, archive state, and memory IDs remain unchanged.

## Write and Read Flow

### Personal extraction

Existing hook and extractor flows write only to `person/<principal_id>/<project_id>/<kind>` for an activated collaborative repository. Phase 1 never lets extraction infer a shared destination.

### Explicit project write

After a durable decision, handoff, operational change, or shared fact, an agent or human explicitly calls the existing add surface with `project/<project_id>/<kind>`. The server verifies project write scope, applies deterministic secret/credential checks, stamps authorship, and stores the memory once.

The Memories skill teaches the question: “Will another contributor need this without the current session?” If yes, create a concise project memory naming the fact or decision, why it matters, and any `until`, `unless`, or `because` boundary.

### Recall

For an activated collaborative repository, recall searches in this order:

1. `project/<project_id>`;
2. `person/<principal_id>/<project_id>`;
3. authorized legacy project prefixes or their kind-level descendants, for continuity only.

The client supplies those scopes in that deterministic order to one
authorization-filtered ranked search. The backend ranks all hits in one score
domain before applying the caller's limit; equal scores retain project, person,
then legacy order.

New writes never target legacy prefixes once collaborative mode is active. Legacy memories are not renamed, copied, or shared automatically. Because old `codex/<project>` and `claude-code/<project>` prefixes do not identify a person, a new collaborator is not granted another person's legacy prefixes; reviewed records may be explicitly promoted to the project namespace.

The memory playbook, plugin skill, hook recall guidance, and MCP client guidance become conditional on a valid project declaration. Without it, their current exact-project prefix behavior remains unchanged.

Project results render author and origin-client labels. Another person's project memory is attributed shared knowledge, not unquestionable truth; contradictions use an explicit superseding project memory with rationale.

## Deployment Boundary

Phase 1 supports exactly one configured Memories backend for an activated collaborative repository. That backend may be remote, so local and cloud sessions still share the same host.

If more than one backend is configured, shared project mode fails closed and existing non-project multi-backend behavior continues unchanged. Phase 1 does not merge project results carrying backend-local numeric IDs.

This avoids partially implementing federation. Safe federated shared projects require one coherent later change: backend-qualified opaque handles plus handle-aware get, update, delete, supersede, feedback, and link operations; deterministic routing; cross-backend promotion semantics; and parity across all clients.

## Failure Semantics

| Failure | Required result |
|---|---|
| Missing or invalid project declaration | Existing personal behavior; no shared-project mode |
| More than one backend configured | Shared-project mode disabled; existing multi-backend behavior otherwise unchanged |
| Caller lacks project write scope | Project write denied; no alternate shared destination |
| Client supplies an author | Server overwrites it with trusted authorship |
| Creation path lacks trusted authorship | Project-namespace creation denied |
| Repository config is malicious | It cannot grant access or impersonate an author |
| Project access is revoked | Subsequent shared reads and writes are denied by prefix ACL |
| Client does not yet support project mode | It retains legacy behavior and must not claim shared-project support |

## Build List

1. Parse and validate `.memories/project.yaml` in supported client entry points.
2. Add stable `principal_id` to managed authentication context.
3. Apply trusted authorship at the shared creation/replacement boundary and reserve its metadata fields.
4. Add person- and project-qualified namespace helpers with segment-safe matching.
5. Require one configured backend for collaborative mode without altering existing multi-backend routing elsewhere.
6. Make collaborative extraction private-only and explicit project writes single-destination.
7. Make recall and injected search guidance conditional on the project declaration, including authorized legacy continuity.
8. Update and contract-test the MCP bridge, packaged Claude Code hooks/skill, packaged Codex hooks, memory playbook, and relevant docs. The two packaged hook copies are separate implementation surfaces.
9. Document collaborator key minting, verification, revocation, and single-host cloud use.
10. Seed reviewed FPLGuru decisions only after access isolation and attribution are verified.
11. Record Phase 1 usage evidence: explicit project writes, failed unauthorized accesses, and reported missed shared context. Do not log memory text.

## Test Contract

- Person A cannot read or search person B's private namespace.
- Both authorized people can read the project namespace.
- Every user-originated creation/replacement path stamps the authenticated principal and rejects spoofed authorship.
- System-derived memories identify the system and preserve contributors and source IDs.
- Missing, invalid, or unknown project configuration never activates shared mode.
- Collaborative extraction writes private; explicit authorized project writes write once to the project namespace.
- Multiple configured backends disable shared mode without changing legacy multi-backend fan-out.
- Project recall searches shared, current-person private, then only authorized legacy prefixes.
- Link and related-memory expansion cannot escape authorized prefixes.
- Legacy repositories, sources, and existing multi-backend behavior remain unchanged.
- MCP, packaged Claude Code, and packaged Codex paths satisfy the same fixtures.
- Fresh local and cloud sessions on the same host recall the same project memory.

## Deferred Follow-Up: Promotion and Reconciliation

The product direction remains: promotion is a project setting, and an explicitly collaborative project defaults to automatic promotion once that feature is enabled. Phase 1 intentionally does not accept that setting or implement the behavior.

The proposed Phase 2 design is tracked separately in
[Shared Project Memory — Phase 2 Promotion and Reconciliation](2026-08-13-shared-project-memory-phase-2-promotion.md).
That proposal is review material, not part of the adopted Phase 1 contract.

A separate follow-up spec is required before implementation. It must resolve all of these points:

1. Separate enforceable structural gates—valid project context, current write authorization, secret/credential patterns, and raw-transcript markers—from semantic model judgments such as personal preference or durable project relevance.
2. Define the review surface before offering `review` mode: named MCP/API operations for listing, approving, rejecting, and auditing candidates.
3. Keep uncertain classification private.
4. Define the evidence that can resolve uncertainty. A scheduled job may not promote merely by rerunning unchanged checks; it needs explicit approval or new contextual signals pinned in the spec.
5. Make reconciliation idempotent, auditable, and same-host first. The existing scheduler at [app.py](../../../app.py) line 1057 is the likely execution mechanism, not a new service.
6. Use Phase 1 evidence—explicit promotion frequency and missed-context reports—to justify the additional pipeline.

## Other Deliberately Deferred Work

- Generic `Entity` and `Space` tables
- Entity aliases and automatic entity resolution
- Project membership or role tables separate from key ACLs
- People or project profile builders
- Generic relationship graphs beyond existing memory links
- Per-memory ACLs
- Cross-project inference and linking
- Federated shared projects and backend-qualified opaque handles
- Server-to-server synchronization or replication
- SQLite as a second canonical memory store or a transactional outbox
- OAuth 2.1 implementation
- UI or web dashboard changes

## Generalization Triggers

- Add promotion automation only after Phase 1 shows enough explicit promotions or missed shared context to justify it and the follow-up safety questions are answered.
- Add a generic Space registry only when a second collaboration boundary has materially different membership or policy semantics.
- Add generic Entity records only when at least two implemented entity types require shared identity, aliases, or relationships.
- Add a dedicated membership model only when managed-key prefix scopes can no longer express authorization correctly.
- Add federation only with backend-qualified handles covering every ID-based operation.

Stable `principal_id` and `project_id` values allow each later change to be additive.

## Exploratory Future Evolution

This section records the broader product direction discussed while shaping
Phase 1. It is not an implementation plan, release commitment, API promise, or
acceptance criterion for this PR. Each step requires its own evidence, design,
and review before implementation.

An earlier shape proposed building generic Entity and Space primitives before
shipping Project. The architectural review deliberately reversed that order:
Phase 1 proves the collaboration boundary with existing storage and ACL
machinery first, and generalizes only when the triggers above are met.

### Directional sequence

1. **Explicit shared project memory (this PR).** Prove person-private and
   project-shared isolation, server-authoritative attribution, explicit shared
   writes, and same-host use from local and cloud sessions.
2. **Promotion and reconciliation.** Specify a project-level promotion policy.
   The intended default for an explicitly collaborative project is automatic
   promotion once the feature exists, with the agent or configured classifier
   judging durable project relevance. Structural policy gates remain
   deterministic, uncertain classifications stay private, and any scheduled
   reconciler must be idempotent and auditable. The requirements in the
   preceding promotion section are mandatory inputs to that spec.
3. **Project knowledge lifecycle and profile.** Evaluate explicit epistemic
   states such as `provisional`, `proposed`, `accepted`, `superseded`, and
   `rejected`, so unfinished discoveries can be shared without presenting them
   as settled decisions. Evaluate a derived Project Profile only after the
   lifecycle, evidence, and supersession rules are defined. Lifecycle state
   must not weaken namespace authorization.
4. **Generic entities and spaces.** If the generalization triggers are met,
   introduce reusable identity and collaboration primitives. Project remains
   the first proven case; possible later entity types include Person,
   Organization, Product, and namespaced custom types. People or project
   profiles, aliases, relationships, and dedicated membership policy belong
   here rather than in the Phase 1 namespace layer.
5. **Federated and synchronized operation.** Validate the one-host model with
   real local and cloud clients first. Cross-backend projects require
   backend-qualified handles and safe parity for every ID-based operation
   before federation, synchronization, or replication can be enabled.
6. **Dedicated product surfaces.** Add review queues, membership management,
   administration UI, and relationship views only when the underlying policy
   and operational evidence justify those additional moving parts.

### External systems research

Before specifying generic entities, automated promotion, or federation,
compare the proven Phase 1 behavior with relevant OSS memory systems such as
Mem0 and Supermemory. The comparison should cover identity and tenancy,
private/shared scopes, provenance, promotion and review, lifecycle state,
cross-client routing, federation, and extension mechanisms. External designs
are research inputs, not compatibility requirements; Memories' isolation and
fail-private contracts remain authoritative.

## Success Criteria

1. dk and Darshan can start fresh local or cloud sessions using the same host and recall the same reviewed FPLGuru decisions and state.
2. Neither can recall the other's private project memories.
3. Every shared memory shows who authored it and which client originated it.
4. Existing extraction remains private unless an explicit authorized project write occurs.
5. Invalid configuration, ambiguous deployment, missing authorship, or revoked access reduces availability but never widens visibility.
6. Existing users without project configuration see no behavioral change.
7. Phase 1 adds no new service, storage authority, background promotion path, or generic entity subsystem.
