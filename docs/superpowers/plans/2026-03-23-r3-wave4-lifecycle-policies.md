# R3 Wave 4: Lifecycle Policies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated lifecycle management — TTL-based retention, confidence-based auto-archive with proof, and confidence as a search ranking signal.

**Architecture:** Extend extraction profiles as per-prefix policy layer. New `POST /maintenance/enforce-policies` endpoint (dry_run=true default). Confidence as 5th RRF signal in hybrid_search. Policy evidence stored in `_policy_*` namespace metadata, protected from ordinary writes.

**Tech Stack:** Python (FastAPI), existing extraction_profiles.py cascade, memory_engine.py

**Design spec:** `docs/superpowers/specs/2026-03-23-r3-wave4-lifecycle-policies-design.md`

**Test baseline:** 991 tests passing

---

## File Map

| File | Role | Action |
|------|------|--------|
| `extraction_profiles.py` | Add ttl_days, confidence_threshold, min_age_days, confidence_half_life_days | **Modify** |
| `memory_engine.py` | enforce_policies(), confidence_weight in hybrid_search, _policy_ protection, per-prefix half-life | **Modify** |
| `app.py` | POST /maintenance/enforce-policies, confidence_weight on SearchRequest | **Modify** |
| `mcp-server/index.js` | confidence_weight on memory_search | **Modify** |
| `tests/test_lifecycle_policies.py` | Policy enforcement tests | **Create** |
| `tests/test_confidence_ranking.py` | Confidence RRF signal tests | **Create** |

---

## B1: Profile Extension + Policy Enforcement

### Task 1: Extend extraction profiles with lifecycle policy fields

**Files:**
- Modify: `extraction_profiles.py` (DEFAULTS dict ~line 7)
- Test: `tests/test_lifecycle_policies.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_lifecycle_policies.py
import pytest
from extraction_profiles import ExtractionProfiles

@pytest.fixture
def profiles(tmp_path):
    return ExtractionProfiles(str(tmp_path / "profiles.json"))

def test_profile_supports_ttl_days(profiles):
    """Profile should accept and resolve ttl_days field."""
    profiles.put("wip/", {"ttl_days": 30})
    resolved = profiles.resolve("wip/test-project")
    assert resolved["ttl_days"] == 30

def test_profile_supports_confidence_threshold(profiles):
    """Profile should accept confidence_threshold and min_age_days."""
    profiles.put("claude-code/", {"confidence_threshold": 0.1, "min_age_days": 90})
    resolved = profiles.resolve("claude-code/test")
    assert resolved["confidence_threshold"] == 0.1
    assert resolved["min_age_days"] == 90

def test_profile_supports_confidence_half_life(profiles):
    """Profile should accept confidence_half_life_days."""
    profiles.put("claude-code/", {"confidence_half_life_days": 60})
    resolved = profiles.resolve("claude-code/test")
    assert resolved["confidence_half_life_days"] == 60

def test_profile_defaults_lifecycle_to_none(profiles):
    """Lifecycle fields should default to None (no policy)."""
    resolved = profiles.resolve("anything/test")
    assert resolved.get("ttl_days") is None
    assert resolved.get("confidence_threshold") is None
    assert resolved.get("min_age_days") is None
    assert resolved.get("confidence_half_life_days") is None

def test_child_profile_overrides_parent_ttl(profiles):
    """Child profile with explicit ttl_days=None should override parent's TTL."""
    profiles.put("wip/", {"ttl_days": 30})
    profiles.put("wip/important/", {"ttl_days": None})
    resolved = profiles.resolve("wip/important/test")
    assert resolved["ttl_days"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_lifecycle_policies.py -v`

- [ ] **Step 3: Add fields to DEFAULTS**

In `extraction_profiles.py`, add to DEFAULTS dict (~line 7):
```python
DEFAULTS = {
    "mode": "standard",
    "max_facts": 30,
    "max_fact_chars": 500,
    "half_life_days": 30,
    "single_call": False,
    "enabled": True,
    "rules": {},
    "ttl_days": None,
    "confidence_threshold": None,
    "min_age_days": None,
    "confidence_half_life_days": None,
}
```

No other changes needed — the cascade resolution in `resolve()` already handles new fields via the `{**DEFAULTS, ...}` pattern.

- [ ] **Step 4: Run test**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_lifecycle_policies.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add extraction_profiles.py tests/test_lifecycle_policies.py
git commit -m "feat: add lifecycle policy fields to extraction profiles"
```

---

### Task 2: Protect _policy_ metadata namespace

**Files:**
- Modify: `memory_engine.py` (~line 982 update_memory)
- Test: `tests/test_lifecycle_policies.py`

- [ ] **Step 1: Write failing test**

```python
def test_policy_metadata_protected_from_patch(engine):
    """PATCH metadata_patch should not overwrite _policy_ fields."""
    mem_id = engine.add_memories([{"text": "test", "source": "test/policy"}])[0]
    # Simulate policy setting evidence
    meta = engine._get_meta_by_id(mem_id)
    meta["_policy_archived_reason"] = "ttl"
    engine._save()

    # Try to overwrite via update_memory
    engine.update_memory(mem_id, metadata_patch={"_policy_archived_reason": "hacked"})
    updated = engine.get_memory(mem_id)
    assert updated.get("_policy_archived_reason") == "ttl"  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add _policy_ protection**

In `memory_engine.py`, modify the reserved fields check in `update_memory()` (~line 982):

```python
if metadata_patch:
    _reserved = {"id", "text", "source", "timestamp", "created_at", "updated_at", "entity_key"}
    for key, value in metadata_patch.items():
        if key in _reserved or key.startswith("_policy_"):
            continue
        meta[key] = value
    updated_fields.append("metadata")
```

- [ ] **Step 4: Run test**

- [ ] **Step 5: Commit**

```bash
git add memory_engine.py tests/test_lifecycle_policies.py
git commit -m "feat: protect _policy_ metadata namespace from ordinary writes"
```

---

### Task 3: Per-prefix confidence half-life in _enrich_with_confidence

**Files:**
- Modify: `memory_engine.py` (~line 906 _enrich_with_confidence)
- Test: `tests/test_lifecycle_policies.py`

- [ ] **Step 1: Write failing test**

```python
def test_per_prefix_confidence_half_life(engine, profiles):
    """Confidence should use per-prefix half-life when configured."""
    profiles.put("fast-decay/", {"confidence_half_life_days": 30})
    # Add a memory 60 days old to fast-decay prefix
    mem_id = engine.add_memories([{"text": "old memory", "source": "fast-decay/test"}])[0]
    # Manually set created_at to 60 days ago
    import datetime
    old_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)).isoformat()
    meta = engine._get_meta_by_id(mem_id)
    meta["created_at"] = old_date
    meta["updated_at"] = old_date
    engine._save()

    mem = engine.get_memory(mem_id)
    # With 30-day half-life and 60 days old: confidence = 0.5^(60/30) = 0.25
    assert 0.20 <= mem["confidence"] <= 0.30
```

- [ ] **Step 2: Run test to verify it fails** (currently uses hardcoded 90-day half-life)

- [ ] **Step 3: Modify _enrich_with_confidence**

Update `_enrich_with_confidence` to accept optional profile resolver:

```python
def _enrich_with_confidence(self, mem: Dict[str, Any]) -> Dict[str, Any]:
    anchor = mem.get("updated_at") or mem.get("created_at") or mem.get("timestamp")
    # Resolve per-prefix half-life
    half_life = 90.0  # default
    if hasattr(self, '_profiles') and self._profiles:
        source = mem.get("source", "")
        resolved = self._profiles.resolve(source)
        if resolved.get("confidence_half_life_days") is not None:
            half_life = resolved["confidence_half_life_days"]
    mem["confidence"] = round(self.compute_confidence(anchor, half_life_days=half_life), 4)
    return mem
```

**Important:** The engine needs access to extraction_profiles. Read how the engine is initialized (in app.py lifespan) to see if profiles is already passed or needs to be injected. If not available, store a reference: `self._profiles = extraction_profiles` during engine init.

**All read surfaces must use this:** `get_memory()`, `get_memories()`, `hybrid_search()`, `_search_no_reinforce()` — they all call `_enrich_with_confidence()`, so fixing it once covers all paths.

- [ ] **Step 4: Run test**

- [ ] **Step 5: Commit**

```bash
git add memory_engine.py tests/test_lifecycle_policies.py
git commit -m "feat: per-prefix confidence half-life via profile resolution"
```

---

### Task 4: enforce_policies engine method

**Files:**
- Modify: `memory_engine.py` (new method)
- Test: `tests/test_lifecycle_policies.py`

- [ ] **Step 1: Write failing tests**

```python
def test_enforce_ttl_dry_run(engine, profiles):
    """TTL policy should identify expired memories in dry-run."""
    profiles.put("wip/", {"ttl_days": 30})
    # Add old memory
    mem_id = engine.add_memories([{"text": "old wip", "source": "wip/test"}])[0]
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=True)
    assert result["summary"]["would_archive"] >= 1
    assert any(a["memory_id"] == mem_id and a["action"] == "would_archive" for a in result["actions"])

def test_enforce_ttl_execute(engine, profiles):
    """TTL policy should archive expired memories when dry_run=False."""
    profiles.put("wip/", {"ttl_days": 30})
    mem_id = engine.add_memories([{"text": "old wip", "source": "wip/test"}])[0]
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=False)
    assert result["summary"]["archived"] >= 1
    mem = engine.get_memory(mem_id)
    assert mem.get("archived") is True
    assert mem.get("_policy_archived_reason") == "ttl"

def test_enforce_confidence_threshold(engine, profiles):
    """Low-confidence memories should be archived with evidence."""
    profiles.put("claude-code/", {"confidence_threshold": 0.1, "min_age_days": 90})
    mem_id = engine.add_memories([{"text": "ancient", "source": "claude-code/test"}])[0]
    _set_age(engine, mem_id, days=365)  # very old, confidence near 0

    result = engine.enforce_policies(dry_run=False)
    assert any(a["memory_id"] == mem_id for a in result["actions"])
    mem = engine.get_memory(mem_id)
    assert mem.get("_policy_archived_reason") == "confidence"
    assert "_policy_archived_confidence" in mem

def test_enforce_excludes_pinned(engine, profiles):
    """Pinned memories should never be archived by policy."""
    profiles.put("wip/", {"ttl_days": 30})
    mem_id = engine.add_memories([{"text": "pinned wip", "source": "wip/test"}])[0]
    engine.update_memory(mem_id, pinned=True)
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=True)
    assert not any(a["memory_id"] == mem_id for a in result["actions"])
    assert result["summary"]["excluded_pinned"] >= 1

def test_enforce_excludes_already_archived(engine, profiles):
    """Already archived memories should be skipped."""
    profiles.put("wip/", {"ttl_days": 30})
    mem_id = engine.add_memories([{"text": "archived wip", "source": "wip/test"}])[0]
    engine.update_memory(mem_id, archived=True)
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=True)
    assert not any(a["memory_id"] == mem_id for a in result["actions"])

def test_enforce_ttl_takes_precedence_over_confidence(engine, profiles):
    """When both TTL and confidence match, TTL is the primary reason."""
    profiles.put("wip/", {"ttl_days": 30, "confidence_threshold": 0.5, "min_age_days": 7})
    mem_id = engine.add_memories([{"text": "both match", "source": "wip/test"}])[0]
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=True)
    action = next(a for a in result["actions"] if a["memory_id"] == mem_id)
    assert action["reasons"][0]["rule"] == "ttl"
    assert len(action["reasons"]) == 2  # both reasons reported

# Helper
def _set_age(engine, mem_id, days):
    import datetime
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    meta = engine._get_meta_by_id(mem_id)
    meta["created_at"] = old
    meta["updated_at"] = old
    engine._save()
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement enforce_policies**

Add to `memory_engine.py`:

```python
def enforce_policies(self, dry_run: bool = True) -> Dict[str, Any]:
    """Evaluate each memory against its resolved policy. Archive candidates if not dry_run."""
    actions = []
    excluded_pinned = 0
    excluded_archived = 0
    candidates_scanned = 0
    by_rule = {"ttl": 0, "confidence": 0}

    now = datetime.now(timezone.utc)

    for mem in self.metadata:
        if mem.get("archived"):
            excluded_archived += 1
            continue
        if mem.get("pinned"):
            excluded_pinned += 1
            continue

        candidates_scanned += 1
        source = mem.get("source", "")
        policy = self._profiles.resolve(source) if self._profiles else {}

        # Compute age
        anchor = mem.get("updated_at") or mem.get("created_at") or mem.get("timestamp")
        age_days = 0
        if anchor:
            try:
                age_days = (now - datetime.fromisoformat(anchor.replace("Z", "+00:00"))).days
            except (ValueError, TypeError):
                pass

        # Compute confidence with per-prefix half-life
        half_life = policy.get("confidence_half_life_days") or 90.0
        confidence = self.compute_confidence(anchor, half_life_days=half_life)

        reasons = []

        # TTL check
        ttl = policy.get("ttl_days")
        if ttl is not None and age_days > ttl:
            reasons.append({"rule": "ttl", "ttl_days": ttl, "age_days": age_days, "prefix": source})
            by_rule["ttl"] += 1

        # Confidence check
        threshold = policy.get("confidence_threshold")
        min_age = policy.get("min_age_days")
        if threshold is not None and min_age is not None:
            if confidence < threshold and age_days > min_age:
                reasons.append({
                    "rule": "confidence", "threshold": threshold,
                    "confidence": round(confidence, 4), "min_age_days": min_age, "prefix": source,
                })
                by_rule["confidence"] += 1

        if not reasons:
            continue

        action_type = "would_archive" if dry_run else "archived"
        actions.append({
            "memory_id": mem["id"],
            "source": source,
            "age_days": age_days,
            "confidence": round(confidence, 4),
            "reasons": reasons,
            "action": action_type,
        })

    # Execute if not dry_run
    if not dry_run and actions:
        archived_at = now.isoformat()
        for a in actions:
            mem_id = a["memory_id"]
            primary_reason = a["reasons"][0]  # TTL takes precedence
            evidence = {
                "_policy_archived_reason": primary_reason["rule"],
                "_policy_archived_policy": f"{primary_reason.get('prefix', '')} {primary_reason['rule']}",
                "_policy_archived_at": archived_at,
                "_policy_archived_confidence": a["confidence"],
                "_policy_archived_age_days": a["age_days"],
            }
            # Direct meta update (bypasses _policy_ protection since we're the policy engine)
            meta = self._get_meta_by_id(mem_id)
            meta["archived"] = True
            meta.update(evidence)
            # Set archived in Qdrant payload
            self.qdrant_store.set_payload(mem_id, {"archived": True, **evidence})
        self._save()

    return {
        "dry_run": dry_run,
        "actions": actions,
        "summary": {
            "candidates_scanned": candidates_scanned,
            "would_archive" if dry_run else "archived": len(actions),
            "by_rule": by_rule,
            "excluded_pinned": excluded_pinned,
            "excluded_already_archived": excluded_archived,
        },
    }
```

Note: Read `memory_engine.py` to verify `self.qdrant_store.set_payload()` exists and its signature. Also verify how `self._save()` works for metadata persistence. The batch path (single `_save()` at the end) avoids N backups.

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add memory_engine.py tests/test_lifecycle_policies.py
git commit -m "feat: enforce_policies engine method with TTL and confidence archival"
```

---

### Task 5: POST /maintenance/enforce-policies endpoint

**Files:**
- Modify: `app.py` (new endpoint)
- Test: `tests/test_lifecycle_policies.py`

- [ ] **Step 1: Write failing test**

```python
def test_enforce_policies_endpoint_dry_run(client):
    """POST /maintenance/enforce-policies should default to dry_run=true."""
    resp = client.post("/maintenance/enforce-policies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dry_run"] is True

def test_enforce_policies_requires_admin(client_readonly):
    """enforce-policies should require admin auth."""
    resp = client_readonly.post("/maintenance/enforce-policies")
    assert resp.status_code == 403
```

- [ ] **Step 2: Implement endpoint**

In `app.py`, add after the existing maintenance endpoints:

```python
@app.post("/maintenance/enforce-policies")
async def enforce_policies(
    request: Request,
    dry_run: bool = Query(True),
):
    """Enforce lifecycle policies (TTL + confidence archival). Dry-run by default."""
    auth = _get_auth(request)
    _require_admin(auth)
    result = memory.enforce_policies(dry_run=dry_run)
    if not dry_run:
        for a in result.get("actions", []):
            _audit(request, "memory.policy_archived", resource_id=str(a["memory_id"]), source=a["source"])
    return result
```

- [ ] **Step 3: Run tests and commit**

```bash
git add app.py tests/test_lifecycle_policies.py
git commit -m "feat: add POST /maintenance/enforce-policies endpoint"
```

---

## B2: Confidence as 5th RRF Signal

### Task 6: confidence_weight in hybrid_search

**Files:**
- Modify: `memory_engine.py` (hybrid_search ~line 1238)
- Modify: `app.py` (SearchRequest)
- Test: `tests/test_confidence_ranking.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_confidence_ranking.py
import pytest
import inspect
from memory_engine import MemoryEngine

def test_hybrid_search_accepts_confidence_weight():
    """hybrid_search should accept confidence_weight parameter."""
    sig = inspect.signature(MemoryEngine.hybrid_search)
    assert "confidence_weight" in sig.parameters

def test_weight_scaling_sums_to_one():
    """All 5 signal weights must sum to 1.0."""
    # Test the scaling formula
    vector_weight = 0.7
    recency_weight = 0.2
    feedback_weight = 0.15
    confidence_weight = 0.1

    total_auxiliary = min(feedback_weight + confidence_weight, 1.0)
    total_core = 1.0 - total_auxiliary
    eff_vector = vector_weight * total_core * (1.0 - recency_weight)
    eff_bm25 = (1.0 - vector_weight) * total_core * (1.0 - recency_weight)
    eff_recency = recency_weight * total_core

    total = eff_vector + eff_bm25 + eff_recency + feedback_weight + confidence_weight
    assert abs(total - 1.0) < 0.0001

def test_combined_weight_guard():
    """Combined auxiliary weights > 1.0 should be clamped."""
    feedback_weight = 0.8
    confidence_weight = 0.5
    total_auxiliary = min(feedback_weight + confidence_weight, 1.0)
    total_core = 1.0 - total_auxiliary
    assert total_core == 0.0  # core signals fully displaced
    assert total_auxiliary == 1.0  # clamped
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add confidence_weight to hybrid_search**

In `memory_engine.py`, modify `hybrid_search()`:

1. Add param: `confidence_weight: float = 0.0` (after `feedback_scores`)

2. **Replace** the 4-signal weight scaling at lines 1302-1307 with 5-signal scaling:
```python
# 5-signal weight scaling
feedback_weight = max(0.0, min(1.0, feedback_weight))
confidence_weight = max(0.0, min(1.0, confidence_weight))
total_auxiliary = min(feedback_weight + confidence_weight, 1.0)
total_core = 1.0 - total_auxiliary
effective_vector_weight = vector_weight * total_core * (1.0 - recency_weight)
effective_bm25_weight = (1.0 - vector_weight) * total_core * (1.0 - recency_weight)
effective_recency_weight = recency_weight * total_core
```

3. After the feedback RRF block (~line 1338), add confidence RRF:
```python
if confidence_weight > 0:
    conf_scored = []
    for doc_id in rrf_scores:
        meta = self._get_meta_by_id(doc_id)
        anchor = meta.get("updated_at") or meta.get("created_at") or meta.get("timestamp")
        # Per-prefix half-life
        half_life = 90.0
        if self._profiles:
            resolved = self._profiles.resolve(meta.get("source", ""))
            if resolved.get("confidence_half_life_days") is not None:
                half_life = resolved["confidence_half_life_days"]
        conf = self.compute_confidence(anchor, half_life_days=half_life)
        conf_scored.append((doc_id, conf))
    conf_scored.sort(key=lambda x: x[1], reverse=True)
    for rank, (doc_id, _) in enumerate(conf_scored):
        rrf_scores[doc_id] += confidence_weight * (1.0 / (rank + rrf_k))
```

4. Update `hybrid_search_explain()` similarly — add `confidence_weight` param, add to scoring_weights:
```python
"confidence": round(confidence_weight, 4),
```

- [ ] **Step 4: Add to SearchRequest in app.py**

```python
confidence_weight: float = Field(0.0, ge=0.0, le=1.0, description="Weight for confidence-based ranking (0=disabled)")
```

Pass to `hybrid_search()` in POST /search handler.

- [ ] **Step 5: Run tests**

- [ ] **Step 6: Commit**

```bash
git add memory_engine.py app.py tests/test_confidence_ranking.py
git commit -m "feat: add confidence as 5th RRF signal in hybrid search"
```

---

### Task 7: MCP confidence_weight param

**Files:**
- Modify: `mcp-server/index.js`

- [ ] **Step 1: Add param to memory_search tool**

In the schema (~line 51), add:
```javascript
confidence_weight: z.number().min(0).max(1).default(0).describe("Weight for confidence-based ranking (0=disabled)"),
```

In the handler, pass it:
```javascript
if (confidence_weight !== undefined && confidence_weight > 0) body.confidence_weight = confidence_weight;
```

- [ ] **Step 2: Commit**

```bash
git add mcp-server/index.js
git commit -m "feat: add confidence_weight param to MCP memory_search tool"
```

---

## Post-Implementation Checklist

- [ ] All baseline tests still pass
- [ ] Profile cascade resolves ttl_days, confidence_threshold, min_age_days, confidence_half_life_days
- [ ] Child profile can override parent's TTL with null
- [ ] enforce-policies defaults to dry_run=true
- [ ] enforce-policies requires admin auth
- [ ] TTL archival stores _policy_ evidence metadata
- [ ] Confidence archival stores _policy_ evidence metadata
- [ ] Pinned memories excluded from all policy archival
- [ ] Already archived memories excluded
- [ ] Both rules matching: TTL takes precedence, both reasons reported
- [ ] _policy_ metadata protected from ordinary PATCH writes
- [ ] confidence_weight in hybrid_search with 5-signal scaling
- [ ] Combined weight guard: feedback + confidence clamped to max 1.0
- [ ] Per-prefix confidence_half_life_days flows through all read surfaces
- [ ] search/explain includes confidence signal
- [ ] MCP memory_search has confidence_weight param
- [ ] Batch archive: single _save(), no per-memory backups, audit events after batch
