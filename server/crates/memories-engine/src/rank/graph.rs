//! The graph step of `MemoryEngine.hybrid_search` (v5.19.0):
//! `_build_adjacency`, `_filter_adjacency`, `_graph_expand` and
//! `_trace_top_contributors`.

use std::borrow::Cow;
use std::collections::HashSet;

use super::pyorder::{OrderedMap, PySet};
use super::scores::py_round;

/// The graph settings. Python reads them from `SEARCH_PPR_ALPHA`,
/// `SEARCH_PPR_MAX_ITERS`, `SEARCH_PPR_MIN_RELATIVE` and
/// `SEARCH_GRAPH_RESERVED_SLOTS` (else `SEARCH_GRAPH_MAX_NEIGHBORS`). The
/// default is the Python default.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GraphParams {
    pub alpha: f64,
    pub max_iters: usize,
    pub min_relative: f64,
    /// Places kept for records that only the graph found.
    pub reserved_slots: usize,
}

impl Default for GraphParams {
    fn default() -> Self {
        Self {
            alpha: 0.85,
            max_iters: 3,
            min_relative: 0.05,
            reserved_slots: 2,
        }
    }
}

/// Upper limit of a graph score, as a part of the top direct score.
pub const GRAPH_CAP: f64 = 0.33;

/// The `related_to` graph with no direction: each record's neighbors.
pub type Adjacency = OrderedMap<PySet>;

/// `_build_adjacency("related_to")`. `links` gives `(from, to, type)` for
/// each link, in the order of the stored records and of their links.
pub fn build_adjacency<'a>(
    links: impl IntoIterator<Item = (i64, i64, &'a str)>,
    exists: impl Fn(i64) -> bool,
) -> Adjacency {
    let mut adj = Adjacency::new();
    for (from, to, kind) in links {
        if kind != "related_to" || !exists(to) || to == from {
            continue;
        }
        adj.entry(from, PySet::new).add(to);
        adj.entry(to, PySet::new).add(from);
    }
    adj
}

/// `_filter_adjacency`: the graph of visible records only. Records with no
/// visible neighbor drop out. `visible` is `None` when no scope applies.
pub fn filter_adjacency<'a>(
    adj: &'a Adjacency,
    visible: Option<&dyn Fn(i64) -> bool>,
) -> Cow<'a, Adjacency> {
    let Some(visible) = visible else {
        return Cow::Borrowed(adj);
    };
    let shown: PySet = adj.keys().filter(|&id| visible(id)).collect();
    let mut out = Adjacency::new();
    for (node, neighbors) in adj.iter() {
        if !shown.contains(node) {
            continue;
        }
        let kept = neighbors.intersection(&shown);
        if !kept.is_empty() {
            out.entry(node, || kept);
        }
    }
    Cow::Owned(out)
}

/// `_trace_top_contributors`: up to 5 seeds with a path of 1 to 3 links.
pub fn trace_top_contributors(
    doc_id: i64,
    personalization: &OrderedMap<f64>,
    adj: &Adjacency,
) -> Vec<i64> {
    let empty = PySet::new();
    let neighbors = adj.get(doc_id).unwrap_or(&empty);
    let mut found: Vec<(i64, f64)> = Vec::new();
    for (seed, &p) in personalization.iter() {
        if seed == doc_id {
            continue;
        }
        if neighbors.contains(seed) {
            found.push((seed, p));
            continue;
        }
        let seed_neighbors = adj.get(seed).unwrap_or(&empty);
        if neighbors.intersects(seed_neighbors) {
            found.push((seed, p * 0.5));
        } else if neighbors
            .iter()
            .any(|n| adj.get(n).unwrap_or(&empty).intersects(seed_neighbors))
        {
            found.push((seed, p * 0.25));
        }
    }
    sort_desc(&mut found, |c| c.1);
    found.into_iter().take(5).map(|c| c.0).collect()
}

/// Python `sorted(..., key=..., reverse=True)`: stable, high to low.
pub fn sort_desc<T>(items: &mut [T], key: impl Fn(&T) -> f64) {
    items.sort_by(|a, b| {
        key(b)
            .partial_cmp(&key(a))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
}

/// Python `min(a, b)`.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

#[derive(Clone, Debug, PartialEq)]
pub struct GraphCandidate {
    pub graph_support: f64,
    pub inject_score: f64,
    pub graph_via: Vec<i64>,
}

/// The `graph` block of `/search/explain`, less `enabled` and `graph_weight`.
#[derive(Clone, Debug, PartialEq)]
pub struct GraphInfo {
    /// The first 10 seeds: (id, rrf score to 6 decimals).
    pub seeds: Vec<(i64, f64)>,
    pub neighbors_found: usize,
    pub neighbors_filtered: usize,
    pub neighbors_added: usize,
    pub ppr_iterations: usize,
    pub ppr_alpha: f64,
}

/// Python `sum(values)` of floats: plain addition from the left.
fn py_sum(values: impl Iterator<Item = f64>) -> f64 {
    values.fold(0.0, |acc, v| acc + v)
}

/// `_graph_expand`: Personalized PageRank from the fused scores, which come
/// in `direct` from high to low.
pub fn graph_expand(
    direct: &[(i64, f64)],
    graph_weight: f64,
    full_adj: &Adjacency,
    visible: Option<&dyn Fn(i64) -> bool>,
    filtered: Option<&Adjacency>,
    params: &GraphParams,
) -> (OrderedMap<GraphCandidate>, GraphInfo) {
    let mut info = GraphInfo {
        seeds: Vec::new(),
        neighbors_found: 0,
        neighbors_filtered: 0,
        neighbors_added: 0,
        ppr_iterations: params.max_iters,
        ppr_alpha: params.alpha,
    };
    let mut candidates = OrderedMap::new();
    if direct.is_empty() {
        return (candidates, info);
    }
    let adj = match filtered {
        Some(f) => Cow::Borrowed(f),
        None => filter_adjacency(full_adj, visible),
    };
    let edges = |a: &Adjacency| a.iter().map(|(_, n)| n.len()).sum::<usize>() / 2;
    let unfiltered = edges(full_adj);
    info.neighbors_found = unfiltered;
    info.neighbors_filtered = unfiltered.saturating_sub(edges(&adj));

    let top = direct[0].1;
    if top <= 0.0 {
        return (candidates, info);
    }
    let personalization: OrderedMap<f64> = direct
        .iter()
        .filter(|d| d.1 > 0.0)
        .map(|&(id, s)| (id, s / top))
        .collect();
    info.seeds = direct
        .iter()
        .take(10)
        .map(|&(id, s)| (id, py_round(s, 6)))
        .collect();

    let p_total = py_sum(personalization.iter().map(|(_, &p)| p));
    let p_total = if p_total == 0.0 { 1.0 } else { p_total };
    let restart = 1.0 - params.alpha;
    let mut ppr = personalization.clone();
    for _ in 0..params.max_iters {
        let mut next: OrderedMap<f64> = OrderedMap::new();
        let dangling = py_sum(
            ppr.iter()
                .filter(|&(node, _)| adj.get(node).is_none_or(PySet::is_empty))
                .map(|(_, &s)| s),
        );
        let dangling_share = params.alpha * dangling;
        for (node, &p) in personalization.iter() {
            let v = next.entry(node, || 0.0);
            *v += restart * p;
            *v += dangling_share * (p / p_total);
        }
        for (node, &score) in ppr.iter() {
            let Some(neighbors) = adj.get(node).filter(|n| !n.is_empty()) else {
                continue;
            };
            let share = params.alpha * score / neighbors.len() as f64;
            for nb in neighbors.iter() {
                *next.entry(nb, || 0.0) += share;
            }
        }
        ppr = next;
    }

    let max_ppr = ppr
        .iter()
        .map(|(_, &v)| v)
        .reduce(|m, v| if v > m { v } else { m })
        .unwrap_or(1.0);
    let direct_ids: HashSet<i64> = direct.iter().map(|d| d.0).collect();
    for (doc_id, &score) in ppr.iter() {
        let gain = score - personalization.get(doc_id).copied().unwrap_or(0.0);
        let is_direct = direct_ids.contains(&doc_id);
        if is_direct && gain <= 0.0 {
            continue;
        }
        let relative = if max_ppr > 0.0 { score / max_ppr } else { 0.0 };
        if !is_direct && relative < params.min_relative {
            continue;
        }
        let support = (gain / max_ppr) * top * graph_weight;
        let inject = (score / max_ppr) * top;
        let cap = GRAPH_CAP * top;
        candidates.entry(doc_id, || GraphCandidate {
            graph_support: py_round(py_min(support, cap), 6),
            inject_score: py_round(py_min(inject, cap), 6),
            graph_via: trace_top_contributors(doc_id, &personalization, &adj),
        });
    }
    info.neighbors_added = candidates.len();
    (candidates, info)
}
