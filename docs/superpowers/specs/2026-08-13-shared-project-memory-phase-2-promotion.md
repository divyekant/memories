---
shaping: true
---

# Shared Project Memory — Phase 2 Promotion and Reconciliation

## Status

Scope converged in PR #97 review on 2026-08-14. This revision encodes that
agreement and is ready for exact-head consistency review together with the
Phase 2 implementation plan. It authorizes implementation after that review;
it does not authorize FPLGuru activation or change the adopted Phase 1 access
boundary.

Phase 2 is intentionally limited to automatic promotion from person-private
memory into an already-authorized shared project namespace, plus the recovery,
audit, maintenance exclusions, evaluation, and operator controls needed to
make that automation safe. Production activation remains a separately gated
rollout decision.

## Source

> My concern is bigger any work done between phase 1 and 2 would leave gaps in
> shared memories because i dont expect explicit visibility management to work
> out. Keep in mind the agent doesnt actually have any instructions to do so.

> lets talk more on the automatic visibility promotion piece - i dont feel we
> should use deterministic approach here and see of miniLM or our extraction
> provider can help

> show me basic working flow and expmple run a few real life dirty tests and
> then lets see?

## Relationship to Phase 1

Phase 1 established the enforceable boundary:

- `person/<principal>/<project>/<kind>` is private to one authenticated
  principal;
- `project/<project>/<kind>` is shared with principals whose managed keys
  authorize that exact project;
- automatic extraction writes only to the current person's namespace;
- shared writes require server-side project authorization and trusted
  authorship;
- deduplication, replacement, consolidation, conflict resolution, and search
  cannot cross policy domains accidentally;
- missing or invalid collaborative context fails closed.

Phase 2 does not weaken any of those rules. A classifier may propose a shared
destination, but it cannot grant access, choose a different project, spoof an
author, or bypass the existing project-write validator.

## Problem

Explicit project writes are an important escape hatch but a poor primary
capture mechanism. Agents may omit them, users should not need to manage
visibility for every durable fact, and the result would be a private-memory gap
between collaborators even though both are working in the same project.

The opposite failure is worse: a broad automatic rule can disclose personal
preferences, unverified opinions, interpersonal assessments, credentials, or
unfinished ideas. Topic relevance alone is not shareability. A technically
project-related sentence may still be explicitly private or too tentative to
present as shared knowledge.

Phase 2 therefore needs semantic judgment without making model output an
authorization decision. It must prefer missed sharing over accidental
disclosure when evidence is incomplete, while providing a recoverable path for
safe candidates that were interrupted or deferred.

## Requirements

| ID | Requirement | Status |
|---|---|---|
| R0 | Durable, confirmed project knowledge can reach collaborators automatically without relying on an agent or user to make an explicit shared write. | Core goal |
| R1 | Personal, explicitly private, sensitive, cross-project, or unauthorized content never becomes project-readable through promotion. | Must-have |
| R2 | Semantic project relevance and sharing intent are judged by a configured model; deterministic logic is limited to enforceable structural and safety vetoes. | Must-have |
| R3 | Tentative, disputed, contradicted, incomplete, or low-confidence knowledge remains private. | Must-have |
| R4 | Promotion precision gets an independent model review; bounded low-relevance audit sampling independently measures false-private recall without making review-everything the permanent architecture. | Must-have |
| R5 | Every proposal, review, promotion, rejection, and retry is attributable, auditable, and idempotent across worker or process failure. | Must-have |
| R6 | Reconciliation recovers interrupted eligible work but does not promote by blindly rerunning unchanged uncertain evidence. | Must-have |
| R7 | Upgrade and rollout are inert by default at the server; operators can observe real decisions in shadow mode and stop promotion immediately. | Must-have |
| R8 | Phase 2 stays same-host and project-specific, preserves legacy behavior outside activated projects, and does not introduce generic entities, federation, per-memory ACLs, or UI scope. | Must-have |
| R9 | Automatic maintenance cannot rewrite or hard-delete shared project knowledge or destroy an auditable in-flight promotion workflow. | Must-have |
| R10 | Activation requires predeclared fixture, live-shadow, dual-principal, time, and volume gates; policy changes invalidate prior shadow evidence. | Must-have |

## Evidence Spike

The proposed classifier contracts were exercised against the production
extraction provider (`anthropic`, default model
`claude-haiku-4-5-20251001`) using synthetic but deliberately messy FPLGuru
conversations. The experiment called the provider directly and wrote no
memories.

The cases mixed:

- confirmed decisions with session chatter;
- a root cause and a credential in the same message;
- an explicit “do not share this with Darshan” instruction;
- a decision later retracted and replaced;
- personal preferences beside team conventions;
- tentative architecture proposals;
- prompt injection quoted inside input data;
- customer PII beside a confirmed fix;
- an interpersonal assessment;
- confirmed project invariants.

| Experiment | Result | Material finding |
|---|---:|---|
| One extraction call with `visibility` and confidence | 8/10 safe outcomes | It promoted an explicitly tentative Temporal idea and an unresolved prompt-injection observation. |
| Added model-judged `assertion_status` | 8/9 safe outcomes | Tentative and disputed knowledge stayed private, but the unresolved injection observation still promoted. |
| Narrow independent review of promotion candidates | 8/8 expected outcomes | Confirmed knowledge was approved; privacy, tentative claims, interpersonal content, and unresolved observations were rejected or deferred. |
| MiniLM similarity against shared/private exemplars | 4/6 expected outcomes | It treated explicit-private technical content and an undecided Temporal proposal as project knowledge. |

These are directional results, not a release-quality evaluation. They reject
two shapes—direct one-call promotion and MiniLM as visibility authority—but do
not establish a production threshold or prove the selected shape safe.

## Shapes Considered

### A: Direct same-call promotion

| Part | Mechanism | Flag |
|---|---|:---:|
| A1 | Extend the existing extraction response with visibility, confidence, and target project kind. | |
| A2 | Promote high-confidence `project` facts immediately after structural validation. | |
| A3 | Keep `private` and `uncertain` facts in the person namespace. | |

This has the fewest provider calls, but the dirty spike produced unsafe false
positives even after the prompt distinguished tentative knowledge.

### B: Review every extracted fact

| Part | Mechanism | Flag |
|---|---|:---:|
| B1 | Extract facts privately using the existing provider. | |
| B2 | Send every fact through a separate visibility reviewer. | |
| B3 | Promote reviewer-approved facts after structural validation. | |

This gives the reviewer a narrow task but doubles provider work even for
obviously personal or non-project facts.

### C: Private-first selective review

| Part | Mechanism | Flag |
|---|---|:---:|
| C1 | The extraction call proposes project relevance, visibility, assertion status, confidence, and one of the four existing project kinds. | |
| C2 | Every extracted fact is first committed to the authenticated person's exact project namespace with server-owned proposal state in the same memory add. | |
| C3 | Server-owned gates reject invalid context, authorization, schema, secrets, PII, and raw-transcript-shaped output before a proposal can advance. | |
| C4 | Plausible project candidates enter a narrow asynchronous promotion review. A fixed, bounded audit floor also reviews low-relevance candidates to measure false-private recall; below the floor all eligible low-relevance candidates are sampled. | |
| C5 | The reviewer sees the candidate plus the current extraction context and returns `approve`, `reject`, or `defer`; output is still subject to project authorization and shared-memory reference data is explicitly untrusted. | |
| C6 | Approval creates one sanitized shared record with trusted authorship and provenance, then archives the active private candidate. | |
| C7 | Candidate state on the private memory plus an entity lock and source-memory lookup make promotion retries idempotent. | |
| C8 | The existing maintenance scheduler reconciles interrupted candidates and deferred candidates with genuinely new evidence, while promotion-state retention prevents maintenance from destroying workflow state. | |
| C9 | Server `off`, `shadow`, and `auto` caps plus an explicit repository project setting control rollout without changing authorization. | |
| C10 | MiniLM supports similarity, duplicate detection, and prioritization only; it never decides visibility. | |

### D: MiniLM visibility authority

| Part | Mechanism | Flag |
|---|---|:---:|
| D1 | Compare each extracted fact with shared-project and private exemplars. | |
| D2 | Promote when shared-project similarity exceeds private similarity and a threshold. | |
| D3 | Use the extraction provider only for facts near the threshold. | |

MiniLM is useful for topical similarity but does not reliably understand
negation, privacy intent, tentative state, or interpersonal sensitivity. The
dirty spike reproduced that limitation.

## Fit Check

| Req | Requirement | Status | A | B | C | D |
|---|---|---|:---:|:---:|:---:|:---:|
| R0 | Durable, confirmed project knowledge can reach collaborators automatically without relying on an agent or user to make an explicit shared write. | Core goal | ✅ | ✅ | ✅ | ✅ |
| R1 | Personal, explicitly private, sensitive, cross-project, or unauthorized content never becomes project-readable through promotion. | Must-have | ❌ | ✅ | ✅ | ❌ |
| R2 | Semantic project relevance and sharing intent are judged by a configured model; deterministic logic is limited to enforceable structural and safety vetoes. | Must-have | ✅ | ✅ | ✅ | ❌ |
| R3 | Tentative, disputed, contradicted, incomplete, or low-confidence knowledge remains private. | Must-have | ❌ | ✅ | ✅ | ❌ |
| R4 | Promotion precision gets an independent model review, while routing remains configurable so later higher-volume deployments can bound provider cost without weakening the review gate. | Must-have | ❌ | ❌ | ✅ | ❌ |
| R5 | Every proposal, review, promotion, rejection, and retry is attributable, auditable, and idempotent across worker or process failure. | Must-have | ✅ | ✅ | ✅ | ✅ |
| R6 | Reconciliation recovers interrupted eligible work but does not promote by blindly rerunning unchanged uncertain evidence. | Must-have | ✅ | ✅ | ✅ | ✅ |
| R7 | Upgrade and rollout are inert by default at the server; operators can observe real decisions in shadow mode and stop promotion immediately. | Must-have | ✅ | ✅ | ✅ | ✅ |
| R8 | Phase 2 stays same-host and project-specific, preserves legacy behavior outside activated projects, and does not introduce generic entities, federation, per-memory ACLs, or UI scope. | Must-have | ✅ | ✅ | ✅ | ✅ |
| R9 | Automatic maintenance cannot rewrite/delete project knowledge or destroy promotion workflow state. | Must-have | ❌ | ❌ | ✅ | ❌ |
| R10 | Activation requires versioned fixture and live-shadow gates, and policy changes invalidate prior evidence. | Must-have | ❌ | ✅ | ✅ | ❌ |

**Notes:**

- A fails R1 and R3 in the dirty spike and has no independent review for R4.
- B fails R4 because it has no selective routing control when volume later
  makes reviewing every fact materially expensive. FPLGuru deliberately starts
  permissively and may review nearly every plausible candidate during shadow.
- D fails R1–R4 because embedding similarity is not a sharing-intent or
  assertion-state classifier.
- A, B, and D do not specify the Phase 2 maintenance exclusion and activation
  evidence required by R9 and R10.

Shape C is selected.

## Detailed Flow

```text
active collaborative extraction
  -> effective mode off: run the unchanged Phase 1 private extraction path
  -> effective mode shadow/auto:
     extraction provider returns durable facts + proposal fields
  -> structural preflight validates exact private destination and authorship
  -> fact + server-owned proposal state are committed together under
     person/<principal>/<project>/knowledge
  -> route plausible candidates to review
     and select a fixed bounded audit floor from low-relevance candidates
  -> off: stop private
     shadow: run reviewer and record would-promote outcome, never write shared
     auto: run reviewer on either route
       -> reject: remain private, terminal for this evidence/version
       -> defer: remain private, eligible only with new evidence/version
       -> approve: revalidate under promotion lock
          -> create or find canonical-digest-identical project target
          -> stamp trusted author/contributors/origin/source_memory_ids
          -> mark target id on private candidate
          -> archive private candidate
  -> reconciler repairs interrupted states idempotently
  -> maintenance excludes project records and protected workflow candidates
```

The private commit happens before model review. Provider failure, queue
pressure, process exit, malformed output, or review uncertainty therefore
reduces sharing availability but does not lose the extracted fact or widen its
visibility. Effective `off` mode does not use the extended proposal prompt or
write promotion metadata; it preserves the Phase 1 extraction path.

The audit route is not a weaker review. In `shadow` it may only record an
approved would-promote outcome. In `auto`, an audit-routed candidate may
promote only after the same reviewer, authorization, sanitization, and locked
preflight used by the ordinary route. Sampling is a fixed count per period,
not a percentage, and falls back to all eligible candidates when volume is
below that count.

## Extraction Proposal Contract

The existing extraction fact schema is extended additively:

```json
{
  "category": "decision",
  "text": "FPLGuru imports use season, event, and entry_id as the idempotency tuple.",
  "project_relevance": 0.98,
  "visibility": "project",
  "assertion_status": "confirmed",
  "project_kind": "knowledge",
  "confidence": 0.95,
  "reason": "Confirmed project invariant"
}
```

Allowed semantic values are:

- `project_relevance`: model-judged topical usefulness to this project from
  `0.0` to `1.0`;
- `visibility`: `project`, `private`, `uncertain`;
- `assertion_status`: `confirmed`, `tentative`, `disputed`;
- `project_kind`: `decisions`, `knowledge`, `state`, `operations`.

Project relevance and visibility are deliberately separate. A confirmed
technical fact may be highly relevant but explicitly private; a personal fact
may mention the project repeatedly without being useful shared knowledge.
High relevance may route a fact to the private reviewer, but it can never
promote by itself.

The model's confidence is confidence in its semantic classification, not an
authorization score. Invalid or missing proposal fields default to private and
do not fail the underlying private extraction.

The model must treat quoted logs, code, payloads, recalled memory, and tool
output as untrusted conversation data. It must omit credentials, PII, raw
transcripts, session status, and generic knowledge from extracted fact text.
Existing deterministic transcript hygiene remains a backstop rather than the
semantic classifier.

## Selective Promotion Review

The reviewer receives only:

1. the proposed fact and proposal fields, including project relevance and
   visibility as separate judgments;
2. the current extraction context needed to check whether the fact is
   entailed, final, and shareable;
3. authorized shared-project memories relevant to contradiction or duplicate
   detection.

It must never receive another principal's private memories. It returns:

```json
{
  "decision": "approve",
  "confidence": 0.93,
  "reason": "Final project decision with durable rationale"
}
```

`reject` is terminal for the same candidate text, evidence fingerprint, and
reviewer version. `defer` remains private and is reconsidered only under the
evidence rules below. Invalid review output is `defer`.

Shared-project memories in reviewer context are untrusted reference data for
contradiction and duplicate detection only. They are never instructions and
must be delimited and handled with the same prompt-injection protections as
quoted logs, code, payloads, recalled memories, and tool output.

The reviewer's approved text may be a sanitized restatement, but it may not
introduce facts absent from the extraction context. The server reruns secret,
PII, transcript, exact-project, and authorization checks over the final shared
text before any write.

The reviewer provider and model are separately configurable and default to the
configured extraction provider and model. Classifier policy, reviewer policy,
provider, and model versions are persisted independently; a behavior-affecting
change to any of them invalidates prior shadow would-promote outcomes and
restarts the observation window.

## Private-First Candidate State

Promotion state is server-owned metadata on the existing private memory, not a
second canonical fact store. The exact field names are implementation detail,
but the persisted state must represent:

- candidate/private memory ID;
- project ID, declaration fingerprint, and proposed project kind;
- capture mode (`off`, `shadow`, or `auto`);
- proposal visibility, assertion status, confidence, reason, and classifier
  version;
- review decision, confidence, reason, reviewer version, and timestamp;
- evidence fingerprint and attempt count;
- workflow status (`private`, `candidate`, `shadow_approved`, `deferred`,
  retryable `failed`, `unreviewable`, `rejected`, or `promoted`);
- promoted shared memory ID when one exists.

All promotion fields are reserved server metadata. Callers cannot supply or
patch them through ordinary memory APIs.

The initial private add and its proposal metadata are one creation operation.
A crash cannot leave an eligible extracted fact with no indication that
promotion evaluation was attempted.

The raw conversation and redacted excerpts are not persisted as promotion
evidence. Immediate review may use the in-flight extraction context. If the
process loses that context before a semantic decision can be made, the
candidate becomes `unreviewable`: private, queryable, alerted, and protected
from automatic maintenance. The reconciler may not reconstruct certainty from
the fact text alone. Only new evidence or an explicit owner/admin decision can
leave `unreviewable`.

Maintenance retention is state-specific:

- `private` means the proposal was not selected for review and follows the
  ordinary private-memory lifecycle;
- `candidate`, `shadow_approved`, `deferred`, and retryable `failed` are
  protected from automatic consolidation and pruning;
- `rejected` is protected for a configurable audit window, default 90 days,
  then returns to the ordinary private-memory lifecycle while its decision
  remains in the audit log;
- `promoted` is archived and remains protected by the existing archive rule;
- `unreviewable` has no automatic expiry and stays protected until an
  owner/admin approves, rejects, or dismisses it, or new evidence produces a
  real review decision.

## Idempotent Promotion

Promotion uses an entity lock keyed by the private candidate ID and re-reads
the candidate under that lock. Before adding a shared record, it checks for an
existing project memory whose server-owned provenance identifies that private
candidate.

The safe mutation order is:

1. validate the still-active private candidate, authenticated principal,
   project, final text, and target kind;
2. find or create the exact `project/<project>/<kind>` record with trusted
   authorship and `source_memory_ids=[<private_id>]`;
3. persist the shared target ID and terminal promotion status on the private
   record;
4. archive the private record.

If the process exits after step 2, reconciliation finds the existing shared
record by its source memory ID, finishes metadata, and archives the private
candidate. It does not create a second project record. Archiving before the
shared add is prohibited because a crash would create a recall gap.

The shared record contains sanitized shared text and server-owned provenance;
it does not copy private review rationale or extraction context.

## Existing Shared Knowledge

Promotion is add-or-reuse, not an automatic shared-memory rewrite mechanism.
Before creating a target, the server searches only the exact authorized
`project/<project>/<kind>` namespace:

- if no canonical-digest-identical shared memory exists, create one;
- if a canonical-digest-identical active shared memory exists, reuse it, append the current
  principal as a contributor, append the private candidate ID to server-owned
  source provenance, and archive the private candidate;
- semantic similarity may be shown to the reviewer and counted as a
  near-duplicate, but it never archives, merges, updates, or suppresses the
  candidate;
- if the candidate would contradict, delete, or supersede existing shared
  knowledge, defer it for owner/admin review rather than mutating the shared
  record automatically.

Canonicalization is deliberately narrow: normalize Unicode to NFC, normalize
line endings, trim outer and per-line whitespace, and collapse repeated spaces
or tabs. Preserve case, punctuation, token order, and line order. The SHA-256
digest covers project ID, project kind, and canonical text. This makes
whitespace-only duplicates reusable without turning semantic similarity into
a destructive equivalence decision.

Reusing a shared record preserves its original author while adding the new
contributor. Phase 2 does not let automatic extraction UPDATE or DELETE shared
project knowledge. Rich project knowledge lifecycle and automated
supersession remain Phase 3 concerns. Semantically similar approved facts may
therefore coexist as separate shared records; that duplicate cost is explicit
and measured.

## Reconciliation

The existing maintenance scheduler is extended with a bounded promotion pass;
Phase 2 does not add a new service. Each pass has a configurable batch size and
time budget so it cannot starve extraction or existing maintenance.

It may act as follows:

| Candidate state | Reconciliation action |
|---|---|
| `candidate` or retryable `failed` with current in-flight evidence available | Resume review or promotion. |
| Shared target exists but private finalization is incomplete | Finish metadata and archive the private candidate. |
| `shadow_approved` under the current accepted policy after both modes become `auto` | Revalidate and promote idempotently. |
| `deferred` with changed private text, explicit owner approval, newly relevant shared-project evidence, or a newer reviewer policy/version **and sufficient evidence** | Re-review. |
| `deferred` with unchanged evidence and reviewer version | Do nothing. |
| `rejected` with unchanged evidence and reviewer version | Do nothing. |
| `unreviewable` without new evidence or manual decision | Do nothing; retain and include it in the new-item rate alert and aged-backlog signal. |
| `promoted` | Verify target linkage only; never create another target. |

New evidence may use the candidate owner's updated private record and
authorized shared-project memories. It may not use another principal's private
memory. Mere passage of time or rerunning the same prompt is not new evidence.

The pass revalidates current managed-key identity and exact project write
authority before any shared mutation. Revoked access leaves the candidate
private. Reconciliation is bounded by both batch size and wall-clock budget and
uses per-candidate locks; one stuck provider call cannot consume the entire
maintenance window. The wall-clock budget is authoritative and the batch size
is only an upper bound: a pass stops before the batch is exhausted whenever
the deadline is reached.

The host cap and declared mode gate only work that would initiate a new review
or create/reuse a shared target. Crash-finalization repair for an already
created shared target, and linkage verification for an already promoted
candidate, continue while the cap is `off`; these operations finish previously
authorized work and never create new shared knowledge.

## Automatic Maintenance Boundary and Phase 1 Prerequisite

Phase 1 currently permits weekly pruning to hard-delete stale, never-retrieved
project records. Before any FPLGuru seed or activation, a standalone hotfix
must exclude **every source beginning with `project/`** from automatic pruning,
including malformed/grandfathered paths such as `project/notes`. This protects
records users reasonably understand as shared while leaving ordinary
`person/...` private-memory pruning unchanged. It is a release prerequisite,
not something activation may wait for Phase 2 to fix.

Phase 2 additionally excludes strict `project/<project>/<kind>` records from
automatic consolidation for the entire phase. Existing LLM consolidation may
rewrite meaning and cannot represent claim-level contributor provenance;
unioning contributors onto synthesized text would falsely imply that every
person supported every clause. Project semantic consolidation, supersession,
and lifecycle management are deferred to Phase 3.

Private promotion candidates follow the state-specific maintenance retention
rules above. Manual and scheduled consolidation/pruning must use the same
central predicate so an operator endpoint cannot bypass protections enforced
by the scheduler.

## Configuration and Rollout Control

Phase 2 extends the strict repository declaration:

```yaml
project_id: fplguru
shared_memory: true
promotion:
  mode: shadow
```

Allowed project modes are `off`, `shadow`, and `auto`. For an explicitly
collaborative project, omitted `promotion` means `off` in the first Phase 2
release. `shadow` or `auto` therefore requires an explicit declaration.
Unknown keys and unsupported values fail closed. A later release may revisit
the default only after production evidence exists.

The declaration is not authority. It cannot grant a project prefix, name a
principal, or override the host's cap. The backend applies an operator-owned
global cap, defaulting to `off` in the first Phase 2 release:

```text
PROJECT_PROMOTION_MODE=off|shadow|auto
```

The first release also exposes bounded operator settings. The relevance
threshold intentionally has no production default: the fixture evaluator must
report routing rates at candidate thresholds, and the operator selects a
deliberately permissive shadow value from that measured distribution before
raising the host cap:

```text
PROJECT_PROMOTION_RELEVANCE_THRESHOLD=
PROJECT_PROMOTION_NEAR_DUPLICATE_THRESHOLD=0.88
PROJECT_PROMOTION_AUDIT_FLOOR=10
PROJECT_PROMOTION_AUDIT_PERIOD_DAYS=7
PROJECT_PROMOTION_RECONCILE_BATCH=25
PROJECT_PROMOTION_RECONCILE_BUDGET_SECONDS=20
PROJECT_PROMOTION_REJECTED_RETENTION_DAYS=90
PROJECT_PROMOTION_UNREVIEWABLE_RATE_COUNT=5
PROJECT_PROMOTION_UNREVIEWABLE_RATE_WINDOW_HOURS=1
PROJECT_PROMOTION_UNREVIEWABLE_BACKLOG_AGE_HOURS=168
PROJECT_PROMOTION_REVIEW_PROVIDER=
PROJECT_PROMOTION_REVIEW_MODEL=
```

An empty review-provider or review-model value inherits the configured
extraction provider or model respectively. A missing relevance threshold is
valid while the host cap is `off`, but `shadow` or `auto` fails closed until a
measured threshold is configured.

FPLGuru shadow starts with a deliberately permissive relevance threshold so
every plausibly durable project fact is reviewed. Tightening it later is an
operator configuration change gated by fixture recall and audit-route drift
evidence, not a redesign.

Effective behavior is the more restrictive of the host cap and the declared
project mode. This makes an upgrade inert until the host operator opts in,
allows a repository to reduce its own automation, and provides an immediate
kill switch without changing keys or history.

When the effective mode is `off`, clients and the backend use the existing
Phase 1 extraction request and response contract. Merely upgrading the binary
does not change extracted facts, add proposal metadata, call the reviewer, or
enqueue reconciliation work.

The extraction request carries the resolved declaration mode and project ID.
That value is untrusted policy input, not authorization; the managed key and
exact server-side namespace checks remain decisive. The mode and declaration
fingerprint are captured on each candidate so worktree or branch divergence is
auditable rather than silently reinterpreted later.

The classifier prompt/policy, reviewer prompt/policy, provider, and model have
explicit version identifiers. Changing any behavior-affecting identifier
invalidates prior shadow would-promote outcomes and restarts both the minimum
time and volume observation gates. Stale shadow approvals are never promoted;
if their evidence is no longer available they become `unreviewable`.

Phase 1 clients reject the new declaration field, so operators must upgrade
the backend and supported clients before committing `promotion` configuration.

## Shadow Mode as the Canary

Shadow mode runs extraction, private storage, structural gates, and selective
review but never creates a shared record. It records the reviewer decision and
the exact result that would have been promoted.

The shadow candidates remain recoverable. When the host and project move to
`auto`, reconciliation may promote candidates that:

- were approved in shadow by the currently accepted reviewer policy;
- still pass exact authorization and final text validation;
- have not been rejected, changed, archived for another reason, or superseded.

This prevents the observation window itself from creating a permanent shared
knowledge gap.

Shadow is mutation-free with respect to shared project state. Both ordinary
and audit-routed approvals are stored only as would-promote outcomes. In
`auto`, an audit-routed approval may promote after the same full review and
locked authorization path as an ordinary candidate.

The proposed FPLGuru rollout is:

1. merge and deploy the standalone `project/` pruning hotfix;
2. deploy Phase 2 with the host cap `off`;
3. provision and verify the two managed principal keys;
4. activate the repository declaration with `promotion.mode: shadow` and set
   the host cap to `shadow`;
5. inspect every would-promote outcome and the bounded audit cohort, with both
   principals represented;
6. if any unsafe candidate would promote, change policy/provider/prompt,
   invalidate all prior outcomes, and restart the observation window;
7. remain in shadow for at least two weeks and until every fixture and live
   volume gate below is satisfied;
8. set the host cap and repository to `auto`;
9. reconcile only shadow approvals from the currently accepted policy;
10. seed the separately reviewed 20–50 historical shared memories described by
   the Phase 1 playbook.

The small synthetic spike is not enough to activate `auto`. Two quiet weeks do
not satisfy the rollout gate.

## Review and Audit Surface

Phase 2 does not add dashboard UI. It defines these owner/admin API operations
before any manual review behavior can ship:

- `GET /promotions` lists candidates by project, owner, state, and date;
- `GET /promotions/{candidate_id}` fetches one candidate and its model
  decisions;
- `POST /promotions/{candidate_id}/approve` approves one's own private
  candidate when the caller also has write access to the target project;
- `POST /promotions/{candidate_id}/reject` rejects one's own candidate or
  explicitly dismisses one's own `unreviewable` candidate, with an audited
  reason;
- the same operations allow an admin to inspect or decide any candidate;
- deny one collaborator access to another collaborator's private candidate
  text and rationale;
- query audit events for proposal, review, defer, reject, promote, recovery,
  and manual decision.

MCP parity, if manual review is exposed through MCP in Phase 2, uses the names
`memory_promotions`, `memory_promotion_get`, `memory_promotion_approve`, and
`memory_promotion_reject`; the two mutating tools remain user-confirmed writes.
The API operations and authorization rules are Phase 2 acceptance criteria.
No manual promotion workflow may ship until these API authorization rules
exist, even if MCP parity is deferred.

There is no bulk dismissal endpoint in Phase 2. An owner may script the
per-item audited reject operation during an incident; a bulk mutation API is
deferred until real volume demonstrates that it is needed. Unreviewable
operations expose two distinct signals so degraded review infrastructure
cannot create silent permanent debt: a page-worthy rate alert for at least
five newly unreviewable candidates within one hour, and an informational
aged-backlog signal when any unresolved item reaches seven days.

## MiniLM's Role

MiniLM remains useful for:

- finding similar shared project memories before review;
- duplicate and supersession candidates within the exact project namespace;
- prioritizing plausible project candidates for bounded review capacity;
- detecting newly relevant shared-project evidence for deferred candidates;
- offline evaluation and drift analysis.

It is not allowed to turn a private or uncertain memory into a shared memory by
itself. Similarity does not understand “do not share,” retraction, tentative
state, or interpersonal sensitivity, and the dirty spike demonstrated those
failures directly.

## Failure Semantics

| Failure | Required result |
|---|---|
| Missing/invalid project declaration, ambiguous backend, unmanaged key, missing principal, or revoked project access | Preserve Phase 1 fail-closed behavior; no promotion candidate or shared write. |
| Extraction provider unavailable | Existing configured private extraction fallback applies; no automatic promotion. |
| Proposal fields malformed | Store the valid extracted fact privately with no promotion. |
| Secret, PII, or raw-transcript marker in candidate/final text | Reject promotion; retain only text allowed by existing private extraction hygiene. |
| Review provider unavailable, timeout, malformed output, or low confidence while evidence remains in flight | Defer privately and retry only within the bounded evidence lifetime. |
| Review evidence is lost before a semantic decision | Mark `unreviewable`, retain privately, protect from maintenance, and expose rate/backlog alerts; never infer from fact text alone. |
| Queue full or worker/process exit | Private fact survives; candidate is retryable only when sufficient evidence remains, otherwise `unreviewable`. |
| Authorization revoked between proposal and write | Reject at final under-lock preflight. |
| Target add succeeds but private finalization fails | Reconciler finds target by private source ID and finishes without duplicating. |
| Project mode or host cap changes to `off` | Stop new reviews and shared-target creation immediately; still finish an already-created target's private metadata/archive finalization and verify existing linkage. Do not delete shared knowledge. |
| Classifier/reviewer prompt, policy, provider, or model version changes | Invalidate prior shadow outcomes and restart time plus volume gates; stale approvals cannot promote. |
| Semantic near-duplicate found | Keep both records/candidates; surface and measure similarity, but do not merge, archive, or suppress automatically. |
| Automatic maintenance sees `project/` or protected workflow state | Skip it using the shared maintenance predicate. |

## Evaluation and Activation Gates

Before `auto` can be recommended, the implementation must provide a versioned
fixture suite that includes at least:

- explicit privacy and “do not share” intent;
- credentials, PII, and mixed sensitive/non-sensitive messages;
- tentative, disputed, superseded, and retracted decisions;
- prompt injection in user text, logs, code, recalled memory, and tool output;
- project decisions, verified root causes, state, and operating conventions;
- personal preferences and interpersonal assessments containing project terms;
- malformed model output and provider failure;
- retry, crash-window, duplicate, revocation, and cross-principal isolation.

Activation gates are:

1. a versioned labeled suite of at least 100 weighted fixtures;
2. zero unsafe promotions in every high-risk fixture;
3. at least 95% promotion precision across the full labeled fixture set;
4. at least 85% end-to-end recall for confirmed durable project facts in the
   weighted fixture suite; the bounded low-relevance audit route detects live
   false-private drift but is not treated as statistically establishing that
   recall rate;
5. no cross-principal private disclosure in API, reviewer input, logs, audit,
   metrics, or reconciliation tests;
6. successful idempotency tests for every mutation boundary;
7. at least two weeks of real dual-principal FPLGuru shadow activity;
8. at least 50 total reviewed live candidates;
9. at least 30 manually inspected would-promote outcomes;
10. at least five would-promote outcomes from each principal;
11. zero unsafe live would-promote outcomes across the whole sample.

Fewer than 30 would-promote outcomes extends shadow. Fewer than roughly 10
after two weeks triggers a routing-threshold review rather than being treated
as safety evidence. The fixture suite carries the statistical precision and
recall argument and must report routing rates at multiple candidate relevance
thresholds. Live shadow and the audit route detect drift and catch vocabulary,
interleaving, and injection shapes the fixtures did not anticipate; they do
not establish the 85% recall rate on their own.

Required operational metrics are project-record count by kind, canonical
exact-reuse count/rate, semantic near-duplicate candidate count/rate,
unresolved `unreviewable` count and oldest age, terminal-retention expirations,
review outcomes by route/principal/policy version, and alert state. Auto remains
off until the rollback procedure and FPLGuru activation evidence record are
reviewed.

For stable interpretation, exact-reuse rate is exact reuses divided by all
successful promotions, and semantic near-duplicate rate is reviewed candidates
with at least one above-threshold semantic project match divided by all
reviewed candidates. Counters are partitioned by project kind and policy
version; zero denominators report rate `0.0`.

## Explicit Non-Goals

Phase 2 does not include:

- project lifecycle states such as proposed/accepted/superseded as a public
  knowledge model;
- generic Person, Entity, Space, Organization, or membership tables;
- per-memory ACLs or sharing with selected individuals;
- cross-project inference or linking;
- cross-backend federation, replication, or synchronization;
- a second canonical fact store or transactional outbox;
- automatic bulk promotion of legacy history;
- a web dashboard or review UI;
- a bulk promotion-review mutation endpoint;
- model training or a MiniLM classification head;
- changing the four project kinds;
- semantic consolidation, supersession, or claim-level contributor provenance
  for project knowledge;
- production FPLGuru activation in the specification PR.

`assertion_status` is classifier evidence used to fail private; it is not the
Phase 3 project knowledge lifecycle.

## Resolved Scope Decisions

PR #97 review resolved the former reviewer questions as follows:

1. The reviewer is a separate narrow call. Its provider/model is independently
   configurable and defaults to the extraction provider/model.
2. Candidate metadata on the private record is the sole durable workflow
   state; Phase 2 adds no queue/outbox or second canonical fact store.
3. No transcript or redacted evidence excerpt is persisted. Lost evidence
   becomes queryable, alerted `unreviewable` state and requires new evidence or
   an owner/admin decision.
4. The first release requires explicit `promotion.mode`; omission is off even
   when `shared_memory: true`.
5. Repository configuration is untrusted project policy input. The server cap,
   managed principal, and exact ACL remain authority, and the declaration
   fingerprint is captured for branch/worktree auditability.
6. The minimum review surface is list, get, approve, and reject/dismiss.
   Collaborators can inspect/decide only their own private candidates; admins
   can inspect/decide all.
7. Deferred work reopens only for changed private text, explicit owner/admin
   action, genuinely new authorized shared-project evidence, or a new accepted
   policy with sufficient evidence. Passage of time is not evidence.
8. The fixture and live gates are the exact counts and thresholds in the
   preceding section.
9. Selective routing is retained, but FPLGuru begins permissively and a fixed
   low-relevance audit floor independently measures recall. Shadow audit
   approvals never mutate shared state; auto audit approvals may promote only
   through the same full review path.
10. Automatic reuse is canonical-digest equality only. Semantic similarity is
    untrusted reviewer context and a metric, never a destructive equivalence
    decision.
11. Project semantic lifecycle is deferred to Phase 3. Phase 2 accepts
    duplicates and excludes project records from automatic consolidation.
12. Every `project/`-prefixed source, including malformed legacy paths, is
    protected from pruning by a standalone pre-activation hotfix.
13. `unreviewable` degradation-rate and aged-backlog signals ship in Phase 2; bulk dismissal is deferred
    in favor of scriptable per-item audited decisions.

## Acceptance Criteria for the Future Implementation

The implementation is not complete until:

1. Phase 1 legacy and inactive-project suites remain unchanged and green.
2. Proposal parsing is additive and malformed fields fail private.
3. All automatic extraction commits the private record before review.
4. Only the selective reviewer can propose an automatic shared write, and the
   existing server authorization boundary remains final.
5. Shared text is sanitized, attributed to the authenticated principal, linked
   to the private source ID, and never exposes private rationale or transcript.
6. Promotion, retry, revocation, and target-finalization races are covered by
   red-first tests.
7. Candidate review APIs enforce owner/admin visibility and target write
   authority.
8. Shadow mode creates no project records and accepted shadow candidates can be
   reconciled once auto is enabled only when their full policy/provider/prompt
   version remains current.
9. Reconciliation is bounded, idempotent, same-host, and evidence-aware.
10. MiniLM cannot independently change visibility in any production path.
11. The global cap defaults off and disabling it stops new promotion without
    deleting prior shared knowledge.
12. Canonical-digest equality is the only automatic shared-record reuse path;
    semantic near-duplicates coexist and are measured.
13. Scheduled and manual automatic pruning skip every `project/`-prefixed
    source, and automatic consolidation skips strict project memories.
14. Promotion-state private memories follow the specified per-state retention
    rules, including non-expiring `unreviewable` and 90-day rejected audit
    retention by default.
15. Unreviewable rate/backlog signals and all specified promotion/duplicate/project
    metrics are exposed without leaking candidate text.
16. The versioned 100+ labeled dirty suite, live-shadow evidence record,
    operational metrics, rollback procedure, and FPLGuru activation playbook
    are documented before release.
17. No production seed, repository activation, or host-cap increase occurs as
    part of implementation or the specification PR.
