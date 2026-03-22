---
id: feat-013
type: feature-doc
title: "R3: Proof & Operator Confidence"
audience: external
generated: 2026-03-22
---

# R3: Proof & Operator Confidence

R3 is about answering the question: "Can I trust this memory system?" It ships across four waves, each addressing a different layer of confidence — from consistent audit trails and extraction transparency, through measurable retrieval quality, to automated lifecycle management that explains itself.

---

## What's New

### Trust & Explainability (Waves 1-2)

- **Consistent audit trail.** All lifecycle events now use namespaced action types (`memory.created`, `memory.deleted`, `memory.archived`, `memory.merged`, `memory.consolidated`, `memory.pruned`, `memory.deduplicated`, `memory.policy_archived`, and others). Nine previously untracked operations now appear in the lifecycle timeline.
- **Extraction reasoning.** When you run extraction with `debug: true`, the trace now shows why each fact was skipped, what existing memory it conflicts with, and what it updates. Protected (pinned/archived) memories that block an update are reported with a `skipped` action and `protected` reason.
- **Evidence strength badges.** The Health page's Problem Queries and Stale Memories views display evidence strength as **strong**, **moderate**, or **weak** based on feedback volume. This tells you at a glance how much signal backs each diagnostic finding.
- **Extraction source labeling.** Memories created through the extraction pipeline are now correctly labeled with `Extraction` origin in the lifecycle tab, so you can distinguish hand-created memories from auto-extracted ones.

### Quality Proof (Wave 3)

- **LongMemEval benchmark.** Run `memories eval longmemeval` to measure retrieval quality against a standardized 500-question benchmark across five categories (information extraction, multi-session reasoning, knowledge update, temporal reasoning, abstaining). Results include per-category scores and regression deltas against previous runs.
- **Signal keyword pre-filter.** Extraction hooks now check for decision, bug, architecture, and other signal keywords before calling the LLM. Conversations without any signals skip extraction entirely, saving cost without losing meaningful facts.

### Lifecycle Management (Wave 4)

- **TTL per source prefix.** Set `ttl_days` on an extraction profile to expire memories automatically. For example, `wip/` memories can expire after 30 days.
- **Confidence-based auto-archive.** Set `confidence_threshold` and `min_age_days` on a profile to archive memories that have decayed below a confidence floor and are older than a minimum age.
- **Policy enforcement endpoint.** `POST /maintenance/enforce-policies` evaluates every memory against its resolved policy. It runs in dry-run mode by default — you see what would happen without any changes.
- **Archived-with-evidence.** Every auto-archived memory stores proof of why it was archived (`_policy_archived_reason`, `_policy_archived_confidence`, `_policy_archived_age_days`) in protected metadata. This evidence is visible in the lifecycle tab and cannot be overwritten by ordinary PATCH requests.
- **Confidence as a search signal.** When you set `confidence_weight` > 0 on a search request, confidence becomes the 5th signal in hybrid search ranking (alongside vector, BM25, recency, and feedback). Higher-confidence memories rank higher.

---

## Feature Guide

### Running the LongMemEval Benchmark

LongMemEval measures how well your Memories instance retrieves the right information from past conversations. It seeds memories from conversation histories, asks 500 questions, and scores the results with an LLM judge.

```bash
# Full benchmark run (costs ~$0.50 with Haiku judge)
memories eval longmemeval --judge-provider anthropic

# Use a specific judge model
memories eval longmemeval --judge-provider anthropic --judge-model claude-haiku-4-5-20251001

# Save results for regression tracking
memories eval longmemeval --output eval/results/longmemeval-v3.4.0.json

# Compare against a previous run
memories eval longmemeval --compare eval/results/longmemeval-v3.3.0.json
```

The output shows an overall score plus per-category breakdowns:

```
LongMemEval v3.4.0 (2026-03-23)
Judge: anthropic/claude-haiku-4-5-20251001
Overall: 72.4% (+2.1% vs v3.3.0)
  Information Extraction: 78.0% (+3.0%)
  Multi-Session Reasoning: 65.2% (+1.5%)
  Knowledge Update: 71.0% (+2.0%)
  Temporal Reasoning: 68.5% (+1.2%)
  Abstaining: 79.3% (+2.8%)
```

Run this after each release to catch retrieval regressions before they affect your workflows.

### Configuring the Signal Keyword Pre-Filter

By default, extraction hooks look for these keywords before calling the LLM:

```
decide, decision, chose, bug, fix, remember, architecture, convention, pattern, learning, mistake
```

If none of these appear in the conversation, the hook exits early without making an API call. This saves cost on routine coding sessions that produce no memorable facts.

You can customize the keyword list:

```bash
# Use your own keywords
export MEMORIES_SIGNAL_KEYWORDS="decide|decision|bug|fix|architecture|convention"

# Disable the filter entirely (extract from everything)
export MEMORIES_SIGNAL_KEYWORDS=""
```

The filter applies to `memory-extract.sh` (tool-use extraction) and `memory-commit.sh` (session-end extraction). It does **not** apply to `memory-flush.sh` (pre-compact extraction), which always extracts because the context window is about to be lost.

### Setting Up TTL Retention Policies

Use TTL when you have memories that should not live forever. Work-in-progress notes, scratch-pad items, and temporary context are good candidates.

Set `ttl_days` on an extraction profile for the relevant source prefix:

```bash
curl -s -X PUT http://localhost:8900/extraction-profiles/wip%2F \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"ttl_days": 30}'
```

This means any memory with a source starting with `wip/` will be flagged for archival after 30 days. The TTL is computed from the memory's last update time (`updated_at`), falling back to `created_at`.

Child prefixes can override the parent. If you want `wip/important/` memories to persist indefinitely:

```bash
curl -s -X PUT http://localhost:8900/extraction-profiles/wip%2Fimportant%2F \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"ttl_days": null}'
```

### Setting Up Confidence-Based Auto-Archive

This catches memories that have decayed to near-zero confidence and are old enough that the decay is meaningful — not just recently created with low initial confidence.

```bash
curl -s -X PUT http://localhost:8900/extraction-profiles/claude-code%2F \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"confidence_threshold": 0.1, "min_age_days": 90}'
```

This archives `claude-code/` memories only when confidence drops below 0.1 **and** the memory is older than 90 days. Both conditions must be true.

You can also set a per-prefix confidence half-life that differs from the engine default (90 days):

```bash
curl -s -X PUT http://localhost:8900/extraction-profiles/scratch%2F \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"confidence_half_life_days": 30, "confidence_threshold": 0.2, "min_age_days": 14}'
```

Scratch memories now decay faster (30-day half-life) and get archived sooner.

### Enforcing Lifecycle Policies

Policies are not enforced automatically. You trigger enforcement manually or via an external scheduler.

```bash
# Dry run — see what would be archived (default)
curl -s -X POST "http://localhost:8900/maintenance/enforce-policies" \
  -H "X-API-Key: $API_KEY" | jq .

# Execute — actually archive the candidates
curl -s -X POST "http://localhost:8900/maintenance/enforce-policies?dry_run=false" \
  -H "X-API-Key: $API_KEY" | jq .
```

The dry-run response shows every memory that would be affected, along with the reasons:

```json
{
  "dry_run": true,
  "actions": [
    {
      "memory_id": 42,
      "source": "wip/scratch-notes",
      "age_days": 45,
      "confidence": 0.35,
      "reasons": [
        {"rule": "ttl", "ttl_days": 30, "age_days": 45, "prefix": "wip/"}
      ],
      "action": "would_archive"
    }
  ],
  "summary": {
    "candidates_scanned": 500,
    "would_archive": 12,
    "by_rule": {"ttl": 8, "confidence": 4},
    "excluded_pinned": 2,
    "excluded_already_archived": 15
  }
}
```

This endpoint requires admin-level authentication.

### Using Confidence-Weighted Search

To let confidence influence search ranking, pass `confidence_weight` on your search request:

```bash
curl -s -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "query": "deployment strategy",
    "k": 5,
    "hybrid": true,
    "confidence_weight": 0.15
  }' | jq .
```

This adds confidence as the 5th signal in hybrid RRF scoring. The weight is opt-in (default `0.0`) because not every use case benefits from confidence bias. When enabled, all five signal weights (vector, BM25, recency, feedback, confidence) are scaled so they sum to 1.0.

The `confidence_weight` parameter is also available on the `memory_search` MCP tool.

### Reading Evidence on Auto-Archived Memories

When a memory is archived by policy enforcement, the system stores evidence in the memory's metadata:

| Field | Value |
|-------|-------|
| `_policy_archived_reason` | `"ttl"` or `"confidence"` |
| `_policy_archived_policy` | The prefix and rule that triggered it (e.g., `"wip/ ttl_days=30"`) |
| `_policy_archived_at` | ISO timestamp of when the archive happened |
| `_policy_archived_confidence` | Confidence score at the time of archival |
| `_policy_archived_age_days` | Age in days at the time of archival |

These fields are visible in the memory detail panel and in the lifecycle tab's audit timeline. They are protected from accidental overwrites — ordinary `PATCH` metadata updates cannot modify any field prefixed with `_policy_`.

### Understanding Evidence Strength Badges

The Health page's Problem Queries and Stale Memories views show evidence strength badges next to each entry:

**Problem Queries:**

| Badge | Criteria | Meaning |
|-------|----------|---------|
| **strong** | 10+ total feedback signals | High confidence this query is genuinely problematic |
| **moderate** | 5-9 total feedback signals | Likely problematic, worth investigating |
| **weak** | 2-4 total feedback signals | Early signal, may need more data |

**Stale Memories:**

| Badge | Criteria | Meaning |
|-------|----------|---------|
| **strong** | 5+ not-useful signals | Strong evidence this memory is not serving its purpose |
| **moderate** | 2-4 not-useful signals | Moderate evidence, consider reviewing |
| **weak** | 1 not-useful signal | Initial signal, monitor for more feedback |

These badges help you prioritize which issues to address first.

### Using Extraction Debug Trace for Reasoning

Pass `debug: true` on extraction requests to see the full reasoning chain:

```bash
curl -s -X POST http://localhost:8900/memory/extract \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "messages": "user: We switched from MySQL to PostgreSQL.\nassistant: Noted, using PostgreSQL going forward.",
    "source": "project/decisions",
    "debug": true
  }' | jq .
```

The debug trace includes:

- **Extracted facts** with categories (decision, preference, detail, deferred)
- **AUDN decisions** — whether each fact was Added, Updated, Deleted, or treated as a No-op, along with the similar memories that influenced the decision and their similarity scores
- **Skip reasons** — when an update targets a pinned or archived memory, the trace reports a `skipped` action with reason `protected`
- **Conflict markers** — when a new fact contradicts an existing memory and the system is unsure which is correct

---

## Tips & Best Practices

- **Run benchmarks after each release.** LongMemEval catches retrieval regressions that are invisible during normal use. Save results with `--output` and compare with `--compare` to track quality over time.
- **Start with dry-run on policy enforcement.** The default is `dry_run=true` for a reason. Review the candidate list before setting `dry_run=false`. Once you trust the policies, wire enforcement to a cron job or CI step.
- **Use TTL for ephemeral sources.** Work-in-progress notes (`wip/`), scratch pads, and temporary project context are ideal TTL candidates. Permanent decisions and architectural knowledge should have no TTL.
- **Set `min_age_days` generously.** Confidence-based archival without a reasonable minimum age can catch fresh memories that simply have not been reinforced yet. 90 days is a sensible default for most source prefixes.
- **Pin critical memories before enabling policies.** Pinned memories are excluded from all policy enforcement — both TTL and confidence-based. If a memory must never be auto-archived, pin it.
- **Check the lifecycle tab for policy evidence.** If a memory was auto-archived and you are unsure why, the lifecycle tab shows the rule, the confidence at archival time, and the age. Use this to tune your thresholds.
- **Keep `confidence_weight` low initially.** A value of 0.1-0.2 gently favors higher-confidence memories without overwhelming the vector and BM25 signals. Increase only if retrieval quality improves in your benchmarks.
- **Customize signal keywords for your domain.** The default keyword list targets software development conversations. If your domain uses different terminology for important decisions, override `MEMORIES_SIGNAL_KEYWORDS` to match.
- **Use evidence badges to prioritize Health page triage.** Start with "strong" evidence items — they have the most data backing them. "Weak" items may resolve on their own as more feedback accumulates.
- **Restore is always possible.** Policy archival is a soft operation (metadata flag, not deletion). You can restore any auto-archived memory from the Archive view in the UI if a policy was too aggressive.
