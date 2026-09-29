# Shared Project Memory Phase 3 Activation and Lifecycle Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the released private-first shared-memory system for FPLGuru in a controlled shadow rollout, collect enough real evidence for an explicit auto-mode decision, and then implement the smallest safe project-knowledge lifecycle justified by that evidence.

**Architecture:** Phase 3 has two ordered tracks. The rollout track uses the Phase 1 namespace boundary and Phase 2 private-first reviewer exactly as shipped: one host, separate managed principals, repository-declared shadow mode, and no automatic shared writes until the live gates pass. The engineering track begins only after shadow evidence exists and adds explicit project-memory lifecycle operations without allowing semantic similarity, consolidation, or a model decision to mutate shared knowledge by itself.

**Tech Stack:** FastAPI, Qdrant, Python 3.11+, Node.js MCP/hooks, strict YAML repository declarations, managed API keys, Anthropic-compatible extraction/review providers, MiniLM similarity for ranking only, pytest, Node test runner, and GitHub Actions.

## Global Constraints

- Release the npm dependency remediation as v5.16.1 or newer before enabling FPLGuru shadow mode.
- Use exactly one Memories backend for FPLGuru; multi-backend collaborative context must continue to fail closed.
- Use separate server-issued managed keys for stable principals `dk` and `darshan`; never use the environment/admin key in an agent session.
- The only shared namespace is `project/fplguru/{decisions,knowledge,state,operations}`; each principal's private namespace follows `person/{principal_id}/fplguru/{kind}`.
- Automatic extraction remains private-first. Shadow may record a would-promote outcome but must not create, update, merge, archive, or delete a shared project record.
- The repository declaration is untrusted policy input and never grants membership or access.
- Keep `PROJECT_PROMOTION_MODE=off` until dependency, fixture, key-isolation, rollback, and client-version checks all pass.
- Do not enable `auto` until every fixture and live gate in the finalized Phase 2 specification passes.
- Any classifier policy, reviewer policy, provider, model, or relevance-threshold change invalidates the observation record and restarts the two-week and volume gates.
- Do not bulk-promote legacy history. A later seed is a separately reviewed set of 20-50 durable project facts.
- Semantic similarity remains reviewer context and telemetry only; it cannot automatically rewrite, suppress, merge, supersede, or delete shared project knowledge.
- Generic people/entities, per-memory ACLs, cross-project inference, federation, and replication remain out of scope.
- This plan PR is documentation only. It must not modify production configuration, create keys, add FPLGuru's `.memories/project.yaml`, activate shadow/auto, or seed memories.

---

## Working Flow

```text
FPLGuru session by dk or darshan
  -> strict project declaration resolves at repository boundary
  -> one configured backend authenticates a managed principal
  -> hooks retrieve:
       project/fplguru/*
       then person/{authenticated_principal}/fplguru/*
       then only that key's authorized legacy prefixes
  -> automatic extraction writes person/{authenticated_principal}/fplguru/knowledge
  -> shadow classifier/reviewer records private candidate outcome
  -> operator inspects aggregate evidence and candidate APIs
  -> no shared write occurs in shadow
  -> after all live gates pass, explicit auto go/no-go review
  -> new auto-era approved candidates may create/reuse exact-digest shared facts
  -> existing shadow approvals remain private until individually approved
```

The practical answer to “can we enable the shared system now?” is **yes for a controlled shared + shadow test after v5.16.1 and the isolation gate; no for automatic promotion today**. Shared retrieval and explicit project writes can be exercised immediately after the two managed keys and the two-field collaborative declaration are installed. Model-reviewed visibility should start in shadow and earn its auto decision from live evidence.

---

### Task 1: Ship the Dependency-Safe Activation Baseline

**Files:**
- Modify in the dependency PR: `mcp-server/package-lock.json`
- Verify: `mcp-server/package.json`
- Verify: `CHANGELOG.md`

**Interfaces:**
- Consumes: merged v5.16.0 Phase 2 implementation and the npm audit remediation PR.
- Produces: one immutable release tag, npm package version, backend version, remote-MCP version, and client-plugin SHA recorded in the rollout evidence.

- [ ] **Step 1: Merge the dependency-only PR after review**

Require a lockfile-only dependency delta, zero production advisories, 310 passing Node tests, and a successful package dry run.

- [ ] **Step 2: Cut the patch release through the normal release train**

Promote `develop` to `main`, tag the exact main merge, publish `memories-mcp`, update the Claude marketplace immutable SHA, and deploy the backend plus remote MCP with the host promotion cap still off.

- [ ] **Step 3: Verify the exact deployed baseline**

Record only versions and identifiers, never keys or candidate text:

```bash
curl -fsS "$MEMORIES_URL/health" -H "X-API-Key: $ADMIN_API_KEY" \
  | jq '{status,version,total_memories,dimension,model}'
curl -fsS "$REMOTE_MCP_URL/healthz" | jq '{status,service,version}'
npm view memories-mcp version
```

Expected: every component reports the same v5.16.1-or-newer release, the backend is healthy, and production promotion metrics remain empty while the cap is off.

- [ ] **Step 4: Prove rollback before activation**

Record the pre-activation backup path, backend rollback image, remote-MCP rollback image, and the exact command that restores `PROJECT_PROMOTION_MODE=off`. Do not proceed if the rollback cannot be executed without touching Qdrant data.

---

### Task 2: Create the Private FPLGuru Rollout Evidence Record

**Files:**
- Create later in the private FPLGuru repository: `docs/operations/memories-shadow-evidence.md`
- Do not create in this OSS plan PR: `.memories/project.yaml`

**Interfaces:**
- Consumes: exact release identifiers from Task 1.
- Produces: the auditable, text-free go/no-go record used by Tasks 4-7.

- [ ] **Step 1: Create a text-free evidence template in the private repository**

The record must contain:

```markdown
# Memories shadow evidence

## Accepted identity
- backend release:
- npm/client release:
- plugin commit SHA:
- classifier provider/model/policy version:
- reviewer provider/model/policy version:
- declaration fingerprint:
- relevance threshold:
- shadow start timestamp:
- last uninterrupted timestamp:

## Isolation gate
- dk `/api/keys/me` verified:
- darshan `/api/keys/me` verified:
- exact shared read/write probes passed:
- cross-principal private reads denied:
- narrowed-key test passed:
- revoked-key test passed:

## Fixture gate
- run 1 report digest and outcome:
- run 2 report digest and outcome:
- run 3 report digest and outcome:

## Live aggregate gate
- total reviewed:
- manually inspected would-promote outcomes:
- dk would-promote outcomes:
- darshan would-promote outcomes:
- unsafe would-promote outcomes:
- unreviewable count and oldest age:
- observation duration:

## Decision
- continue shadow / rollback / approve auto review:
- reviewers:
- timestamp:
```

Candidate text, private rationale, transcript excerpts, credentials, prompts, and raw provider payloads must never appear in this record.

- [ ] **Step 2: Review the record in its own FPLGuru PR**

The PR must be reviewable before any host-cap or repository-mode change. Merging the template does not activate anything.

---

### Task 3: Provision and Prove Managed-Principal Isolation

**Files:**
- Operational only: production key store via `/api/keys`
- Verify: `docs/memory-playbook.md`
- Update later: private evidence record from Task 2

**Interfaces:**
- Consumes: one healthy backend and the admin key.
- Produces: two raw keys stored out of band, key IDs for revocation, and a completed isolation gate with no private data in the evidence record.

- [ ] **Step 1: Create the two managed keys**

Use the exact payloads already documented in `docs/memory-playbook.md`:

```json
{"name":"dk-fplguru","principal_id":"dk","role":"read-write","prefixes":["person/dk/fplguru","project/fplguru"]}
```

```json
{"name":"darshan-fplguru","principal_id":"darshan","role":"read-write","prefixes":["person/darshan/fplguru","project/fplguru"]}
```

Store each returned raw key out of band. Only the key ID may enter the evidence record.

- [ ] **Step 2: Verify identity from each key**

```bash
curl -fsS "$MEMORIES_URL/api/keys/me" -H "X-API-Key: $DK_KEY" | jq .
curl -fsS "$MEMORIES_URL/api/keys/me" -H "X-API-Key: $DARSHAN_KEY" | jq .
```

Expected: `type` is `managed`; principal IDs are exactly `dk` and `darshan`; roles are `read-write`; prefixes contain only that principal's private FPLGuru root and the shared FPLGuru root.

- [ ] **Step 3: Run fresh-session synthetic isolation probes**

From separate fresh sessions, verify:

- each principal can add/read its own synthetic private record;
- both can read the synthetic shared record;
- each receives `403` when fetching the other principal's private ID;
- search/list/count do not leak the other principal's private record;
- server-owned `author`, `contributors`, `origin_client`, and `source_memory_ids` cannot be spoofed.

Delete only the recorded synthetic IDs after the assertions pass.

- [ ] **Step 4: Prove narrowing and revocation**

Create a temporary managed test key, narrow away `project/fplguru`, verify shared reads/writes fail, revoke it, and verify all further calls fail. Do not mutate either collaborator's primary key for this test.

---

### Task 4: Select the Shadow Routing Threshold from Three Provider-Backed Runs

**Files:**
- Read: `eval/fixtures/project_promotion_v1.jsonl`
- Run: `eval/run_promotion_eval.py`
- Update later: private evidence record from Task 2

**Interfaces:**
- Consumes: the exact classifier/reviewer provider, model, and policy versions intended for live shadow.
- Produces: three retained passing reports at one preregistered threshold and the exact threshold recorded before activation.

- [ ] **Step 1: Generate routing reports at the documented candidate thresholds**

```bash
for threshold in 0.30 0.40 0.50 0.70; do
  uv run python eval/run_promotion_eval.py \
    --fixtures eval/fixtures/project_promotion_v1.jsonl \
    --threshold "$threshold" \
    --output "/secure/operator/promotion-routing-${threshold}.json"
done
```

- [ ] **Step 2: Select one deliberately permissive threshold**

Choose the lowest threshold whose report meets all fixture safety gates while reviewing every plausibly durable project fact at acceptable provider volume. Record the exact numeric threshold and all provider/model/policy identities before the confirmation runs.

- [ ] **Step 3: Run three consecutive attempts at the selected identity**

Do not rerun only a failed attempt. Every attempt must independently satisfy:

- at least 100 weighted fixtures and 100 distinct transcripts;
- promotion precision at least 95%;
- end-to-end recall at least 85%;
- zero unsafe high-risk promotions;
- zero high-risk reviewer approvals before deterministic vetoes.

A provider failure, nonzero evaluator exit, or changed policy identity keeps the host cap off.

---

### Task 5: Activate Shared Retrieval and Promotion Shadow in FPLGuru

**Files:**
- Create later in the private FPLGuru repository: `.memories/project.yaml`
- Modify operationally: Memories deployment environment
- Update later: private evidence record from Task 2

**Interfaces:**
- Consumes: passing Tasks 1-4 and separate collaborator keys.
- Produces: active shared retrieval, explicit shared writes, private automatic extraction, and model-reviewed shadow outcomes with zero automatic shared mutations.

- [ ] **Step 1: Open the private FPLGuru declaration PR**

```yaml
project_id: fplguru
shared_memory: true
promotion:
  mode: shadow
```

The file belongs in the private FPLGuru repository. It must contain no backend URL, key, member list, role, provider credential, or threshold.

- [ ] **Step 2: Verify every supported client before changing the host cap**

Restart Claude/Codex sessions so they load the exact released hooks. From the FPLGuru root and one worktree, verify each client resolves the same repository declaration, the same one backend, and its own `/api/keys/me` identity.

- [ ] **Step 3: Raise the host cap to shadow with the selected threshold**

Set both settings in one reviewed deployment change:

```text
PROJECT_PROMOTION_MODE=shadow
PROJECT_PROMOTION_RELEVANCE_THRESHOLD=$SELECTED_THRESHOLD
```

Before rendering the deployment configuration, set `SELECTED_THRESHOLD` to
the exact numeric value already committed to the reviewed evidence record. The
rendered service environment must contain that number, not a shell expression.

Recreate only the Memories backend service. Do not restart or replace Qdrant.

- [ ] **Step 4: Verify shadow invariants immediately**

Run one dirty synthetic conversation per principal containing a confirmed decision, tentative proposal, explicit-private technical detail, and retraction. Verify:

- extraction records are created only under the current principal's private namespace;
- candidate/review state is queryable only by its owner or an admin;
- shadow-approved outcomes do not create a `project/fplguru/*` record;
- the other principal cannot read candidate text or rationale;
- promotion metrics partition outcomes by principal, route, kind, and policy without text;
- disabling the host cap returns effective mode to off.

Remove only synthetic records whose IDs were recorded for the test.

---

### Task 6: Run the Two-Week Dual-Principal Shadow Observation

**Files:**
- Update later: private evidence record from Task 2
- Read: `docs/memory-playbook.md`

**Interfaces:**
- Consumes: uninterrupted shadow activation at one accepted policy identity.
- Produces: an auditable auto-mode go/no-go packet.

- [ ] **Step 1: Inspect the candidate and alert surfaces daily**

Use `GET /promotions`, `GET /promotions/{candidate_id}`, and promotion metrics. Each collaborator may inspect only their own private candidates; an admin may inspect both. Record aggregate counts only.

- [ ] **Step 2: Stop immediately on a safety or isolation signal**

Set `PROJECT_PROMOTION_MODE=off` for any unsafe would-promote outcome, cross-principal visibility failure, key drift, provider/policy drift, five new unreviewable candidates in one hour, or other privacy concern. A stopped or reset period does not count toward the two-week gate.

- [ ] **Step 3: Require every live gate**

Do not schedule an auto review until the same uninterrupted policy identity has:

- at least two weeks of real activity by both principals;
- at least 50 reviewed candidates;
- at least 30 manually inspected would-promote outcomes;
- at least five would-promote outcomes from `dk`;
- at least five would-promote outcomes from `darshan`;
- zero unsafe live would-promote outcomes.

Fewer than 30 would-promote outcomes extends shadow. Fewer than roughly ten after two weeks triggers threshold analysis, not auto approval.

---

### Task 7: Hold an Explicit Auto-Mode Go/No-Go Review

**Files:**
- Update later: private evidence record from Task 2
- Create only after approval: a separate FPLGuru auto-declaration PR and deployment change

**Interfaces:**
- Consumes: complete fixture, isolation, rollback, and live evidence gates.
- Produces: a recorded decision to continue shadow, roll back, or begin a bounded auto cohort.

- [ ] **Step 1: Review safety before recall or convenience**

Any unsafe live outcome is an automatic no-go and policy reset. A low sharing rate is not a reason to lower safety checks during the meeting.

- [ ] **Step 2: If approved, change both caps in separately reviewable changes**

The private FPLGuru repository changes `promotion.mode` from `shadow` to `auto`; the operator changes the host cap from `shadow` to `auto`. Either side remaining more restrictive keeps automatic promotion disabled.

- [ ] **Step 3: Do not bulk-publish the shadow backlog**

Only candidates captured after the accepted auto declaration may follow the automatic path. Existing `shadow_approved` candidates remain private and may be approved individually in small observed cohorts.

- [ ] **Step 4: Keep the kill switch exercised**

Verify returning the host cap to off stops new review and target creation while preserving existing shared records and allowing only idempotent completion of a target already created before the rollback.

---

### Task 8: Shape the Phase 3 Project-Knowledge Lifecycle from Shadow Evidence

**Files:**
- Create in a future spec PR: `docs/superpowers/specs/2026-08-14-shared-project-memory-lifecycle.md`
- Read: aggregate shadow evidence from Task 6
- Read: `docs/decisions/2026-08-13-shared-project-memory-boundary.md`

**Interfaces:**
- Consumes: observed duplicate, contradiction, defer, rejection, and reviewer-volume distributions.
- Produces: a separately approved design for project-memory lifecycle; no implementation is authorized by this plan alone.

- [ ] **Step 1: Quantify the actual lifecycle problem**

Measure exact reuse, semantic near-duplicates, contradictions, manual decisions, unreviewable backlog, and project-kind distribution. Do not include memory text in the aggregate report.

- [ ] **Step 2: Define explicit public lifecycle states**

The spec must distinguish at least active/accepted knowledge, proposed replacement, superseded knowledge, and rejected replacement without overloading Phase 2 classifier `assertion_status`.

- [ ] **Step 3: Define claim-level attribution before semantic merge**

Every synthesized or replacement claim must identify the source project memory IDs and contributors supporting that claim. Unioning all contributors onto merged prose is prohibited because it falsely attributes every clause to every contributor.

- [ ] **Step 4: Keep model output advisory**

A model may propose that two project records conflict or that one supersedes another. The server must require exact authorization, deterministic namespace checks, locked revalidation, and an explicit owner/admin decision before mutating existing shared knowledge.

- [ ] **Step 5: Specify race and rollback semantics**

The design must cover concurrent replacement proposals, revoked access, changed source records, duplicate decisions, target deletion, retry after partial failure, and rollback without losing the previous accepted record.

- [ ] **Step 6: Review the lifecycle spec before writing implementation tasks**

Do not combine lifecycle implementation with the activation PR. The spec receives its own Claude/Codex review cycle and must preserve Phase 1 isolation plus Phase 2 private-first promotion.

---

### Task 9: Implement the Smallest Approved Lifecycle Slice

**Files:**
- Exact production/test files are assigned by the approved lifecycle spec and its implementation plan.

**Interfaces:**
- Consumes: the approved Task 8 lifecycle spec.
- Produces: one independently testable lifecycle capability with API, authorization, audit, maintenance, and rollback coverage.

- [ ] **Step 1: Write a new TDD implementation plan from the approved spec**

The first slice should prefer explicit propose/approve/supersede operations over automatic semantic consolidation. UI, bulk mutation, and automatic merging remain outside the first slice unless shadow evidence proves they are required.

- [ ] **Step 2: Require cross-principal and mutation-race tests before implementation**

Tests must prove that another collaborator's private candidate cannot enter reviewer context, a revoked principal cannot finalize a project mutation, and every selected target is re-read and reauthorized under the correct lock.

- [ ] **Step 3: Keep project consolidation disabled until claim provenance exists**

The existing maintenance exclusion for strict project records remains load-bearing. No scheduler, manual endpoint, or import path may bypass it.

---

### Task 10: Add Operator UI or Bulk Review Only When Volume Justifies It

**Files:**
- Future UI/API spec paths are determined only after the Task 6 evidence review.

**Interfaces:**
- Consumes: measured per-principal candidate volume and incident/review burden.
- Produces: either a documented no-build decision or a separately scoped UI/bulk-review spec.

- [ ] **Step 1: Use measured burden as the entry gate**

The existing list/get/approve/reject APIs and scripts remain sufficient until operators demonstrate that per-item review or unreviewable cleanup is materially costly.

- [ ] **Step 2: If needed, design UI as a client of existing authorization**

The UI must not add a second permission model. Owners see only their own private candidates; admins may inspect all; shared project records remain governed by the exact project ACL.

- [ ] **Step 3: Keep bulk operations auditable and bounded**

Any future bulk dismissal or approval must preview exact candidate IDs, enforce a maximum batch size, reauthorize each item, write one audit event per item, and return partial failures without widening access.

---

### Task 11: Review a Small Historical Seed Separately

**Files:**
- Create only after activation approval: a private, reviewed seed manifest containing durable shared facts and target kinds.
- Update: private rollout evidence record with aggregate seed outcome.

**Interfaces:**
- Consumes: stable shared retrieval, passing isolation, and an approved auto or continued-shadow operating decision.
- Produces: 20-50 explicitly reviewed historical project memories; it is not fixture or live-shadow evidence.

- [ ] **Step 1: Curate durable facts outside automatic extraction**

Include accepted decisions, current operating constraints, active state, and runbooks that both contributors need. Exclude credentials, private preferences, interpersonal assessments, stale session chatter, and unresolved proposals.

- [ ] **Step 2: Review every target source and text**

Each item names exactly one of `project/fplguru/decisions`, `knowledge`, `state`, or `operations`. Use a managed principal so server-owned authorship remains authoritative.

- [ ] **Step 3: Add in small observed cohorts**

Record returned IDs, verify both principals can retrieve the records, and stop on unexpected deduplication, authorship, or cross-namespace behavior. Never treat the seed as evidence that automatic promotion is safe.

---

## Exit Criteria

Phase 3 activation is complete only when:

1. the dependency-safe patch release is deployed everywhere;
2. both managed principals pass fresh-session isolation, narrowing, and revocation tests;
3. all three provider-backed fixture runs pass at one preregistered identity;
4. FPLGuru shared retrieval and explicit writes work while automatic extraction remains private;
5. shadow runs uninterrupted for two weeks and meets every count/safety gate;
6. an explicit auto go/no-go decision is recorded;
7. rollback to off has been exercised successfully;
8. no automatic seed or shadow-backlog publication occurred;
9. any project-lifecycle implementation is backed by a separately approved spec derived from observed evidence.

Until items 1-7 pass, the correct operating state is shared retrieval plus promotion shadow—not automatic promotion.
