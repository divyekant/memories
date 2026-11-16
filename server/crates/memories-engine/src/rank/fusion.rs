//! Reciprocal rank fusion of `MemoryEngine.hybrid_search` (v5.19.0) and
//! `_merge_graph_results`. The functions do no input or output: the caller
//! gives the hits and a lookup for the stored fields of a record.

use super::graph::{Adjacency, GraphCandidate, GraphInfo, graph_expand, sort_desc};
use super::pydt::PyDateTime;
use super::pyorder::OrderedMap;
use super::scores::{
    Weights, compute_confidence, first_set, passes_temporal_filter, py_round, recency_score,
};

pub const RRF_K: usize = 60;
pub const GRAPH_RESERVED_SLOTS: usize = 2;
pub const MAX_K: usize = 100;
const DEFAULT_CONFIDENCE_HALF_LIFE: f64 = 90.0;

/// The stored fields of a record that the rank rules read.
#[derive(Clone, Copy, Debug, Default)]
pub struct RecordFields<'a> {
    pub document_at: Option<&'a str>,
    pub created_at: Option<&'a str>,
    pub timestamp: Option<&'a str>,
    pub updated_at: Option<&'a str>,
    pub last_reinforced_at: Option<&'a str>,
    /// `confidence_half_life_days` of the extraction profile of the source.
    pub confidence_half_life_days: Option<f64>,
}

impl RecordFields<'_> {
    /// `document_at or created_at or timestamp`.
    pub fn document_date(&self) -> Option<&str> {
        first_set(&[self.document_at, self.created_at, self.timestamp])
    }

    /// `last_reinforced_at or updated_at or created_at or timestamp`.
    pub fn confidence_anchor(&self) -> Option<&str> {
        first_set(&[
            self.last_reinforced_at,
            self.updated_at,
            self.created_at,
            self.timestamp,
        ])
    }

    fn raw_confidence(&self, now: &PyDateTime) -> f64 {
        let half_life = self
            .confidence_half_life_days
            .unwrap_or(DEFAULT_CONFIDENCE_HALF_LIFE);
        compute_confidence(self.confidence_anchor(), half_life, now)
    }

    /// `MemoryEngine._enrich_with_confidence`: the confidence to 4 decimals.
    pub fn confidence(&self, now: &PyDateTime) -> f64 {
        py_round(self.raw_confidence(now), 4)
    }
}

/// `k = min(k, count, 100)` and the hit count of each side, `min(3 * k, count)`.
pub fn candidate_counts(k: usize, record_count: usize) -> (usize, usize) {
    let k = k.min(record_count).min(MAX_K);
    (k, (3 * k).min(record_count))
}

fn rrf(weight: f64, rank: usize) -> f64 {
    weight * (1.0 / (rank + RRF_K) as f64)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MatchType {
    Direct,
    DirectGraph,
    Graph,
}

impl MatchType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Direct => "direct",
            Self::DirectGraph => "direct+graph",
            Self::Graph => "graph",
        }
    }
}

/// The graph fields of a result. Absent when the graph step does not run.
#[derive(Clone, Debug, PartialEq)]
pub struct GraphFields {
    pub base_rrf_score: f64,
    pub match_type: MatchType,
    pub graph_support: f64,
    pub graph_via: Vec<i64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Ranked {
    pub id: i64,
    /// To 6 decimals.
    pub rrf_score: f64,
    /// To 4 decimals.
    pub confidence: f64,
    pub graph: Option<GraphFields>,
    /// `/search` reinforces this record after the search (`/search/explain` does not).
    pub reinforce: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Ranking {
    pub results: Vec<Ranked>,
    /// Set when the graph step ran.
    pub graph: Option<GraphInfo>,
}

/// The inputs of one hybrid search, after the retrieval.
pub struct HybridInput<'a, 'r> {
    pub k: usize,
    /// The record count of the store (`len(self.metadata)`).
    pub record_count: usize,
    pub threshold: Option<f64>,
    pub weights: Weights,
    pub since: Option<&'a str>,
    pub until: Option<&'a str>,
    /// Vector hits in rank order: (id, similarity to 6 decimals).
    pub vector: &'a [(i64, f64)],
    /// Keyword hits with a positive score, in index order: (id, score).
    pub keyword: &'a [(i64, f64)],
    /// Net feedback scores, in the order of the usage tracker.
    pub feedback: Option<&'a [(i64, f64)]>,
    /// The stored fields of a record; `None` when it no longer exists.
    pub record: &'a dyn Fn(i64) -> Option<RecordFields<'r>>,
    /// The scope test; `None` when no scope applies.
    pub visible: Option<&'a dyn Fn(i64) -> bool>,
    pub adjacency: &'a Adjacency,
    pub now: PyDateTime,
}

/// Adds `weight / (rank + 60)` by the rank of each value, high to low.
fn add_ranked(scores: &mut OrderedMap<f64>, mut ranked: Vec<(i64, f64)>, weight: f64) {
    sort_desc(&mut ranked, |r| r.1);
    for (rank, (id, _)) in ranked.into_iter().enumerate() {
        if let Some(s) = scores.get_mut(id) {
            *s += rrf(weight, rank);
        }
    }
}

/// `hybrid_search` from the vector and keyword hits to the results.
///
/// To match `/search`, the caller first sets `last_reinforced_at` of each
/// vector hit to `now`: the Python vector search reinforces its hits before
/// the fusion. `/search/explain` does not.
pub fn hybrid_rank(input: &HybridInput) -> Ranking {
    if input.record_count == 0 {
        return Ranking {
            results: Vec::new(),
            graph: None,
        };
    }
    let (k, oversample) = candidate_counts(input.k, input.record_count);
    let w = input.weights;
    let record = input.record;

    let mut keyword: Vec<(i64, f64)> = input
        .keyword
        .iter()
        .copied()
        .filter(|&(id, _)| record(id).is_some() && input.visible.is_none_or(|v| v(id)))
        .collect();
    sort_desc(&mut keyword, |h| h.1);
    keyword.truncate(oversample);

    let mut scores: OrderedMap<f64> = OrderedMap::new();
    for (rank, &(id, _)) in input.vector.iter().enumerate() {
        *scores.entry(id, || 0.0) += rrf(w.vector, rank);
    }
    for (rank, &(id, _)) in keyword.iter().enumerate() {
        *scores.entry(id, || 0.0) += rrf(w.keyword, rank);
    }

    if w.recency_raw > 0.0 {
        let ranked = scores
            .keys()
            .filter_map(|id| {
                let r = record(id)?;
                let s = recency_score(r.document_date(), w.recency_half_life_days, &input.now);
                Some((id, s))
            })
            .collect();
        add_ranked(&mut scores, ranked, w.recency);
    }
    if let Some(feedback) = input.feedback.filter(|f| w.feedback > 0.0 && !f.is_empty()) {
        let positive = feedback
            .iter()
            .copied()
            .filter(|&(id, s)| s > 0.0 && scores.contains(id))
            .collect();
        add_ranked(&mut scores, positive, w.feedback);
    }
    if w.confidence > 0.0 {
        let ranked = scores
            .keys()
            .filter_map(|id| Some((id, record(id)?.raw_confidence(&input.now))))
            .collect();
        add_ranked(&mut scores, ranked, w.confidence);
    }

    let has_bound = |b: Option<&str>| b.is_some_and(|s| !s.is_empty());
    if has_bound(input.since) || has_bound(input.until) {
        scores = scores
            .iter()
            .filter(|&(id, _)| {
                record(id).is_some_and(|r| {
                    passes_temporal_filter(r.document_date(), input.since, input.until)
                })
            })
            .map(|(id, &s)| (id, s))
            .collect();
    }

    let mut direct: Vec<(i64, f64)> = scores.iter().map(|(id, &s)| (id, s)).collect();
    sort_desc(&mut direct, |d| d.1);

    if w.graph <= 0.0 {
        let results = direct
            .iter()
            .take(k)
            .filter_map(|&(id, score)| {
                let r = record(id)?;
                (!below_threshold(input, id)).then(|| Ranked {
                    id,
                    rrf_score: py_round(score, 6),
                    confidence: r.confidence(&input.now),
                    graph: None,
                    reinforce: true,
                })
            })
            .collect();
        return Ranking {
            results,
            graph: None,
        };
    }

    let (candidates, info) = graph_expand(&direct, w.graph, input.adjacency, input.visible);
    Ranking {
        results: merge_graph_results(&direct, &candidates, k, input),
        graph: Some(info),
    }
}

/// The threshold applies to the vector similarity only.
fn below_threshold(input: &HybridInput, id: i64) -> bool {
    input.threshold.is_some_and(|t| {
        input
            .vector
            .iter()
            .find(|v| v.0 == id)
            .is_some_and(|v| v.1 < t)
    })
}

/// `_merge_graph_results`: direct scores plus graph support, with up to 2
/// places kept for records that only the graph found.
fn merge_graph_results(
    direct: &[(i64, f64)],
    candidates: &OrderedMap<GraphCandidate>,
    k: usize,
    input: &HybridInput,
) -> Vec<Ranked> {
    let mut merged: OrderedMap<(f64, GraphFields)> = OrderedMap::new();
    for &(id, score) in direct {
        merged.entry(id, || {
            let fields = GraphFields {
                base_rrf_score: score,
                match_type: MatchType::Direct,
                graph_support: 0.0,
                graph_via: Vec::new(),
            };
            (score, fields)
        });
    }
    for (id, cand) in candidates.iter() {
        if let Some((score, fields)) = merged.get_mut(id) {
            *score += cand.graph_support;
            fields.graph_support = cand.graph_support;
            fields.graph_via = cand.graph_via.clone();
            fields.match_type = MatchType::DirectGraph;
        } else {
            merged.entry(id, || {
                let fields = GraphFields {
                    base_rrf_score: 0.0,
                    match_type: MatchType::Graph,
                    graph_support: cand.graph_support,
                    graph_via: cand.graph_via.clone(),
                };
                (cand.inject_score, fields)
            });
        }
    }

    let mut all: Vec<(i64, &(f64, GraphFields))> = merged.iter().collect();
    sort_desc(&mut all, |e| e.1.0);
    let (graph_only, other): (Vec<_>, Vec<_>) = all
        .into_iter()
        .partition(|e| e.1.1.match_type == MatchType::Graph);
    let reserved = GRAPH_RESERVED_SLOTS.min(graph_only.len()).min(k);
    let mut chosen: Vec<_> = other.into_iter().take(k - reserved).collect();
    chosen.extend(graph_only.into_iter().take(reserved));
    sort_desc(&mut chosen, |e| e.1.0);

    chosen
        .into_iter()
        .filter_map(|(id, (score, fields))| {
            let r = (input.record)(id)?;
            if input.threshold.is_some() {
                let has_vector = input.vector.iter().any(|v| v.0 == id);
                let graph_only = !has_vector && fields.match_type == MatchType::Graph;
                if below_threshold(input, id) || graph_only {
                    return None;
                }
            }
            Some(Ranked {
                id,
                rrf_score: py_round(*score, 6),
                confidence: r.confidence(&input.now),
                graph: Some(GraphFields {
                    base_rrf_score: py_round(fields.base_rrf_score, 6),
                    match_type: fields.match_type,
                    graph_support: py_round(fields.graph_support, 6),
                    graph_via: fields.graph_via.clone(),
                }),
                reinforce: fields.match_type != MatchType::Graph,
            })
        })
        .collect()
}
