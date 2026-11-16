//! The search flow of `MemoryEngine` v5.19.0: `search` (vector only),
//! `hybrid_search`, `hybrid_search_explain` and `is_novel`. The rank rules
//! are in [`crate::rank`]; this module does the retrieval and builds the
//! result records.

use std::collections::{HashMap, HashSet};

use memories_embed::TextKind;
use memories_store::qdrant::scope_filter;
use memories_types::Record;
use serde_json::{Value, json};

use crate::engine::{Engine, EngineError};
use crate::keyword::query_vector;
use crate::rank::fusion::{GraphFields, candidate_counts};
use crate::rank::graph::sort_desc;
use crate::rank::scores::{passes_temporal_filter, py_round};
use crate::rank::{HybridInput, PyDateTime, RecordFields, WeightInput, Weights, hybrid_rank};
use crate::records::{rank_fields, with_confidence};
use crate::scope::Scope;

/// The arguments of a search, after the intent step of the route.
#[derive(Clone, Debug)]
pub struct SearchArgs {
    pub query: String,
    pub k: usize,
    pub threshold: Option<f64>,
    pub vector_weight: f64,
    pub recency_weight: f64,
    pub recency_half_life_days: f64,
    pub feedback_weight: f64,
    pub confidence_weight: f64,
    pub graph_weight: f64,
    pub since: Option<String>,
    pub until: Option<String>,
    pub scope: Scope,
}

impl Default for SearchArgs {
    fn default() -> Self {
        Self {
            query: String::new(),
            k: 5,
            threshold: None,
            vector_weight: 0.7,
            recency_weight: 0.0,
            recency_half_life_days: 30.0,
            feedback_weight: 0.0,
            confidence_weight: 0.0,
            graph_weight: 0.0,
            since: None,
            until: None,
            scope: Scope::default(),
        }
    }
}

impl SearchArgs {
    fn weights(&self) -> Weights {
        Weights::new(WeightInput {
            vector: self.vector_weight,
            recency: self.recency_weight,
            recency_half_life_days: self.recency_half_life_days,
            feedback: self.feedback_weight,
            confidence: self.confidence_weight,
            graph: self.graph_weight,
        })
    }
}

/// One clock read for a request: the rank rules and the reinforcement use it.
struct Clock {
    now: PyDateTime,
    iso: String,
}

impl Clock {
    fn new() -> Self {
        let now = PyDateTime::now_utc();
        Self {
            iso: now.isoformat_utc(),
            now,
        }
    }
}

/// A vector hit: the record (with pending reinforcement) and its similarity.
struct Hit {
    record: Record,
    similarity: f64,
}

fn as_id(record: &Record) -> i64 {
    record.id as i64
}

fn text_200(record: &Record) -> String {
    record
        .text
        .as_deref()
        .unwrap_or("")
        .chars()
        .take(200)
        .collect()
}

/// `search(...)` from a result value: `{**meta, "similarity": s}` plus confidence.
fn vector_result(hit: &Hit, now: &PyDateTime) -> Value {
    let mut v = with_confidence(&hit.record, None, now);
    v["similarity"] = json!(hit.similarity);
    v
}

impl Engine {
    async fn embed_query(&self, query: &str) -> Result<Vec<f32>, EngineError> {
        let mut v = self
            .embedder
            .embed(vec![query.to_owned()], TextKind::Query)
            .await?;
        Ok(v.pop().unwrap_or_default())
    }

    /// The Qdrant filter of `_build_source_filter`.
    fn filter(&self, scope: &Scope) -> Option<memories_store::Filter> {
        let sources = scope.filter_sources(self.catalog().sources().keys().map(String::as_str));
        scope_filter(sources.as_deref(), scope.include_archived)
    }

    /// The hits of `search` for `limit` (already `min(k, count, 100)`), after
    /// the scope, temporal and threshold checks, with the similarity to 6
    /// decimals.
    async fn vector_hits(&self, args: &SearchArgs, limit: usize) -> Result<Vec<Hit>, EngineError> {
        let vector = self.embed_query(&args.query).await?;
        let filter = self.filter(&args.scope);
        let found = self
            .store
            .search_dense(
                vector,
                limit as u64,
                args.threshold.map(|t| t as f32),
                filter,
            )
            .await?;
        let mut hits = Vec::with_capacity(found.len());
        for (mut record, score) in found {
            // Python reads the record from its metadata: a deleted one drops out.
            let Some(entry) = self.catalog().get(as_id(&record)).cloned() else {
                continue;
            };
            if !args.scope.admits_hit(&entry.source) {
                continue;
            }
            let date = rank_fields(&record, None)
                .document_date()
                .map(str::to_owned);
            if !passes_temporal_filter(
                date.as_deref(),
                args.since.as_deref(),
                args.until.as_deref(),
            ) {
                continue;
            }
            // Python reads the score from the REST JSON, which holds the
            // shortest f32 text (0.88, not 0.8799999952316284).
            let similarity: f64 = score.to_string().parse().unwrap_or(f64::from(score));
            if args.threshold.is_some_and(|t| similarity < t) {
                continue;
            }
            self.overlay(&mut record);
            hits.push(Hit {
                record,
                similarity: py_round(similarity, 6),
            });
        }
        Ok(hits)
    }

    /// `MemoryEngine.search`: vector-only search. With `reinforce`, each
    /// result is reinforced after the result is built (the result shows the
    /// old value).
    pub async fn search(
        &self,
        args: &SearchArgs,
        reinforce: bool,
    ) -> Result<Vec<Value>, EngineError> {
        let n = self.count();
        if n == 0 {
            return Ok(Vec::new());
        }
        let clock = Clock::new();
        let hits = self.vector_hits(args, args.k.min(n).min(100)).await?;
        let results = hits.iter().map(|h| vector_result(h, &clock.now)).collect();
        if reinforce {
            self.reinforce
                .add(hits.iter().map(|h| h.record.id), &clock.iso);
        }
        Ok(results)
    }

    /// The keyword hits in record ID order (the corpus order): `(record, score)`.
    async fn keyword_hits(
        &self,
        args: &SearchArgs,
        limit: usize,
    ) -> Result<Vec<(Record, f64)>, EngineError> {
        let query = query_vector(&args.query);
        let filter = self.filter(&args.scope);
        let mut hits: Vec<(Record, f64)> = self
            .store
            .search_keyword(query, limit as u64, filter)
            .await?
            .into_iter()
            .map(|(r, s)| (r, f64::from(s)))
            .collect();
        // Same rule as the vector hits: a point with no catalog entry is not shown.
        {
            let catalog = self.catalog();
            hits.retain(|h| catalog.contains(as_id(&h.0)));
        }
        hits.sort_by_key(|h| h.0.id);
        Ok(hits)
    }

    /// `hybrid_search`, and `hybrid_search_explain` when `explain` is set.
    async fn hybrid(&self, args: &SearchArgs, explain: bool) -> Result<HybridOut, EngineError> {
        let n = self.count();
        if n == 0 {
            return Ok(HybridOut::default());
        }
        let clock = Clock::new();
        let counts = candidate_counts(args.k, n);
        let mut vector = self.vector_hits(args, counts.vector).await?;
        if !explain {
            // Python reinforces the vector hits before the fusion.
            self.reinforce
                .add(vector.iter().map(|h| h.record.id), &clock.iso);
            for h in &mut vector {
                h.record.last_reinforced_at = Some(clock.iso.clone());
                h.record.extra.remove("last_reinforced_at");
            }
        }
        let keyword = self.keyword_hits(args, counts.keyword).await?;

        let mut records: HashMap<i64, Record> = HashMap::new();
        for (r, _) in &keyword {
            let mut r = r.clone();
            self.overlay(&mut r);
            records.insert(as_id(&r), r);
        }
        for h in &vector {
            records.insert(as_id(&h.record), h.record.clone());
        }
        let vector_ranked: Vec<(i64, f64)> = vector
            .iter()
            .map(|h| (as_id(&h.record), h.similarity))
            .collect();
        let keyword_ranked: Vec<(i64, f64)> = keyword.iter().map(|(r, s)| (as_id(r), *s)).collect();

        let weights = args.weights();
        let ranking = {
            let catalog = self.catalog();
            let adjacency = if weights.graph > 0.0 {
                catalog.adjacency()
            } else {
                Default::default()
            };
            let record = |id: i64| -> Option<RecordFields<'_>> {
                match records.get(&id) {
                    Some(r) => Some(rank_fields(r, None)),
                    // A record that only the graph found: the rank rules need
                    // only that it exists. Its result reads it below.
                    None => catalog.contains(id).then(RecordFields::default),
                }
            };
            let scope = &args.scope;
            let visible = |id: i64| {
                catalog
                    .get(id)
                    .is_some_and(|e| scope.visible(&e.source, e.archived))
            };
            let input = HybridInput {
                k: args.k,
                record_count: n,
                threshold: args.threshold,
                weights,
                since: args.since.as_deref(),
                until: args.until.as_deref(),
                vector: &vector_ranked,
                keyword: &keyword_ranked,
                // shortcut: no feedback scores until usage.db is ported (task P5-1).
                feedback: None,
                record: &record,
                visible: scope
                    .limits_visibility()
                    .then_some(&visible as &dyn Fn(i64) -> bool),
                adjacency: &adjacency,
                graph_params: self.graph,
                now: clock.now,
            };
            hybrid_rank(&input)
        };

        let missing: Vec<u64> = ranking
            .results
            .iter()
            .map(|r| r.id as u64)
            .filter(|id| !records.contains_key(&(*id as i64)))
            .collect();
        for mut r in self.store.get_many(&missing).await? {
            self.overlay(&mut r);
            records.insert(as_id(&r), r);
        }
        let mut results = Vec::with_capacity(ranking.results.len());
        let mut reinforced = Vec::new();
        for ranked in &ranking.results {
            let Some(record) = records.get(&ranked.id) else {
                continue;
            };
            let mut v = with_confidence(record, None, &clock.now);
            v["rrf_score"] = json!(ranked.rrf_score);
            if let Some(g) = &ranked.graph {
                graph_fields(&mut v, g);
            }
            results.push(v);
            if ranked.reinforce {
                reinforced.push(ranked.id as u64);
            }
        }
        if !explain {
            self.reinforce.add(reinforced, &clock.iso);
        }

        let mut out = HybridOut {
            results,
            ..Default::default()
        };
        if explain {
            out.explain = Some(
                self.explain_block(
                    args,
                    &vector,
                    &keyword,
                    &records,
                    counts.keyword,
                    &ranking.graph,
                    weights,
                )
                .await?,
            );
        }
        Ok(out)
    }

    #[allow(clippy::too_many_arguments)]
    async fn explain_block(
        &self,
        args: &SearchArgs,
        vector: &[Hit],
        keyword: &[(Record, f64)],
        records: &HashMap<i64, Record>,
        oversample: usize,
        graph: &Option<crate::rank::graph::GraphInfo>,
        weights: Weights,
    ) -> Result<Value, EngineError> {
        let vector_candidates: Vec<Value> = vector
            .iter()
            .map(|h| json!({"id": h.record.id, "text": text_200(&h.record), "score": h.similarity}))
            .collect();
        // The keyword list of the fusion: visible hits, high score first, at most `oversample`.
        let mut ranked: Vec<(i64, f64)> = {
            let catalog = self.catalog();
            keyword
                .iter()
                .map(|(r, s)| (as_id(r), *s))
                .filter(|(id, _)| {
                    catalog
                        .get(*id)
                        .is_some_and(|e| args.scope.visible(&e.source, e.archived))
                })
                .collect()
        };
        sort_desc(&mut ranked, |h| h.1);
        ranked.truncate(oversample);
        let bm25_candidates: Vec<Value> = ranked
            .iter()
            .filter(|(_, s)| *s > 0.0)
            .filter_map(|(id, s)| {
                let r = records.get(id)?;
                Some(json!({"id": id, "text": text_200(r), "score": py_round(*s, 6)}))
            })
            .collect();
        let mut seen: HashSet<i64> = vector.iter().map(|h| as_id(&h.record)).collect();
        seen.extend(ranked.iter().filter(|(_, s)| *s > 0.0).map(|(id, _)| *id));

        let scope = &args.scope;
        let source_scoped = scope
            .source_prefix
            .as_deref()
            .is_some_and(|p| !p.is_empty())
            || scope.source_exact.is_some()
            || scope.allowed_prefixes.is_some();
        let filtered_by_source = if source_scoped {
            let all = self
                .store
                .search_keyword(query_vector(&args.query), self.count() as u64, None)
                .await?;
            let unscoped = Scope {
                include_archived: true,
                ..scope.clone()
            };
            let catalog = self.catalog();
            let kept = all
                .iter()
                .filter(|(r, _)| {
                    catalog
                        .get(as_id(r))
                        .is_some_and(|e| unscoped.visible(&e.source, false))
                })
                .count();
            all.len() - kept
        } else {
            0
        };

        let graph = match graph {
            None => json!({"enabled": false, "graph_weight": 0.0}),
            Some(g) => json!({
                "enabled": true,
                "graph_weight": weights.graph,
                "seeds": g.seeds.iter().map(|(id, s)| json!({"id": id, "rrf_score": s})).collect::<Vec<_>>(),
                "neighbors_found": g.neighbors_found,
                "neighbors_filtered": g.neighbors_filtered,
                "neighbors_added": g.neighbors_added,
                "ppr_iterations": g.ppr_iterations,
                "ppr_alpha": g.ppr_alpha,
            }),
        };
        Ok(json!({
            "candidates_considered": seen.len(),
            "vector_candidates": vector_candidates,
            "bm25_candidates": bm25_candidates,
            "filtered_by_source": filtered_by_source,
            "filtered_by_auth": 0,
            "scoring_weights": {
                "vector": py_round(args.vector_weight, 4),
                "bm25": py_round(1.0 - args.vector_weight, 4),
                "recency": py_round(weights.recency_raw, 4),
                "feedback": py_round(weights.feedback, 4),
                "confidence": py_round(weights.confidence, 4),
            },
            "rrf_k": crate::rank::fusion::RRF_K,
            "graph": graph,
        }))
    }

    /// `MemoryEngine.hybrid_search`.
    pub async fn hybrid_search(&self, args: &SearchArgs) -> Result<Vec<Value>, EngineError> {
        Ok(self.hybrid(args, false).await?.results)
    }

    /// `MemoryEngine.hybrid_search_explain`: `{"results": [...], "explain": {...}}`.
    pub async fn hybrid_search_explain(&self, args: &SearchArgs) -> Result<Value, EngineError> {
        if self.count() == 0 {
            return Ok(json!({
                "results": [],
                "explain": {
                    "candidates_considered": 0,
                    "vector_candidates": [],
                    "bm25_candidates": [],
                    "filtered_by_source": 0,
                    "filtered_by_auth": 0,
                    "scoring_weights": {
                        "vector": args.vector_weight,
                        "bm25": py_round(1.0 - args.vector_weight, 4),
                        "recency": args.recency_weight,
                        "feedback": py_round(args.feedback_weight, 4),
                        "confidence": py_round(args.confidence_weight, 4),
                    },
                    "rrf_k": crate::rank::fusion::RRF_K,
                    "graph": {"enabled": false, "graph_weight": 0.0},
                },
            }));
        }
        let out = self.hybrid(args, true).await?;
        Ok(json!({"results": out.results, "explain": out.explain.unwrap_or(Value::Null)}))
    }

    /// `MemoryEngine.is_novel` with no scope (the `/memory/is-novel` route):
    /// a vector search with k = 1, which reinforces its hit as in Python.
    pub async fn is_novel(
        &self,
        text: &str,
        threshold: f64,
    ) -> Result<(bool, Option<Value>), EngineError> {
        let args = SearchArgs {
            query: text.to_owned(),
            k: 1,
            ..Default::default()
        };
        let top = self.search(&args, true).await?.into_iter().next();
        Ok(match top {
            None => (true, None),
            Some(t) => {
                let similarity = t["similarity"].as_f64().unwrap_or(0.0);
                (similarity < threshold, Some(t))
            }
        })
    }
}

#[derive(Default)]
struct HybridOut {
    results: Vec<Value>,
    explain: Option<Value>,
}

fn graph_fields(v: &mut Value, g: &GraphFields) {
    v["base_rrf_score"] = json!(g.base_rrf_score);
    v["match_type"] = json!(g.match_type.as_str());
    v["graph_support"] = json!(g.graph_support);
    v["graph_via"] = json!(g.graph_via);
}
