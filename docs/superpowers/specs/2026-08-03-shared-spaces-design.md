---
shaping: true
---

# Shared Project Memory — Multi-Collaborator Design

## Status

Revised 2026-08-11 after review of the original Shared Spaces proposal.

The original proposal correctly identified the collaboration problem, but made source-prefix conventions carry person identity, project identity, visibility, provenance, and routing at the same time. A later Entity + Space design separated those concepts correctly, but introduced too many new runtime components for the first use case.

This revision keeps **Project** first-class in the product while implementing it as a thin, explicit namespace and policy over the machinery Memories already has. Generic Entity and Space registries remain a future evolution, not a prerequisite.

## Problem

Two people collaborate on the same project using their own agents and sessions. Each person's agents accumulate decisions, constraints, handoffs, and operational knowledge, but the other person's agents cannot reliably recall them. Decisions are re-derived, contradicted, or lost at the collaborator boundary.

For the first consumer:

- dk and Darshan both work on FPLGuru.
- Either may use Codex, Claude Code, another supported client, or a cloud session.
- They may work on the same item or different items.
- Durable project knowledge must be available to both people regardless of who or which agent captured it.
- Personal memory must remain private.

The feature is part of the OSS Memories product. FPLGuru is the proving case, not a hard-coded special case.

## Design Principles

1. **Project is a collaboration boundary.** It is not an agent prefix and not a person's private memory.
2. **Person, Project, Visibility, and Source are separate concepts.** Authorization follows visibility; attribution follows person; provenance records the producing client or session.
3. **Reuse existing enforcement.** Prefix-scoped managed keys remain the authorization mechanism in this version.
4. **Inference never grants access.** An agent or extractor may classify whether a memory is project-relevant; only deterministic policy and server authorization may place it in a shared namespace.
5. **Fail private.** Missing context, invalid configuration, classifier failure, stale authorization, or reconciliation failure must not widen visibility.
6. **One storage path.** Memories continue through the existing metadata + Qdrant write path. This feature does not introduce a second canonical memory store, transactional outbox, replication protocol, or server mode.
7. **Generalize after a second proven boundary.** Preserve stable identifiers and additive migrations, but do not build generic Entity, Space, alias, profile, or relationship subsystems yet.

## Current State (verified against `develop` at the original PR head)

Memories already has most of the required mechanics:

- [auth_context.py](../../../auth_context.py) carries the authenticated key context and enforces source-prefix reads and writes. Search results are filtered again before disclosure.
- [key_store.py](../../../key_store.py) persists managed keys with roles and allowed prefixes.
- [memory_engine.py](../../../memory_engine.py) stores caller metadata with a memory, returns that metadata from search, supports source-only transitions, and already provides supersede, archive, and link operations.
- [qdrant_store.py](../../../qdrant_store.py) supports payload filtering used by project-scoped retrieval.
- [mcp-server/index.js](../../../mcp-server/index.js) and the client hooks already support single- and multi-backend routing.
- The container already runs scheduled maintenance. Candidate reconciliation can extend that mechanism instead of creating another service.

The confirmed gaps are:

1. Existing sources such as `codex/fplguru` identify a client, not a person. They cannot be a reliable private ownership boundary.
2. Shared project knowledge has no explicit, portable project declaration.
3. Managed writes do not stamp a stable person identity onto memories server-authoritatively.
4. Multi-backend writes do not consistently choose the personal or project backend from the destination namespace.
5. Agents and extractors have no shared promotion contract.
6. Existing maintenance does not revisit uncertain project candidates.
7. Collaborator onboarding and revocation are not documented end to end.

## Requirements

| ID | Requirement |
|---|---|
| R1 | Two or more collaborators can recall durable project knowledge from local or cloud sessions. |
| R2 | Personal memories remain person-scoped and are not readable by other project members. |
| R3 | Every managed write is attributed to a server-derived principal; clients cannot impersonate another author. |
| R4 | A repository can explicitly declare its stable project identity and promotion mode without containing credentials or granting access. |
| R5 | Shared project reads require no per-result agent decision; the server enforces the caller's prefix authorization. |
| R6 | Writes route to either the personal or project destination, never both by default. |
| R7 | Explicitly collaborative projects default to automatic promotion, with deterministic exclusions and private fallback. |
| R8 | Uncertain candidates can be revisited by existing scheduled maintenance without a new service or cross-server sync protocol. |
| R9 | Existing single-user and unconfigured repositories continue working unchanged. |
| R10 | The design leaves an additive path to future entity types such as People and Organizations. |

## Conceptual Model

The product model has four orthogonal concepts:

| Concept | Example | Responsibility |
|---|---|---|
| Person | `dk`, `darshan` | Authorship and private ownership |
| Project | `fplguru` | Durable collaboration boundary |
| Visibility | `private`, `project` | Who may read the memory |
| Source | `codex`, `claude-code`, `hook`, `manual` | Provenance only |

Project is first-class conceptually, but this version does not materialize a generic entity graph. Its concrete representation is a stable project ID, an explicit repository declaration, and a project-qualified source namespace.

## Project Declaration

A collaborative repository may commit `.memories/project.yaml`:

```yaml
project_id: fplguru
shared_memory: true
promotion: auto
```

Fields:

- `project_id` is a stable, explicit slug. A repository basename or worktree directory may suggest it during setup, but must not silently establish a shared identity.
- `shared_memory` opts the repository into project memory. If absent or false, existing personal behavior is unchanged.
- `promotion` is `auto`, `review`, or `off`. For a repository with `shared_memory: true`, omission defaults to `auto`.

The declaration is portable across clones and cloud sessions. It contains no backend URL, API key, member list, or secret.

Supported hooks and clients resolve the file and send a normalized project context with add or extraction requests. The Memories server does not need access to the caller's repository filesystem. An invalid file never falls back to a directory basename for shared writes.

The declaration **never grants access**. A malicious or accidental repository file cannot make a caller a project member; the server still requires a key authorized for the project's namespace.

If the file is missing, invalid, or contains an unsupported promotion value, automatic project writes are disabled and extraction stays private. Explicit writes to a project namespace still require normal server authorization.

## Namespaces

This version uses two ownership boundaries:

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

The project kinds are deliberately small:

- `decisions` — accepted choices, rationale, and boundary conditions; superseded explicitly.
- `knowledge` — domain and system facts, constraints, conventions, and durable references.
- `state` — current work, handoffs, blockers, and temporary coordination; freely superseded.
- `operations` — runbooks, release/deploy procedures, and recurring operational knowledge.

The prefix is the access and routing boundary. The producing client is metadata, not part of ownership. This avoids treating `codex/fplguru` and `claude-code/fplguru` as different owners or sharing domains.

Legacy personal prefixes remain readable and writable for backward compatibility. New collaborative setup uses person-qualified prefixes; migration of old personal memories is explicit and out of the initial write path.

## Identity and Authorization

Managed keys need a stable `principal_id` representing the person. In the first implementation it may default to the managed key name for backward compatibility, but it must be carried separately in the authenticated context so multiple client keys can later map to the same person without parsing naming conventions.

Server-authoritative fields:

- `author` is stamped from `AuthContext.principal_id` on add, batch add, and extraction.
- A client-supplied `author` is overwritten.
- The producing key ID remains audit data and is not used as the displayed person identity.
- OAuth can later populate the same principal field from its authenticated subject.

Project membership reuses existing prefix authorization:

- a personal key receives `person/<principal_id>/...`;
- a collaborator key receives the required `project/<project_id>` prefix;
- read-only and read-write behavior uses existing key roles;
- revocation removes or narrows the relevant key scopes.

There is no separate membership database or project-role model in this version. All read-write project keys are equivalent contributors. Owner/maintainer governance is deferred until there is an operation that needs the distinction.

Every project retrieval is pre-filtered by authorized source prefixes and checked again before results are returned. Link expansion, related-memory retrieval, summaries, and any later derived views must apply the same filter to their final records.

## Memory Metadata

Only metadata needed for attribution and the promotion lifecycle is added:

| Field | Authority | Purpose |
|---|---|---|
| `author` | Server | Stable person attribution |
| `origin_client` | Client, validated as descriptive | Codex, Claude Code, hook, manual, or other producer |
| `promotion_state` | Server pipeline | `direct`, `candidate`, or `promoted` |
| `promotion_reason` | Server pipeline | Short auditable reason for a promoted memory |
| `promotion_project_id` | Server pipeline | Target project for a private candidate |
| `promotion_mode` | Server pipeline | Captured `auto` or `review` behavior for a private candidate |

The server also retains the originating managed key ID as internal audit context for candidates. It is not a displayed author field, but lets scheduled maintenance recheck the exact credential that requested the candidate before changing visibility. Promotion fields and audit context are reserved: client metadata cannot overwrite the server pipeline's values.

The source prefix already carries the active owner/project and kind, so this version does not duplicate them into a generic `entity_id`, `space_id`, or policy object. Classification confidence may be recorded in the audit event for diagnosis, but it is not an authorization signal and need not become permanent memory metadata.

Existing timestamps, supersede links, archive state, and memory IDs remain unchanged.

## Write and Promotion Flow

### Explicit writes

An authorized agent or human may write directly to `project/<project_id>/<kind>`. The server verifies the caller's project prefix, stamps the author, and stores the memory once through the existing write path.

### Automatic extraction

For a valid collaborative project declaration:

1. The agent or existing extraction LLM classifies a candidate as personal, clearly project-relevant, or uncertain.
2. Deterministic guards in the backend promotion path reject automatic sharing of credentials, secrets, authentication material, raw transcripts, personal preferences, and content without explicit project context. Prompt instructions may help classification, but they are not the enforcement layer.
3. A clearly project-relevant memory in `auto` mode is written directly to `project/<project_id>/<kind>` after the current authenticated context passes the project write check.
4. A personal memory stays in `person/<principal_id>/<project_id>/<kind>`.
5. An uncertain memory stays private with `promotion_state: candidate` and a target `promotion_project_id`.
6. In `review` mode, project-relevant results are candidates until explicitly approved.
7. In `off` mode, automatic extraction never creates or promotes shared project memories; explicit authorized writes still work.

MiniLM remains a semantic similarity mechanism for duplicate detection, candidate linking, and reconciliation support. It does not classify privacy, select a destination, or authorize a write.

The critical invariant is:

> Model output may recommend a project destination, but only explicit project context, deterministic safety checks, and server authorization can make the memory shared.

## Scheduled Reconciliation

Reconciliation extends the existing in-container maintenance schedule. It is a bounded job, not a standalone service or generic workflow engine.

For each private `candidate` on the same Memories instance, the job:

1. reloads the memory and its current state;
2. verifies the captured promotion mode is valid for automatic or reviewed promotion;
3. verifies the originating managed key is still active and authorized to write the target project prefix;
4. reapplies deterministic sensitivity exclusions;
5. checks for an equivalent project memory using existing similarity and duplicate mechanisms;
6. either leaves the candidate private, records a proposed review item, or transitions its source in place to the project namespace;
7. records the old source, new source, author, reason, and timestamp in the audit log.

The job is idempotent. Reprocessing an already promoted memory must not create a second copy or widen access again. A maintenance failure leaves the memory private and eligible for a later run.

Source-in-place promotion is preferred on a single instance because it preserves the memory ID and provenance and avoids personal/project duplicates. It uses the existing source-only update path.

There is no server-to-server reconciliation. In federated installations, clear project memories can still route directly to the project backend at write time, but a private candidate on a different personal backend cannot be promoted by the project host's cron. Cross-backend candidate promotion requires a later authorized client action and remains outside the first implementation.

## Read Path

When a valid project declaration is active, recall searches the caller's authorized personal project prefix and the shared project prefix. Existing fan-out may query more than one configured backend, but each backend enforces its own source ACL before returning records.

Project results include author and origin-client labels. An agent should treat another contributor's project memory as attributed shared knowledge, not unquestionable truth: contradictions are resolved by an explicit superseding project memory with rationale and boundary conditions.

Project retrieval must not:

- return another person's private prefix;
- follow a memory link to an unauthorized target;
- broaden from `project/fplguru` into another project during similarity expansion;
- use vector metadata as a substitute for server authorization.

## Backend Routing

The routing rule is destination-based:

1. One backend configured: use it, subject to its server ACL.
2. An explicit operation routing map: honor it if it produces a backend authorized for the requested namespace.
3. A backend may claim write prefixes such as `project/` or `person/darshan/`.
4. Route a write to the one matching destination. Do not dual-write personal and project memories.
5. If no destination is safe and unambiguous, fail the project write or keep automatic extraction private.

Search may continue to fan out and merge authorized results.

Prefix matching uses segment boundaries: `project/fplguru` matches `project/fplguru/state`, not `project/fplgurux`. Ambiguous overlapping write claims are a configuration error and must not fan out silently.

The MCP bridge and shell hooks currently duplicate backend parsing. Implementation must share fixtures and contract tests across the Node and shell paths so their routing behavior cannot drift unnoticed.

## Failure Semantics

| Failure | Required result |
|---|---|
| Missing or invalid project declaration | Personal behavior only; no automatic sharing |
| Classifier or extraction unavailable | No automatic project write; retain or write private memory when possible |
| Deterministic safety guard matches | Private only; do not allow confidence to override |
| Caller lacks project write scope | Project write denied; no alternate shared destination |
| Reconciler unavailable | Candidates remain private and wait |
| Candidate authorization was revoked | Candidate remains private |
| Duplicate or repeated reconciliation | At most one shared memory; stable memory ID on in-place promotion |
| Backend routing is ambiguous | Fail closed; never dual-write |
| Repository config is malicious | It cannot grant server access or impersonate an author |
| Project membership is revoked | Subsequent shared reads and writes are denied by prefix ACL |

Automatic promotion deliberately accepts possible false negatives. A missed promotion can be reviewed and corrected; an unintended disclosure cannot be made unseen.

## Compatibility and Migration

- Repositories without `.memories/project.yaml` behave exactly as they do today.
- Existing source prefixes and API keys remain valid.
- Existing memories are not silently renamed, copied, or shared.
- Collaborators may explicitly promote selected legacy memories after reviewing their contents.
- A hosted instance may contain private prefixes for several people, but each managed key sees only its authorized person and project prefixes.
- The initial FPLGuru setup uses one host instance, which allows direct shared writes and same-instance candidate reconciliation without replication.

## Build List

### Phase 1 — Explicit project memory

1. Parse and validate `.memories/project.yaml` in supported clients and hooks.
2. Add stable `principal_id` to managed authentication context and server-authoritative author stamping.
3. Introduce person- and project-qualified namespace helpers with segment-safe matching.
4. Route explicit writes by destination prefix without dual writes.
5. Recall authorized personal + project prefixes and render author/provenance labels.
6. Document collaborator key minting, verification, revocation, and federated routing.
7. Seed reviewed FPLGuru decisions into `project/fplguru/decisions` only after the access path is verified.

### Phase 2 — Write-time automatic promotion

1. Add the promotion rubric to agent and extractor instructions.
2. Implement `auto`, `review`, and `off` behavior.
3. Add deterministic exclusions and fail-private error handling.
4. Store uncertain results as private candidates.

### Phase 3 — Scheduled candidate reconciliation

1. Extend existing scheduled maintenance with a bounded candidate scan.
2. Recheck current authorization and exclusions.
3. Perform idempotent same-instance source transitions with audit events.
4. Expose candidate and promotion counts for operations and evaluation.

Each phase must be useful and safe on its own. Phase 1 does not depend on automatic classification; Phase 2 does not depend on the scheduled reconciler.

## Test Contract

The implementation plan must include, at minimum:

- person A cannot read or search person B's private namespace;
- both authorized people can read the project namespace;
- a client-supplied author cannot override the authenticated principal;
- absent, invalid, and disabled project declarations never trigger automatic sharing;
- clear project, personal, uncertain, and sensitive examples exercise every promotion mode;
- model confidence cannot bypass deterministic exclusions or prefix authorization;
- single-backend and federated direct-write routing choose one destination;
- overlapping backend claims fail closed;
- a revoked key cannot promote a queued candidate;
- reconciliation is idempotent and preserves the memory ID;
- link and related-memory expansion cannot escape authorized project prefixes;
- legacy repositories and existing source prefixes remain unchanged;
- Claude Code, Codex, and the generic MCP path satisfy the same namespace and routing fixtures.

## Deliberately Deferred

- Generic `Entity` and `Space` tables
- Entity aliases and automatic entity resolution
- Project membership or role tables separate from managed-key ACLs
- People profiles or project profile builders
- Generic relationship graphs beyond existing memory links
- Per-memory ACLs
- Cross-project inference and linking
- Server-to-server synchronization or replication
- Cross-backend background promotion
- SQLite as a second canonical memory store or a transactional outbox
- Global/federated memory IDs
- OAuth 2.1 implementation
- UI or web dashboard changes

## Generalization Triggers

The thin representation intentionally leaves room for later first-class entities:

- Introduce a generic **Space** registry only when a second collaboration boundary has membership or policy semantics materially different from Project.
- Introduce generic **Entity** records only when at least two implemented entity types need shared identity, aliases, or relationships that project-qualified fields cannot safely express.
- Introduce a dedicated membership model only when authorization can no longer be represented correctly by managed-key prefix scopes.
- Introduce global memory IDs or synchronization only when a proven cross-server workflow requires them.

At that point, existing stable `principal_id` and `project_id` values can be migrated additively into entity and space records without changing current namespace identity.

## Success Criteria

The design succeeds when:

1. dk and Darshan can each start a fresh local or cloud session in FPLGuru and recall the same reviewed project decisions and current state.
2. Neither can recall the other's private project memories.
3. Every shared memory shows who authored it and where it originated.
4. Clearly project-relevant knowledge is shared automatically for an opted-in project, while ambiguous or sensitive content remains private.
5. A failed classifier, scheduler, backend route, or revoked key reduces sharing availability but does not widen access.
6. Existing users who do not configure project memory see no behavioral change.
7. The implementation adds no new service, storage authority, or generic entity subsystem.
