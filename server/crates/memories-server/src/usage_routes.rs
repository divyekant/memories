//! The usage, metrics and feedback routes (task P5-1): `GET /usage`,
//! `GET /metrics`, the eight `/metrics/*` routes of Python, the three
//! `/search/feedback` routes, and the new route `GET /metrics/prometheus`.
//!
//! Only `DELETE /search/feedback/{feedback_id}` writes an audit row in Python.

#![allow(clippy::result_large_err)]

use std::fmt::Write as _;
use std::sync::Arc;

use axum::{
    Extension, Json, Router,
    extract::{Path, Query, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
};
use serde_json::{Value, json};

use crate::{
    AppState,
    admin::{last, query_int},
    audit::Entry,
    auth::AuthContext,
    error::{ApiError, JsonBody, item, missing, path_int},
    keys::utc_now_iso,
    model::Model,
    shell::{Client, internal_error},
    usage::{Row, Usage},
};

pub fn routes(r: Router<Arc<AppState>>) -> Router<Arc<AppState>> {
    r.route("/usage", get(usage))
        .route("/metrics", get(metrics))
        .route("/metrics/prometheus", get(prometheus))
        .route("/metrics/search-quality", get(search_quality))
        .route("/metrics/extraction-quality", get(extraction_quality))
        .route("/metrics/quality-summary", get(quality_summary))
        .route("/metrics/failures", get(failures))
        .route("/metrics/problem-queries", get(problem_queries))
        .route("/metrics/stale-memories", get(stale_memories))
        .route("/metrics/graph-search", get(graph_search))
        .route("/metrics/temporal-search", get(temporal_search))
        .route("/search/feedback", post(record_feedback))
        .route("/search/feedback/history", get(feedback_history))
        .route("/search/feedback/{feedback_id}", delete(retract_feedback))
}

type Q = Query<Vec<(String, String)>>;
type Reply = Result<Json<Value>, Response>;

const PERIODS: (&[&str], &str) = (&["today", "7d", "30d", "all"], "^(today|7d|30d|all)$");

/// A `str` query parameter with an optional `pattern` (the allowed values
/// and the regex text) and `max_length`.
fn query_str(
    q: &[(String, String)],
    name: &str,
    default: &str,
    pattern: Option<(&[&str], &str)>,
    max: Option<usize>,
    errors: &mut Vec<Value>,
) -> String {
    let Some(raw) = last(q, name) else {
        return default.into();
    };
    let loc = json!(["query", name]);
    if let Some(m) = max.filter(|m| raw.chars().count() > *m) {
        let mut e = item(
            "string_too_long",
            loc,
            &format!("String should have at most {m} characters"),
            json!(raw),
        );
        e["ctx"] = json!({"max_length": m});
        errors.push(e);
    } else if let Some((_, text)) = pattern.filter(|(a, _)| !a.contains(&raw)) {
        errors.push(pattern_error(loc, raw, text));
    } else {
        return raw.into();
    }
    default.into()
}

fn pattern_error(loc: Value, input: &str, pattern: &str) -> Value {
    let mut e = item(
        "string_pattern_mismatch",
        loc,
        &format!("String should match pattern '{pattern}'"),
        json!(input),
    );
    e["ctx"] = json!({"pattern": pattern});
    e
}

fn checked(errors: Vec<Value>) -> Result<(), Response> {
    if errors.is_empty() {
        Ok(())
    } else {
        Err(ApiError::validation(errors).into_response())
    }
}

fn admin(auth: &AuthContext) -> Result<(), Response> {
    auth.require_admin().map_err(IntoResponse::into_response)
}

/// Runs a read of `usage.db` after the queued writes, off the async threads.
/// `None` when usage tracking is off.
async fn read<T: Send + 'static>(
    state: &Arc<AppState>,
    f: impl FnOnce(&Usage) -> rusqlite::Result<T> + Send + 'static,
) -> Result<Option<T>, Response> {
    let Some(u) = &state.usage else {
        return Ok(None);
    };
    u.flush().await;
    let st = state.clone();
    match tokio::task::spawn_blocking(move || f(st.usage.as_ref().unwrap())).await {
        Ok(Ok(v)) => Ok(Some(v)),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "usage.db: read failed");
            Err(internal_error())
        }
        Err(e) => {
            tracing::error!(error = %e, "usage.db: read stopped");
            Err(internal_error())
        }
    }
}

fn disabled() -> Value {
    json!({"enabled": false})
}

// ---- /usage ----------------------------------------------------------------------

async fn usage(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    let mut errors = Vec::new();
    let period = query_str(&q, "period", "7d", Some(PERIODS), None, &mut errors);
    let session = query_str(&q, "session_id", "", None, Some(200), &mut errors);
    checked(errors)?;
    admin(&auth)?;
    let out = read(&state, move |u| u.usage(&period, &session)).await?;
    Ok(Json(out.unwrap_or_else(disabled)))
}

// ---- /search/feedback ------------------------------------------------------------

fn engine(state: &AppState) -> Result<&Arc<memories_engine::Engine>, Response> {
    state.engine.get().ok_or_else(|| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "Memory engine not available: QDRANT_URL is not set",
        )
        .into_response()
    })
}

/// The source of a memory (`memory.get_memory`), or the 404 reply.
fn memory_source(state: &AppState, id: i64) -> Result<String, Response> {
    let engine = engine(state)?;
    let catalog = engine.catalog();
    match catalog.get(id) {
        Some(e) => Ok(e.source.clone()),
        None => Err(
            ApiError::new(StatusCode::NOT_FOUND, format!("Memory {id} not found")).into_response(),
        ),
    }
}

/// `SearchFeedbackRequest`.
fn feedback_body(
    map: &serde_json::Map<String, Value>,
) -> Result<(i64, String, String, String), ApiError> {
    let mut m = Model::new(map, vec![json!("body")]);
    let memory_id = m.int_required("memory_id", i64::MIN, i64::MAX);
    let query = m.string("query", Some(""), false, 0, usize::MAX);
    let signal = m.string("signal", None, false, 0, usize::MAX);
    let signal = match signal {
        Some(s) if s != "useful" && s != "not_useful" => {
            let pattern = "^(useful|not_useful)$";
            m.errors
                .push(pattern_error(json!(["body", "signal"]), &s, pattern));
            None
        }
        s => s,
    };
    let search_id = m.string("search_id", Some(""), false, 0, usize::MAX);
    match (memory_id, query, signal, search_id) {
        (Some(a), Some(b), Some(c), Some(d)) if m.errors.is_empty() => Ok((a, b, c, d)),
        _ => Err(ApiError::validation(m.errors)),
    }
}

async fn record_feedback(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Reply {
    let (memory_id, query, signal, search_id) =
        feedback_body(&map).map_err(IntoResponse::into_response)?;
    let source = memory_source(&state, memory_id)?;
    if auth.prefixes.is_some() && !auth.can_read(&source) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "Memory is outside your allowed source scope",
        )
        .into_response());
    }
    if let Some(u) = &state.usage {
        u.log(Row::Feedback {
            memory_id,
            query,
            signal,
            search_id,
        });
        // Python writes the row before the reply: the next search sees it.
        u.flush().await;
    }
    Ok(Json(json!({"status": "recorded"})))
}

async fn feedback_history(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    let mut errors = Vec::new();
    let memory_id = match last(&q, "memory_id") {
        None => {
            errors.push(missing(json!(["query", "memory_id"]), Value::Null));
            0
        }
        Some(_) => query_int(&q, "memory_id", 0, i64::MIN, None, &mut errors),
    };
    let limit = query_int(&q, "limit", 50, 1, Some(200), &mut errors);
    checked(errors)?;
    let source = memory_source(&state, memory_id)?;
    if auth.prefixes.is_some() && !auth.can_read(&source) {
        return Err(
            ApiError::new(StatusCode::FORBIDDEN, "Memory outside your scope").into_response(),
        );
    }
    let entries = read(&state, move |u| u.feedback_history(memory_id, limit))
        .await?
        .unwrap_or_default();
    Ok(Json(json!({"count": entries.len(), "entries": entries})))
}

async fn retract_feedback(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    Path(raw): Path<String>,
) -> Reply {
    let id = path_int("feedback_id", &raw).map_err(IntoResponse::into_response)?;
    admin(&auth)?;
    let deleted = match &state.usage {
        Some(u) => u.delete_feedback(id).await,
        None => false,
    };
    if !deleted {
        return Err(
            ApiError::new(StatusCode::NOT_FOUND, format!("Feedback {id} not found"))
                .into_response(),
        );
    }
    let mut entry = Entry::from_request(&auth, client.as_ref().map(|c| &c.0), "feedback.retracted");
    entry.resource_id = id.to_string();
    state.audit(entry).await;
    Ok(Json(json!({"status": "retracted", "id": id})))
}

// ---- /metrics/* ------------------------------------------------------------------

async fn search_quality(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    let mut errors = Vec::new();
    let period = query_str(&q, "period", "7d", Some(PERIODS), None, &mut errors);
    let prefix = query_str(&q, "source_prefix", "", None, Some(500), &mut errors);
    checked(errors)?;
    // No admin check: a scoped key sees the numbers of its own memories.
    let ids: Option<Vec<i64>> = if auth.prefixes.is_some() || !prefix.is_empty() {
        let engine = engine(&state)?;
        let catalog = engine.catalog();
        Some(
            catalog
                .iter()
                .filter(|(_, e)| prefix.is_empty() || e.source.starts_with(&prefix))
                .filter(|(_, e)| auth.prefixes.is_none() || auth.can_read(&e.source))
                .map(|(id, _)| id as i64)
                .collect(),
        )
    } else {
        None
    };
    let out = read(&state, move |u| u.search_quality(&period, ids.as_deref())).await?;
    Ok(Json(out.unwrap_or_else(disabled)))
}

/// An admin route with only a `period` parameter.
async fn period_route(
    state: Arc<AppState>,
    auth: AuthContext,
    q: Vec<(String, String)>,
    off: Value,
    f: fn(&Usage, &str) -> rusqlite::Result<Value>,
) -> Reply {
    let mut errors = Vec::new();
    let period = query_str(&q, "period", "7d", Some(PERIODS), None, &mut errors);
    checked(errors)?;
    admin(&auth)?;
    let out = read(&state, move |u| f(u, &period)).await?;
    Ok(Json(out.unwrap_or(off)))
}

async fn extraction_quality(
    State(s): State<Arc<AppState>>,
    Extension(a): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    period_route(s, a, q, disabled(), Usage::extraction_quality).await
}

async fn quality_summary(
    State(s): State<Arc<AppState>>,
    Extension(a): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    period_route(s, a, q, disabled(), Usage::quality_summary).await
}

async fn graph_search(
    State(s): State<Arc<AppState>>,
    Extension(a): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    period_route(s, a, q, json!({}), Usage::graph_stats).await
}

async fn temporal_search(
    State(s): State<Arc<AppState>>,
    Extension(a): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    period_route(s, a, q, json!({}), Usage::temporal_stats).await
}

async fn failures(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    let mut errors = Vec::new();
    let kinds: (&[&str], &str) = (&["retrieval", "extraction"], "^(retrieval|extraction)$");
    let kind = query_str(&q, "type", "retrieval", Some(kinds), None, &mut errors);
    let limit = query_int(&q, "limit", 10, 1, Some(100), &mut errors);
    checked(errors)?;
    admin(&auth)?;
    let out = read(&state, move |u| u.failures(&kind, limit)).await?;
    Ok(Json(out.unwrap_or_else(disabled)))
}

async fn problem_queries(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    let mut errors = Vec::new();
    let min = query_int(&q, "min_feedback", 2, 1, None, &mut errors);
    let limit = query_int(&q, "limit", 20, 1, Some(100), &mut errors);
    checked(errors)?;
    admin(&auth)?;
    let out = read(&state, move |u| u.problem_queries(min, limit)).await?;
    Ok(Json(json!({"queries": out.unwrap_or_default()})))
}

async fn stale_memories(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Reply {
    let mut errors = Vec::new();
    let min = query_int(&q, "min_retrievals", 3, 1, None, &mut errors);
    let limit = query_int(&q, "limit", 20, 1, Some(100), &mut errors);
    checked(errors)?;
    admin(&auth)?;
    let out = read(&state, move |u| u.stale_memories(min, limit)).await?;
    Ok(Json(json!({"memories": out.unwrap_or_default()})))
}

// ---- /metrics --------------------------------------------------------------------

/// `_read_process_memory_kb`: fields of `/proc/self/status`, 0 if not there.
fn process_memory_kb() -> Value {
    let fields = [
        ("VmRSS", "rss_kb"),
        ("RssAnon", "rss_anon_kb"),
        ("RssFile", "rss_file_kb"),
        ("VmHWM", "rss_high_water_kb"),
        ("VmSize", "vmsize_kb"),
    ];
    let mut out = serde_json::Map::new();
    for (_, k) in fields {
        out.insert(k.into(), json!(0));
    }
    if let Ok(text) = std::fs::read_to_string("/proc/self/status") {
        for line in text.lines() {
            let Some((field, raw)) = line.split_once(':') else {
                continue;
            };
            if let Some((_, k)) = fields.iter().find(|(f, _)| *f == field.trim())
                && let Some(Ok(v)) = raw.split_whitespace().next().map(str::parse::<i64>)
            {
                out.insert((*k).into(), json!(v));
            }
        }
    }
    Value::Object(out)
}

/// The `embedder_reload` part of the Python body. This server has no
/// embedder reload, so the counters stay 0 and `enabled` is false.
fn embedder_reload() -> Value {
    use memories_types::config::parse_int;
    let var = |n: &str| std::env::var(n).ok();
    let int = |n: &str, d: i64, min: i64| parse_int(var(n).as_deref(), d, min).0;
    // Python `_env_float`: max(minimum, float(raw)); invalid gives the default.
    let float = |n: &str, d: f64, min: f64| {
        let raw = var(n).unwrap_or_default();
        let v = memories_engine::records::py_float(raw.trim()).unwrap_or(d);
        if v > min { v } else { min }
    };
    json!({
        "enabled": false,
        "policy": {
            "rss_kb_threshold": int("EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD", 1_200_000, 100_000),
            "check_sec": float("EMBEDDER_AUTO_RELOAD_CHECK_SEC", 15.0, 1.0),
            "required_high_streak": int("EMBEDDER_AUTO_RELOAD_HIGH_STREAK", 3, 1),
            "min_interval_sec": float("EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC", 900.0, 30.0),
            "window_sec": float("EMBEDDER_AUTO_RELOAD_WINDOW_SEC", 3600.0, 60.0),
            "max_per_window": int("EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW", 2, 1),
            "max_active_requests": int("EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS", 2, 0),
            "max_queue_depth": int("EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH", 0, 0),
        },
        "auto": {
            "checks_total": 0, "skipped_total": 0, "triggered_total": 0,
            "succeeded_total": 0, "failed_total": 0, "decision_reasons": {},
            "last_decision_reason": null, "last_rss_kb": 0, "last_triggered_at": null,
            "last_completed_at": null, "last_reload_duration_ms": 0.0,
            "last_gc_collected": null, "last_trim_reason": null, "last_error": null,
            "last_error_at": null,
        },
        "manual": {
            "requests_total": 0, "succeeded_total": 0, "failed_total": 0,
            "last_requested_at": null, "last_completed_at": null,
            "last_reload_duration_ms": 0.0, "last_gc_collected": null,
            "last_trim_reason": null, "last_error": null, "last_error_at": null,
        },
    })
}

/// The `GET /metrics` body. If `sample` is true, it adds a sample to the
/// memory trend (only `GET /metrics` adds samples, as in Python).
async fn snapshot(state: &Arc<AppState>, sample: bool) -> Result<Value, Response> {
    let total = state.engine.get().map_or(0, |e| e.count());
    let trend = {
        let mut t = state.trend.lock().unwrap_or_else(|p| p.into_inner());
        if sample {
            t.push_back(json!({"timestamp": utc_now_iso(), "total_memories": total}));
        }
        while t.len() > state.trend_max {
            t.pop_front();
        }
        let delta = match (t.front(), t.back()) {
            (Some(a), Some(b)) if t.len() >= 2 => {
                b["total_memories"].as_i64().unwrap_or(0)
                    - a["total_memories"].as_i64().unwrap_or(0)
            }
            _ => 0,
        };
        json!({"window_size": state.trend_max, "delta": delta, "samples": Vec::from(t.clone())})
    };
    let requests = state.metrics.snapshot();
    // shortcut: no `promotion` part until the promotion service is ported (task P5-6).
    Ok(json!({
        "uptime_sec": state.started.elapsed().as_secs(),
        "extract": crate::extract::queue_fields(state).await?,
        "memory": {"current_total": total, "trend": trend, "process": process_memory_kb()},
        "embedder_reload": embedder_reload(),
        "requests": requests["requests"],
        "routes": requests["routes"],
    }))
}

async fn metrics(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Reply {
    admin(&auth)?;
    snapshot(&state, true).await.map(Json)
}

/// A Prometheus label value: `\`, `"` and line ends need an escape.
fn label(v: &str) -> String {
    v.replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
}

/// The numbers of `GET /metrics` in the Prometheus text format (new route).
/// Admin only, as `GET /metrics`.
async fn prometheus(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Result<Response, Response> {
    admin(&auth)?;
    let m = snapshot(&state, false).await?;
    let mut out = String::new();
    let mut metric = |name: &str, kind: &str, help: &str, rows: Vec<(String, Value)>| {
        let _ = writeln!(out, "# HELP {name} {help}");
        let _ = writeln!(out, "# TYPE {name} {kind}");
        for (labels, v) in rows {
            let _ = writeln!(out, "{name}{labels} {v}");
        }
    };
    let one = |v: &Value| vec![(String::new(), v.clone())];
    metric(
        "memories_uptime_seconds",
        "gauge",
        "Seconds since the server started.",
        one(&m["uptime_sec"]),
    );
    metric(
        "memories_records",
        "gauge",
        "Memories in the store.",
        one(&m["memory"]["current_total"]),
    );
    metric(
        "memories_process_resident_memory_bytes",
        "gauge",
        "Resident memory of the server process.",
        one(&json!(
            m["memory"]["process"]["rss_kb"].as_i64().unwrap_or(0) * 1024
        )),
    );
    metric(
        "memories_extract_queue_depth",
        "gauge",
        "Extract jobs that wait in the queue.",
        one(&m["extract"]["queue_depth"]),
    );
    metric(
        "memories_extract_queue_max",
        "gauge",
        "The most extract jobs that the queue accepts.",
        one(&m["extract"]["queue_max"]),
    );
    metric(
        "memories_http_active_requests",
        "gauge",
        "HTTP requests in progress.",
        one(&m["requests"]["active_http_requests"]),
    );
    let routes = m["routes"].as_object().cloned().unwrap_or_default();
    let mut keys: Vec<&String> = routes.keys().collect();
    keys.sort();
    let per_route = |field: &str| -> Vec<(String, Value)> {
        keys.iter()
            .map(|k| {
                (
                    format!("{{route=\"{}\"}}", label(k)),
                    routes[*k][field].clone(),
                )
            })
            .collect()
    };
    metric(
        "memories_http_requests_total",
        "counter",
        "HTTP requests for each route.",
        per_route("count"),
    );
    metric(
        "memories_http_request_errors_total",
        "counter",
        "HTTP replies with status 400 or more for each route.",
        per_route("error_count"),
    );
    Ok((
        [(
            header::CONTENT_TYPE,
            "text/plain; version=0.0.4; charset=utf-8",
        )],
        out,
    )
        .into_response())
}
