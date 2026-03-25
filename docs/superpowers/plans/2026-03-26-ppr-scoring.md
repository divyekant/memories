# PPR Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat graph scoring with Personalized PageRank in `_graph_expand()` for mathematically principled multi-hop memory retrieval.

**Architecture:** New `_build_adjacency()` and `_filter_adjacency()` methods build a scope-filtered bidirectional adjacency index. Rewritten `_graph_expand()` runs truncated PPR (3 iterations, alpha=0.85) on this index, seeded from max-normalized RRF scores. Same return contract, same `_merge_graph_results()` consumer.

**Tech Stack:** Python 3.11, pytest, no new dependencies

**Spec:** `docs/superpowers/specs/2026-03-26-ppr-scoring-design.md`

---

### Task 1: Add new constants + `_build_adjacency()` — tests

**Files:**
- Modify: `tests/test_graph_search.py`

- [ ] **Step 1: Write test — adjacency builds bidirectional edges**

Add new test class at the end of `tests/test_graph_search.py`:

```python
class TestBuildAdjacency:
    """Test _build_adjacency() method."""

    def test_builds_bidirectional_edges(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "B", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 2 in adj.get(1, set())
        assert 1 in adj.get(2, set())

    def test_skips_non_related_to_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [
                 {"to_id": 2, "type": "supersedes", "created_at": now},
                 {"to_id": 3, "type": "related_to", "created_at": now},
             ]},
            {"id": 2, "text": "B", "source": "t", "created_at": now},
            {"id": 3, "text": "C", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 2 not in adj.get(1, set())
        assert 3 in adj.get(1, set())

    def test_skips_dangling_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 999, "type": "related_to", "created_at": now}]},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 999 not in adj.get(1, set())

    def test_skips_self_links(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 1, "type": "related_to", "created_at": now}]},
        ]
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert 1 not in adj.get(1, set())

    def test_empty_metadata_returns_empty(self, engine):
        engine.metadata = []
        engine._rebuild_id_map()
        adj = engine._build_adjacency()
        assert adj == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_graph_search.py::TestBuildAdjacency -v
```

Expected: FAIL — `_build_adjacency` doesn't exist yet.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_graph_search.py
git commit -m "test: add failing tests for _build_adjacency()"
```

---

### Task 2: Implement `_build_adjacency()` + new constants

**Files:**
- Modify: `memory_engine.py:38-41` (constants)
- Modify: `memory_engine.py` (add method before `_graph_expand`)

- [ ] **Step 1: Replace constants block (lines 38-41)**

Change:
```python
# Graph expansion tuning knobs (env-configurable)
SEARCH_GRAPH_SEED_K = int(os.environ.get("SEARCH_GRAPH_SEED_K", "0"))  # 0 = use all top-k results as seeds
SEARCH_GRAPH_MAX_NEIGHBORS = int(os.environ.get("SEARCH_GRAPH_MAX_NEIGHBORS", "2"))
SEARCH_GRAPH_DECAY = float(os.environ.get("SEARCH_GRAPH_DECAY", "0.5"))
```
To:
```python
# Graph expansion tuning knobs (env-configurable)
# Legacy constants (deprecated — PPR handles seed selection and decay)
SEARCH_GRAPH_SEED_K = int(os.environ.get("SEARCH_GRAPH_SEED_K", "0"))
SEARCH_GRAPH_MAX_NEIGHBORS = int(os.environ.get("SEARCH_GRAPH_MAX_NEIGHBORS", "2"))
SEARCH_GRAPH_DECAY = float(os.environ.get("SEARCH_GRAPH_DECAY", "0.5"))

# PPR scoring constants
PPR_ALPHA = float(os.environ.get("SEARCH_PPR_ALPHA", "0.85"))
PPR_MAX_ITERS = int(os.environ.get("SEARCH_PPR_MAX_ITERS", "3"))
PPR_MIN_RELATIVE = float(os.environ.get("SEARCH_PPR_MIN_RELATIVE", "0.05"))
GRAPH_RESERVED_SLOTS = int(os.environ.get("SEARCH_GRAPH_RESERVED_SLOTS", "2"))
```

- [ ] **Step 2: Add `_build_adjacency()` method**

Add to `MemoryEngine` class, just before `_graph_expand()`:

```python
def _build_adjacency(self, link_type: str = "related_to") -> Dict[int, Set[int]]:
    """Build bidirectional adjacency index for a link type.

    Single O(M*L) scan replaces per-seed get_links(include_incoming=True).
    """
    adj: Dict[int, Set[int]] = {}
    for m in self.metadata:
        mid = m["id"]
        for link in m.get("links", []):
            if link["type"] != link_type:
                continue
            tid = link["to_id"]
            if not self._id_exists(tid):
                continue
            if tid == mid:
                continue
            adj.setdefault(mid, set()).add(tid)
            adj.setdefault(tid, set()).add(mid)
    return adj
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest tests/test_graph_search.py::TestBuildAdjacency -v --tb=short
```

Expected: ALL pass.

- [ ] **Step 4: Commit**

```bash
git add memory_engine.py tests/test_graph_search.py
git commit -m "feat: add _build_adjacency() + PPR constants"
```

---

### Task 3: Add `_filter_adjacency()` — tests + implementation

**Files:**
- Modify: `tests/test_graph_search.py`
- Modify: `memory_engine.py`

- [ ] **Step 1: Write tests**

```python
class TestFilterAdjacency:
    """Test _filter_adjacency() scope filtering."""

    def test_no_filter_returns_original(self, engine):
        adj = {1: {2}, 2: {1}}
        result = engine._filter_adjacency(adj, None, True)
        assert result == adj

    def test_filters_by_source_prefix(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "wip/proj", "created_at": now},
            {"id": 2, "text": "B", "source": "learning/other", "created_at": now},
            {"id": 3, "text": "C", "source": "wip/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = {1: {2, 3}, 2: {1}, 3: {1}}
        result = engine._filter_adjacency(adj, "wip/", False)
        assert 2 not in result  # out-of-scope node removed entirely
        assert result.get(1) == {3}  # 1's neighbor 2 removed
        assert result.get(3) == {1}

    def test_filters_archived(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now},
            {"id": 2, "text": "B", "source": "t", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        adj = {1: {2}, 2: {1}}
        result = engine._filter_adjacency(adj, None, False)
        assert 2 not in result
        assert 1 not in result  # 1 has no visible neighbors after filtering

    def test_includes_archived_when_flag_set(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now},
            {"id": 2, "text": "B", "source": "t", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        adj = {1: {2}, 2: {1}}
        result = engine._filter_adjacency(adj, None, True)
        assert result == adj

    def test_scope_blocks_transit(self, engine):
        """Out-of-scope node B cannot bridge in-scope A and C."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "wip/proj", "created_at": now},
            {"id": 2, "text": "B", "source": "learning/other", "created_at": now},
            {"id": 3, "text": "C", "source": "wip/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        adj = {1: {2}, 2: {1, 3}, 3: {2}}
        result = engine._filter_adjacency(adj, "wip/", False)
        # A and C are both visible but have no visible path between them
        assert 1 not in result  # 1's only neighbor (2) is filtered
        assert 3 not in result  # 3's only neighbor (2) is filtered
```

- [ ] **Step 2: Implement `_filter_adjacency()`**

Add to `MemoryEngine` class, after `_build_adjacency()`:

```python
def _filter_adjacency(
    self,
    adj: Dict[int, Set[int]],
    source_prefix: Optional[str],
    include_archived: bool,
) -> Dict[int, Set[int]]:
    """Filter adjacency to visible subgraph.

    Scope is a boundary — out-of-scope nodes cannot act as transit bridges.
    Nodes with no visible neighbors are omitted from the result.
    """
    if not source_prefix and include_archived:
        return adj

    visible = set()
    for m in self.metadata:
        if source_prefix and not m.get("source", "").startswith(source_prefix):
            continue
        if not include_archived and m.get("archived"):
            continue
        visible.add(m["id"])

    filtered = {}
    for node, neighbors in adj.items():
        if node not in visible:
            continue
        filtered_neighbors = neighbors & visible
        if filtered_neighbors:
            filtered[node] = filtered_neighbors
    return filtered
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest tests/test_graph_search.py::TestFilterAdjacency tests/test_graph_search.py::TestBuildAdjacency -v --tb=short
```

Expected: ALL pass.

- [ ] **Step 4: Commit**

```bash
git add memory_engine.py tests/test_graph_search.py
git commit -m "feat: add _filter_adjacency() for scope-safe PPR subgraph"
```

---

### Task 4: Rewrite `_graph_expand()` with PPR — tests

**Files:**
- Modify: `tests/test_graph_search.py`

- [ ] **Step 1: Replace existing `TestGraphExpand` tests with PPR-based semantic tests**

Replace the `TestGraphExpand` class entirely. The old tests asserted exact flat-scoring values. New tests assert semantic properties (ordering, monotonicity, multi-hop discovery):

```python
class TestGraphExpand:
    """Test _graph_expand() with PPR scoring."""

    def test_no_links_returns_empty(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "seed", "source": "t", "created_at": now}]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert candidates == {}

    def test_1hop_neighbor_discovered(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "neighbor", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 in candidates
        assert candidates[2]["graph_support"] > 0
        assert candidates[2]["inject_score"] > 0

    def test_2hop_neighbor_discovered(self, engine):
        """PPR discovers 2-hop neighbors via iterative propagation."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "bridge", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "2hop target", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 3 in candidates  # 2-hop discovery!

    def test_1hop_scores_higher_than_2hop(self, engine):
        """PPR decay: 1-hop neighbors score higher than 2-hop."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "1hop", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "2hop", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert candidates[2]["inject_score"] > candidates[3]["inject_score"]

    def test_multi_seed_convergence(self, engine):
        """Node reached from multiple seeds scores higher than single-seed."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "B", "source": "t", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "shared", "source": "t", "created_at": now},
            {"id": 4, "text": "C", "source": "t", "created_at": now,
             "links": [{"to_id": 5, "type": "related_to", "created_at": now}]},
            {"id": 5, "text": "single", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand(
            [(1, 0.025), (2, 0.020), (4, 0.015)], 0.1, None, False
        )
        # Node 3 (reached from seeds 1 + 2) should score higher than node 5 (from seed 4 only)
        assert candidates[3]["inject_score"] >= candidates[5]["inject_score"]

    def test_scope_filters_neighbors(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "wip/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "out", "source": "learning/other", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, "wip/", False)
        assert 2 not in candidates

    def test_scope_blocks_transit(self, engine):
        """Out-of-scope B cannot bridge in-scope A to in-scope C."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A", "source": "wip/proj", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "B (hidden)", "source": "learning/other", "created_at": now,
             "links": [{"to_id": 3, "type": "related_to", "created_at": now}]},
            {"id": 3, "text": "C", "source": "wip/proj", "created_at": now},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, "wip/", False)
        assert 3 not in candidates  # C unreachable because B is out of scope

    def test_archived_filtered(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "seed", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "archived", "source": "t", "created_at": now, "archived": True},
        ]
        engine._rebuild_id_map()
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 not in candidates

    def test_empty_direct_results(self, engine):
        candidates, info = engine._graph_expand([], 0.1, None, False)
        assert candidates == {}

    def test_disconnected_subgraphs_no_leak(self, engine):
        """Two disconnected clusters — PPR doesn't leak between them."""
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [
            {"id": 1, "text": "A1", "source": "t", "created_at": now,
             "links": [{"to_id": 2, "type": "related_to", "created_at": now}]},
            {"id": 2, "text": "A2", "source": "t", "created_at": now},
            {"id": 3, "text": "B1", "source": "t", "created_at": now,
             "links": [{"to_id": 4, "type": "related_to", "created_at": now}]},
            {"id": 4, "text": "B2", "source": "t", "created_at": now},
        ]
        engine._rebuild_id_map()
        # Only seed from cluster A
        candidates, _ = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert 2 in candidates      # A2 reachable from A1
        assert 4 not in candidates   # B2 not reachable — disconnected

    def test_info_includes_ppr_params(self, engine):
        now = datetime.now(timezone.utc).isoformat()
        engine.metadata = [{"id": 1, "text": "A", "source": "t", "created_at": now}]
        engine._rebuild_id_map()
        _, info = engine._graph_expand([(1, 0.025)], 0.1, None, False)
        assert "ppr_iterations" in info
        assert "ppr_alpha" in info
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/pytest tests/test_graph_search.py::TestGraphExpand -v --tb=short
```

Expected: FAIL — old `_graph_expand` doesn't support 2-hop, different score semantics.

- [ ] **Step 3: Commit**

```bash
git add tests/test_graph_search.py
git commit -m "test: replace flat-scoring tests with PPR semantic tests"
```

---

### Task 5: Rewrite `_graph_expand()` with PPR — implementation

**Files:**
- Modify: `memory_engine.py:1420-1525`

- [ ] **Step 1: Replace `_graph_expand()` method entirely**

Replace from `def _graph_expand(` through the closing `return candidates, info` with the PPR implementation from the spec. Key sections:

1. Build + filter adjacency
2. Max-normalize personalization from RRF scores
3. Iterate PPR with dangling mass handling
4. Build candidates with relative threshold + score scaling
5. Track contributors via `_trace_top_contributors`

The full implementation is in `docs/superpowers/specs/2026-03-26-ppr-scoring-design.md` Change 3. Make `_trace_top_contributors` a module-level helper (not a method) since it doesn't access `self`.

Note: keep the same method signature — `(self, direct_results, graph_weight, source_prefix, include_archived)`. The return type is unchanged: `(candidates_dict, info_dict)`.

- [ ] **Step 2: Run PPR tests**

```bash
.venv/bin/pytest tests/test_graph_search.py::TestGraphExpand -v --tb=short
```

Expected: ALL pass.

- [ ] **Step 3: Run full graph test suite**

```bash
.venv/bin/pytest tests/test_graph_search.py -v --tb=short
```

Expected: ALL pass (adjacency + filter + PPR + hybrid integration + explain).

- [ ] **Step 4: Run regression tests**

```bash
.venv/bin/pytest tests/test_recency_boost.py tests/test_search_feedback.py tests/test_search_explain.py tests/test_memory_api.py -v --tb=short
```

Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add memory_engine.py
git commit -m "feat: rewrite _graph_expand() with truncated PPR scoring"
```

---

### Task 6: Update `_merge_graph_results()` reserved slots constant

**Files:**
- Modify: `memory_engine.py:1580`

- [ ] **Step 1: Replace `SEARCH_GRAPH_MAX_NEIGHBORS` with `GRAPH_RESERVED_SLOTS`**

Change line 1580:
```python
reserved = min(SEARCH_GRAPH_MAX_NEIGHBORS, len(graph_only), k)
```
To:
```python
reserved = min(GRAPH_RESERVED_SLOTS, len(graph_only), k)
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/test_graph_search.py -v --tb=short
```

Expected: ALL pass.

- [ ] **Step 3: Commit**

```bash
git add memory_engine.py
git commit -m "refactor: use GRAPH_RESERVED_SLOTS constant in _merge_graph_results()"
```

---

### Task 7: Update `hybrid_search_explain()` graph info

**Files:**
- Modify: `memory_engine.py` (explain variant graph section)

- [ ] **Step 1: Add PPR params to explain graph output**

Find the `graph_explain` dict in `hybrid_search_explain()` and ensure it includes `ppr_alpha` and `ppr_iterations` from the info dict returned by `_graph_expand()`.

The info dict already includes these fields from the PPR implementation. The explain code at the graph section should spread them into the explain output.

- [ ] **Step 2: Run explain tests**

```bash
.venv/bin/pytest tests/test_graph_search.py::TestHybridSearchExplainGraph tests/test_search_explain.py -v --tb=short
```

Expected: ALL pass.

- [ ] **Step 3: Commit**

```bash
git add memory_engine.py
git commit -m "feat: add PPR params to hybrid_search_explain() graph section"
```

---

### Task 8: Final verification

**Files:** All modified files

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/pytest --tb=short -q
```

Expected: All tests pass. No regressions.

- [ ] **Step 2: Verify no syntax issues**

```bash
python3 -m py_compile memory_engine.py
```

- [ ] **Step 3: Run graph tests one more time**

```bash
.venv/bin/pytest tests/test_graph_search.py -v
```

Expected: ALL pass.

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "chore: cleanup after PPR scoring implementation"
```
