//! The extract job API: `POST /memory/extract`, `GET /memory/extract/{job_id}`
//! and `GET /extract/status`, with the durable queue in `state.db`.
//!
//! The request handler only checks the body and writes the job. Workers take
//! the jobs and give each one to the [`Pipeline`]. The pipeline of task P4-3
//! does the extraction; until then the binary uses [`ProviderOnly`].

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use std::future::Future;
use std::path::Path;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::{
    Extension, Json,
    extract::{Path as UrlPath, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};
use memories_extract::provider::Provider;
use memories_store::{Enqueue, JobQueue, QueueStats, StateError};
use memories_types::config;
use serde_json::{Map, Value, json};
use tokio::sync::Notify;

use crate::{
    AppState,
    audit::Entry,
    auth::AuthContext,
    error::{ApiError, JsonBody, item, missing, string_too_short},
    keys::{is_valid_slug, utc_now_iso, uuid4_hex},
    shell::{Client, internal_error},
    usage::Row,
};

/// A job gets this many tries. A try fails when the pipeline returns an error,
/// panics, or the server stops during the try.
pub const MAX_TRIES: u32 = 3;
/// `GET /extract/status` calls the provider at most once in this time.
const HEALTH_CACHE: Duration = Duration::from_secs(60);
/// Python `_periodic_job_cleanup`.
const TRIM_INTERVAL: Duration = Duration::from_secs(60);
/// A worker with no work looks at the queue again after this time.
const IDLE_POLL: Duration = Duration::from_secs(1);
/// The tries to write the result of a job before the job stays `running`.
const WRITE_TRIES: u32 = 5;

/// The settings of the queue, with the Python names.
#[derive(Clone, Debug)]
pub struct ExtractConfig {
    /// `EXTRACT_MAX_INFLIGHT`: the worker count.
    pub workers: u64,
    /// `EXTRACT_QUEUE_MAX`. The Python default is 20 for each worker; this
    /// server keeps the jobs on disk, so the default is 10,000.
    pub queue_max: u64,
    /// `EXTRACT_JOB_RETENTION_SEC`: a finished job stays this long.
    pub retention_sec: u64,
    /// `EXTRACT_JOBS_MAX`: above this many jobs, the oldest finished jobs go.
    pub jobs_max: u64,
    /// `MAX_EXTRACT_MESSAGE_CHARS`.
    pub max_message_chars: usize,
    /// `EXTRACT_FALLBACK_ADD`. Shown by `/extract/status`; task P4-3 ports the fallback.
    pub fallback_add: bool,
    /// The delay before try n+1 is n times this value (10 s). Not an env var.
    pub retry_delay: Duration,
}

impl Default for ExtractConfig {
    fn default() -> Self {
        Self::from_lookup(|_| None)
    }
}

impl ExtractConfig {
    pub fn from_env() -> Self {
        Self::from_lookup(|n| std::env::var(n).ok())
    }

    pub fn from_lookup(var: impl Fn(&str) -> Option<String>) -> Self {
        let int = |name: &str, default: i64, minimum: i64| {
            let (v, warn) = config::parse_int(var(name).as_deref(), default, minimum);
            if let Some(w) = warn {
                tracing::warn!("{name}: {w}");
            }
            v as u64
        };
        Self {
            workers: int("EXTRACT_MAX_INFLIGHT", 2, 1),
            queue_max: int("EXTRACT_QUEUE_MAX", 10_000, 1),
            retention_sec: int("EXTRACT_JOB_RETENTION_SEC", 300, 60),
            jobs_max: int("EXTRACT_JOBS_MAX", 200, 10),
            max_message_chars: int("MAX_EXTRACT_MESSAGE_CHARS", 120_000, 1) as usize,
            fallback_add: config::parse_bool(var("EXTRACT_FALLBACK_ADD").as_deref(), false),
            retry_delay: Duration::from_secs(10),
        }
    }
}

/// The result of one try of a job: the job result, or an error that uses a try.
/// A job can run more than once (a retry, or a stop during a try), so the
/// writes of a pipeline must give the same result when they run again.
pub type PipelineFuture = Pin<Box<dyn Future<Output = Result<Value, String>> + Send>>;

/// The extraction of one job. `request` is the JSON that `POST /memory/extract`
/// stored: `messages`, `source`, `context`, `allowed_prefixes`, `debug`,
/// `dry_run`, `document_at`, `trusted_authorship` and `promotion_context`.
/// `promotion_context` is the declaration of the client, checked only for its
/// shape. The pipeline must apply the gates of `build_promotion_context`.
pub trait Pipeline: Send + Sync {
    fn run(&self, request: Value) -> PipelineFuture;
}

/// The pipeline of the binary until task P4-3. A provider that does not answer
/// gives the Python result of a provider failure (a completed job). A provider
/// that answers fails the try, because the extraction is not ported yet.
// shortcut: no extraction; P4-3 replaces this type with the real pipeline.
pub struct ProviderOnly(pub Arc<Provider>);

impl Pipeline for ProviderOnly {
    fn run(&self, _request: Value) -> PipelineFuture {
        let provider = self.0.clone();
        Box::pin(async move {
            if provider.health_check().await {
                return Err("the extraction pipeline is not ported yet (task P4-3)".into());
            }
            Ok(json!({
                "actions": [],
                "extracted_count": 0,
                "stored_count": 0,
                "updated_count": 0,
                "deleted_count": 0,
                "promotion_candidates": [],
                "tokens": {"extract": {"input": 0, "output": 0}, "audn": {"input": 0, "output": 0}},
                "error": "provider_runtime_failure",
                "error_stage": "extract_facts",
                "error_message": format!("{} provider is not reachable", provider.name()),
            }))
        })
    }
}

pub struct Extract {
    pub cfg: ExtractConfig,
    pub queue: Arc<JobQueue>,
    pub provider: Option<Arc<Provider>>,
    /// `None`: `POST /memory/extract` answers 501.
    pub pipeline: Option<Arc<dyn Pipeline>>,
    wake: Notify,
    health: Mutex<Option<(Instant, bool)>>,
    /// One health call at a time, so that the cache holds for each caller.
    health_call: tokio::sync::Mutex<()>,
}

impl Extract {
    /// Opens the queue in `<data_dir>/state.db`, and puts each job that was
    /// running at the last stop back in the queue.
    pub fn open(
        data_dir: &Path,
        cfg: ExtractConfig,
        provider: Option<Arc<Provider>>,
        pipeline: Option<Arc<dyn Pipeline>>,
    ) -> Result<Self, StateError> {
        let queue = JobQueue::open(&data_dir.join("state.db"))?;
        let (back, failed) = queue.recover(MAX_TRIES, &utc_now_iso())?;
        if back + failed > 0 {
            tracing::info!(
                requeued = back,
                failed,
                "extract jobs that were running at the last stop"
            );
        }
        Ok(Self {
            cfg,
            queue: Arc::new(queue),
            provider,
            pipeline,
            wake: Notify::new(),
            health: Mutex::new(None),
            health_call: tokio::sync::Mutex::new(()),
        })
    }

    fn cached_health(&self) -> Option<bool> {
        let cached = *self.health.lock().unwrap();
        cached
            .filter(|(at, _)| at.elapsed() < HEALTH_CACHE)
            .map(|(_, ok)| ok)
    }

    async fn provider_healthy(&self, provider: &Provider) -> bool {
        if let Some(ok) = self.cached_health() {
            return ok;
        }
        let _one = self.health_call.lock().await;
        if let Some(ok) = self.cached_health() {
            return ok;
        }
        let ok = provider.health_check().await;
        *self.health.lock().unwrap() = Some((Instant::now(), ok));
        ok
    }
}

/// Runs a queue call off the async threads.
async fn blocking<T: Send + 'static>(
    queue: &Arc<JobQueue>,
    f: impl FnOnce(&JobQueue) -> Result<T, StateError> + Send + 'static,
) -> Result<T, StateError> {
    let q = queue.clone();
    match tokio::task::spawn_blocking(move || f(&q)).await {
        Ok(r) => r,
        Err(e) => Err(StateError::Task(e.to_string())),
    }
}

async fn stats(ex: &Extract) -> Result<QueueStats, Response> {
    blocking(&ex.queue, |q| q.stats()).await.map_err(|e| {
        tracing::error!(error = %e, "state.db: queue stats failed");
        internal_error()
    })
}

// ---- Workers -------------------------------------------------------------------

/// Starts `EXTRACT_MAX_INFLIGHT` workers if a pipeline is set.
pub fn spawn_workers(state: &Arc<AppState>) -> Vec<tokio::task::JoinHandle<()>> {
    let Some(pipeline) = state.extract.pipeline.clone() else {
        return Vec::new();
    };
    tracing::info!(
        "Extraction queue enabled with {} worker(s)",
        state.extract.cfg.workers
    );
    (1..=state.extract.cfg.workers)
        .map(|id| tokio::spawn(worker(state.clone(), pipeline.clone(), id)))
        .collect()
}

async fn worker(state: Arc<AppState>, pipeline: Arc<dyn Pipeline>, id: u64) {
    let ex = &state.extract;
    let mut last_trim = Instant::now();
    loop {
        let claimed = blocking(&ex.queue, |q| q.claim(&utc_now_iso())).await;
        let job = match claimed {
            Ok(Some(job)) => job,
            Ok(None) => {
                if last_trim.elapsed() >= TRIM_INTERVAL {
                    trim(ex).await;
                    last_trim = Instant::now();
                }
                tokio::select! {
                    _ = ex.wake.notified() => {}
                    _ = tokio::time::sleep(IDLE_POLL) => {}
                }
                continue;
            }
            Err(e) => {
                tracing::error!(worker = id, error = %e, "state.db: cannot take a job");
                tokio::time::sleep(IDLE_POLL).await;
                continue;
            }
        };
        let source = job.request["source"].as_str().unwrap_or("").to_string();
        // A task of its own, so that a panic in the pipeline fails only the try.
        let outcome = match tokio::spawn(pipeline.run(job.request)).await {
            Ok(r) => r,
            Err(e) => Err(format!("the pipeline stopped: {e}")),
        };
        let job_id = job.job_id;
        if let Ok(result) = &outcome {
            log_outcome(&state, &source, result);
        }
        if let Err(err) = &outcome {
            tracing::warn!(job_id, attempt = job.attempts, error = %err, "Extraction try failed");
        }
        let delay = ex.cfg.retry_delay * job.attempts;
        let mut done = Ok(());
        for n in 0..WRITE_TRIES {
            let (id2, out) = (job_id.clone(), outcome.clone());
            done = blocking(&ex.queue, move |q| match out {
                Ok(result) => q.complete(&id2, &utc_now_iso(), result),
                Err(err) => q
                    .fail(&id2, &utc_now_iso(), &err, MAX_TRIES, delay)
                    .map(drop),
            })
            .await;
            if done.is_ok() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(200 << n)).await;
        }
        if let Err(e) = done {
            // The job stays `running`; the next start puts it back in the queue.
            tracing::error!(job_id, error = %e, "state.db: cannot write the job result");
        }
        trim(ex).await;
    }
}

/// The usage rows of a finished extraction: the outcome, and the tokens of
/// each stage that used tokens.
fn log_outcome(state: &AppState, source: &str, result: &Value) {
    let Some(u) = &state.usage else {
        return;
    };
    let n = |k: &str| result[k].as_i64().unwrap_or(0);
    let source = source.to_string();
    let noop = match result.get("noop_count").filter(|v| !v.is_null()) {
        Some(v) => v.as_i64().unwrap_or(0),
        None => result["actions"].as_array().map_or(0, |a| {
            a.iter().filter(|x| x["action"] == "noop").count() as i64
        }),
    };
    u.log(Row::Outcome {
        source: source.clone(),
        extracted: n("extracted_count"),
        stored: n("stored_count"),
        updated: n("updated_count"),
        deleted: n("deleted_count"),
        noop,
        conflict: n("conflict_count"),
        fallback: n("fallback_count"),
        links_created: result["links_created"]
            .as_array()
            .map_or(0, |a| a.len() as i64),
    });
    let Some(provider) = &state.extract.provider else {
        return;
    };
    for stage in ["extract", "audn"] {
        let t = &result["tokens"][stage];
        let (input, output) = (
            t["input"].as_i64().unwrap_or(0),
            t["output"].as_i64().unwrap_or(0),
        );
        if input != 0 || output != 0 {
            u.log(Row::Tokens {
                provider: provider.name().into(),
                model: provider.model().into(),
                stage: stage.into(),
                input_tokens: input,
                output_tokens: output,
                source: source.clone(),
            });
        }
    }
}

async fn trim(ex: &Extract) {
    let (retention, max) = (ex.cfg.retention_sec, ex.cfg.jobs_max);
    if let Err(e) = blocking(&ex.queue, move |q| q.trim(retention, max)).await {
        tracing::debug!(error = %e, "state.db: job trim failed");
    }
}

// ---- Body ----------------------------------------------------------------------

/// The fields of the Python `ExtractRequest` that the route uses.
struct ExtractBody {
    messages: String,
    source: String,
    context: String,
    debug: bool,
    dry_run: bool,
    document_at: Option<String>,
    promotion_context: Option<Value>,
}

/// A pydantic `pattern` and the function that does the same match.
type Pattern = (&'static str, fn(&str) -> bool);

/// pydantic 2 errors for a model, with `loc` under `prefix`.
struct Checker {
    prefix: Vec<Value>,
    errors: Vec<Value>,
}

impl Checker {
    fn loc(&self, name: &str) -> Value {
        let mut l = self.prefix.clone();
        l.push(json!(name));
        Value::Array(l)
    }

    /// A `str` field. `required` has no default; `nullable` is `Optional[str]`.
    fn string(
        &mut self,
        map: &Map<String, Value>,
        name: &str,
        required: bool,
        nullable: bool,
        bounds: (usize, usize),
        pattern: Option<Pattern>,
    ) -> Option<String> {
        let loc = self.loc(name);
        let v = match map.get(name) {
            None if required => {
                self.errors.push(missing(loc, Value::Object(map.clone())));
                return None;
            }
            None => return None,
            Some(Value::Null) if nullable => return None,
            Some(v) => v,
        };
        let Some(s) = v.as_str() else {
            self.errors.push(item(
                "string_type",
                loc,
                "Input should be a valid string",
                v.clone(),
            ));
            return None;
        };
        let (min, max) = bounds;
        let len = s.chars().count();
        if len < min {
            self.errors.push(string_too_short(loc, v.clone(), min));
        } else if len > max {
            let plural = if max == 1 { "" } else { "s" };
            let mut e = item(
                "string_too_long",
                loc,
                &format!("String should have at most {max} character{plural}"),
                v.clone(),
            );
            e["ctx"] = json!({"max_length": max});
            self.errors.push(e);
        } else if let Some((p, _)) = pattern.filter(|(_, ok)| !ok(s)) {
            let mut e = item(
                "string_pattern_mismatch",
                loc,
                &format!("String should match pattern '{p}'"),
                v.clone(),
            );
            e["ctx"] = json!({"pattern": p});
            self.errors.push(e);
        } else {
            return Some(s.to_string());
        }
        None
    }

    /// A `bool` field with a default, in the lax mode of pydantic 2.
    fn boolean(&mut self, map: &Map<String, Value>, name: &str) -> bool {
        let Some(v) = map.get(name) else {
            return false;
        };
        let parsed = match v {
            Value::Bool(b) => Ok(*b),
            Value::String(s) => match s.to_lowercase().as_str() {
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
                _ => Err("bool_parsing"),
            },
            Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
                (Some(0), ..) => Ok(false),
                (Some(1), ..) => Ok(true),
                (Some(_), ..) | (None, Some(_), _) => Err("bool_parsing"),
                (None, None, Some(0.0)) => Ok(false),
                (None, None, Some(1.0)) => Ok(true),
                _ => Err("bool_type"),
            },
            _ => Err("bool_type"),
        };
        match parsed {
            Ok(b) => b,
            Err(kind) => {
                let msg = if kind == "bool_parsing" {
                    "Input should be a valid boolean, unable to interpret input"
                } else {
                    "Input should be a valid boolean"
                };
                let loc = self.loc(name);
                self.errors.push(item(kind, loc, msg, v.clone()));
                false
            }
        }
    }
}

fn is_fingerprint(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// `PromotionRequestContext` (`extra="forbid"`). Returns the checked object.
fn promotion_context(c: &mut Checker, v: &Value) -> Option<Value> {
    let Some(map) = v.as_object() else {
        let loc = c.loc("promotion_context");
        c.errors.push(item(
            "model_attributes_type",
            loc,
            "Input should be a valid dictionary or object to extract fields from",
            v.clone(),
        ));
        return None;
    };
    let mut inner = Checker {
        prefix: match c.loc("promotion_context") {
            Value::Array(a) => a,
            _ => unreachable!(),
        },
        errors: Vec::new(),
    };
    inner.string(
        map,
        "project_id",
        true,
        false,
        (1, 200),
        Some(("^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$", is_valid_slug)),
    );
    match map.get("mode") {
        None => {
            let loc = inner.loc("mode");
            inner.errors.push(missing(loc, v.clone()));
        }
        Some(m) if matches!(m.as_str(), Some("off" | "shadow" | "auto")) => {}
        Some(m) => {
            let expected = "'off', 'shadow' or 'auto'";
            let mut e = item(
                "literal_error",
                inner.loc("mode"),
                &format!("Input should be {expected}"),
                m.clone(),
            );
            e["ctx"] = json!({"expected": expected});
            inner.errors.push(e);
        }
    }
    inner.string(
        map,
        "declaration_fingerprint",
        true,
        false,
        (64, 64),
        Some(("^[0-9a-f]{64}$", is_fingerprint)),
    );
    for (k, x) in map {
        if !matches!(
            k.as_str(),
            "project_id" | "mode" | "declaration_fingerprint"
        ) {
            let loc = inner.loc(k);
            inner.errors.push(item(
                "extra_forbidden",
                loc,
                "Extra inputs are not permitted",
                x.clone(),
            ));
        }
    }
    let ok = inner.errors.is_empty();
    c.errors.extend(inner.errors);
    ok.then(|| v.clone())
}

fn check_body(map: &Map<String, Value>, max_chars: usize) -> Result<ExtractBody, ApiError> {
    let mut c = Checker {
        prefix: vec![json!("body")],
        errors: Vec::new(),
    };
    let messages = c.string(map, "messages", true, false, (1, max_chars), None);
    let source = c.string(map, "source", false, false, (0, usize::MAX), None);
    let context = c.string(map, "context", false, false, (0, usize::MAX), None);
    let debug = c.boolean(map, "debug");
    let dry_run = c.boolean(map, "dry_run");
    let document_at = c.string(map, "document_at", false, true, (0, usize::MAX), None);
    let promotion = match map.get("promotion_context") {
        None | Some(Value::Null) => None,
        Some(v) => promotion_context(&mut c, v),
    };
    if !c.errors.is_empty() {
        return Err(ApiError::validation(c.errors));
    }
    Ok(ExtractBody {
        messages: messages.unwrap_or_default(),
        source: source.unwrap_or_default(),
        context: context.unwrap_or_else(|| "stop".into()),
        debug,
        dry_run,
        document_at,
        promotion_context: promotion,
    })
}

/// Python `_trusted_authorship`: only a managed key with a valid principal.
fn trusted_authorship(auth: &AuthContext, headers: &HeaderMap) -> Value {
    let Some(pid) = auth
        .principal_id
        .as_deref()
        .filter(|p| auth.key_type == "managed" && is_valid_slug(p))
    else {
        return Value::Null;
    };
    let client = headers
        .get("X-Memories-Client")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.trim().to_lowercase())
        .filter(|s| {
            matches!(
                s.as_str(),
                "codex" | "claude-code" | "hook" | "manual" | "other"
            )
        })
        .unwrap_or_else(|| "other".into());
    json!({"author": pid, "origin_client": client, "contributors": [], "source_memory_ids": []})
}

// ---- Routes --------------------------------------------------------------------

pub async fn enqueue(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Response, Response> {
    let ex = &state.extract;
    let body = check_body(&map, ex.cfg.max_message_chars).map_err(IntoResponse::into_response)?;
    let deny = |status, msg: &str| ApiError::new(status, msg).into_response();
    if !body.source.is_empty() {
        if !auth.can_write(&body.source) {
            return Err(deny(
                StatusCode::FORBIDDEN,
                &format!("Key does not have write access to source: {}", body.source),
            ));
        }
    } else if auth.role == "read-only" {
        return Err(deny(
            StatusCode::FORBIDDEN,
            "Read-only keys cannot trigger extraction",
        ));
    } else if auth.role != "admin" && auth.prefixes.is_some() {
        return Err(deny(
            StatusCode::BAD_REQUEST,
            "source is required for scoped keys",
        ));
    }
    if ex.pipeline.is_none() {
        // shortcut: the synchronous fallback (`EXTRACT_FALLBACK_ADD` with no
        // provider) is task P4-3; until then that setting also gives 501.
        return Err(deny(
            StatusCode::NOT_IMPLEMENTED,
            "Extraction not configured. Set EXTRACT_PROVIDER env var.",
        ));
    }

    crate::search::log(
        &state,
        crate::search::Caller::of(&headers).row("extract", &body.source, 1),
    );
    let mut entry = Entry::from_request(&auth, client.as_ref().map(|c| &c.0), "extract");
    entry.source_prefix = body.source.clone();
    state.audit(entry).await;

    let job_id = uuid4_hex();
    let authorship = trusted_authorship(&auth, &headers);
    let job = json!({
        "job_id": job_id,
        "status": "queued",
        "source": body.source,
        "context": body.context,
        "message_length": body.messages.chars().count(),
        "created_at": utc_now_iso(),
        "auth_key_id": auth.key_id,
        "trusted_authorship": authorship,
    });
    // Admin managed keys have an empty prefix list but no source limit.
    let allowed = if auth.role == "admin" {
        Value::Null
    } else {
        json!(auth.prefixes)
    };
    let request = json!({
        "messages": body.messages,
        "source": body.source,
        "context": body.context,
        "allowed_prefixes": allowed,
        "debug": body.debug,
        "dry_run": body.dry_run,
        "document_at": body.document_at,
        "trusted_authorship": authorship,
        "promotion_context": body.promotion_context,
    });
    let (id, max) = (job_id.clone(), ex.cfg.queue_max);
    let added = blocking(&ex.queue, move |q| q.enqueue(&id, &job, &request, max))
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "state.db: enqueue failed");
            internal_error()
        })?;
    let depth = match added {
        Enqueue::Queued { depth } => depth,
        Enqueue::Full { depth } => {
            let retry = (depth / ex.cfg.workers.max(1) + 1).clamp(1, 30);
            tracing::warn!("Extract queue full: depth={depth} max={max}");
            let mut res = ApiError::new(
                StatusCode::TOO_MANY_REQUESTS,
                json!({
                    "error": "extract_queue_full",
                    "message": "Extraction queue is full. Retry later.",
                    "queue_depth": depth,
                    "queue_max": max,
                    "retry_after_sec": retry,
                }),
            )
            .into_response();
            res.headers_mut()
                .insert(header::RETRY_AFTER, HeaderValue::from(retry));
            return Err(res);
        }
    };
    ex.wake.notify_one();
    tracing::info!(
        "Extract queued: job_id={job_id} source={} context={} queue_depth={depth}",
        body.source,
        body.context
    );
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({
            "job_id": job_id,
            "status": "queued",
            "queue_depth": depth,
            "result_url": format!("/memory/extract/{job_id}"),
        })),
    )
        .into_response())
}

/// Python `_can_access_extract_job`.
fn can_access(auth: &AuthContext, job: &Value) -> bool {
    if auth.can_manage_keys() {
        return true;
    }
    let source = job["source"].as_str().unwrap_or("");
    if !source.is_empty() && auth.can_read(source) {
        return true;
    }
    auth.key_id
        .as_deref()
        .is_some_and(|k| job["auth_key_id"].as_str() == Some(k))
}

pub async fn get_job(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    UrlPath(job_id): UrlPath<String>,
) -> Result<Json<Value>, Response> {
    let id = job_id.clone();
    let job = blocking(&state.extract.queue, move |q| q.get(&id))
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "state.db: job read failed");
            internal_error()
        })?;
    let Some(job) = job else {
        return Err(ApiError::new(
            StatusCode::NOT_FOUND,
            format!("Extraction job not found: {job_id}"),
        )
        .into_response());
    };
    if !can_access(&auth, &job) {
        return Err(
            ApiError::new(StatusCode::FORBIDDEN, "Access denied to extraction job").into_response(),
        );
    }
    Ok(Json(job))
}

/// The queue fields of `/extract/status`, also the `extract` part of `/metrics`.
pub async fn queue_fields(state: &AppState) -> Result<Value, Response> {
    let ex = &state.extract;
    let s = stats(ex).await?;
    Ok(json!({
        "queue_depth": s.depth,
        "queue_max": ex.cfg.queue_max,
        "queue_remaining": ex.cfg.queue_max.saturating_sub(s.depth),
        "workers": ex.cfg.workers,
        "jobs_tracked": s.tracked,
    }))
}

pub async fn status(State(state): State<Arc<AppState>>) -> Result<Json<Value>, Response> {
    let ex = &state.extract;
    let mut body = queue_fields(&state).await?;
    body["fallback_add_enabled"] = json!(ex.cfg.fallback_add);
    let Some(provider) = &ex.provider else {
        body["enabled"] = json!(false);
        return Ok(Json(body));
    };
    let healthy = ex.provider_healthy(provider).await;
    body["enabled"] = json!(true);
    body["provider"] = json!(provider.name());
    body["model"] = json!(provider.model());
    body["status"] = json!(if healthy { "healthy" } else { "unhealthy" });
    Ok(Json(body))
}

/// The queue fields of the health body for a caller with a valid key.
pub async fn health_fields(state: &Arc<AppState>) -> Map<String, Value> {
    let mut m = Map::new();
    match blocking(&state.extract.queue, |q| q.stats()).await {
        Ok(s) => {
            m.insert("extract_queue_depth".into(), json!(s.depth));
            m.insert(
                "extract_oldest_job_age_sec".into(),
                json!(s.oldest_age_sec.map(|a| (a * 1000.0).round() / 1000.0)),
            );
        }
        Err(e) => tracing::warn!(error = %e, "state.db: queue stats failed"),
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_defaults_and_minimums() {
        let c = ExtractConfig::default();
        assert_eq!(
            (
                c.workers,
                c.queue_max,
                c.retention_sec,
                c.jobs_max,
                c.max_message_chars
            ),
            (2, 10_000, 300, 200, 120_000)
        );
        assert!(!c.fallback_add);
        let c = ExtractConfig::from_lookup(|n| {
            Some(match n {
                "EXTRACT_MAX_INFLIGHT" => "0".into(),
                "EXTRACT_QUEUE_MAX" => "40".into(),
                "EXTRACT_JOB_RETENTION_SEC" => "5".into(),
                "EXTRACT_JOBS_MAX" => "x".into(),
                "EXTRACT_FALLBACK_ADD" => "yes".into(),
                _ => return None,
            })
        });
        assert_eq!(
            (c.workers, c.queue_max, c.retention_sec, c.jobs_max),
            (1, 40, 60, 200)
        );
        assert!(c.fallback_add);
    }

    #[test]
    fn trusted_authorship_only_for_a_managed_key_with_a_principal() {
        let mut auth = AuthContext::unrestricted();
        let mut h = HeaderMap::new();
        assert_eq!(trusted_authorship(&auth, &h), Value::Null);
        auth.key_type = "managed";
        auth.principal_id = Some("dk".into());
        h.insert("X-Memories-Client", HeaderValue::from_static(" Codex "));
        assert_eq!(
            trusted_authorship(&auth, &h),
            json!({"author": "dk", "origin_client": "codex", "contributors": [], "source_memory_ids": []})
        );
        h.insert("X-Memories-Client", HeaderValue::from_static("vim"));
        assert_eq!(trusted_authorship(&auth, &h)["origin_client"], "other");
        auth.principal_id = Some("Not A Slug".into());
        assert_eq!(trusted_authorship(&auth, &h), Value::Null);
    }
}
