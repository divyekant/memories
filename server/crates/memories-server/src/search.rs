//! The search routes (task P2-2): `POST /search`, `/search/batch`,
//! `/search/explain`, `/search/evidence` and `/memory/is-novel`.
//!
//! None of these routes writes an audit row in Python (no `_audit` call).
//! `/search` (each item of a batch), `/search/evidence` and `/memory/is-novel`
//! write usage rows (task P5-1).

#![allow(clippy::result_large_err)]

use std::sync::Arc;

use axum::{
    Extension, Json,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use memories_engine::{
    Engine,
    evidence::build_evidence_packet,
    rank::{PyDateTime, intent::classify_query, scores::relative_scores},
    records::py_str,
    scope::{Scope, source_matches_prefixes},
    search::SearchArgs,
};
use serde_json::{Map, Value, json};
use tokio::sync::Semaphore;

use crate::{
    AppState,
    auth::AuthContext,
    error::{ApiError, JsonBody, item, list_too_short, missing},
    model::{Bounds, Model},
    shell::internal_error,
    usage::Row,
};

/// The default of `graph_weight`. Python treats this exact value as "not given".
const DEFAULT_GRAPH_WEIGHT: f64 = 0.1;
/// Searches of one batch that run at the same time.
const BATCH_LIMIT: usize = 8;

/// `SearchRequest` of app.py.
#[derive(Clone, Debug, PartialEq)]
pub struct SearchBody {
    pub query: String,
    pub k: i64,
    pub threshold: Option<f64>,
    pub hybrid: bool,
    pub vector_weight: f64,
    pub source_prefix: Option<String>,
    pub source_prefixes: Option<Vec<String>>,
    pub source_boundary: bool,
    pub recency_weight: f64,
    pub recency_half_life_days: f64,
    pub feedback_weight: f64,
    pub confidence_weight: f64,
    pub graph_weight: f64,
    pub since: Option<String>,
    pub until: Option<String>,
    pub source: String,
    pub include_archived: bool,
    pub auto_intent: bool,
    pub reference_date: Option<String>,
    /// Set by the intent step when it detected a time or graph intent.
    pub auto_detected: bool,
}

const UNIT: Bounds = Bounds {
    ge: Some(0.0),
    gt: None,
    le: Some(1.0),
};

impl SearchBody {
    /// Checks the fields in the order of the model. `None` if a field failed.
    pub fn parse(m: &mut Model) -> Option<Self> {
        let query = m.string("query", None, false, 1, 10_000);
        let k = m.int("k", 5, 1, 100);
        let threshold = m.float("threshold", None, true, UNIT);
        let hybrid = m.boolean("hybrid", true);
        let vector_weight = m.float("vector_weight", Some(0.7), false, UNIT);
        let source_prefix = m.opt_string("source_prefix", 500);
        let source_prefixes = m.string_list("source_prefixes");
        let source_boundary = m.boolean("source_boundary", false);
        let recency_weight = m.float("recency_weight", Some(0.0), false, UNIT);
        let half_life = Bounds {
            ge: None,
            gt: Some(0.0),
            le: Some(365.0),
        };
        let recency_half_life_days =
            m.float("recency_half_life_days", Some(30.0), false, half_life);
        let feedback_weight = m.float("feedback_weight", Some(0.1), false, UNIT);
        let confidence_weight = m.float("confidence_weight", Some(0.0), false, UNIT);
        let graph_weight = m.float("graph_weight", Some(DEFAULT_GRAPH_WEIGHT), false, UNIT);
        let since = m.opt_string("since", 50);
        let until = m.opt_string("until", 50);
        let source = m.string("source", Some(""), false, 0, 500);
        let include_archived = m.boolean("include_archived", false);
        let auto_intent = m.boolean("auto_intent", true);
        let reference_date = m.opt_string("reference_date", 50);
        if !m.errors.is_empty() {
            return None;
        }
        Some(Self {
            query: query?,
            k: k?,
            threshold,
            hybrid: hybrid?,
            vector_weight: vector_weight?,
            source_prefix,
            source_prefixes,
            source_boundary: source_boundary?,
            recency_weight: recency_weight?,
            recency_half_life_days: recency_half_life_days?,
            feedback_weight: feedback_weight?,
            confidence_weight: confidence_weight?,
            graph_weight: graph_weight?,
            since,
            until,
            source: source?,
            include_archived: include_archived?,
            auto_intent: auto_intent?,
            reference_date,
            auto_detected: false,
        })
    }

    pub fn from_map(map: &Map<String, Value>) -> Result<Self, ApiError> {
        let mut m = Model::new(map, vec![json!("body")]);
        let body = Self::parse(&mut m);
        body.ok_or_else(|| ApiError::validation(m.errors))
    }

    /// `_validated_search_scope_kwargs`.
    fn check_scope(&self, auth: &AuthContext) -> Result<(), ApiError> {
        let unprocessable = |d: &str| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, d.to_owned());
        if self.source_prefix.as_deref().is_some_and(|p| !p.is_empty())
            && self.source_prefixes.is_some()
        {
            return Err(unprocessable(
                "source_prefix and source_prefixes are mutually exclusive",
            ));
        }
        if let Some(prefixes) = &self.source_prefixes {
            if prefixes
                .iter()
                .any(|p| p.is_empty() || p.chars().count() > 500)
            {
                return Err(unprocessable(
                    "source_prefixes must contain non-empty paths up to 500 characters",
                ));
            }
            if let Some(p) = prefixes.iter().find(|p| !auth.can_read(p)) {
                return Err(ApiError::new(
                    StatusCode::FORBIDDEN,
                    format!("Key does not have read access to source: {p}"),
                ));
            }
        }
        Ok(())
    }

    /// The intent step of `/search` and `/search/evidence`. An error is HTTP
    /// 500, as in Python (for example "1000000000 days ago").
    fn apply_intent(&mut self) -> Result<(), Response> {
        if !self.auto_intent {
            return Ok(());
        }
        let reference = self
            .reference_date
            .as_deref()
            .filter(|r| !r.is_empty())
            .and_then(|r| PyDateTime::fromisoformat(&r.replace('Z', "+00:00")))
            .unwrap_or_else(PyDateTime::now_utc);
        let [y, mo, d, ..] = reference.fields();
        let given = |v: &Option<String>| v.clone().filter(|s| !s.is_empty());
        let caller_graph = (self.graph_weight != DEFAULT_GRAPH_WEIGHT).then_some(self.graph_weight);
        let adj = classify_query(
            &self.query,
            (y, mo, d),
            given(&self.since).as_deref(),
            given(&self.until).as_deref(),
            caller_graph,
        )
        .map_err(|e| {
            tracing::error!(error = ?e, "query intent failed");
            internal_error()
        })?;
        if adj.auto_detected {
            self.auto_detected = true;
            if adj.since.as_deref().is_some_and(|s| !s.is_empty()) && given(&self.since).is_none() {
                self.since = adj.since;
            }
            if adj.until.as_deref().is_some_and(|s| !s.is_empty()) && given(&self.until).is_none() {
                self.until = adj.until;
            }
            if let Some(g) = adj.graph_weight
                && self.graph_weight == DEFAULT_GRAPH_WEIGHT
            {
                self.graph_weight = g;
            }
            if let Some(r) = adj.recency_weight
                && self.recency_weight == 0.0
            {
                self.recency_weight = r;
            }
        }
        Ok(())
    }

    fn args(&self, feedback: Option<Vec<(i64, f64)>>) -> SearchArgs {
        SearchArgs {
            feedback,
            query: self.query.clone(),
            k: self.k as usize,
            threshold: self.threshold,
            vector_weight: self.vector_weight,
            recency_weight: self.recency_weight,
            recency_half_life_days: self.recency_half_life_days,
            feedback_weight: self.feedback_weight,
            confidence_weight: self.confidence_weight,
            graph_weight: self.graph_weight,
            since: self.since.clone(),
            until: self.until.clone(),
            scope: Scope {
                source_prefix: self.source_prefix.clone(),
                source_exact: None,
                source_boundary: self.source_boundary,
                allowed_prefixes: self.source_prefixes.clone(),
                exclude_reserved: false,
                include_archived: self.include_archived,
            },
        }
    }
}

fn engine(state: &AppState) -> Result<&Arc<Engine>, Response> {
    state.engine.get().ok_or_else(|| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Memory engine not available: QDRANT_URL is not set",
        )
        .into_response()
    })
}

/// The 500 reply of a failure inside the `try` block of a Python search route.
fn failed(what: &str, e: impl std::fmt::Display) -> Response {
    tracing::error!(error = %e, "{what} failed");
    ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "Internal server error").into_response()
}

/// The attribution headers of `_log_usage_event`, cut as Python cuts them.
#[derive(Clone, Debug, Default)]
pub struct Caller {
    pub client: String,
    pub session_id: String,
    pub invocation: String,
}

impl Caller {
    pub fn of(headers: &HeaderMap) -> Self {
        // Starlette reads header bytes as Latin-1.
        let h = |name: &str, n: usize| -> String {
            headers
                .get(name)
                .map(|v| v.as_bytes().iter().map(|&b| b as char).take(n).collect())
                .unwrap_or_default()
        };
        Self {
            client: h("X-Memories-Client", 100),
            session_id: h("X-Memories-Session-Id", 200),
            invocation: h("X-Memories-Invocation", 100),
        }
    }

    pub fn row(&self, operation: &str, source: &str, count: i64) -> Row {
        Row::Api {
            operation: operation.into(),
            source: source.into(),
            count,
            client: self.client.clone(),
            session_id: self.session_id.clone(),
            invocation: self.invocation.clone(),
        }
    }
}

/// Writes a usage row if `USAGE_TRACKING` is on.
pub fn log(state: &AppState, row: Row) {
    if let Some(u) = &state.usage {
        u.log(row);
    }
}

/// `get_feedback_scores()` when `feedback_weight > 0` and the corpus is not
/// empty, as Python reads them. `None` when usage tracking is off.
async fn feedback_scores(
    state: &Arc<AppState>,
    engine: &Engine,
    weight: f64,
) -> Option<Vec<(i64, f64)>> {
    if weight <= 0.0 || engine.count() == 0 {
        return None;
    }
    state.usage.as_ref()?;
    let st = state.clone();
    let read = tokio::task::spawn_blocking(move || st.usage.as_ref().map(|u| u.feedback_scores()));
    match read.await {
        Ok(Some(Ok(scores))) => Some(scores),
        Ok(Some(Err(e))) => {
            tracing::warn!(error = %e, "usage.db: cannot read the feedback scores");
            None
        }
        _ => None,
    }
}

/// `auth.filter_results`, then `annotate_relative_scores`.
fn filter_and_annotate(auth: &AuthContext, results: Vec<Value>) -> Vec<Value> {
    let mut kept: Vec<Value> = match &auth.prefixes {
        None => results,
        Some(p) => results
            .into_iter()
            .filter(|r| {
                let source = match r.get("source") {
                    None => String::new(),
                    Some(v) => py_str(v),
                };
                source_matches_prefixes(&source, Some(p))
            })
            .collect(),
    };
    let raw: Vec<f64> = kept
        .iter()
        .map(|r| memories_engine::evidence::score(r.as_object().unwrap_or(&Map::new())))
        .collect();
    for (r, rel) in kept.iter_mut().zip(relative_scores(&raw)) {
        r["relative_score"] = json!(rel);
    }
    kept
}

async fn run_search(
    state: &Arc<AppState>,
    engine: &Engine,
    body: &SearchBody,
    what: &str,
) -> Result<Vec<Value>, Response> {
    let feedback = if body.hybrid {
        feedback_scores(state, engine, body.feedback_weight).await
    } else {
        None
    };
    let args = body.args(feedback);
    let found = if body.hybrid {
        engine.hybrid_search(&args).await
    } else {
        engine.search(&args, true).await
    };
    found.map_err(|e| failed(what, e))
}

/// `search()` of app.py for one checked body.
async fn search_one(
    state: &Arc<AppState>,
    auth: &AuthContext,
    caller: &Caller,
    mut body: SearchBody,
) -> Result<Value, Response> {
    tracing::info!(query = %body.query.chars().take(80).collect::<String>(), k = body.k, hybrid = body.hybrid, "search");
    body.apply_intent()?;
    search_body(state, auth, caller, &body).await
}

/// The part of `/search` after the intent step.
async fn search_body(
    state: &Arc<AppState>,
    auth: &AuthContext,
    caller: &Caller,
    body: &SearchBody,
) -> Result<Value, Response> {
    let engine = engine(state)?;
    let results = filter_and_annotate(auth, run_search(state, engine, body, "search").await?);
    log_search(state, caller, body, &results);
    Ok(json!({"query": body.query, "count": results.len(), "results": results}))
}

/// The usage rows of one search: the event, a retrieval row for each result,
/// and the graph and temporal rows.
fn log_search(state: &AppState, caller: &Caller, body: &SearchBody, results: &[Value]) {
    let Some(u) = &state.usage else {
        return;
    };
    let count = results.len() as i64;
    let query: String = body.query.chars().take(200).collect();
    u.log(caller.row("search", &body.source, 1));
    for (rank, r) in results.iter().enumerate() {
        if let Some(id) = r.get("id").and_then(Value::as_i64) {
            u.log(Row::Retrieval {
                memory_id: id,
                query: query.clone(),
                source: body.source.clone(),
                rank: rank as i64 + 1,
                result_count: count,
            });
        }
    }
    if body.graph_weight > 0.0 {
        let graph = results
            .iter()
            .filter(|r| r["match_type"].as_str().unwrap_or("").contains("graph"))
            .count() as i64;
        u.log(Row::Graph {
            query: query.clone(),
            graph_weight: body.graph_weight,
            direct_count: count - graph,
            graph_count: graph,
            total_results: count,
        });
    }
    // Python: the test is truthy, but has_since is `since is not None`.
    let set = |v: &Option<String>| v.as_deref().is_some_and(|s| !s.is_empty());
    if set(&body.since) || set(&body.until) {
        u.log(Row::Temporal {
            query,
            has_since: body.since.is_some(),
            has_until: body.until.is_some(),
            auto_detected: body.auto_detected,
        });
    }
}

pub async fn search(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let body = SearchBody::from_map(&map).map_err(IntoResponse::into_response)?;
    body.check_scope(&auth)
        .map_err(IntoResponse::into_response)?;
    search_one(&state, &auth, &Caller::of(&headers), body)
        .await
        .map(Json)
}

pub async fn search_explain(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let body = SearchBody::from_map(&map).map_err(IntoResponse::into_response)?;
    auth.require_admin().map_err(IntoResponse::into_response)?;
    body.check_scope(&auth)
        .map_err(IntoResponse::into_response)?;
    let engine = engine(&state)?;
    let feedback = feedback_scores(&state, engine, body.feedback_weight).await;
    let mut out = engine
        .hybrid_search_explain(&body.args(feedback))
        .await
        .map_err(|e| failed("search explain", e))?;
    let raw = match out["results"].take() {
        Value::Array(a) => a,
        _ => Vec::new(),
    };
    let total = raw.len();
    let kept = filter_and_annotate(&auth, raw);
    out["explain"]["filtered_by_auth"] = json!(total - kept.len());
    out["results"] = Value::Array(kept);
    Ok(Json(out))
}

pub async fn search_evidence(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let mut body = SearchBody::from_map(&map).map_err(IntoResponse::into_response)?;
    body.check_scope(&auth)
        .map_err(IntoResponse::into_response)?;
    body.apply_intent()?;
    let engine = engine(&state)?;
    let results = filter_and_annotate(
        &auth,
        run_search(&state, engine, &body, "search evidence").await?,
    );
    let packet = build_evidence_packet(&body.query, &results);
    log(
        &state,
        Caller::of(&headers).row("search_evidence", &body.source, 1),
    );
    Ok(Json(json!({
        "query": body.query,
        "results": results,
        "count": results.len(),
        "evidence_packet": packet,
    })))
}

/// `SearchBatchRequest`: `queries` with 1 to 200 search bodies.
pub fn parse_batch(map: &Map<String, Value>) -> Result<Vec<SearchBody>, ApiError> {
    let loc = json!(["body", "queries"]);
    let Some(v) = map.get("queries") else {
        return Err(ApiError::validation(vec![missing(
            loc,
            Value::Object(map.clone()),
        )]));
    };
    let Some(items) = v.as_array() else {
        return Err(ApiError::validation(vec![item(
            "list_type",
            loc,
            "Input should be a valid list",
            v.clone(),
        )]));
    };
    let mut errors = Vec::new();
    let mut bodies = Vec::with_capacity(items.len());
    for (i, x) in items.iter().enumerate() {
        let at = vec![json!("body"), json!("queries"), json!(i)];
        match x.as_object() {
            Some(obj) => {
                let mut m = Model::new(obj, at);
                if let Some(b) = SearchBody::parse(&mut m) {
                    bodies.push(b);
                }
                errors.extend(m.errors);
            }
            None => {
                let msg = "Input should be a valid dictionary or instance of SearchRequest";
                let mut e = item("model_type", Value::Array(at), msg, x.clone());
                e["ctx"] = json!({"class_name": "SearchRequest"});
                errors.push(e);
            }
        }
    }
    if errors.is_empty() {
        if items.is_empty() {
            errors.push(list_too_short(loc, v.clone(), 1, 0));
        } else if items.len() > 200 {
            let msg = format!(
                "List should have at most 200 items after validation, not {}",
                items.len()
            );
            let mut e = item("too_long", loc, &msg, v.clone());
            e["ctx"] =
                json!({"field_type": "List", "max_length": 200, "actual_length": items.len()});
            errors.push(e);
        }
    }
    if errors.is_empty() {
        Ok(bodies)
    } else {
        Err(ApiError::validation(errors))
    }
}

pub async fn search_batch(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let bodies = parse_batch(&map).map_err(IntoResponse::into_response)?;
    // Refuse a bad item before any search runs.
    for b in &bodies {
        b.check_scope(&auth).map_err(IntoResponse::into_response)?;
    }
    // Python order: each item takes a permit, then runs its intent step at
    // once, then waits on its search. The reply carries the first error in
    // time, so the results come back over a channel as each search ends.
    let caller = Caller::of(&headers);
    let limit = Arc::new(Semaphore::new(BATCH_LIMIT));
    let total = bodies.len();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    let mut outputs: Vec<Option<Value>> = vec![None; total];
    let mut take = |(i, r): (usize, Result<Value, Response>)| -> Result<(), Response> {
        outputs[i] = Some(r?);
        Ok(())
    };
    for (i, mut b) in bodies.into_iter().enumerate() {
        let permit = limit.clone().acquire_owned().await.expect("semaphore");
        while let Ok(done) = rx.try_recv() {
            take(done)?;
        }
        b.apply_intent()?;
        let (state, auth, tx, caller) = (state.clone(), auth.clone(), tx.clone(), caller.clone());
        tokio::spawn(async move {
            let r = search_body(&state, &auth, &caller, &b).await;
            drop(permit);
            let _ = tx.send((i, r));
        });
    }
    drop(tx);
    while let Some(done) = rx.recv().await {
        take(done)?;
    }
    let outputs: Vec<Value> = outputs
        .into_iter()
        .map(|o| o.ok_or_else(internal_error))
        .collect::<Result<_, _>>()?;
    Ok(Json(json!({"count": outputs.len(), "results": outputs})))
}

/// `IsNovelRequest`: `text` and `threshold` (default 0.88).
pub fn parse_is_novel(map: &Map<String, Value>) -> Result<(String, f64), ApiError> {
    let mut m = Model::new(map, vec![json!("body")]);
    let text = m.string("text", None, false, 1, 50_000);
    let threshold = m.float("threshold", Some(0.88), false, UNIT);
    match (text, threshold) {
        (Some(t), Some(th)) if m.errors.is_empty() => Ok((t, th)),
        _ => Err(ApiError::validation(m.errors)),
    }
}

/// `POST /memory/is-novel`. As in Python, the check reads all sources: the
/// route does not apply the prefix scope of the key.
pub async fn is_novel(
    State(state): State<Arc<AppState>>,
    Extension(_auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let (text, threshold) = parse_is_novel(&map).map_err(IntoResponse::into_response)?;
    let engine = engine(&state)?;
    let (novel, similar) = engine
        .is_novel(&text, threshold)
        .await
        .map_err(|e| failed("is-novel check", e))?;
    log(&state, Caller::of(&headers).row("is_novel", "", 1));
    Ok(Json(
        json!({"is_novel": novel, "threshold": threshold, "most_similar": similar}),
    ))
}
