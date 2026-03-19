# Extraction Quality Overhaul — Design

**Date:** 2026-02-21
**Branch:** `feat/extraction-quality`
**Scope:** Wave 1 (A, B, C, F + timestamps) + Wave 2 (D, E)

## Problem Statement

The automated memory extraction pipeline stores too much implementation noise
and too few durable insights. Of ~335 stored memories, a significant portion
are session artifacts ("all 44 tests pass", "Task 15 started") that fail the
"would this be useful in 30 days?" test.

Five root causes:
1. Extraction prompts don't distinguish durable vs ephemeral facts
2. Stop hook sends only last_assistant_message — no decision context
3. AUDN catches exact duplicates but not redundant clusters
4. No decay/pruning mechanism for stale memories
5. No retrieval tracking to measure what's actually useful

## Wave 1: Extraction Quality (single PR)

### 1. Timestamps — `created_at` + `updated_at`

**Files:** `memory_engine.py`

- `add_memories()`: Set both `created_at` and `updated_at` to current UTC ISO
- `update_memory()`: Only update `updated_at`, preserve `created_at`
- `supersede()`: New memory gets fresh `created_at`
- Migration: Existing memories with only `timestamp` → copy to `created_at`,
  set `updated_at` = `created_at`, keep `timestamp` as alias
- Add `created_at` to reserved metadata keys

API response shape:
```json
{
  "id": 300,
  "text": "...",
  "source": "claude-code/memories",
  "category": "decision",
  "created_at": "2026-02-20T06:44:28+00:00",
  "updated_at": "2026-02-20T06:44:28+00:00",
  "timestamp": "2026-02-20T06:44:28+00:00"
}
```

### 2. Better Extraction Prompts (A) + Categories (B) + Source-Aware (F)

**Files:** `llm_extract.py`

New standard extraction prompt:
```
Extract durable facts worth remembering from this conversation about the {project} project.

Categorize each fact:
- DECISION: Architectural choices, library selections, design patterns, preferences. WHY > WHAT.
- LEARNING: Bug root causes + fixes, gotchas, workarounds, performance findings.
- DETAIL: File paths, API signatures, config values — project-specific conventions.

Skip anything that fails: "Would this still be useful 30 days from now?"

DO NOT extract:
- Task completion status ("done", "all tests pass", "deployed")
- Commit hashes, PR numbers, branch names
- Counts or metrics ("44 tests", "5 files changed")
- Session-specific context ("currently working on...", "next step is...")
- Generic programming knowledge any developer would know

Output JSON array: [{"category": "DECISION"|"LEARNING"|"DETAIL", "text": "..."}]
Self-contained facts. Output [] if nothing worth storing.
```

Aggressive prompt (pre_compact) — same structure, adds:
```
This context is about to be lost permanently. Be thorough but still apply the
30-day test. Include DETAIL-category items you'd normally skip.
```

`{project}` derived from source field (e.g., `claude-code/memories` → "memories").

**Category stored in metadata:**
- `execute_actions()` passes `category` from extraction result into `metadata_list`
- AUDN prompt updated to receive category with each fact for smarter merge decisions
- Fallback extraction defaults to `category: "detail"`

### 3. Fix Stop Hook (C) — All Integrations

**Files:** `integrations/claude-code/hooks/memory-extract.sh`,
         `integrations/codex/memory-codex-notify.sh`

**Claude Code Stop hook:**
- Read transcript for last user+assistant pair (2 messages) instead of just
  `last_assistant_message`
- Falls back to `last_assistant_message` if transcript unavailable
- Truncated to 4000 chars

**Codex notify hook:**
- Already sends both user input-messages + last-assistant-message ✓
- Add context tag: `"context": "after_agent"` (already present) ✓
- No structural changes needed, but update prompt context handling in
  `llm_extract.py` to recognize `after_agent` as standard context

**OpenClaw skill (`openclaw-skill.md`):**
- Update extraction examples in docs to show the new JSON format with categories
- No code changes (OpenClaw calls the API directly)

## Wave 2: Maintenance System (separate PR)

### 4. Retrieval Tracking (E)

**Files:** `usage_tracker.py`, `app.py`, `memory_engine.py`

New SQLite table:
```sql
CREATE TABLE IF NOT EXISTS retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    memory_id INTEGER NOT NULL,
    query TEXT,
    source TEXT DEFAULT ''
);
```

Instrumentation:
- `/search` and `/search/batch` log each returned memory_id
- Lightweight INSERT per result (no write amplification)

New metadata fields (updated lazily during maintenance runs):
- `last_retrieved_at`: ISO timestamp of most recent retrieval
- `retrieval_count`: Total retrievals

### 5. Consolidation Job (D)

**Files:** New `consolidator.py`, `app.py` (endpoints + scheduler)

Algorithm:
1. Group memories by source prefix
2. Within each group, cluster by semantic similarity (threshold > 0.75)
3. Clusters with 3+ members → consolidation candidates
4. LLM merges cluster into 1-2 facts preserving decisions/learnings
5. Replace cluster: delete old, insert consolidated with
   `metadata: {consolidated_from: [old_ids]}`

Scheduling:
- Auto: daily at 3 AM UTC, env-gated via `MAINTENANCE_ENABLED=true`
- Manual: `POST /maintenance/consolidate?dry_run=true&source_prefix=...`

### 6. Pruning

**Files:** `consolidator.py`, `app.py`

Logic:
- Memories older than 90 days (`created_at`) with `retrieval_count == 0`
- LLM final check: "Is this still valuable?" before deletion
- `category: "detail"` pruned more aggressively (60 days) than
  `"decision"` or `"learning"` (120 days)

Scheduling:
- Auto: weekly on Sunday at 4 AM UTC
- Manual: `POST /maintenance/prune?dry_run=true`

## Environment Variables

New config (all optional, disabled by default):
```
MAINTENANCE_ENABLED=false          # Enable auto-consolidation + pruning
CONSOLIDATION_SCHEDULE=0 3 * * *   # Daily 3 AM UTC
PRUNE_SCHEDULE=0 4 * * 0           # Weekly Sunday 4 AM UTC
PRUNE_DETAIL_DAYS=60               # Days before unused DETAIL memories pruned
PRUNE_DECISION_DAYS=120            # Days before unused DECISION/LEARNING pruned
CONSOLIDATION_SIMILARITY=0.75      # Clustering threshold
CONSOLIDATION_MIN_CLUSTER=3        # Minimum cluster size to trigger consolidation
```

## Testing Strategy

- Unit tests for new prompt parsing (category extraction)
- Unit tests for timestamp migration logic
- Unit tests for consolidation clustering algorithm
- Unit tests for pruning eligibility logic
- Integration test: extract → store → verify category in metadata
- Test backward compat: old memories without `created_at` handled gracefully
