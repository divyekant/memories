# Memories Efficacy Measurement — Design

**Date:** 2026-03-03
**Status:** Approved
**Approach:** Eval Harness (benchmark-led), then live observational metrics

## Problem

We need to prove that Memories makes AI agents more effective — not just that it stores and retrieves data, but that having memories produces measurably better outcomes than not having them.

## Success Signals

1. **Agents make fewer mistakes** — avoid repeating errors, follow established patterns
2. **Context carries across sessions** — no re-explaining project setup, preferences, decisions
3. **Knowledge compounds over time** — better coverage, less noise, more useful retrievals as it grows

## Approach

**Phase 1: Eval Harness** — structured test cases run with and without memories, scored against rubrics. Produces hard numbers: "Memories makes agents X% more effective."

**Phase 2: Live Metrics** — use benchmark findings to define thresholds for observational proxy metrics, exposed via `/efficacy` endpoint and WebUI dashboard.

## Test Categories

### Coding Tasks (40% weight)

| Scenario | Seeded Memory | Measured |
|----------|--------------|----------|
| Fix bug using known pattern | Prior fix for same bug class | Applies pattern directly vs. trial-and-error |
| Implement feature following conventions | Coding style, file structure, naming | Output matches project patterns |
| Avoid known pitfall | "Don't use X library, it breaks on Y" | Avoids pitfall without being told |
| Use correct API endpoint | Internal API docs | Calls right endpoint first try |
| Follow established architecture | Architecture decisions, module boundaries | Places code in correct location |

### Knowledge Recall (35% weight)

| Scenario | Seeded Memory | Measured |
|----------|--------------|----------|
| Recall a decision and rationale | "We chose X because Y" | Accuracy and completeness |
| Answer project setup question | Environment config, tooling | Correct setup instructions |
| Recall user preference | "Always use bun, not npm" | Follows the preference |
| Cross-reference related memories | Multiple related facts | Synthesizes correctly |
| Handle outdated memory | A superseded decision | Notes staleness |

### Compounding Value (25% weight)

| Scenario | Method | Measured |
|----------|--------|----------|
| Growing store helps | Same query against 10, 100, 1000 memories | Retrieval quality vs. scale |
| Dedup keeps signal clean | Add duplicates, then search | Noise dilution prevented |
| Extraction quality over time | Feed N sessions through extraction, search output | Extracted memories retrievable and accurate |

**Total: ~15-20 test cases for v1.**

## Scenario Design Principles

**Key constraint: Claude's inherent knowledge.** Claude has broad general knowledge from training. Test scenarios must use **project-specific, non-generalizable facts** that Claude cannot derive from training data alone. The efficacy we're measuring is: "does storing YOUR specific context help beyond what Claude already knows?"

| Principle | Why |
|-----------|-----|
| Use project-specific decisions, not general best practices | Claude already knows "use snake_case in Python" — test whether it knows *your* `svc_` prefix convention |
| Include dates, ADR references, team-specific rationale | Grounds the memory in your project context, impossible to guess |
| Test recall of arbitrary choices, not optimal ones | "We chose SQLite over Postgres for local storage" — Claude might recommend Postgres by default, but the right answer is what *you* decided |
| Use synthetic/fictional project context for reproducibility | Avoids coupling benchmarks to real projects that change |

## Test Case Format

```yaml
id: coding-001
category: coding
name: "Fix bug using known pattern"
description: "Agent fixes a null-check bug that was solved before in a different file"

memories:
  - text: "NoneType errors in API handlers are caused by missing auth middleware check"
    source: "eval/coding-001"

prompt: "Fix the TypeError: 'NoneType' in user_handler.py line 42"

expected:
  - type: "contains"
    value: "auth middleware"
    weight: 0.5
  - type: "no_retry"
    description: "Agent doesn't ask clarifying questions"
    weight: 0.3
  - type: "correct_fix"
    description: "The fix addresses the root cause, not symptoms"
    weight: 0.2
```

## Scoring

### Per-Test

Each test produces a score from 0.0 to 1.0. The delta between runs is the efficacy signal:

```
efficacy_delta = score_with_memory - score_without_memory
```

### Rubric Types

| Type | Evaluation | Example |
|------|-----------|---------|
| `contains` | Output contains expected text/pattern | Mentions "auth middleware" |
| `not_contains` | Output avoids known bad pattern | Doesn't use broken library |
| `correct_fix` | LLM-as-judge evaluates correctness | Fix addresses root cause |
| `no_retry` | Agent doesn't ask clarifying questions | Went straight to answer |
| `match_convention` | Output follows seeded style/pattern | Used snake_case correctly |
| `recall_accuracy` | LLM-as-judge scores factual accuracy | Recalled decision + rationale |

### Aggregation

```
Category Score = weighted average of test scores in that category
Overall Score  = weighted average of category scores
```

### LLM-as-Judge

For non-deterministic rubrics (`correct_fix`, `recall_accuracy`), send the test prompt, expected outcome, and actual output to an LLM judge for a 0-1 score with reasoning. Reuses existing provider infrastructure.

## Efficacy Report Format

```json
{
  "version": "1.0.0",
  "timestamp": "2026-03-03T...",
  "overall": {
    "with_memory": 0.82,
    "without_memory": 0.41,
    "efficacy_delta": 0.41
  },
  "categories": {
    "coding": { "with": 0.85, "without": 0.38, "delta": 0.47 },
    "recall": { "with": 0.80, "without": 0.35, "delta": 0.45 },
    "compounding": { "with": 0.78, "without": 0.55, "delta": 0.23 }
  },
  "tests": [ "...per-test detail..." ]
}
```

## Implementation Architecture

```
eval/
  config.yaml          # Runner config (provider, model, weights)
  scenarios/
    coding/
      coding-001.yaml
    recall/
      recall-001.yaml
    compounding/
      compounding-001.yaml
  runner.py            # Orchestrates test execution
  scorer.py            # Rubric evaluation + LLM-as-judge
  reporter.py          # Generates JSON report + summary
  results/
    2026-03-03T....json
```

### Isolation Strategy

CC has multiple memory sources that could contaminate tests. All must be controlled:

| Source | Isolation Strategy |
|--------|-------------------|
| **Memories MCP** | Seed or clear via API before each test — we control this |
| **CLAUDE.md** | Use a clean temp project with no CLAUDE.md, or a controlled one per scenario |
| **Auto-memory** | Run CC with `--project` pointing to an empty temp dir |
| **Conversation context** | Each test is a fresh `claude -p` invocation — no prior conversation |

### Runner Flow

1. Load scenario YAML
2. Create temp project dir (no CLAUDE.md, no auto-memory)
3. Clear Memories store via API
4. Run **without memories**: `claude -p "prompt" --project /tmp/eval-xxx` — CC has no memories anywhere
5. Seed Memories store via API with scenario memories
6. Run **with memories**: `claude -p "prompt" --project /tmp/eval-xxx` — CC can find memories via MCP
7. Score both outputs, compute delta
8. Clean up temp dir
9. Repeat for all scenarios
10. Aggregate → generate report

### Key Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Test runner | Python script, not pytest | Scenarios are data-driven YAML, not code |
| Memory isolation | Reset store + temp project per test | No cross-contamination from any source |
| Agent execution | `claude -p` (programmatic mode) | Tests the real pipeline: CC → MCP → Memories → response |
| LLM judge | Configurable provider | Reuses existing infrastructure |
| Results storage | JSON files | Simple, diffable, no new dependencies |

## Phase 2: Live Metrics

Once benchmarks establish baselines, add an `/efficacy` endpoint reporting proxy metrics:

- **Search hit rate** — % of searches returning ≥1 result above threshold
- **Memory freshness** — average age of retrieved memories
- **Dedup ratio** — % of adds blocked by novelty check
- **Retrieval concentration** — breadth of memory utilization

Exposed in WebUI dashboard alongside existing stats.
