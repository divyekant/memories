# PPR Scoring for Graph-Aware Search

**Date:** 2026-03-26
**Status:** Draft
**Branch:** feat/ppr-scoring (targets develop)
**Depends on:** PR #61 (graph-aware search), PR #62 (graph eval)

## Problem

The current graph expansion uses flat scoring: `graph_support = seed_rrf * SEARCH_GRAPH_DECAY * graph_weight`. This has three limitations:

1. **No multi-hop:** Only 1-hop neighbors are discovered. A→B→C paths require B to be in the RRF pool.
2. **Arbitrary decay:** The 0.5 decay factor is hand-tuned, not principled.
3. **No convergence signal:** Nodes reached from multiple paths get additive bonuses but without degree normalization, high-degree hubs accumulate disproportionate scores.

## Solution

Replace `_graph_expand()` internals with truncated Personalized PageRank (PPR) on a scope-filtered bidirectional adjacency index. Based on HippoRAG (NeurIPS 2024) and FastGraphRAG.

## Scope

### In scope
- Bidirectional adjacency index built from `related_to` links
- Truncated PPR (2-3 iterations) seeded from RRF scores
- Scope-safe propagation (PPR runs on visible subgraph only)
- New constants: `PPR_ALPHA`, `PPR_MAX_ITERS`, `GRAPH_RESERVED_SLOTS`
- Same return contract from `_graph_expand()`
- Same `_merge_graph_results()` behavior (reserved slots kept)

### Out of scope
- scipy/networkx dependencies (pure Python PPR)
- Multi-link-type traversal (still `related_to` only)
- Removing reserved slots (keep for now, evaluate after benchmark)
- Cached adjacency across queries (build per-query for correctness)
- Auth-prefix filtering in engine (deferred — app.py still does post-search auth)

## Design

### Change 1: `_build_adjacency()` private method

Builds a bidirectional adjacency dict from memory metadata. Called once per graph-expanded search.

```python
def _build_adjacency(self, link_type: str = "related_to") -> Dict[int, Set[int]]:
    """Build bidirectional adjacency index for a link type.

    Replaces per-seed get_links(include_incoming=True) calls with a single
    metadata scan. Both directions are captured: if A→B exists, both
    adj[A] contains B and adj[B] contains A.
    """
    adj: Dict[int, Set[int]] = {}
    for m in self.metadata:
        mid = m["id"]
        for link in m.get("links", []):
            if link["type"] != link_type:
                continue
            tid = link["to_id"]
            if not self._id_exists(tid):
                continue  # skip dangling links
            adj.setdefault(mid, set()).add(tid)
            adj.setdefault(tid, set()).add(mid)
    return adj
```

**Performance:** Single O(M×L) scan where M = memories, L = avg links per memory. At 10K memories with 1-3 links each, this is ~30K operations — sub-millisecond.

### Change 2: Scope-filtered subgraph

Before running PPR, filter the adjacency to only include visible nodes:

```python
def _filter_adjacency(
    self,
    adj: Dict[int, Set[int]],
    source_prefix: Optional[str],
    include_archived: bool,
) -> Dict[int, Set[int]]:
    """Filter adjacency to visible subgraph.

    Scope is a boundary, not a hint — out-of-scope nodes cannot act as
    transit bridges for PPR propagation. This prevents hidden content from
    influencing visible ranking.
    """
    if not source_prefix and include_archived:
        return adj  # no filtering needed

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

### Change 3: Truncated PPR in `_graph_expand()`

Replace the current 1-hop neighbor collection with iterative PPR:

```python
def _graph_expand(self, direct_results, graph_weight, source_prefix, include_archived):
    # 1. Build and filter adjacency
    adj = self._build_adjacency("related_to")
    adj = self._filter_adjacency(adj, source_prefix, include_archived)

    # 2. Initialize PPR from RRF scores
    #    Personalization uses max-normalized RRF (not sum-normalized) so the
    #    top seed starts at 1.0 regardless of pool size. This keeps PPR scores
    #    stable as the seed pool grows.
    top_rrf = direct_results[0][1] if direct_results else 1.0
    personalization = {doc_id: score / top_rrf for doc_id, score in direct_results if score > 0}

    ppr = dict(personalization)

    # 3. Iterate PPR with proper dangling mass handling
    alpha = PPR_ALPHA  # 0.85 default (propagation strength)
    restart = 1.0 - alpha  # 0.15 (teleport back to personalization)

    for _ in range(PPR_MAX_ITERS):  # 3 iterations default
        new_ppr = {}

        # Collect dangling mass: score on nodes with no visible neighbors
        # (isolated seeds or nodes whose links were filtered by scope)
        dangling_mass = 0.0
        for node, score in ppr.items():
            if not adj.get(node):
                dangling_mass += score

        # Restart: every node gets restart * personalization + share of dangling
        # This ensures mass conservation — no score is lost
        dangling_share = alpha * dangling_mass  # redistribute dangling via restart
        for node, p_score in personalization.items():
            new_ppr[node] = new_ppr.get(node, 0.0) + restart * p_score
            # Dangling redistribution proportional to personalization
            new_ppr[node] += dangling_share * (p_score / sum(personalization.values()))

        # Propagation: distribute alpha * score to neighbors (degree-normalized)
        for node, score in ppr.items():
            neighbors = adj.get(node, set())
            if neighbors:
                share = alpha * score / len(neighbors)
                for neighbor in neighbors:
                    new_ppr[neighbor] = new_ppr.get(neighbor, 0.0) + share

        ppr = new_ppr

    # 4. Build candidates from PPR scores
    direct_ids = {doc_id for doc_id, _ in direct_results}
    max_ppr = max(ppr.values()) if ppr else 1.0
    candidates = {}

    for doc_id, ppr_score in ppr.items():
        original_score = personalization.get(doc_id, 0.0)
        graph_gain = ppr_score - original_score

        if doc_id in direct_ids and graph_gain <= 0:
            continue  # direct hit with no graph boost — skip

        # Threshold on relative PPR (% of max) rather than absolute value.
        # This is stable regardless of seed pool size.
        relative_ppr = ppr_score / max_ppr if max_ppr > 0 else 0.0
        if doc_id not in direct_ids and relative_ppr < PPR_MIN_RELATIVE:
            continue  # graph-only with negligible PPR score — skip

        # Track contributing seeds: seeds that have adjacency paths to this node.
        # For multi-hop (A→B→C), we track the top contributing seeds by PPR
        # contribution mass, not just direct adjacency. This is approximate but
        # more useful than exact path tracking (which PPR doesn't naturally provide).
        via = _trace_top_contributors(doc_id, personalization, adj, ppr)

        # Score scaling: derive inject_score from PPR rank relative to top seed.
        # inject_score stays in RRF magnitude so graph-only results compete
        # with direct results in _merge_graph_results().
        scaled_support = (graph_gain / max_ppr) * top_rrf * graph_weight
        scaled_inject = (ppr_score / max_ppr) * top_rrf  # NOT multiplied by graph_weight
        # inject_score uses full seed-scale score (same as current inject_score = seed_rrf)
        # graph_weight only affects additive support, not injection competitiveness

        candidates[doc_id] = {
            "graph_support": round(min(scaled_support, 0.33 * top_rrf), 6),
            "inject_score": round(min(scaled_inject, 0.33 * top_rrf), 6),
            "graph_via": via,
        }

    info = {
        "seeds": [{"id": s[0], "rrf_score": round(s[1], 6)}
                  for s in direct_results[:min(10, len(direct_results))]],
        "neighbors_found": sum(len(n) for n in adj.values()) // 2,
        "neighbors_filtered": 0,  # filtering happens at adjacency level
        "neighbors_added": len(candidates),
        "ppr_iterations": PPR_MAX_ITERS,
        "ppr_alpha": PPR_ALPHA,
    }

    return candidates, info


def _trace_top_contributors(doc_id, personalization, adj, ppr, max_via=5):
    """Approximate top contributing seeds for a PPR-scored node.

    Since PPR doesn't track exact paths, we use a heuristic: the seed's
    personalization weight times the adjacency proximity (1-hop = direct,
    2-hop = via shared neighbor). Returns top seeds by estimated contribution.
    """
    contributions = []
    neighbors = adj.get(doc_id, set())
    for seed_id, p_score in personalization.items():
        if seed_id == doc_id:
            continue
        if seed_id in neighbors:
            # 1-hop: direct link
            contributions.append((seed_id, p_score))
        else:
            # 2-hop: check if any neighbor of doc_id is also a neighbor of seed
            seed_neighbors = adj.get(seed_id, set())
            if neighbors & seed_neighbors:
                contributions.append((seed_id, p_score * 0.5))  # decay for 2-hop
    contributions.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in contributions[:max_via]]
```

### Change 4: New constants

```python
PPR_ALPHA = float(os.environ.get("SEARCH_PPR_ALPHA", "0.85"))
PPR_MAX_ITERS = int(os.environ.get("SEARCH_PPR_MAX_ITERS", "3"))
PPR_MIN_RELATIVE = float(os.environ.get("SEARCH_PPR_MIN_RELATIVE", "0.05"))
# PPR_MIN_RELATIVE: minimum PPR score as fraction of max PPR score.
# Relative threshold is stable regardless of seed pool size (unlike absolute threshold).
GRAPH_RESERVED_SLOTS = int(os.environ.get("SEARCH_GRAPH_RESERVED_SLOTS", "2"))
```

`SEARCH_GRAPH_SEED_K` and `SEARCH_GRAPH_DECAY` become deprecated (PPR handles both). `SEARCH_GRAPH_MAX_NEIGHBORS` retained for the reserved slots cap in `_merge_graph_results()`, renamed to `GRAPH_RESERVED_SLOTS` for clarity.

### Change 5: `_merge_graph_results()` update

Minimal change — replace `SEARCH_GRAPH_MAX_NEIGHBORS` with `GRAPH_RESERVED_SLOTS` in the reserved slots calculation:

```python
reserved = min(GRAPH_RESERVED_SLOTS, len(graph_only), k)
```

All other merge/annotation/threshold logic stays unchanged.

### Change 6: `hybrid_search_explain()` update

Add PPR parameters to graph explain section:

```python
"graph": {
    "enabled": True,
    "graph_weight": 0.1,
    "ppr_alpha": 0.85,
    "ppr_iterations": 3,
    "seeds": [...],
    "neighbors_found": 42,
    "neighbors_added": 5,
}
```

### What doesn't change

- `hybrid_search()` call structure (fast path when graph_weight=0)
- `_merge_graph_results()` merge/annotate/threshold/reinforce logic
- Result annotations: `match_type`, `base_rrf_score`, `graph_support`, `graph_via`
- MCP and HTTP API parameters (`graph_weight` param)
- Graph-only results excluded when `threshold` is set (added in PR #61 fix commit 64f6aec)
- Graph-only results not reinforced

### Scope safety

PPR runs only on the **visible subgraph**. Nodes that fail `source_prefix` or `include_archived` checks are removed from the adjacency before propagation. This prevents:
- Cross-prefix information leakage via graph transit
- Archived content influencing active search results
- Out-of-scope nodes acting as bridges between in-scope clusters

Defense-in-depth: results are also filtered post-PPR in `_merge_graph_results()`.

### Performance

| Operation | Cost | Notes |
|---|---|---|
| Build adjacency | O(M×L) | M=memories, L=avg links. ~10K × 2 = 20K ops |
| Filter adjacency | O(M) | Single metadata scan |
| PPR iteration | O(N×D) per iter | N=visible nodes with score, D=avg degree |
| 3 iterations | O(3×N×D) | With sparse links: ~1000 scored nodes × 2 neighbors = 6K ops |
| Total overhead | <5ms | At 10K memories with sparse links |

### Testing Strategy

**Unit tests (replace exact-value assertions with semantic ones):**
- PPR with no links → candidates empty, no errors
- PPR with 1-hop link → neighbor discovered with positive score
- PPR with 2-hop chain A→B→C → C discovered (multi-hop)
- PPR multi-seed convergence → node reached from multiple seeds scores higher than single-seed
- PPR scope filtering → out-of-scope nodes excluded from propagation
- PPR scope safety → out-of-scope transit node doesn't bridge in-scope clusters
- PPR archived filtering → archived nodes excluded unless include_archived=True
- PPR alpha sensitivity → higher alpha = more propagation to neighbors
- PPR with graph_weight=0 → fast path, no PPR run
- Reserved slots still work with PPR scoring
- Graph-only PPR results excluded when threshold set
- Graph-only PPR results not reinforced

**Integration tests:**
- Full `hybrid_search()` with PPR → graph neighbor appears in results
- `hybrid_search_explain()` includes PPR params in graph section
- MuSiQue benchmark: delta >= +10% (no regression from current)
- Voltis synthetic: delta >= +15% (no regression from current)

### Eval Plan

Run before/after on both benchmarks:
1. MuSiQue 50q: current baseline OFF=48% ON=62% (+14%)
2. Voltis 2K: current baseline OFF=67% ON=87% (+20%)
3. Compare PPR vs flat scoring on same questions

### Risks

1. **PPR score calibration:** PPR scores are in a different range than RRF scores. Need scaling factor to make graph_support/inject_score competitive. Mitigated by scaling to top_rrf magnitude.
2. **Multi-hop noise:** 2-hop neighbors may be less relevant than 1-hop. Mitigated by PPR's natural decay (85% per hop) and min score threshold.
3. **Adjacency build cost:** Per-query metadata scan. Mitigated by being O(M×L) which is fast for 10K×2.
4. **Scope filtering may break 2-hop paths:** Intentional — scope is a boundary, not a hint (Codex design decision).
