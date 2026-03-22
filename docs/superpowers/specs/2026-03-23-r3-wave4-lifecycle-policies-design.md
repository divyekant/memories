# R3 Wave 4: Lifecycle Policies — Design Spec

## Context

Waves 1-3 hardened trust, explainability, and quality proof. Wave 4 adds automated lifecycle management: memories that expire, memories that auto-archive when confidence drops, and confidence as a ranking signal.

**Product thesis:** Memory that manages itself — with full transparency about why.

**Scale context:** ~9,500 memories, single operator. Policies are periodic (manual trigger or external cron), not continuous background jobs.

**Design decisions from review:**
1. Extraction profiles (`extraction_profiles.py`) are the policy store — treat as per-prefix policy layer, not extraction-specific
2. Endpoint: `POST /maintenance/enforce-policies` (not enforce-retention), `dry_run=true` default
3. Explicit policy semantics: age from `updated_at || created_at || timestamp`, exclude pinned + already archived, deterministic precedence when both TTL and confidence match
4. No auto-snapshot before archival — archival is reversible metadata flip, not destructive. Instead: per-memory audit event + stored evidence in metadata
5. Confidence ranking uses the same `compute_confidence()` as UI display — no divergence

---

## Item 24: Retention Policies (TTL)

### Profile Extension

Add `ttl_days` to extraction profile schema:

```json
{
  "source_prefix": "wip/",
  "ttl_days": 30,
  "mode": "standard",
  "max_facts": 30,
  ...
}
```

- `ttl_days: null` (default) — no TTL, memories live forever
- `ttl_days: 30` — memories older than 30 days get archived by policy enforcement
- Age computed from `updated_at || created_at || timestamp` (same priority as confidence)
- Inherits via cascade: `wip/memories/` inherits `wip/` TTL unless overridden

**Files:** `extraction_profiles.py` (add `ttl_days` to DEFAULTS and schema)

---

## Item 25: Auto-Archive with Proof

### Profile Extension

Add `confidence_threshold` and `min_age_days` to profile schema:

```json
{
  "source_prefix": "claude-code/",
  "confidence_threshold": 0.1,
  "min_age_days": 90,
  ...
}
```

- `confidence_threshold: null` (default) — no confidence-based archival
- `confidence_threshold: 0.1` + `min_age_days: 90` — archive if confidence < 0.1 AND age > 90 days
- `min_age_days` is required when `confidence_threshold` is set (prevents archiving fresh memories with low initial confidence)
- Confidence computed via existing `compute_confidence()` — same formula as UI display, no divergence

### Policy Enforcement Endpoint

```
POST /maintenance/enforce-policies?dry_run=true
```

**Default:** `dry_run=true` — shows what would happen without acting. Operator must explicitly set `dry_run=false` to execute. Trust-first posture.

**Logic:**

Evaluate each memory **once** against its **resolved** policy (not per-profile iteration):

1. Iterate all non-archived, non-pinned memories
2. For each memory, resolve its effective policy via `extraction_profiles.resolve(memory.source)` — this cascades `claude-code/foo/` → `claude-code/` → DEFAULTS, returning one merged config per memory
3. From the resolved config, check:
   - TTL: if `ttl_days` is set and `age_days > ttl_days`
   - Confidence: if `confidence_threshold` is set and `confidence < threshold AND age_days > min_age_days`
   - If both match: report both reasons, archive for the **first matching rule** (TTL takes precedence — it's a harder policy)
4. Collect all candidates with their resolved policies and evidence
5. If `dry_run=false`: batch-archive candidates and store evidence

**Why per-memory resolution, not per-profile iteration:** With profiles at both `claude-code/` and `claude-code/foo/`, iterating profiles would scan the same memory twice — once for the parent, once for the child. The child might override `ttl_days: null` (no TTL) while the parent has `ttl_days: 30`. Per-profile iteration would incorrectly apply the parent's TTL. Per-memory resolution respects the cascade: the child's explicit `null` overrides the parent's `30`.

**Performance at scale:** At ~9,500 memories, resolving a profile per memory is O(N * P) where P is the profile depth (typically 2-3). This is ~30K lookups against an in-memory dict — negligible.

**Response:**

```json
{
  "dry_run": true,
  "actions": [
    {
      "memory_id": 42,
      "source": "wip/memories",
      "age_days": 45,
      "confidence": 0.35,
      "reasons": [
        {"rule": "ttl", "ttl_days": 30, "age_days": 45, "prefix": "wip/"}
      ],
      "action": "would_archive"
    },
    {
      "memory_id": 99,
      "source": "claude-code/old-project",
      "age_days": 180,
      "confidence": 0.08,
      "reasons": [
        {"rule": "confidence", "threshold": 0.1, "confidence": 0.08, "min_age_days": 90, "prefix": "claude-code/"}
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

When `dry_run=false`, `action` becomes `"archived"` and evidence is stored.

### Evidence Storage

**Per-memory metadata** (not audit schema extension — simpler, queryable):

When archiving via policy, set these metadata fields on the memory:

```python
metadata_patch = {
    "_policy_archived_reason": "ttl" | "confidence",
    "_policy_archived_policy": "wip/ ttl_days=30" | "claude-code/ confidence<0.1 min_age=90",
    "_policy_archived_at": "2026-03-23T12:00:00Z",
    "_policy_archived_confidence": 0.08,  # snapshot at time of archival
    "_policy_archived_age_days": 180,
}
```

**Protected namespace:** All policy-evidence fields use the `_policy_` prefix. Add `_policy_` prefixed keys to the reserved fields list in `update_memory()` (`memory_engine.py:982`). This prevents ordinary `PATCH /memory/{id}` with `metadata_patch` from overwriting or clearing lifecycle evidence that the policy engine depends on.

```python
# In update_memory(), extend the reserved fields check:
RESERVED_FIELDS = {"id", "text", "source", "timestamp", "created_at", "updated_at", "entity_key"}
for key in metadata_patch:
    if key in RESERVED_FIELDS or key.startswith("_policy_"):
        continue  # skip reserved and policy-protected fields
    meta[key] = metadata_patch[key]
```

Policy-evidence fields can only be written by `enforce_policies()` (which bypasses the reserved check by writing directly to meta dict). Operators can still see the evidence in the UI but cannot accidentally clear it.

Plus one audit event per archived memory:

```python
_audit(request, "memory.policy_archived", resource_id=str(memory_id), source=source)
```

**Bulk performance:** `enforce_policies` must NOT call `update_memory()` per candidate (which creates a backup per call). Instead, use a batch path:
1. Collect all candidate IDs and evidence
2. Batch-set `archived=True` + evidence metadata via direct Qdrant `set_payload()` calls
3. Update in-memory metadata dict in bulk
4. Save metadata once at the end
5. Emit audit events after the batch completes

This avoids N backups and N metadata saves for N archived memories.

The lifecycle tab already renders audit events. The metadata fields make the evidence inspectable in the detail panel's Overview tab (existing editable fields pattern).

### Auth

Admin only (`_require_admin`). This is a maintenance operation.

**Files:** `extraction_profiles.py` (schema), `memory_engine.py` (enforce_policies method), `app.py` (endpoint)

---

## Item 28: Confidence as 5th RRF Signal

### hybrid_search Extension

New parameter: `confidence_weight: float = 0.0`

Follow the exact pattern of `feedback_weight` (R2):

1. After collecting candidates, compute confidence for each via `compute_confidence()`
2. Rank candidates by confidence descending
3. Add to RRF: `confidence_weight * (1.0 / (rank + rrf_k))`

### 5-Signal Weight Scaling

```python
# All 5 signals sum to 1.0
confidence_weight = max(0.0, min(1.0, confidence_weight))
feedback_weight = max(0.0, min(1.0, feedback_weight))
total_auxiliary = min(feedback_weight + confidence_weight, 1.0)  # guard: combined cannot exceed 1.0
total_core = 1.0 - total_auxiliary  # always >= 0.0
effective_vector_weight = vector_weight * total_core * (1.0 - recency_weight)
effective_bm25_weight = (1.0 - vector_weight) * total_core * (1.0 - recency_weight)
effective_recency_weight = recency_weight * total_core
# feedback_weight and confidence_weight stay as-is
# Sum: total_core * (1-rw)(vw + (1-vw)) + total_core * rw + fw + cw
#     = total_core + fw + cw = (1 - fw - cw) + fw + cw = 1.0
```

### SearchRequest + Explain

Add to `SearchRequest`:
```python
confidence_weight: float = Field(0.0, ge=0.0, le=1.0, description="Weight for confidence-based ranking (0=disabled)")
```

Add to `hybrid_search_explain()` scoring_weights:
```python
"confidence": round(confidence_weight, 4),
```

### MCP

Add `confidence_weight` to `memory_search` tool (default 0.0 — opt-in, unlike feedback which defaults to 0.1).

### Key constraint

**Policy confidence and displayed confidence must not diverge.** Both use `compute_confidence()` with the same anchor priority (`updated_at || created_at || timestamp`) and half-life.

**Half-life mismatch resolution:** The extraction profile DEFAULTS has `half_life_days: 30` (for extraction decay tuning). But `compute_confidence()` defaults to `half_life_days=90.0` (for display/ranking). These serve different purposes — extraction half-life controls how aggressively extraction treats old facts, while confidence half-life controls the decay curve shown to operators and used in ranking.

To avoid silently changing all confidence scores when resolving profiles:
- Add a new field `confidence_half_life_days` to profiles (default: `null` → falls back to engine default of 90)
- Leave `half_life_days` as extraction-only (existing behavior preserved)
- Policy enforcement and confidence ranking both use `confidence_half_life_days` when set, else 90
- This keeps backward compatibility — no existing confidence scores change

When enriching search results, resolve the profile for the memory's source:
- If `confidence_half_life_days` is set: use it
- If not: use the engine default (90 days)
- Pass to `compute_confidence(anchor, half_life_days=resolved_half_life)`

---

## Batching

| Batch | Items | Theme |
|-------|-------|-------|
| **B1: Profile + Enforcement** | 24, 25 | TTL + confidence archival with proof |
| **B2: Confidence Ranking** | 28 | 5th RRF signal |

B1 first — the policy infrastructure. B2 builds on the same confidence model but is independent.

---

## Test Strategy

| File | Key Scenarios |
|------|--------------|
| `tests/test_lifecycle_policies.py` | TTL archival, confidence archival, dry_run default, pinned exclusion, already-archived exclusion, both rules match (TTL precedence), evidence metadata stored, per-prefix half-life resolution |
| `tests/test_confidence_ranking.py` | confidence_weight boosts fresh memories, weight scaling sums to 1.0, 0.0 weight = no effect, explain includes confidence signal |

---

## Files Modified

| File | Batch | Changes |
|------|-------|---------|
| `extraction_profiles.py` | B1 | Add `ttl_days`, `confidence_threshold`, `min_age_days` to DEFAULTS and schema |
| `memory_engine.py` | B1, B2 | `enforce_policies()` method, `confidence_weight` in hybrid_search, per-prefix half-life in `_enrich_with_confidence()` |
| `app.py` | B1, B2 | `POST /maintenance/enforce-policies`, `confidence_weight` on SearchRequest |
| `mcp-server/index.js` | B2 | `confidence_weight` on memory_search tool |
| `tests/test_lifecycle_policies.py` | B1 | Policy enforcement tests |
| `tests/test_confidence_ranking.py` | B2 | Confidence RRF signal tests |

## What Is Explicitly Out of Scope

- Project entity / project isolation (R4 — skill + hook + UI problem)
- Scheduled/cron enforcement (operator triggers manually or wires external cron)
- UI for managing policies (use API or edit profiles JSON directly)
- Background policy enforcement daemon
- Automatic confidence threshold suggestion
