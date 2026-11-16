//! The maintenance routes and the maintenance scheduler of `app.py` v5.19.0
//! (task P5-3). One job runs at a time (`AppState::jobs`): a route or a
//! scheduled job waits for the job that runs.

#![allow(clippy::result_large_err)]

use std::collections::HashSet;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::{
    Extension, Json,
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use memories_engine::rank::PyDateTime;
use memories_engine::rank::pydt::weekday;
use memories_engine::records::{py_str, truthy};
use memories_engine::writes::maintenance::Profiles;
use memories_engine::writes::{WriteError, Writer};
use memories_extract::provider::Provider;
use memories_types::config;
use serde_json::{Map, Value, json};

use crate::{
    AppState,
    auth::AuthContext,
    error::JsonBody,
    model::{Bounds, Model},
    shell::{Client, internal_error},
    validate::finish,
    writes::{audit, body_loc, err, server_error, writer},
};

type Reply = Result<Json<Value>, Response>;

const CONSOLIDATION_SYSTEM: &str =
    "You are a memory consolidation assistant. Output only valid JSON.";

/// `promotion_config.rejected_retention_days`
/// (`PROJECT_PROMOTION_REJECTED_RETENTION_DAYS`, 90). A bad value gives the
/// default, as Python falls back to the default config.
pub fn retention_days() -> i64 {
    match std::env::var("PROJECT_PROMOTION_REJECTED_RETENTION_DAYS") {
        Ok(v) => v
            .trim()
            .parse::<i64>()
            .ok()
            .filter(|d| *d >= 0)
            .unwrap_or(90),
        Err(_) => 90,
    }
}

/// The query parameters as a model map: the last value of each name.
fn query_map(q: &[(String, String)]) -> Map<String, Value> {
    let mut m = Map::new();
    for (k, v) in q {
        m.insert(k.clone(), json!(v));
    }
    m
}

fn query_dry_run(q: &[(String, String)]) -> Result<bool, Response> {
    let map = query_map(q);
    let mut m = Model::new(&map, vec![json!("query")]);
    let dry_run = m.boolean("dry_run", true);
    finish(std::mem::take(&mut m.errors), dry_run).map_err(IntoResponse::into_response)
}

fn admin(auth: &AuthContext) -> Result<(), Response> {
    auth.require_admin().map_err(IntoResponse::into_response)
}

// ---- consolidation -------------------------------------------------------------

/// `_parse_json_array`: the first JSON array in an LLM reply, or empty.
pub fn parse_json_array(text: &str) -> Vec<Value> {
    let text = text.trim();
    let array = |s: &str| match serde_json::from_str::<Value>(s) {
        Ok(Value::Array(a)) => Some(a),
        _ => None,
    };
    if let Some(a) = array(text) {
        return a;
    }
    if text.contains("```") {
        for block in text.split("```") {
            let mut block = block.trim();
            if let Some(rest) = block.strip_prefix("json") {
                block = rest.trim();
            }
            if let Some(a) = array(block) {
                return a;
            }
        }
    }
    if let (Some(start), Some(end)) = (text.find('['), text.rfind(']'))
        && end > start
        && let Some(a) = array(&text[start..=end])
    {
        return a;
    }
    Vec::new()
}

/// `json.dumps(value)` with `ensure_ascii` for a string or a scalar.
fn py_dumps(v: &Value) -> String {
    match v {
        Value::String(s) => {
            let mut out = String::from("\"");
            for c in s.chars() {
                match c {
                    '"' => out.push_str("\\\""),
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    '\u{8}' => out.push_str("\\b"),
                    '\u{c}' => out.push_str("\\f"),
                    c if (c as u32) < 0x20 || (c as u32) > 0x7f => {
                        let mut buf = [0u16; 2];
                        for u in c.encode_utf16(&mut buf) {
                            out.push_str(&format!("\\u{u:04x}"));
                        }
                    }
                    c => out.push(c),
                }
            }
            out.push('"');
            out
        }
        Value::Null => "null".into(),
        // shortcut: lists and maps use the compact JSON text, not the
        // indented text of Python; a category of that type is not known.
        other => other.to_string(),
    }
}

/// `CONSOLIDATION_PROMPT.format(...)`, with the members as `json.dumps(indent=2)`.
pub fn consolidation_prompt(cluster: &[Value], project: &str) -> String {
    let items: Vec<String> = cluster
        .iter()
        .map(|m| {
            let category = m.get("category").cloned().unwrap_or(json!("DETAIL"));
            format!(
                "  {{\n    \"id\": {},\n    \"text\": {},\n    \"category\": {}\n  }}",
                py_dumps(&m["id"]),
                py_dumps(m.get("text").unwrap_or(&Value::Null)),
                py_dumps(&category)
            )
        })
        .collect();
    let memories = if items.is_empty() {
        "[]".to_owned()
    } else {
        format!("[\n{}\n]", items.join(",\n"))
    };
    format!(
        "These {n} memories are about the same topic in the {project} project.\n\
         Consolidate them into 1-2 concise memories that capture ALL unique information.\n\
         Drop redundant or overlapping details. Preserve: decisions and reasoning, bug fixes, conventions.\n\
         \n\
         Memories to consolidate:\n\
         {memories}\n\
         \n\
         Output a JSON array of consolidated text strings. Each must be self-contained.",
        n = cluster.len()
    )
}

fn member_str(m: &Value, key: &str) -> String {
    match m.get(key) {
        None => String::new(),
        Some(v) => py_str(v),
    }
}

/// `_infer_project`.
fn infer_project(cluster: &[Value]) -> String {
    for m in cluster {
        let s = member_str(m, "source");
        let parts: Vec<&str> = s.split('/').collect();
        if parts.len() > 1 && !parts[parts.len() - 1].is_empty() {
            return parts[parts.len() - 1].to_owned();
        }
        if !parts[0].is_empty() {
            return parts[0].to_owned();
        }
    }
    "unknown".into()
}

/// `_dominant_category`: the most common value; a tie keeps the first seen.
fn dominant_category(cluster: &[Value]) -> Value {
    let mut counts: Vec<(Value, usize)> = Vec::new();
    for m in cluster {
        let c = m.get("category").cloned().unwrap_or(json!("detail"));
        match counts.iter_mut().find(|(v, _)| *v == c) {
            Some((_, n)) => *n += 1,
            None => counts.push((c, 1)),
        }
    }
    let max = counts.iter().map(|(_, n)| *n).max().unwrap_or(0);
    counts
        .into_iter()
        .find(|(_, n)| *n == max)
        .map_or(json!("detail"), |(v, _)| v)
}

/// The failure of a consolidation run.
#[derive(Debug)]
pub enum Failure {
    Write(WriteError),
    Provider(String),
}

/// `consolidate_cluster`: merge one cluster with the LLM. The reply has
/// `merged_count`, `new_count`, `old_ids`, `new_texts` and `dry_run`, plus
/// `skipped_reason` or `error` when nothing changed.
pub async fn consolidate_cluster(
    provider: &Provider,
    w: &Writer,
    cluster: &[Value],
    dry_run: bool,
) -> Result<Value, Failure> {
    let old_ids: Vec<Value> = cluster.iter().map(|m| m["id"].clone()).collect();
    let none = |extra: (&str, Value), texts: Vec<String>| {
        let mut v = json!({"merged_count": 0, "new_count": 0, "old_ids": old_ids,
                           "new_texts": texts, "dry_run": dry_run});
        v[extra.0] = extra.1;
        v
    };
    let sources: HashSet<String> = cluster.iter().map(|m| member_str(m, "source")).collect();
    if sources.len() > 1
        && sources
            .iter()
            .any(|s| memories_engine::policy::is_reserved_namespace_source(s))
    {
        let why = "cluster contains structured memories from multiple exact sources";
        return Ok(none(("skipped_reason", json!(why)), Vec::new()));
    }
    let protected: Vec<i64> = cluster
        .iter()
        .filter(|m| m.get("pinned").is_some_and(truthy) || m.get("archived").is_some_and(truthy))
        .filter_map(|m| m["id"].as_i64())
        .collect();
    if !protected.is_empty() {
        let why = format!("cluster contains pinned/archived ids {protected:?}");
        return Ok(none(("skipped_reason", json!(why)), Vec::new()));
    }
    let prompt = consolidation_prompt(cluster, &infer_project(cluster));
    let reply = provider
        .complete(CONSOLIDATION_SYSTEM, &prompt)
        .await
        .map_err(|e| Failure::Provider(e.to_string()))?;
    let parsed = parse_json_array(&reply.text);
    // Python makes `str(t)` of each item, then needs a text that is not blank.
    let texts: Vec<String> = parsed
        .iter()
        .map(|t| match t {
            Value::String(s) => s.clone(),
            other => py_str(other),
        })
        .collect();
    if texts.is_empty() || texts.iter().any(|t| t.trim().is_empty()) {
        let e = json!("unparseable consolidation response");
        return Ok(none(("error", e), Vec::new()));
    }
    if !dry_run {
        let source = cluster
            .first()
            .and_then(|m| m.get("source"))
            .map_or_else(|| "consolidated".to_owned(), py_str);
        let category = match dominant_category(cluster) {
            Value::String(s) => s,
            other => py_str(&other),
        };
        let r = w
            .replace_consolidation_cluster(cluster, &texts, &source, &category)
            .await
            .map_err(Failure::Write)?;
        if let Some(e) = r.get("error") {
            return Ok(none(("error", e.clone()), texts));
        }
    }
    Ok(
        json!({"merged_count": cluster.len(), "new_count": texts.len(), "old_ids": old_ids,
              "new_texts": texts, "dry_run": dry_run}),
    )
}

fn seed() -> u64 {
    let mut b = [0u8; 8];
    let _ = getrandom::fill(&mut b);
    u64::from_le_bytes(b)
}

/// `POST /maintenance/consolidate?dry_run=&source_prefix=`.
pub async fn consolidate(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    Query(q): Query<Vec<(String, String)>>,
) -> Reply {
    let dry_run = query_dry_run(&q)?;
    let prefix = q
        .iter()
        .rev()
        .find(|(k, _)| k == "source_prefix")
        .map(|(_, v)| v.clone())
        .unwrap_or_default();
    admin(&auth)?;
    let Some(provider) = state.extract.provider.clone() else {
        return Err(err(
            StatusCode::SERVICE_UNAVAILABLE,
            "No LLM provider configured for consolidation",
        ));
    };
    let w = writer(&state)?;
    let _job = state.jobs.lock().await;
    let clusters = w
        .consolidation_clusters(&prefix, retention_days(), seed())
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "consolidation scan failed");
            internal_error()
        })?;
    let mut results = Vec::with_capacity(clusters.len());
    for cluster in &clusters {
        match consolidate_cluster(&provider, &w, cluster, dry_run).await {
            Ok(r) => results.push(r),
            Err(Failure::Write(WriteError::Policy(p))) => {
                return Err(err(StatusCode::UNPROCESSABLE_ENTITY, p.0));
            }
            Err(e) => {
                tracing::error!(error = ?e, "consolidation failed");
                return Err(internal_error());
            }
        }
    }
    if !dry_run {
        let source = format!("maintenance:count={}", results.len());
        audit(
            &state,
            &auth,
            &client,
            "memory.consolidated",
            String::new(),
            &source,
        )
        .await;
    }
    Ok(Json(
        json!({"clusters_found": clusters.len(), "results": results, "dry_run": dry_run}),
    ))
}

// ---- prune ---------------------------------------------------------------------

/// The IDs that no search returned (`get_unretrieved_memory_ids`). With usage
/// tracking off, no record (the Python `NullTracker`).
async fn unretrieved(state: &AppState, w: &Writer) -> rusqlite::Result<HashSet<i64>> {
    let Some(u) = &state.usage else {
        return Ok(HashSet::new());
    };
    let retrieved = u.retrieved_ids().await?;
    Ok(w.engine
        .catalog()
        .iter()
        .map(|(id, _)| id as i64)
        .filter(|id| !retrieved.contains(id))
        .collect())
}

async fn prune_candidates(state: &AppState, w: &Writer) -> Result<Vec<i64>, String> {
    let unretrieved = unretrieved(state, w).await.map_err(|e| e.to_string())?;
    w.prune_candidates(&unretrieved, &PyDateTime::now_utc(), retention_days())
        .await
        .map_err(|e| e.to_string())
}

/// `POST /maintenance/prune?dry_run=`.
pub async fn prune(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    Query(q): Query<Vec<(String, String)>>,
) -> Reply {
    let dry_run = query_dry_run(&q)?;
    admin(&auth)?;
    let w = writer(&state)?;
    let _job = state.jobs.lock().await;
    let candidates = prune_candidates(&state, &w).await.map_err(|e| {
        tracing::error!(error = %e, "prune scan failed");
        internal_error()
    })?;
    let mut pruned = 0;
    if !dry_run {
        for id in &candidates {
            w.delete_memory(*id, false).await.map_err(|e| {
                tracing::error!(error = %e, id, "prune delete failed");
                internal_error()
            })?;
            pruned += 1;
        }
        let source = format!("maintenance:count={pruned}");
        audit(
            &state,
            &auth,
            &client,
            "memory.pruned",
            String::new(),
            &source,
        )
        .await;
    }
    Ok(Json(
        json!({"candidates": candidates.len(), "pruned": pruned, "dry_run": dry_run}),
    ))
}

// ---- policies, compact, reembed --------------------------------------------------

/// `POST /maintenance/enforce-policies?dry_run=`.
pub async fn enforce_policies(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    Query(q): Query<Vec<(String, String)>>,
) -> Reply {
    let dry_run = query_dry_run(&q)?;
    admin(&auth)?;
    let w = writer(&state)?;
    let _job = state.jobs.lock().await;
    let profiles =
        Profiles::load(&state.data_dir.join("extraction_profiles.json")).map_err(|e| {
            tracing::error!(error = %e, "cannot read extraction_profiles.json");
            internal_error()
        })?;
    let r = w
        .enforce_policies(dry_run, &profiles, &PyDateTime::now_utc())
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "enforce policies failed");
            internal_error()
        })?;
    if !dry_run {
        for a in r["actions"].as_array().into_iter().flatten() {
            let id = a["memory_id"].to_string();
            let source = a["source"].as_str().unwrap_or_default();
            audit(&state, &auth, &client, "memory.policy_archived", id, source).await;
        }
    }
    Ok(Json(r))
}

/// `POST /maintenance/compact` (`CompactRequest`): the clusters only, no change.
pub async fn compact(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Reply {
    let mut m = Model::new(&map, body_loc());
    let bounds = Bounds {
        ge: Some(0.5),
        gt: None,
        le: Some(1.0),
    };
    let threshold = m.float("threshold", Some(0.85), false, bounds);
    let threshold =
        finish(std::mem::take(&mut m.errors), threshold).map_err(IntoResponse::into_response)?;
    admin(&auth)?;
    let w = writer(&state)?;
    let _job = state.jobs.lock().await;
    let fail = |e: WriteError| {
        tracing::error!(error = %e, "compact failed");
        server_error()
    };
    let clusters = w.similar_clusters(threshold).await.map_err(fail)?;
    let mut details = Vec::new();
    for ids in clusters {
        let mut mems = Vec::new();
        for &id in &ids {
            // Python skips a record that it cannot read.
            if let Ok(r) = w.get(id).await {
                let text: String = memories_engine::writes::text_of(&r)
                    .unwrap_or_default()
                    .chars()
                    .take(200)
                    .collect();
                mems.push(json!({"id": id, "text": text, "source": memories_engine::writes::source_of(&r)}));
            }
        }
        if !mems.is_empty() {
            details.push(json!({"ids": ids, "size": ids.len(), "memories": mems}));
        }
    }
    let total: usize = details
        .iter()
        .map(|c| c["size"].as_u64().unwrap_or(0) as usize)
        .sum();
    Ok(Json(
        json!({"clusters": details, "cluster_count": details.len(), "total_memories_in_clusters": total}),
    ))
}

/// `POST /maintenance/reembed` (`ReembedRequest`). This server has one
/// embedding model: a different `model` gets HTTP 400.
pub async fn reembed(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Reply {
    let mut m = Model::new(&map, body_loc());
    let model = m.opt_string("model", usize::MAX);
    finish(std::mem::take(&mut m.errors), Some(())).map_err(IntoResponse::into_response)?;
    admin(&auth)?;
    let w = writer(&state)?;
    let current = w.engine.embedder.config().model.clone();
    if let Some(name) = model.filter(|n| !n.is_empty() && *n != current) {
        return Err(err(
            StatusCode::BAD_REQUEST,
            format!(
                "Failed to load model {name}: this server uses one embedding model ({current}); set EMBED_MODEL and migrate to change it"
            ),
        ));
    }
    let _job = state.jobs.lock().await;
    let total = w.reembed().await.map_err(|e| {
        tracing::error!(error = %e, "re-embed failed");
        server_error()
    })?;
    tracing::info!("Re-embed complete: {current} -> {current} ({total} memories)");
    Ok(Json(
        json!({"status": "completed", "old_model": current, "new_model": current,
                   "memories_processed": total, "dimension": w.engine.dim}),
    ))
}

// ---- routes that do no work ------------------------------------------------------

/// The `manual` counters of `embedder_reload` in `GET /metrics`.
pub fn reload_counters() -> Value {
    json!({
        "requests_total": 0, "succeeded_total": 0, "failed_total": 0,
        "last_requested_at": null, "last_completed_at": null,
        "last_reload_duration_ms": 0.0, "last_gc_collected": null,
        "last_trim_reason": null, "last_error": null, "last_error_at": null,
    })
}

/// `POST /maintenance/embedder/reload`: the Python reply with no work. This
/// server loads the model one time and has no garbage collector to run.
pub async fn embedder_reload(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Reply {
    admin(&auth)?;
    let w = writer(&state)?;
    let at = PyDateTime::now_utc().isoformat_utc();
    {
        let mut c = state.reload.lock().unwrap_or_else(|e| e.into_inner());
        let n = |c: &Value, k: &str| c[k].as_i64().unwrap_or(0) + 1;
        c["requests_total"] = json!(n(&c, "requests_total"));
        c["succeeded_total"] = json!(n(&c, "succeeded_total"));
        c["last_requested_at"] = json!(at);
        c["last_completed_at"] = json!(at);
        c["last_reload_duration_ms"] = json!(0.0);
        c["last_gc_collected"] = json!(0);
        c["last_trim_reason"] = json!("disabled");
        c["last_error"] = Value::Null;
        c["last_error_at"] = Value::Null;
    }
    Ok(Json(json!({
        "success": true, "reloaded": true,
        "model": w.engine.embedder.config().model, "dimension": w.engine.dim,
        "gc_collected": 0, "trim": {"trimmed": false, "reason": "disabled"},
    })))
}

/// `POST /index/build` (`BuildIndexRequest`): the Python reply with zero
/// counts. The Python route replaces the whole index with the workspace
/// files; this server does not do that.
pub async fn index_build(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    JsonBody(map): JsonBody,
) -> Reply {
    let mut m = Model::new(&map, body_loc());
    let sources = m.string_list("sources");
    drop(sources);
    finish(std::mem::take(&mut m.errors), Some(())).map_err(IntoResponse::into_response)?;
    admin(&auth)?;
    writer(&state)?;
    tracing::info!("index build: no operation in this server");
    audit(
        &state,
        &auth,
        &client,
        "index.rebuilt",
        String::new(),
        "maintenance:count=0",
    )
    .await;
    Ok(Json(
        json!({"success": true, "files_processed": 0, "memories_added": 0,
                   "backup_location": "", "message": "Index rebuilt successfully"}),
    ))
}

// ---- scheduler -------------------------------------------------------------------

const DAY_US: i64 = 86_400_000_000;

/// One scheduled job: a time of day in UTC, each day or on Sunday only.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Job {
    pub name: &'static str,
    pub hour: i64,
    pub minute: i64,
    pub sunday_only: bool,
}

pub const CONSOLIDATION: Job = Job {
    name: "consolidation",
    hour: 3,
    minute: 0,
    sunday_only: false,
};
pub const CONFLICTS: Job = Job {
    name: "conflict_drain",
    hour: 3,
    minute: 30,
    sunday_only: false,
};
pub const PRUNE: Job = Job {
    name: "prune",
    hour: 4,
    minute: 0,
    sunday_only: true,
};

/// The last scheduled time of `job` at or before `now`, in microseconds since
/// 0001-01-01 UTC (`PyDateTime::utc_us`).
pub fn last_due(job: &Job, now_us: i64) -> i64 {
    let at = (job.hour * 60 + job.minute) * 60 * 1_000_000;
    let mut day = now_us.div_euclid(DAY_US);
    if day * DAY_US + at > now_us {
        day -= 1;
    }
    // `weekday` takes the ordinal (day + 1); Sunday is 6.
    while job.sunday_only && weekday(day + 1) != 6 {
        day -= 1;
    }
    day * DAY_US + at
}

/// What the scheduler does with a job at a tick.
#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    /// No last run is stored (a new install): store `now`, run nothing.
    FirstStart,
    Run,
    Wait,
}

/// A job runs when its last run is before its last scheduled time. Thus a
/// job that was due while the server did not run, runs after the start.
pub fn decide(job: &Job, last_run_us: Option<i64>, now_us: i64) -> Decision {
    match last_run_us {
        None => Decision::FirstStart,
        Some(last) if last < last_due(job, now_us) => Decision::Run,
        Some(_) => Decision::Wait,
    }
}

fn meta_key(job: &Job) -> String {
    format!("maintenance_last_{}", job.name)
}

/// The settings of the scheduler (`MAINTENANCE_*`).
#[derive(Clone, Debug)]
pub struct SchedulerConfig {
    pub enabled: bool,
    pub conflict_drain: bool,
    pub conflict_max: usize,
}

impl SchedulerConfig {
    pub fn from_env() -> Self {
        let var = |n: &str| std::env::var(n).ok();
        let max = var("MAINTENANCE_CONFLICT_MAX")
            .and_then(|v| v.trim().parse::<i64>().ok())
            .unwrap_or(200);
        Self {
            enabled: config::parse_bool(var("MAINTENANCE_ENABLED").as_deref(), false),
            conflict_drain: config::parse_bool(var("MAINTENANCE_CONFLICT_DRAIN").as_deref(), true),
            conflict_max: max.max(0) as usize,
        }
    }
}

async fn run_job(
    state: &AppState,
    w: &Writer,
    provider: &Provider,
    job: &Job,
    cfg: &SchedulerConfig,
) {
    let _job = state.jobs.lock().await;
    let t0 = Instant::now();
    tracing::info!("Maintenance started: {}", job.name);
    let outcome: Result<String, String> = match job.name {
        "consolidation" => match w.consolidation_clusters("", retention_days(), seed()).await {
            Err(e) => Err(e.to_string()),
            Ok(clusters) => {
                let mut r = Ok(format!("{} clusters merged", clusters.len()));
                for c in &clusters {
                    if let Err(e) = consolidate_cluster(provider, w, c, false).await {
                        r = Err(format!("{e:?}"));
                        break;
                    }
                }
                r
            }
        },
        "conflict_drain" => w
            .resolve_conflicts(false, cfg.conflict_max)
            .await
            .map(|r| {
                format!(
                    "{} resolved, {} need review, {} orphaned markers cleared",
                    r["resolved_count"], r["needs_review_count"], r["orphaned_count"]
                )
            })
            .map_err(|e| e.to_string()),
        _ => match prune_candidates(state, w).await {
            Err(e) => Err(e),
            Ok(ids) => {
                let (mut deleted, mut skipped) = (0, 0);
                for id in ids {
                    match w.delete_memory(id, false).await {
                        Ok(_) => deleted += 1,
                        Err(_) => skipped += 1,
                    }
                }
                Ok(format!("{deleted} pruned, {skipped} skipped"))
            }
        },
    };
    let secs = t0.elapsed().as_secs_f64();
    match outcome {
        Ok(msg) => tracing::info!(
            "Maintenance complete: {}, {msg}, {secs:.1}s elapsed",
            job.name
        ),
        Err(e) => tracing::error!("Scheduled {} failed: {e}", job.name),
    }
    if secs > 30.0 {
        tracing::warn!(
            "Maintenance slow: {} took {secs:.1}s (threshold 30s)",
            job.name
        );
    }
}

/// Checks the jobs one time each minute. Starts when the engine is ready.
pub async fn scheduler(state: Arc<AppState>, cfg: SchedulerConfig, provider: Arc<Provider>) {
    let mut jobs = vec![CONSOLIDATION, PRUNE];
    if cfg.conflict_drain {
        jobs.insert(1, CONFLICTS);
    }
    tracing::info!("Maintenance scheduler started");
    loop {
        if let Some(w) = state.writer.get().cloned() {
            for job in &jobs {
                let now = PyDateTime::now_utc().utc_us();
                let key = meta_key(job);
                let last = match w.engine.state.get_meta(&key) {
                    Ok(v) => v.and_then(|s| s.parse::<i64>().ok()),
                    Err(e) => {
                        tracing::error!(error = %e, "maintenance: cannot read state.db");
                        continue;
                    }
                };
                let decision = decide(job, last, now);
                if decision == Decision::Wait {
                    continue;
                }
                // As Python: record the run before the job, so a failed job
                // does not run again at each tick.
                if let Err(e) = w.engine.state.set_meta(&[(&key, &now.to_string())]) {
                    tracing::error!(error = %e, "maintenance: cannot write state.db");
                    continue;
                }
                if decision == Decision::Run {
                    run_job(&state, &w, &provider, job, &cfg).await;
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn us(text: &str) -> i64 {
        PyDateTime::fromisoformat(text).unwrap().utc_us()
    }

    #[test]
    fn last_due_finds_the_last_time_of_each_job() {
        // 2026-11-16 is a Monday.
        let now = us("2026-11-16T03:10:00+00:00");
        assert_eq!(
            last_due(&CONSOLIDATION, now),
            us("2026-11-16T03:00:00+00:00")
        );
        assert_eq!(last_due(&CONFLICTS, now), us("2026-11-15T03:30:00+00:00"));
        assert_eq!(last_due(&PRUNE, now), us("2026-11-15T04:00:00+00:00"));
        let sunday_early = us("2026-11-15T03:59:59+00:00");
        assert_eq!(
            last_due(&PRUNE, sunday_early),
            us("2026-11-08T04:00:00+00:00")
        );
        assert_eq!(
            last_due(&CONSOLIDATION, us("2026-11-16T03:00:00+00:00")),
            us("2026-11-16T03:00:00+00:00")
        );
    }

    #[test]
    fn decide_runs_due_and_missed_jobs_only() {
        let now = us("2026-11-16T03:10:00+00:00");
        assert_eq!(decide(&CONSOLIDATION, None, now), Decision::FirstStart);
        // Ran yesterday after 03:00: due again today.
        let yesterday = us("2026-11-15T03:01:00+00:00");
        assert_eq!(decide(&CONSOLIDATION, Some(yesterday), now), Decision::Run);
        // Ran at 03:00 today: wait.
        let today = us("2026-11-16T03:00:30+00:00");
        assert_eq!(decide(&CONSOLIDATION, Some(today), now), Decision::Wait);
        // The server was down on Sunday at 04:00: prune runs after the start.
        let before_sunday = us("2026-11-14T12:00:00+00:00");
        assert_eq!(decide(&PRUNE, Some(before_sunday), now), Decision::Run);
        assert_eq!(
            decide(&PRUNE, Some(us("2026-11-15T04:00:01+00:00")), now),
            Decision::Wait
        );
    }

    #[test]
    fn parse_json_array_follows_python() {
        assert_eq!(parse_json_array(" [\"a\", 1] "), vec![json!("a"), json!(1)]);
        assert_eq!(parse_json_array("```json\n[\"b\"]\n```"), vec![json!("b")]);
        assert_eq!(parse_json_array("Here: [\"c\"] done"), vec![json!("c")]);
        assert!(parse_json_array("{\"a\": 1}").is_empty());
        assert!(parse_json_array("no array").is_empty());
    }

    #[test]
    fn prompt_matches_python_json_dumps() {
        let cluster = vec![
            json!({"id": 3, "text": "Caf\u{e9} \"x\"\n", "source": "a/b", "category": "decision"}),
            json!({"id": 4, "text": "y"}),
        ];
        let p = consolidation_prompt(&cluster, &infer_project(&cluster));
        // Python: json.dumps([...], indent=2) and the prompt template.
        let want = "These 2 memories are about the same topic in the b project.\n\
                    Consolidate them into 1-2 concise memories that capture ALL unique information.\n\
                    Drop redundant or overlapping details. Preserve: decisions and reasoning, bug fixes, conventions.\n\
                    \n\
                    Memories to consolidate:\n\
                    [\n  {\n    \"id\": 3,\n    \"text\": \"Caf\\u00e9 \\\"x\\\"\\n\",\n    \"category\": \"decision\"\n  },\n  \
                    {\n    \"id\": 4,\n    \"text\": \"y\",\n    \"category\": \"DETAIL\"\n  }\n]\n\
                    \n\
                    Output a JSON array of consolidated text strings. Each must be self-contained.";
        assert_eq!(p, want);
        assert_eq!(dominant_category(&cluster), json!("decision"));
        assert_eq!(infer_project(&[json!({"source": "solo"})]), "solo");
        assert_eq!(infer_project(&[json!({"source": "a/"})]), "a");
        assert_eq!(infer_project(&[json!({})]), "unknown");
    }
}
