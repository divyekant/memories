---
shaping: true
---

# Shared Project Memory — Phase 2 Promotion and Reconciliation

## Status

Proposed for architectural review on 2026-08-13. This document specifies a
candidate scope; it does not authorize implementation or change the adopted
Phase 1 boundary.

Phase 2 is intentionally limited to automatic promotion from person-private
memory into an already-authorized shared project namespace, plus the recovery,
audit, and operator controls needed to make that automation safe. The final
scope will be selected after review of this proposal.

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
| R4 | Promotion precision gets an independent model review without paying a second provider call for every extracted fact. | Must-have |
| R5 | Every proposal, review, promotion, rejection, and retry is attributable, auditable, and idempotent across worker or process failure. | Must-have |
| R6 | Reconciliation recovers interrupted eligible work but does not promote by blindly rerunning unchanged uncertain evidence. | Must-have |
| R7 | Upgrade and rollout are inert by default at the server; operators can observe real decisions in shadow mode and stop promotion immediately. | Must-have |
| R8 | Phase 2 stays same-host and project-specific, preserves legacy behavior outside activated projects, and does not introduce generic entities, federation, per-memory ACLs, or UI scope. | Must-have |

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
| C4 | Only plausible project candidates enter a narrow asynchronous promotion review, selected by model-judged project relevance rather than visibility alone; ordinary non-project facts incur no second call. | |
| C5 | The reviewer sees the candidate plus the current extraction context and returns `approve`, `reject`, or `defer`; output is still subject to project authorization. | |
| C6 | Approval creates one sanitized shared record with trusted authorship and provenance, then archives the active private candidate. | |
| C7 | Candidate state on the private memory plus an entity lock and source-memory lookup make promotion retries idempotent. | |
| C8 | The existing maintenance scheduler reconciles interrupted candidates and deferred candidates with genuinely new evidence. | |
| C9 | Server `off`, `shadow`, and `auto` caps plus the repository project setting control rollout without changing authorization. | |
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
| R4 | Promotion precision gets an independent model review without paying a second provider call for every extracted fact. | Must-have | ❌ | ❌ | ✅ | ❌ |
| R5 | Every proposal, review, promotion, rejection, and retry is attributable, auditable, and idempotent across worker or process failure. | Must-have | ✅ | ✅ | ✅ | ✅ |
| R6 | Reconciliation recovers interrupted eligible work but does not promote by blindly rerunning unchanged uncertain evidence. | Must-have | ✅ | ✅ | ✅ | ✅ |
| R7 | Upgrade and rollout are inert by default at the server; operators can observe real decisions in shadow mode and stop promotion immediately. | Must-have | ✅ | ✅ | ✅ | ✅ |
| R8 | Phase 2 stays same-host and project-specific, preserves legacy behavior outside activated projects, and does not introduce generic entities, federation, per-memory ACLs, or UI scope. | Must-have | ✅ | ✅ | ✅ | ✅ |

**Notes:**

- A fails R1 and R3 in the dirty spike and has no independent review for R4.
- B fails R4 because every fact pays for a second provider call.
- D fails R1–R4 because embedding similarity is not a sharing-intent or
  assertion-state classifier.

Shape C is the proposed selection, pending review.

## Detailed Flow

```text
active collaborative extraction
  -> effective mode off: run the unchanged Phase 1 private extraction path
  -> effective mode shadow/auto:
     extraction provider returns durable facts + proposal fields
  -> structural preflight validates exact private destination and authorship
  -> fact + server-owned proposal state are committed together under
     person/<principal>/<project>/knowledge
  -> off: stop private
     shadow: run reviewer and record would-promote outcome, stop private
     auto: run reviewer
       -> reject: remain private, terminal for this evidence/version
       -> defer: remain private, eligible only with new evidence/version
       -> approve: revalidate under promotion lock
          -> create or find exact project target
          -> stamp trusted author/contributors/origin/source_memory_ids
          -> mark target id on private candidate
          -> archive private candidate
  -> reconciler repairs interrupted states idempotently
```

The private commit happens before model review. Provider failure, queue
pressure, process exit, malformed output, or review uncertainty therefore
reduces sharing availability but does not lose the extracted fact or widen its
visibility. Effective `off` mode does not use the extended proposal prompt or
write promotion metadata; it preserves the Phase 1 extraction path.

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

The reviewer's approved text may be a sanitized restatement, but it may not
introduce facts absent from the extraction context. The server reruns secret,
PII, transcript, exact-project, and authorization checks over the final shared
text before any write.

## Private-First Candidate State

Promotion state is server-owned metadata on the existing private memory, not a
second canonical fact store. The exact field names are implementation detail,
but the persisted state must represent:

- candidate/private memory ID;
- project ID and proposed project kind;
- capture mode (`off`, `shadow`, or `auto`);
- proposal visibility, assertion status, confidence, reason, and classifier
  version;
- review decision, confidence, reason, reviewer version, and timestamp;
- evidence fingerprint and attempt count;
- terminal status (`rejected`, `promoted`) or recoverable status (`candidate`,
  `deferred`, `failed`);
- promoted shared memory ID when one exists.

All promotion fields are reserved server metadata. Callers cannot supply or
patch them through ordinary memory APIs.

The initial private add and its proposal metadata are one creation operation.
A crash cannot leave an eligible extracted fact with no indication that
promotion evaluation was attempted.

The raw conversation is not persisted as promotion evidence. Immediate review
may use the in-flight extraction context. If the process loses that context
before review, the candidate stays private and records a recoverable failure;
the reconciler may not reconstruct certainty from the fact text alone.

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

- if no equivalent shared memory exists, create one;
- if an equivalent active shared memory exists, reuse it, append the current
  principal as a contributor, append the private candidate ID to server-owned
  source provenance, and archive the private candidate;
- if the candidate would update, contradict, delete, or supersede existing
  shared knowledge, defer it for owner/admin review rather than mutating the
  shared record automatically.

Reusing a shared record preserves its original author while adding the new
contributor. Phase 2 does not let automatic extraction UPDATE or DELETE shared
project knowledge. Rich project knowledge lifecycle and automated
supersession remain Phase 3 concerns.

## Reconciliation

The existing maintenance scheduler is extended with a bounded promotion pass;
Phase 2 does not add a new service. Each pass has a configurable batch size and
time budget so it cannot starve extraction or existing maintenance.

It may act as follows:

| Candidate state | Reconciliation action |
|---|---|
| `candidate` or retryable `failed` with current in-flight evidence available | Resume review or promotion. |
| Shared target exists but private finalization is incomplete | Finish metadata and archive the private candidate. |
| `deferred` with changed private text, explicit owner approval, newly relevant shared-project evidence, or a newer reviewer policy/version | Re-review. |
| `deferred` with unchanged evidence and reviewer version | Do nothing. |
| `rejected` with unchanged evidence and reviewer version | Do nothing. |
| `promoted` | Verify target linkage only; never create another target. |

New evidence may use the candidate owner's updated private record and
authorized shared-project memories. It may not use another principal's private
memory. Mere passage of time or rerunning the same prompt is not new evidence.

## Configuration and Rollout Control

Phase 2 extends the strict repository declaration:

```yaml
project_id: fplguru
shared_memory: true
promotion:
  mode: shadow
```

Allowed project modes are `off`, `shadow`, and `auto`. For an explicitly
collaborative project, omitted `promotion` defaults to `auto`, preserving the
product decision that automatic promotion is the normal project behavior once
the feature is enabled. Unknown keys and unsupported values fail closed.

The declaration is not authority. It cannot grant a project prefix, name a
principal, or override the host's cap. The backend applies an operator-owned
global cap, defaulting to `off` in the first Phase 2 release:

```text
PROJECT_PROMOTION_MODE=off|shadow|auto
```

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

The proposed FPLGuru rollout is:

1. deploy Phase 2 with the host cap `off`;
2. provision and verify the two managed principal keys;
3. activate the repository declaration with `promotion.mode: shadow` and set
   the host cap to `shadow`;
4. inspect a minimum review set drawn from both principals, including every
   high-risk and every would-promote candidate;
5. correct prompts/policy and repeat if any unsafe candidate would promote;
6. set the host cap and repository to `auto`;
7. reconcile accepted shadow candidates;
8. seed the separately reviewed 20–50 historical shared memories described by
   the Phase 1 playbook.

The minimum shadow sample size and release metric are review questions below;
the small synthetic spike is not enough to activate `auto`.

## Review and Audit Surface

Phase 2 does not add dashboard UI. It defines these owner/admin API operations
before any manual review behavior can ship:

- `GET /promotions` lists candidates by project, owner, state, and date;
- `GET /promotions/{candidate_id}` fetches one candidate and its model
  decisions;
- `POST /promotions/{candidate_id}/approve` approves one's own private
  candidate when the caller also has write access to the target project;
- `POST /promotions/{candidate_id}/reject` rejects one's own private candidate;
- the same operations allow an admin to inspect or decide any candidate;
- deny one collaborator access to another collaborator's private candidate
  text and rationale;
- query audit events for proposal, review, defer, reject, promote, recovery,
  and manual decision.

MCP parity, if manual review is exposed through MCP in Phase 2, uses the names
`memory_promotions`, `memory_promotion_get`, `memory_promotion_approve`, and
`memory_promotion_reject`; the two mutating tools remain user-confirmed writes.
The API operations and authorization rules are Phase 2 acceptance criteria.
“Review mode” is not an accepted project mode until they exist.

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
| Review provider unavailable, timeout, malformed output, or low confidence | Defer privately. |
| Queue full or worker/process exit | Private fact survives; candidate is retryable only when sufficient evidence remains. |
| Authorization revoked between proposal and write | Reject at final under-lock preflight. |
| Target add succeeds but private finalization fails | Reconciler finds target by private source ID and finishes without duplicating. |
| Project mode or host cap changes to `off` | Stop new reviews/promotions immediately; do not delete existing shared knowledge. |

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

Proposed activation gates for review are:

1. zero unsafe promotions in the high-risk fixture suite;
2. at least 95% promotion precision across the full labeled fixture set;
3. at least 85% recall for confirmed durable project facts;
4. no cross-principal private disclosure in API, reviewer input, logs, audit, or
   reconciliation tests;
5. successful idempotency tests for every mutation boundary;
6. a manually inspected FPLGuru shadow sample from both principals with zero
   unsafe would-promote outcomes.

The final fixture count, shadow sample size, and acceptable recall target remain
open to review. Privacy and authorization gates are not negotiable thresholds.

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
- model training or a MiniLM classification head;
- changing the four project kinds;
- production FPLGuru activation in the specification PR.

`assertion_status` is classifier evidence used to fail private; it is not the
Phase 3 project knowledge lifecycle.

## Reviewer Questions

Review should focus on these unresolved or high-risk decisions:

1. Is a second call only for plausible promotion candidates enough independent
   scrutiny when it uses the same configured provider/model, or must the
   reviewer support a separately configured provider?
2. Does private-memory metadata provide enough durable candidate state without
   introducing a queue/outbox, especially across the target-add/finalization
   crash window?
3. Is it correct to refuse automatic retry when the in-flight transcript was
   lost, rather than persist a redacted evidence excerpt?
4. Does “project default auto, host default off” satisfy backward compatibility
   for existing Phase 1 declarations, or should auto require a new explicit
   declaration field in the first release?
5. Is the repository declaration an acceptable project-level policy input when
   different branches/worktrees can temporarily declare different modes?
6. Which exact review operations belong in the minimum implementation, and may
   collaborators manually approve only their own private candidates?
7. What evidence besides text change, explicit approval, shared-project
   similarity, or reviewer-version change may legitimately re-open a deferred
   candidate?
8. What fixture size and real shadow sample are sufficient before enabling
   FPLGuru `auto` without a separate canary environment?
9. Does routing high-project-relevance facts to the reviewer recover enough
   false-private proposals without effectively reviewing every technical fact,
   and how should that recall/cost tradeoff be measured?

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
   reconciled once auto is enabled.
9. Reconciliation is bounded, idempotent, same-host, and evidence-aware.
10. MiniLM cannot independently change visibility in any production path.
11. The global cap defaults off and disabling it stops new promotion without
    deleting prior shared knowledge.
12. The labeled dirty suite, operational metrics, rollback procedure, and
    FPLGuru activation playbook are documented before release.
