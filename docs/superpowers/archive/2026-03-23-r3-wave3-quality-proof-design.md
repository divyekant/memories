# R3 Wave 3: Quality Proof and Recovery Confidence — Design Spec

## Context

R3 Waves 1-2 closed trust gaps and tightened explainability. Wave 3 shifts to proving the system works and making operations safe. Two themes: (1) measurable retrieval quality via benchmarks, (2) validated recovery paths.

**Scale context:** ~9,500 memories, single operator. Eval runs are periodic (per-release), not continuous.

**Existing infrastructure:**
- `eval/` directory with benchmarks, runner, scorer, judge, scenario loader
- Snapshot create/list/restore fully implemented (Qdrant + local modes)
- Import/export with add/smart/smart+extract strategies
- Extraction hooks shared across Claude Code, Codex, and Cursor

---

## Batching Strategy

| Batch | Items | Theme |
|-------|-------|-------|
| **B1: Eval Harness** | LongMemEval integration + regression tracking | Prove quality |
| **B2: Operational Hardening** | Signal keyword pre-filter + backup/restore validation + import/export round-trip | Trust operations |

---

## B1: LongMemEval Integration + Regression Tracking

### Architecture

Extend the existing `eval/` framework with a LongMemEval adapter. The adapter communicates with the Memories service via HTTP (not direct engine calls) to test the full stack including auth, API contracts, and extraction pipeline.

1. **Loads** the LongMemEval dataset (500 questions, 5 categories) from HuggingFace NDJSON
2. **Seeds** the engine with conversation histories via `POST /memory/extract` (tests extraction quality, not just retrieval)
3. **Queries** for each question via `POST /search` (hybrid mode, through the HTTP API)
4. **Judges** answer correctness via configurable LLM provider (default: Anthropic Haiku)
5. **Reports** per-category and overall scores with regression delta

### Client Extensions

The existing `eval/` framework uses `MemoriesClient` (HTTP client). It currently supports `seed_memories`, `clear_by_prefix`, `health_check`, `get_stats`. Two new methods needed:

```python
# Add to MemoriesClient
def search(self, query, k=5, hybrid=True, feedback_weight=0.0):
    """POST /search — returns results list."""

def extract(self, messages, source, context="stop", dry_run=False):
    """POST /memory/extract — returns job_id, then polls for completion."""
```

### Files

| File | Action | Purpose |
|------|--------|---------|
| `eval/longmemeval.py` | **Create** | LongMemEval adapter: load, seed, query, judge, report |
| `eval/runner.py` | **Modify** | Add `search()` and `extract()` to MemoriesClient |
| `eval/scenarios/longmemeval/.gitkeep` | **Create** | Dataset cache directory (gitignored) |
| `eval/results/` | Existing | Result JSON storage for regression tracking |
| `cli/commands/eval_cmd.py` | **Create** | CLI entry point: `memories eval longmemeval` |
| `cli/__init__.py` | **Modify** | Register eval command group |

### Result Model

LongMemEval results use a separate `LongMemEvalResult` dataclass (not `EvalReport`) because the structure is fundamentally different — category-based scoring vs scenario-based rubrics. The two models serve different purposes: `EvalReport` for internal scenario evals, `LongMemEvalResult` for standardized benchmark comparison.

### LongMemEval Adapter (`eval/longmemeval.py`)

```python
class LongMemEvalRunner:
    def __init__(self, client, judge_provider="anthropic", judge_model=None):
        """
        client: MemoriesClient (HTTP, not direct engine)
        judge_provider: anthropic|openai|chatgpt-subscription|ollama
        judge_model: override model name (default per provider)
        """

    def load_dataset(self, cache_dir="eval/scenarios/longmemeval/"):
        """Download LongMemEval from HuggingFace if not cached."""

    def seed_memories(self, conversations):
        """Extract memories from conversation histories via client.extract()."""

    def run_questions(self, questions, k=5):
        """For each question: client.search() for context, format, generate answer."""

    def judge_answers(self, questions, answers):
        """Score each answer using LLM judge."""

    def report(self, results, previous_results=None):
        """Generate per-category scores + regression delta."""
```

### Scoring Categories

| Category | Tests | What It Measures |
|----------|-------|-----------------|
| Information Extraction | ~100 | Can the system store and retrieve factual data? |
| Multi-Session Reasoning | ~100 | Can it synthesize across conversation sessions? |
| Knowledge Update | ~100 | Does it handle contradictions (newer supersedes older)? |
| Temporal Reasoning | ~100 | Does it understand event sequences and time? |
| Abstaining | ~100 | Does it correctly decline when info is insufficient? |

### CLI Entry Point

```bash
# Full benchmark run (~$0.50 with Haiku judge)
python -m memories eval longmemeval --judge-provider anthropic

# With specific model
python -m memories eval longmemeval --judge-provider anthropic --judge-model claude-haiku-4-5-20251001

# Compare against previous run
python -m memories eval longmemeval --compare eval/results/longmemeval-v3.2.1.json

# Output
python -m memories eval longmemeval --output eval/results/longmemeval-v3.2.2.json
```

### Regression Tracking

Results saved as JSON:
```json
{
  "version": "3.2.2",
  "timestamp": "2026-03-23T12:00:00Z",
  "judge": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
  "overall": 0.724,
  "categories": {
    "information_extraction": 0.780,
    "multi_session_reasoning": 0.652,
    "knowledge_update": 0.710,
    "temporal_reasoning": 0.685,
    "abstaining": 0.793
  },
  "delta": {
    "vs_version": "3.2.1",
    "overall": +0.021,
    "categories": { ... }
  }
}
```

CLI output:
```
LongMemEval v3.2.2 (2026-03-23)
Judge: anthropic/claude-haiku-4-5-20251001
Overall: 72.4% (+2.1% vs v3.2.1)
  Information Extraction: 78.0% (+3.0%)
  Multi-Session Reasoning: 65.2% (+1.5%)
  Knowledge Update: 71.0% (+2.0%)
  Temporal Reasoning: 68.5% (+1.2%)
  Abstaining: 79.3% (+2.8%)
```

---

## B2: Operational Hardening

### Signal Keyword Pre-Filter

**Files:**
- Modify: `integrations/claude-code/hooks/memory-extract.sh`
- Modify: `integrations/claude-code/hooks/memory-flush.sh`
- Modify: `integrations/claude-code/hooks/memory-commit.sh`

**Applies to:** Claude Code, Codex, and Cursor (all use the same hook scripts). OpenClaw uses skill-driven extraction — agent already decides when to extract.

**Implementation:**

Before sending transcript text to `/memory/extract`, check for signal keywords:

Insert AFTER `$MESSAGES` is assembled (after the conversation pair extraction loop) but BEFORE the `curl` call to `/memory/extract`. The variable in the hooks is `$MESSAGES`, not `$TRANSCRIPT_TEXT`.

```bash
# Signal keyword pre-filter — skip extraction if no signals detected
SIGNAL_KEYWORDS="${MEMORIES_SIGNAL_KEYWORDS:-decide|decision|chose|bug|fix|remember|architecture|convention|pattern|learning|mistake}"

# Guard: empty SIGNAL_KEYWORDS disables filter (extracts everything)
if [ -n "$SIGNAL_KEYWORDS" ] && ! echo "$MESSAGES" | grep -qiE "$SIGNAL_KEYWORDS"; then
  # No signal keywords found — skip extraction to save LLM cost
  exit 0
fi
```

**Behavior:**
- Default conservative list: `decide|decision|chose|bug|fix|remember|architecture|convention|pattern|learning|mistake`
- Env-overridable via `MEMORIES_SIGNAL_KEYWORDS`
- Setting `MEMORIES_SIGNAL_KEYWORDS=""` (empty) disables the filter — all extractions proceed
- Case-insensitive match
- Applied to the assembled `$MESSAGES` variable (after extraction from conversation pairs)
- `memory-flush.sh` (PreCompact) ALWAYS extracts regardless of keywords — context is about to be lost, so extract aggressively
- `memory-commit.sh` (SessionEnd) also applies the filter — session-end extraction is less critical than pre-compact

### Backup/Restore Validation Test

**File:** `tests/test_snapshot_roundtrip.py` (new)

End-to-end test:
1. Add 5 test memories with known text/source
2. Create snapshot via API
3. Delete 3 memories
4. Verify only 2 remain
5. Restore from snapshot
6. Verify all 5 are back with correct text/source
7. Verify search finds restored memories

### Import/Export Round-Trip Test

**File:** `tests/test_import_export_roundtrip.py` (new)

End-to-end test:
1. Add 5 test memories with various metadata (pinned, archived, category, custom fields)
2. Export via `GET /export`
3. Delete all memories
4. Import via `POST /import?strategy=add`
5. Verify all 5 restored with correct text, source
6. Verify metadata preserved (category, custom fields)
7. Verify search finds imported memories
8. Additional test: import via `POST /import?strategy=smart` with duplicates — verify dedup works
9. Additional test: export with `source` filter — verify only matching memories exported

---

## Test Strategy

| File | Key Scenarios |
|------|--------------|
| `tests/test_longmemeval.py` | Dataset loading, memory seeding, question-answer flow, scoring, regression delta |
| `tests/test_snapshot_roundtrip.py` | Full create-mutate-restore-verify cycle |
| `tests/test_import_export_roundtrip.py` | Full export-clear-import-verify cycle |
| `tests/test_signal_filter.py` | Hook keyword filter (bash unit test via subprocess) |

**Testing notes:**
- The full LongMemEval benchmark is NOT run in CI (too expensive). The test file validates the adapter logic with a small synthetic dataset (3-5 questions). The full 500-question run is manual via CLI.
- LongMemEval tests mock the LLM judge provider — return canned scores for synthetic questions. This follows the existing pattern in `eval/` where the judge is injectable.
- Signal filter tests: test the grep pattern in isolation (not full hook subprocess). Verify: keywords match, no-match skips, empty SIGNAL_KEYWORDS disables filter, case insensitivity.
- Import/export tests cover both `strategy=add` and `strategy=smart` (with duplicate detection).

---

## Files Modified

| File | Batch | Changes |
|------|-------|---------|
| `eval/longmemeval.py` | B1 | LongMemEval adapter (load, seed, query, judge, report) |
| `cli/commands/eval_cmd.py` | B1 | CLI: `memories eval longmemeval` |
| `cli/__init__.py` | B1 | Register eval command group |
| `integrations/claude-code/hooks/memory-extract.sh` | B2 | Signal keyword pre-filter |
| `integrations/claude-code/hooks/memory-flush.sh` | B2 | Note: flush ALWAYS extracts (no filter) |
| `integrations/claude-code/hooks/memory-commit.sh` | B2 | Signal keyword pre-filter |
| `tests/test_longmemeval.py` | B1 | Adapter logic tests (synthetic, not full benchmark) |
| `tests/test_snapshot_roundtrip.py` | B2 | Snapshot round-trip validation |
| `tests/test_import_export_roundtrip.py` | B2 | Import/export round-trip validation |

## What Is Explicitly Out of Scope

- Running LongMemEval in CI (manual CLI only, ~$0.50 per run)
- LoCoMo or ConvoMem benchmarks (LongMemEval is sufficient for now)
- Continuous quality monitoring (periodic per-release is enough at current scale)
- Auto-archive based on confidence (Wave 4)
- Confidence affecting ranking (Wave 4)
