# Memories: Functional Scope Proposal

> Created: 2026-03-18
> Status: Proposed
> Scope: Functional product priorities after the trust and explainability follow-up work

## Why This Doc Exists

The previous proposal narrowed the product thesis:

> Memories should be the self-hosted memory layer that teams can trust, inspect, and prove.

That still stands.

What this document adds is a stronger product-surface opinion:

> The UI is not optional polish. It is how the product becomes legible, actionable, and real.

If Memories is only an API plus metrics, operators will understand it intellectually but
not feel the product working. The next functional phase should therefore treat the UI as a
first-class operator workbench, not as a passive dashboard.

## Product Goal

Build the best self-hosted memory workbench for serious agent systems.

That means a team should be able to:

- see what the system remembered
- understand why it remembered it
- understand why it retrieved something
- correct memory behavior when it is wrong
- prove the system is helping agent workflows
- operate it safely in a self-hosted environment

## Core Product Thesis

Memories should not feel like:

- a vector store with extra endpoints
- a dashboard suite with memory attached
- a pile of clever hooks and background automations

It should feel like:

> a memory system with a visible, controllable lifecycle

That lifecycle is the product:

1. information enters through extraction, add, import, or agent workflows
2. the system decides what to store, update, skip, link, or mark conflicting
3. retrieval surfaces the memory back to an agent or operator
4. operators inspect, correct, reinforce, merge, or remove memories
5. the product shows whether those decisions improved real outcomes

## Functional North Star

The next phase should optimize for five user-visible outcomes.

### 1. Legibility

A user can look at a memory and understand:

- where it came from
- why it exists
- how strong it is
- what it links to
- whether it is decaying
- what it superseded or conflicts with

### 2. Control

A user can intervene when memory behavior is wrong without dropping to raw APIs or logs.

### 3. Retrieval Confidence

A user can understand why a memory ranked where it did and whether retrieval quality is
actually improving.

### 4. Proof

A user can see evidence that Memories improves agent workflows, not just that it is
collecting events.

### 5. Self-Hosted Trust

A user can operate the system safely through backup, migration, auth, and recovery flows
without fearing silent corruption or invisible regressions.

## Product Surface Model

Memories should be designed as three tightly connected surfaces.

### 1. Memory Engine

This is the source of truth:

- storage
- retrieval
- extraction
- auth
- audit
- hooks
- evaluation data

### 2. Agent Integration Layer

This is how memory participates in real workflows:

- MCP tools
- HTTP API
- Claude/Codex/Cursor hooks
- context hydration and extraction triggers

### 3. Operator Workbench UI

This is how the product becomes understandable and usable:

- inspect memory lifecycle
- review failures
- resolve conflicts
- replay retrievals
- review extractions
- operate the system safely

Important product rule:

> APIs remain the source of truth, but the UI should be the default operator experience.

## The Right Functional Focus

The right next phase is not "more features."

It is turning recent infrastructure and debugging investments into coherent operator
workflows.

That means the product should focus on these six functional pillars.

## Pillar 1: Memory Lifecycle Workbench

This should become the center of the product.

Today the product can show memories. Next it should show the full lifecycle of a memory
as an object that can be understood and managed.

### What the user should be able to do

- open any memory and see source, confidence, decay state, timestamps, links, conflict state, and supersession history
- see how the memory entered the system: manual add, extraction, import, hook, or consolidation
- inspect what changed over time
- pin or protect canonical memories
- archive or deprecate stale memories without destroying history
- undo destructive actions where possible

### Required UI moves

- evolve the current memory detail panel into a true lifecycle panel
- show relationships and lineage as first-class concepts, not metadata fragments
- show action history in human terms, not only raw audit rows

### Why this matters

If a user cannot understand a memory as a living object, the product never feels more
real than a search result list.

## Pillar 2: Retrieval Workbench

Retrieval is where the product promise is won or lost.

The current explainability direction is good, but it should become an investigation flow,
not just an endpoint and tooltip.

### What the user should be able to do

- run a query and inspect why each result ranked where it did
- see vector, BM25, recency, confidence, and any post-filtering effects
- compare the visible result set with filtered or rejected candidates
- replay known bad searches
- submit and review feedback tied to actual search sessions
- inspect retrieval failures over time

### Required UI moves

- make the Memories page the primary retrieval workbench
- keep search explain inline and actionable, not tucked away in admin-only corners
- add a saved failure/replay concept for queries worth revisiting

### Why this matters

Users believe in agentic memory when retrieval feels earned, not mysterious.

## Pillar 3: Extraction Review Inbox

Extraction should stop feeling like a black box.

The current extraction debug work is the right foundation. The next step is to turn it
into a reviewable workflow.

### What the user should be able to do

- inspect extracted facts from a run
- inspect the similar memories consulted for each fact
- inspect the chosen AUDN action per fact
- see the actual persisted effect: add, update, delete, noop, or conflict
- re-run or compare extraction behavior when prompts/models/config change
- review suspicious runs: high noop, high conflict, high delete, low add

### Required UI moves

- turn the Extractions page into a review inbox, not just a status page
- add per-job trace viewing and lightweight triage actions
- elevate "why did this noop?" and "why did this become a conflict?" into normal user flows

### Why this matters

A self-hosted memory product needs to help users trust write judgment, not just read
results.

## Pillar 4: Conflict and Relationship Management

Conflicts and links should become a real feature area, not just metadata plus a few
actions.

### What the user should be able to do

- see unresolved conflicts as a queue of real decisions
- compare both sides with context
- keep one, keep both, merge, or defer
- inspect why the conflict was created
- browse related memories and follow chains of reasoning
- distinguish link types in meaningful ways

### Required UI moves

- keep Health as the cross-memory issue view
- improve conflict resolution from a one-shot action into a structured decision flow
- make relationship browsing useful for investigation, not decorative

### Why this matters

Agent memory is valuable when it can preserve ambiguity without becoming chaos.

## Pillar 5: Proof And Regression Management

This is where the dashboards should matter.

The product should not add more charts unless they support a concrete operator decision.

### What the user should be able to do

- see a small number of quality metrics that map to product value
- inspect concrete failures behind those metrics
- compare benchmark runs over time
- detect regressions after model, prompt, or retrieval changes
- understand whether memory is improving task outcomes, not just activity volume

### The right proof surfaces

- task-level benchmark outcomes
- top-k retrieval usefulness over time
- extraction quality by scenario
- regression alerts for benchmark drops
- curated failure examples with replay paths

### The wrong proof surfaces

- charts that only show request volume
- charts that celebrate internal activity without user value
- dashboard breadth without investigation depth

### Why this matters

Metrics should answer "is Memories helping?" not just "is Memories busy?"

## Pillar 6: Self-Hosted Operator Control

Self-hosting is part of the wedge, so operator flows are user-facing functionality.

### What the user should be able to do

- verify deployment health quickly
- inspect hook status and integration coverage
- manage keys and scopes with confidence
- run backup, restore, and reembed workflows safely
- understand what changed after config updates
- recover from failure without guessing

### Required product stance

- operator flows should be explicit and inspectable
- destructive operations should feel safe and reversible
- docs, UI, and API should agree on what the system version and capabilities actually are

### Why this matters

The moment a self-hosted operator fears migrations or silent regressions, the product
loses its main strategic advantage.

## UI Strategy

The UI should now be treated as the main expression of the product, not a sidecar.

But that does not mean adding pages carelessly.

The right move is to turn the current UI into a coherent workbench with clearer jobs per
surface.

### Dashboard

Purpose:
- fast status and orientation

Should answer:
- is the system healthy?
- where should I look next?
- are there urgent conflicts or failures?

Should not become:
- a dense analytics wall

### Memories

Purpose:
- primary retrieval and inspection workspace

Should answer:
- what does the system know?
- why did this result show up?
- how do I inspect and correct a memory?

This should become the most-used page in the product.

### Extractions

Purpose:
- write-path review workspace

Should answer:
- what did the system try to remember?
- why did it choose those actions?
- which extraction runs need attention?

### Health

Purpose:
- issue investigation and quality review

Should answer:
- where are failures or conflicts accumulating?
- are quality trends improving or regressing?
- what needs operator attention now?

### Settings / Operations

Purpose:
- self-hosted trust surface

Should answer:
- are hooks installed and working?
- are keys and scopes configured correctly?
- are backups, reembed, and maintenance flows safe?

This can be a new page or a clearer operator section, but the function matters more than
the page count.

## Recommended Release Sequence

To keep scope disciplined, the next work should land in three product-tightening releases.

## Release 1: Controllable Memory

Goal:
- make memory objects and write decisions inspectable and editable

Functional priorities:
- richer memory detail lifecycle view
- extraction trace review in the UI
- stronger conflict resolution flows
- relationship browsing that supports investigation
- safe corrective actions: pin, archive, supersede, resolve, dismiss

This release should make users feel:

> "I can see what Memories is doing and I can intervene."

## Release 2: Retrieval Confidence

Goal:
- make retrieval understandable, replayable, and tunable

Functional priorities:
- better search explain integration
- replayable failed searches
- tighter feedback workflows
- saved retrieval cases
- failure views tied to concrete result sets

This release should make users feel:

> "I understand why this result showed up, and I can improve it."

## Release 3: Proof And Operator Confidence

Goal:
- prove efficacy and make self-hosted operation feel safe

Functional priorities:
- benchmark and regression views tied to real workflows
- quality trend surfaces tied to failure samples
- operator controls for hooks, keys, backups, and reembed
- explicit recovery and migration affordances
- consistency across docs, API, UI, and release/version surfaces

This release should make users feel:

> "This system is not just clever. It is dependable."

## What To De-Prioritize

The following should stay out of the near-term core scope unless they directly strengthen
the six pillars above.

- broad plugin ecosystem expansion
- collaboration and sharing features
- distributed cluster ambitions
- extra dashboard pages without operator workflows
- novelty features that are hard to inspect or correct
- autonomous behavior that increases magic while decreasing control

## Functional Acceptance Criteria

The next phase should only be considered successful if Memories can credibly demonstrate
all of the following.

### Operator experience

- an operator can investigate a bad memory outcome without reading source code
- an operator can correct wrong memory behavior through the UI
- an operator can inspect both retrieval and extraction reasoning

### Product clarity

- the UI clearly expresses what Memories knows, why it knows it, and how it is behaving
- the main user journeys are obvious: inspect, search, review, resolve, operate

### Product proof

- quality views connect directly to failure samples and replay paths
- benchmark/regression flows are tied to agentic use cases, not vanity numbers

### Self-hosted trust

- destructive maintenance flows feel safe
- release/version/config surfaces are internally consistent
- auth and scope boundaries remain legible and testable

## Final Recommendation

Memories should now aim to become:

> the self-hosted memory workbench for serious agent teams

That wording matters because it integrates the engine and the experience.

The storage and retrieval core remains essential, but the product only becomes convincing
when users can see the lifecycle, feel the control, and trust the outcomes.

So the next functional phase should not ask:

> what else can Memories do?

It should ask:

> how do we make memory behavior visible, correctable, and provably useful?

That is the path from powerful infrastructure to a real product.

---

## Addendum: Product Deep Dive (2026-03-18)

> Added after a full codebase audit across engine, UI, hooks, MCP, and CLI.
> Extends the six pillars above with seven cross-cutting product problems.

### The 7 Product Problems Worth Solving

#### Problem 1: Extraction Is the Highest-Leverage, Least-Controllable Part

Extraction decides what gets remembered. It is the write gate for the entire product.
Users have almost zero control over it.

**What is wrong:**

- The AUDN prompt is hardcoded — no per-project customization
- The 30-day test heuristic is one-size-fits-all (a data scientist needs different retention
  logic than a DevOps engineer)
- No extraction policies (always remember API contracts, never remember temp debugging state)
- No way for a user to say "you should have remembered this" after the fact
- Two-call architecture (fact extraction then AUDN decision) doubles cost and latency with
  no partial mode
- `EXTRACT_MAX_FACTS=30` and `EXTRACT_MAX_FACT_CHARS=500` are global — no source-level tuning

**What this costs:**

- Users cannot trust the write path because they cannot shape it
- Memory quality is locked to prompt engineering quality — no user feedback loop
- The product feels like a black box on the most critical path

**What good looks like:**

- Extraction profiles per source prefix (aggressive capture for code projects, conservative
  for learning notes)
- User-definable extraction rules per project
- A "missed memory" flow — user flags something that should have been captured, system learns
- Single-call extraction mode for cost-sensitive deployments
- Extraction dry-run: show what would be extracted before committing

#### Problem 2: Recall Timing Is an Agent-Side Gap, Not a Server-Side Gap

The server does search and extraction well. The gap is in when the agent decides to search.

**What is wrong:**

- Hooks inject context passively — if the search query misses, prior work stays invisible
- No proactive surfacing of deferred or incomplete work at session start
- Claude Code does not automatically check for unfinished work on the current topic
- `memory-query.sh` fires on every prompt but uses a best-effort query built from transcript
  snippets — no semantic understanding of intent
- PostCompact cannot inject `additionalContext` — it can only hydrate MEMORY.md, which is a
  weaker signal

**What this costs:**

- Users repeat questions they have already answered in prior sessions
- Deferred work gets forgotten unless the user explicitly asks
- The system knows things but fails to surface them at the right moment

**What good looks like:**

- Intent-aware recall: classify the prompt type (question, new work, resuming, debugging)
  and adjust search strategy accordingly
- Proactive deferred-work surfacing: on session start, search `wip/{project}` for incomplete
  threads and surface them before the user asks
- Smarter query construction: use project context, file being edited, and prompt intent —
  not just raw transcript text
- A "what do I know about this?" flow that the agent triggers when entering a new domain

#### Problem 3: The Cold Start Problem Blocks Adoption

A new user installs Memories, gets nothing. An empty store with no guidance on how to populate it.

**What is wrong:**

- No bootstrap flow for existing projects (import from README, docs, CLAUDE.md, decision logs)
- No guided onboarding
- No template memory sets for common project types
- Import exists but is NDJSON-only — no way to ingest unstructured docs
- The value proposition requires days of accumulated sessions before the system feels useful

**What this costs:**

- Time-to-value is measured in days or weeks, not minutes
- Users who try Memories for a day and do not see results churn
- The product cannot demonstrate value in a demo or trial

**What good looks like:**

- `memories bootstrap` — scans project for README, docs, CLAUDE.md, decision logs, and
  extracts seed memories
- First-session guided extraction from existing documentation
- Template packs for common scenarios (onboarding, incident investigation, code review context)
- Import from markdown, not just NDJSON

#### Problem 4: Multi-Project Isolation Is a Prefix Convention, Not a Product Feature

Source prefixes are the only isolation mechanism. This works for a single developer but breaks
down for teams or multi-project workflows.

**What is wrong:**

- No true project boundaries — a misconfigured hook can write memories to the wrong prefix
- No project-level settings (extraction rules, retention policies, decay rates)
- No project-level dashboards — the UI shows everything in one flat view
- Source filter on the Memories page is a dropdown, not a project-scoped workspace
- API keys can be prefix-scoped, but that is auth, not project isolation
- No way to say "these 3 prefixes are the same project"

**What this costs:**

- Teams cannot share a Memories instance safely
- Cross-project contamination is silent
- The source filter UX makes multi-project feel like an afterthought

**What good looks like:**

- First-class project entity with name, prefixes, settings, and access rules
- Project selector in the UI (not a filter — a workspace switch)
- Per-project extraction policies, retention rules, and decay rates
- Project-level quality metrics and health views

#### Problem 5: The Feedback Loop Does Not Close

Search feedback (useful / not_useful) exists as a signal, but nothing acts on it.

**What is wrong:**

- Feedback is collected but never influences ranking
- No way to see memories that were surfaced repeatedly but never marked useful
- No way to see queries that consistently return poor results
- No automatic quality improvement — feedback is stored but inert
- No way to see feedback history or retract bad feedback

**What this costs:**

- Users stop giving feedback because nothing visibly improves
- The quality promise (retrieval improves over time) is not real yet
- Competitive memory systems will close this loop first

**What good looks like:**

- Feedback-weighted retrieval: memories with consistent not_useful signals get demoted
- Problem queries view: queries where feedback is consistently negative
- Stale memories view: memories that surface often but never get positive signal
- Monthly quality report with actionable insights
- Feedback influencing extraction behavior over time

#### Problem 6: Memory Lifecycle Has No Policies

Confidence decay exists but has no teeth. Nothing happens when a memory decays to near-zero.

**What is wrong:**

- No TTL on memories — they live forever unless manually deleted
- No auto-archive after N days without access
- No source-level retention policies
- Confidence can decay to 0.01 but the memory still surfaces in search
- No distinction between cold-but-potentially-useful and genuinely stale
- Compaction and consolidation are manual-only — no scheduled maintenance
- No capacity planning

**What this costs:**

- Memory stores bloat over time
- Search quality degrades as noise accumulates
- Operators have no tools to maintain memory hygiene at scale
- The confidence system is a display feature, not a lifecycle mechanism

**What good looks like:**

- Retention policies per source prefix (wip memories expire after 30 days)
- Auto-archive: memories below 0.1 confidence that have not been accessed in 90 days
  get archived (reversible)
- Scheduled compaction: weekly merge of redundant memories
- Capacity dashboard: active, decaying, archived counts with cleanup suggestions
- Confidence thresholds that actually affect retrieval

#### Problem 7: The UI Is Read-Only — It Is a Viewer, Not a Workbench

The UI has no write-path at all.

**What is wrong:**

- Cannot create memories from the UI
- Cannot create links between memories from the UI (API exists, UI does not expose it)
- Cannot trigger extraction from the UI
- Cannot edit memory text from the UI
- Cannot merge two memories from the UI
- Cannot import from the UI
- Cannot pin, archive, or tag from the UI
- The only write actions are Delete and Conflict Resolution (keep A / keep B)
- Settings page has Export and Rebuild — both destructive, neither creative

**What this costs:**

- Operators who want to curate memories must use CLI or API
- The workbench vision requires actual tool affordances, not just inspection
- The product feels like a monitoring dashboard attached to a memory engine

**What good looks like:**

- Inline memory editing (click to edit text, source, tags)
- Add memory from the UI with source and category selection
- Drag-to-link: connect related memories visually
- Merge flow: select two or more memories, preview consolidated version, confirm
- Bulk actions: select multiple, archive, delete, retag, re-source
- Extraction trigger: paste conversation text, run extraction, review results,
  approve or reject per fact

### Priority Stack

| Priority | Problem | Rationale |
|----------|---------|-----------|
| P0 | Extraction controllability | Highest leverage — controls what enters the system. Without this, everything downstream is bounded by prompt quality. |
| P0 | UI write-path | The workbench vision requires write affordances. Ship this with Release 1 or the workbench label is premature. |
| P1 | Recall timing (agent-side) | This is the user-facing quality gap. Server is good; the integration layer needs intent-awareness. |
| P1 | Feedback loop closure | Feedback without action erodes trust. Even a simple demote-low-signal-memories would be transformative. |
| P2 | Cold start | Adoption blocker. Bootstrap flow would dramatically shorten time-to-value. |
| P2 | Lifecycle policies | Scaling blocker. Not urgent at 1K memories, critical at 10K plus. |
| P3 | Multi-project isolation | Team adoption blocker. Not urgent for single-developer use. |

### How This Maps to the Release Plan

**Release 1 (Controllable Memory)** — add:

- Extraction profiles and per-source rules (P0)
- UI write-path: create, edit, link, merge (P0)
- Missed memory capture flow

**Release 2 (Retrieval Confidence)** — add:

- Intent-aware recall in hooks (P1)
- Feedback influencing ranking (P1)
- Problem queries and stale memories views

**Release 3 (Proof and Operator Confidence)** — add:

- Lifecycle policies and auto-archive (P2)
- Cold start bootstrap flow (P2)
- Capacity planning dashboard
- Project isolation foundations (P3)

### The One-Liner Test

If a user installs Memories and uses it for one week, can they answer these questions?

| Question | Today | After improvements |
|----------|-------|--------------------|
| What does my memory system know? | Partially (list view) | Yes (workbench with lifecycle) |
| Why did it remember this? | No | Yes (extraction trace plus rules) |
| Why did it surface that? | Barely (admin-only tooltip) | Yes (explain inline) |
| How do I fix wrong memories? | Delete only | Edit, merge, archive, retag |
| Is it getting better over time? | No | Yes (feedback-driven quality trends) |
| What should I be working on? | No (passive recall) | Yes (proactive deferred-work surface) |

The product thesis — self-hosted memory workbench for serious agent teams — becomes real
when all six answers are yes.
