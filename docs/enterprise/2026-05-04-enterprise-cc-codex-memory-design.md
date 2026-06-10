# Enterprise CC + Codex Memory Design

Date: 2026-05-04
Branch: `enterprise/cc-codex-enterprise-plan`
Status: review draft

## Goal

Make Memories enterprise-grade for both Claude Code and Codex without risking the deployed production instance.

This is not a generic "add enterprise features" effort. The product goal is:

> Teams should be able to run agent memory across Claude Code and Codex, prove what memory affected an answer, isolate experiments from production, and promote improvements only after reproducible evidence.

The first approved build milestone should be eval isolation and proof infrastructure, not a retrieval tweak. Without isolation, every later eval can contaminate production data or produce false confidence.

## Current Experience

### Claude Code

Claude Code is the most complete integration today.

- MCP exposes memory search, add, extract, delete, list, stats, novelty, feedback, conflicts, and deferred work tools.
- Hooks cover session start, prompt-time recall, Stop extraction, compaction, subagents, tool observation, guardrails, and session-end capture.
- Eval harness runs real `claude -p` A/B tests through MCP with `--strict-mcp-config`.
- Main risk: global Claude Code hooks and auto-memory can still create side effects outside the eval harness unless the eval runtime controls `HOME`, hook config, and cleanup.

### Codex

Codex is real but thinner.

- Repo-local Codex plugin packages the memory discipline skill and setup skill.
- Installer writes Codex hooks, MCP config, read-only tool permissions, and developer instructions.
- Codex has only 5 hook events: `SessionStart`, `UserPromptSubmit`, `Stop`, `PreToolUse`, and `PostToolUse`.
- Stop extraction is intentionally heavier because Codex lacks `PreCompact` and `SessionEnd`.
- Main risk: no Codex eval harness exists at the same maturity as Claude Code, and Codex has less lifecycle surface for high-confidence memory capture.

### Backend

The backend already has many enterprise primitives:

- prefix-scoped auth keys with role tiers
- audit log
- usage tracking and quality metrics
- search explainability
- extraction debug traces
- temporal filters and version links
- graph-aware retrieval
- lifecycle policies
- backups and import/export
- eval datasets and historical result files

The gap is not the absence of primitives. The gap is packaging those primitives into a controlled, provable, team-safe workflow.

## Requirements

| Req | Requirement | Status |
|-----|-------------|--------|
| R0 | Protect the deployed production instance from experiments, evals, and branch work. | Must-have |
| R1 | Support both Claude Code and Codex as first-class enterprise clients. | Must-have |
| R2 | Make evals reproducible and contamination-resistant. | Must-have |
| R3 | Prove why a memory-influenced answer happened. | Must-have |
| R4 | Improve currentness, contradiction handling, and temporal reasoning. | Must-have |
| R5 | Keep self-hosted/local-first deployment viable. | Must-have |
| R6 | Give operators controls for audit, cleanup, and promotion decisions. | Must-have |
| R7 | Keep the first milestone small enough to verify on this machine. | Must-have |
| R8 | Do not implement product behavior until the plan is reviewed and approved. | Must-have |

## Enterprise Gaps

| Area | Gap | Why It Matters |
|------|-----|----------------|
| Product | The product does not yet present a clear enterprise workflow: install, isolate, prove, review, promote. | Enterprise users need operational confidence, not just features. |
| Architecture | Current docs explicitly leave tenant isolation, distributed multi-writer consistency, and ACID vector+metadata semantics out of scope. | This bounds near-term enterprise to single-instance or separated-instance deployments. |
| Reliability | Eval cleanup is prefix-based and URL-dependent. A wrong URL can delete or mutate the wrong instance. | One mistake can corrupt prod or invalidate results. |
| Trust | Search explain and audit exist separately, but agents do not receive a single proof packet. | Operators cannot quickly answer "why did the agent believe this?" |
| Security | Scoped keys exist, but setup can still default to broad/admin paths. | Enterprise default should be least privilege and environment-specific. |
| Eval | Claude Code eval is mature; Codex eval parity is missing. | Product claims must cover both clients. |
| Integration | Codex has fewer lifecycle hooks than Claude Code. | Capture and recall quality can diverge by client. |
| DX | Setup is spread across README, plugin setup, installer, hook env, MCP config, and manual scoped-key notes. | Enterprise onboarding needs a doctor/profile flow. |

## Shapes

### A: Safety-First Enterprise Foundation

Build eval isolation, client parity smoke tests, run manifests, and proof scaffolding before changing memory behavior.

Parts:

- A1: Eval target guard that refuses prod-like URLs, collections, and broad prefixes by default.
- A2: Isolated Claude Code executor with temp `HOME`, strict MCP config, no global hooks, and exact cleanup.
- A3: Isolated Codex executor or hook-level Codex smoke harness with temp `HOME`, temp Codex config, and exact cleanup.
- A4: Run manifest that records branch, commit, URL, Qdrant collection, source prefix, temp dirs, cleanup result, and safety checks.
- A5: Enterprise evidence packet API/MCP in Milestone 2, built only after the isolation harness can prove regressions safely.
- A6: Temporal/currentness planner in Milestone 3, measured against the isolated harness before promotion.

Tradeoff: This delays direct product feature work, but prevents false eval results and production contamination.

### B: Trust Layer First

Build the evidence packet API/MCP first, using current search/explain/audit primitives.

Parts:

- B1: `memory_evidence_packet` MCP tool.
- B2: API endpoint that returns current answer candidates, supporting memories, older superseded versions, conflicts, confidence, and missing-evidence hints.
- B3: Operator UI panel for proof inspection.

Tradeoff: This creates visible product value quickly, but it relies on eval safety that is not yet hard enough.

### C: Temporal Reasoning First

Optimize temporal query planning and contradiction/currentness handling before eval infrastructure.

Parts:

- C1: Query planner for temporal questions.
- C2: Fanout across current, archived, superseded, neighboring session, and date-window retrieval.
- C3: Agent-facing synthesis labels: current, older conflicting evidence, uncertain, follow-up needed.

Tradeoff: This attacks the known product weakness, but without isolated evals it is hard to prove safely on this machine.

## Fit Check

| Req | Requirement | Status | A | B | C |
|-----|-------------|--------|---|---|---|
| R0 | Protect the deployed production instance from experiments, evals, and branch work. | Must-have | PASS | FAIL | FAIL |
| R1 | Support both Claude Code and Codex as first-class enterprise clients. | Must-have | PASS | FAIL | FAIL |
| R2 | Make evals reproducible and contamination-resistant. | Must-have | PASS | FAIL | FAIL |
| R3 | Prove why a memory-influenced answer happened. | Must-have | PASS | PASS | FAIL |
| R4 | Improve currentness, contradiction handling, and temporal reasoning. | Must-have | PASS | FAIL | PASS |
| R5 | Keep self-hosted/local-first deployment viable. | Must-have | PASS | PASS | PASS |
| R6 | Give operators controls for audit, cleanup, and promotion decisions. | Must-have | PASS | PASS | FAIL |
| R7 | Keep the first milestone small enough to verify on this machine. | Must-have | PASS | FAIL | FAIL |
| R8 | Do not implement product behavior until the plan is reviewed and approved. | Must-have | PASS | PASS | PASS |

Notes:

- B fails R0, R1, R2, and R7 because it creates trust-layer product surface before the eval and client-isolation boundary is hard enough.
- C fails R0, R1, R2, R3, R6, and R7 because it attacks the known reasoning weakness before the product can prove results safely across both clients.

Selected shape: **A: Safety-First Enterprise Foundation**.

Reason: it is the only shape that satisfies the user's hard constraints before any product behavior changes. B and C are valuable but should follow after the eval and contamination boundary is hardened.

## First Milestone

Milestone 1: **Enterprise Eval Isolation for Claude Code and Codex**.

Outcome:

- A developer can run a tiny enterprise smoke eval against the eval instance only.
- The run refuses to target the production service on `localhost:8900`.
- The run uses an eval-only Memories URL, source prefix, Qdrant collection, and temp home/config dirs.
- Claude Code and Codex paths are both represented.
- A manifest proves what happened and what was cleaned up.

Explicit non-goals for Milestone 1:

- No retrieval ranking changes.
- No extraction model promotion.
- No production data migration.
- No merge to `main`.
- No use of the current `experiment/shadow-extraction-models` branch.

## Safe Experiment Strategy

Use a fresh worktree from `main`:

```bash
git worktree add /Users/dk/.config/superpowers/worktrees/memories/enterprise-cc-codex-eval-isolation \
  -b enterprise/cc-codex-eval-isolation main
```

Use eval services only:

- Memories URL: `http://localhost:8901`
- Qdrant URL: `http://localhost:6335`
- data dir: `./data-eval`
- source prefix: `eval/enterprise/<run-id>/...`
- collection: `memories_eval_<run-id>` or an eval-only collection declared in the run manifest

Guardrails:

- Refuse `localhost:8900`, `127.0.0.1:8900`, and empty URL unless `MEMORIES_ALLOW_PROD_EVAL=I_UNDERSTAND` is set.
- Refuse broad cleanup prefixes such as `eval/`, `codex/`, `claude-code/`, `learning/`, `wip/`, and empty string.
- Run Claude Code and Codex with temp `HOME` so global hooks/config cannot fire.
- Force MCP/backend config from temp files.
- Save every temp path and cleanup action in the manifest.
- Cleanup only the exact run prefix.

## Acceptance Criteria

Milestone 1 is complete only when all of these are true:

- `docker compose -f docker-compose.eval.yml up -d` can start the eval stack without touching prod containers.
- A guard test proves prod URL `http://localhost:8900` is rejected by default.
- A cleanup test proves only `eval/enterprise/<run-id>/` can be deleted.
- Claude Code eval smoke uses strict MCP config and temp `HOME`.
- Codex eval smoke uses temp Codex config/hooks and does not read global `~/.codex`.
- The manifest records branch, commit, URL, source prefix, collection, temp dirs, client, and cleanup result.
- A dry-run mode prints the planned target and cleanup scope without mutating memory state.
- Tests cover the guardrails.

## Build Gate

No implementation should begin until this design and the Milestone 1 implementation plan are reviewed and approved.

Approval phrase to move into build:

```text
Approved: build Milestone 1 eval isolation.
```
