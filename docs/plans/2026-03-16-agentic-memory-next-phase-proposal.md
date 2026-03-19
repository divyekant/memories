# Memories: Next-Phase Product Proposal

> Created: 2026-03-16
> Status: Proposed
> Scope: What Memories should do next after merged PRs #26 through #33

## Executive Summary

Memories should stop broadening for a while.

The product already has enough surface area to feel powerful: local-first storage,
MCP support, extraction, scoped auth, web UI, CLI, SDK, events, audit, compaction,
feedback, and operator tooling. The problem is no longer lack of features. The
problem is that the core claim is not yet airtight:

> Memories should be the best self-hosted memory layer for agentic systems.

That implies four things:

1. It must be safe to trust in real multi-agent and multi-user environments.
2. It must be easy to debug when memory behavior is wrong.
3. It must be measurable enough to prove it improves agent performance.
4. It must stay self-hosted, practical, and operationally simple.

The next phase should therefore be a focused product-tightening phase, not a feature
expansion phase.

## Original Problem Statement

The original problem is still the right one:

> Build a self-hosted, highly capable tool for agentic memory.

The debugging tools and dashboards are important, but they are not the primary
product. They are supporting machinery that should help an operator answer:

- What did the memory system store?
- Why did it store it?
- What did it retrieve?
- Why did it rank those results?
- Did that help the agent do better?
- Can I trust this behavior with my data and my teams?

That framing is important because it prevents the product from drifting into "memory
platform + dashboard suite + infrastructure toolbox." The product is the memory
layer. The observability exists to make that layer trustworthy and provable.

## Product Thesis

Memories should position itself as:

> The self-hosted memory substrate for serious AI agents.

That means:

- Self-hosted and local-first by default
- MCP-native and API-accessible
- Good enough for real workflows, not toy demos
- Safe enough for scoped/team use
- Inspectable enough that operators can debug decisions
- Measurable enough that teams can prove it works

This is a stronger wedge than "general memory system" or "assistant analytics."

## Primary User

The primary user for the next phase should be:

> An engineer or small infra team running agent workflows and needing persistent,
> inspectable, self-hosted memory.

This user cares about:

- local deployment
- API/MCP ergonomics
- prefix-scoped access
- deterministic debugging
- eval-backed confidence
- not being locked into a hosted black box

This user does not primarily care about:

- elaborate end-user dashboards
- wide plugin ecosystems
- cross-org collaboration features
- distributed deployment sophistication

Those may matter later, but they should not drive the next phase.

## What Success Looks Like

The next phase should be considered successful only if Memories can credibly say:

1. A self-hosting team can deploy it and trust the auth, audit, and mutation paths.
2. An engineer can explain why a memory was written, skipped, retrieved, or ranked.
3. There is a reproducible eval harness showing that Memories improves agent outcomes.
4. The product story is simple enough to explain in one sentence.

If a proposed feature does not materially improve one of those four outcomes, it
should probably not be in the next phase.

## Product Principles For The Next Phase

### 1. Trust before breadth

Every trust boundary bug is more damaging than a missing feature. Auth leaks,
incorrect rollback behavior, missing audit fidelity, and unreliable webhook delivery
directly undermine the product's credibility.

### 2. Explainability over mystery

The product should help operators understand memory behavior without reading source
code or manually stitching together logs.

### 3. Evidence over intuition

Ranking tweaks and extraction changes should be justified by evaluation and quality
signals, not by "it seems better."

### 4. Self-hosted simplicity over distributed ambition

Being excellent as a single-node self-hosted memory layer is more valuable right
now than opening larger architectural fronts.

### 5. Agent workflows over human dashboard polish

The UI should exist to support operator workflows, not to become the center of the
product.

## Recommended Focus Areas

### Focus Area A: Trustworthy Core Memory Behavior

This is the highest-priority area and should be treated as release-blocking work.

The merged review surfaced several correctness and trust issues that must be closed
before adding new major features:

- lock down search-quality endpoints to proper auth scope
- include source on all auth-filtered lifecycle events
- add real rollback for re-embed after destructive migration starts
- preserve webhook delivery from worker-thread event emitters
- fix audit fidelity for admin/env deletes
- fix search-quality metric correctness
- clarify or tighten compaction semantics

This work is not maintenance busywork. It is the difference between "interesting
infra" and "production-capable memory layer."

### Focus Area B: Make Memory Decisions Inspectable

The product currently has operational features, but the main debugging loop is still
too implicit. The next phase should add first-class explainability for the two most
important behaviors:

#### Search explainability

Operators need to see:

- which memories were candidates
- vector score, BM25 score, recency/confidence contributions
- which filters were applied
- which results were removed by auth or post-filtering
- final rank and why

Recommended deliverable:

- `POST /search/explain`
- same input shape as `/search`
- response includes candidate set, component scores, filter decisions, and final rank

This should be operator-facing first. It can power a UI page later, but the API
must exist as the source of truth.

#### Extraction explainability

Operators need to see:

- extracted facts
- similar memories consulted
- selected AUDN action per fact
- why a fact became ADD / UPDATE / DELETE / NOOP / CONFLICT
- actual persisted effect

Recommended deliverable:

- `POST /memory/extract/explain` or `debug=true` mode on extract
- durable per-job trace attached to extraction jobs
- ability to view the trace after completion

This is especially important because extraction quality is one of the main reasons
to adopt a dedicated memory system instead of a naive vector store.

### Focus Area C: Prove Efficacy, Not Just Activity

Memories already has metrics and benchmark work, but the product still risks proving
that it is "busy" rather than proving that it is "useful."

The next phase should define a small set of product-level quality dimensions and
measure them rigorously:

- retrieval precision
- write judgment
- extraction accuracy
- context injection timing

Recommended deliverables:

- a stable eval corpus built around agentic tasks, not just API correctness
- baseline comparisons:
  - no memory
  - naive retrieval only
  - Memories full stack
- CI or scheduled regression runs on the same benchmark set
- one operator-facing quality view that shows trend lines and failure samples

The key principle is that dashboards should show quality outcomes, not just internal
pipeline counters.

Examples of good proof:

- "Memories improved task success on project recall scenarios by X%."
- "Extraction precision stayed above target after prompt changes."
- "Top-3 retrieval precision held under larger stores."

Examples of weak proof:

- "The server handled N requests."
- "The dashboard has more charts."
- "Search feedback volume increased."

### Focus Area D: Tighten The Self-Hosted Operator Experience

The product should feel boring to deploy and operate.

That means the next phase should include targeted operator improvements, but only
the ones that directly support the main wedge:

- clean deployment guidance for the recommended self-hosted path
- reliable backup/restore validation
- consistent CLI/SDK/MCP coverage for core memory workflows
- clear env/config debugging
- load testing used as an engineering guardrail, not a marketing feature

The recent load testing work is useful, but it should stay in service of answering:

- where does single-node write contention become a problem?
- what workloads degrade search latency?
- what is the safe concurrency envelope?

That is engineering truth, not product differentiation by itself.

## Proposed Non-Goals

The next phase should explicitly not focus on:

### 1. More major surface area

Do not add large new product areas unless they directly improve trust, explainability,
or efficacy. Examples to defer:

- plugin systems
- cross-project knowledge sharing
- new maintenance families
- broader UI feature sets
- more event system complexity

### 2. Team platform expansion

Memories has the beginnings of a team story, but the next phase should not optimize
for broad enterprise collaboration features. Get the core safe first.

### 3. Distributed architecture work

Do not chase clustering, high-availability complexity, or sophisticated distributed
coordination until the single-node product is clearly winning for its target user.

### 4. Dashboard proliferation

Do not add more pages simply because metrics exist. Add only the views necessary
to answer operator questions about correctness and efficacy.

## Proposed Plan

## Phase 0: Freeze Breadth, Fix Trust

Goal: no new large features until the core trust issues are closed.

Must-do items:

- fix issues #34 through #40
- add tests for scoped behavior on new endpoints
- add tests for source-less event visibility
- add tests for webhook delivery from threadpool-originated event emits
- add tests for re-embed failure after destructive migration starts

Exit criteria:

- merged review issues are closed
- no known auth-boundary regressions remain in merged memory features
- re-embed has a credible failure story
- audit/event behavior matches product claims

## Phase 1: Build Explainability APIs

Goal: make the memory system debuggable without reading source code.

Must-do items:

- implement `search explain`
- implement extraction trace / extraction explain
- standardize debug payload shape
- add operator docs for how to inspect bad retrieval and bad extraction outcomes

Recommended response model:

- concise summary for humans
- structured details for tooling and future UI use

Exit criteria:

- an engineer can explain one bad retrieval and one bad extraction end-to-end using
  the product, not internal ad hoc debugging

## Phase 2: Build The Quality Proof Layer

Goal: prove that Memories helps agents, not just that it stores and retrieves data.

Must-do items:

- define official benchmark scenarios tied to real memory jobs
- create baseline comparison runs
- publish operator-facing quality reports
- connect search/extraction explainability to failure analysis

Recommended benchmark categories:

- architectural decision recall
- debugging pattern recall
- preference retention
- contradiction handling
- extraction from noisy real conversations
- retrieval under growing memory stores

Exit criteria:

- a repeatable benchmark suite exists
- regressions are detectable
- the product can make concrete efficacy claims

## Phase 3: Polish The Operator Surface

Goal: make the product easy to run once the behavior is trustworthy and measurable.

Must-do items:

- tighten deployment docs around the recommended self-hosted path
- validate backup/restore and disaster-recovery steps
- close CLI/SDK/MCP consistency gaps for core workflows
- make debug flows available from the UI where useful

Exit criteria:

- a small team can deploy, operate, debug, and validate Memories without reading
  internal implementation docs

## Concrete Deliverables

If the next phase were reduced to a short, disciplined list, it should be:

### Release-blocking

- fix review issues #34-#40
- ship stronger tests around auth, events, audit, rollback, and metrics

### Core product

- `/search/explain`
- extraction trace / extraction explain
- benchmark corpus for agentic memory tasks
- quality dashboard centered on efficacy, not just pipeline activity

### Operator support

- backup/restore validation docs
- self-hosted deployment path cleanup
- CLI/SDK/MCP consistency for debug and proof workflows

## What Should Be Deferred

These should wait until the above is done:

- plugin architecture
- cross-project sharing
- broader enterprise/team features
- additional dashboard pages
- new major API families unrelated to core memory correctness or proof

## Recommended Public Narrative

Once this plan is executed, the public story should become:

> Memories is the self-hosted memory layer for serious AI agents: scoped, inspectable,
> eval-backed, and MCP-native.

That is materially stronger than:

- "vector memory plus extraction"
- "assistant memory with dashboards"
- "toolbox for memory workflows"

## Decision Rule For Future Work

Every new proposal should answer:

1. Does this make the memory layer safer to trust?
2. Does this make memory behavior easier to explain?
3. Does this help prove the system improves agent outcomes?
4. Does this make the self-hosted operator experience meaningfully better?

If the answer is "no" to all four, it should not be in the next phase.

## Final Recommendation

The right move now is not to add more product area.

The right move is to turn Memories from a highly capable collection of memory
features into a product with one clear promise:

> self-hosted agentic memory you can trust, inspect, and prove.

That should be the center of the next phase.
