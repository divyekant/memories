# ADR-001: Efficacy Eval Approach

**Date:** 2026-03-03
**Status:** Accepted

## Context

We need to prove that Memories makes AI assistants measurably better — not just that it stores and retrieves data. The question is how to measure this.

## Decision

We chose a **benchmark-led eval harness** that runs controlled A/B tests via Claude Code's programmatic mode (`claude -p`).

### Approach

Each scenario runs twice through the real CC→MCP→Memories pipeline:
1. **Without memories** — baseline (isolated project, no MCP config)
2. **With memories** — seeded with scenario-specific memories (isolated project with `.mcp.json`)

The difference in scores (efficacy delta) quantifies the value Memories adds.

### Key design decisions

1. **Claude Code, not raw API** — Tests run through `claude -p` so they exercise the real MCP tool-calling pipeline, not a synthetic API call.

2. **Full project isolation** — Each run uses a fresh temp directory with no CLAUDE.md, no `.claude/`, and no conversation history to prevent contamination.

3. **Fictional project context** — Scenarios use project-specific, non-generalizable facts (e.g., fictional "Voltis" project with team decisions) to avoid confounds from Claude's training data.

4. **Deterministic + LLM-judged rubrics** — Simple checks (string contains, no retry questions) scored programmatically; complex quality judgments delegated to LLM-as-judge.

5. **Category-weighted aggregation** — coding (40%), recall (35%), compounding (25%) with renormalization for present categories.

## Alternatives Considered

- **Replay-based benchmarking** — Record real sessions, replay with/without memories. Higher fidelity but harder to maintain and slower to iterate.
- **Observational metrics + proxy signals** — Measure live usage patterns (retry rate, search hit rate). Useful but doesn't establish causation. Planned as phase 2.

## Consequences

- Test scenarios must be carefully designed to avoid testing Claude's inherent knowledge
- Running the full eval requires Claude Code CLI installed and a running Memories instance
- LLM-as-judge introduces non-determinism in scoring; mitigated by structured output format
