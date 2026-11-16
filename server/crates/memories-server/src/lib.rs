//! HTTP routes, auth, middleware and the `memories-server` binary.

pub mod admin;
pub mod audit;
pub mod auth;
pub mod backup;
pub mod bulk;
pub mod error;
pub mod extract;
pub mod healthcheck;
pub mod keys;
pub mod maintenance;
pub mod migrate;
pub mod model;
pub mod pipeline;
pub mod promotions;
pub mod reads;
pub mod rollback;
pub mod search;
pub mod shell;
pub mod sync;
pub mod usage;
pub mod usage_routes;
pub mod validate;
pub mod writes;

use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock, Mutex, OnceLock};
use std::time::Instant;

use axum::{
    Extension, Json, Router,
    extract::{DefaultBodyLimit, State},
    http::HeaderMap,
    middleware::from_fn,
    middleware::from_fn_with_state,
    routing::{MethodFilter, delete, get, on, patch, post},
};
use memories_engine::Engine;
use memories_engine::writes::Writer;
use memories_types::config;
use serde_json::{Value, json};
use tower_http::catch_panic::CatchPanicLayer;

use crate::{
    audit::AuditLog,
    auth::{AuthContext, Failures},
    extract::{Extract, ExtractConfig},
    keys::KeyStore,
    shell::{Metrics, ROUTES, TrustedProxies},
    usage::Usage,
};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// The settings that this server version reads from the environment.
#[derive(Clone, Debug)]
pub struct Config {
    pub data_dir: PathBuf,
    /// Empty: a request with no key is admin (local-only installs).
    pub api_key: String,
    pub auth_rate_limit: i64,
    pub metrics_latency_samples: usize,
    pub forwarded_allow_ips: String,
    /// `AUDIT_LOG`: write and read `audit.db`.
    pub audit_log: bool,
    /// `MAX_BACKUPS`: the newest backup folders that stay (see `backup::survivors`).
    pub max_backups: usize,
    /// `USAGE_TRACKING`: write and read `usage.db`.
    pub usage_tracking: bool,
    /// `METRICS_TREND_SAMPLES`: the memory count samples that `GET /metrics` keeps.
    pub metrics_trend_samples: usize,
}

impl Config {
    /// Reads the variables with the Python names and defaults. An error is a
    /// value that the Python server also cannot start with.
    pub fn from_env() -> Result<Self, String> {
        let var = |n| std::env::var(n).ok();
        let (samples, warn) = config::parse_int(var("METRICS_LATENCY_SAMPLES").as_deref(), 200, 20);
        if let Some(w) = warn {
            tracing::warn!("METRICS_LATENCY_SAMPLES: {w}");
        }
        let (trend, warn) = config::parse_int(var("METRICS_TREND_SAMPLES").as_deref(), 120, 5);
        if let Some(w) = warn {
            tracing::warn!("METRICS_TREND_SAMPLES: {w}");
        }
        Ok(Self {
            data_dir: config::string("DATA_DIR", "/data").into(),
            api_key: config::string("API_KEY", ""),
            auth_rate_limit: config::int_strict(
                "AUTH_RATE_LIMIT",
                var("AUTH_RATE_LIMIT").as_deref(),
                50,
            )?,
            metrics_latency_samples: samples as usize,
            forwarded_allow_ips: config::string("FORWARDED_ALLOW_IPS", "127.0.0.1"),
            audit_log: config::parse_bool(var("AUDIT_LOG").as_deref(), false),
            max_backups: config::int_strict("MAX_BACKUPS", var("MAX_BACKUPS").as_deref(), 10)?
                .max(0) as usize,
            usage_tracking: config::parse_bool(var("USAGE_TRACKING").as_deref(), false),
            metrics_trend_samples: trend as usize,
        })
    }
}

pub struct AppState {
    pub api_key: String,
    pub data_dir: PathBuf,
    pub auth_rate_limit: i64,
    pub keys: KeyStore,
    /// `None` when `AUDIT_LOG` is off (the Python `NullAuditLog`).
    pub audit: Option<AuditLog>,
    pub failures: Failures,
    pub metrics: Metrics,
    pub trusted: TrustedProxies,
    /// The store, embedder and catalog. Not set when `QDRANT_URL` is not set
    /// (the routes that need them answer 503), or until the first-start
    /// migration ends.
    pub engine: OnceLock<Arc<Engine>>,
    /// True while the first-start migration runs: only `/health` answers.
    pub migrating: AtomicBool,
    pub extract: Extract,
    /// `None` when `USAGE_TRACKING` is off (the Python `NullTracker`).
    pub usage: Option<Usage>,
    pub started: Instant,
    /// The memory count samples of `GET /metrics` (`memory_trend`).
    pub trend: Mutex<VecDeque<Value>>,
    pub trend_max: usize,
    /// The write flow of the engine. Set with `engine`.
    pub writer: OnceLock<Arc<Writer>>,
    pub max_backups: usize,
    /// `_missed_counts`: `POST /memory/missed` calls of each source since the start.
    pub missed: Mutex<HashMap<String, u64>>,
    /// Set at shutdown: the scheduled job stops between clusters and deletes.
    pub stopping: AtomicBool,
    /// One maintenance job at a time (routes and the scheduler).
    pub jobs: tokio::sync::Mutex<()>,
    /// The `manual` counters of `embedder_reload` in `GET /metrics`.
    pub reload: Mutex<Value>,
}

impl AppState {
    /// A state with the default queue settings in `DATA_DIR` and no extract
    /// provider. Panics if `state.db` cannot open.
    pub fn new(cfg: &Config, keys: KeyStore, audit: Option<AuditLog>) -> Arc<Self> {
        let extract = Extract::open(&cfg.data_dir, ExtractConfig::default(), None, None)
            .expect("cannot open state.db");
        Self::with_extract(cfg, keys, audit, extract, None)
    }

    pub fn with_extract(
        cfg: &Config,
        keys: KeyStore,
        audit: Option<AuditLog>,
        extract: Extract,
        engine: Option<Arc<Engine>>,
    ) -> Arc<Self> {
        // As Python: a usage.db that cannot open stops the start.
        let usage = cfg.usage_tracking.then(|| {
            let u = Usage::open(&cfg.data_dir.join("usage.db")).expect("cannot open usage.db");
            tracing::info!("Usage tracking enabled");
            u
        });
        Arc::new(Self {
            api_key: cfg.api_key.clone(),
            data_dir: cfg.data_dir.clone(),
            auth_rate_limit: cfg.auth_rate_limit,
            keys,
            audit,
            failures: Failures::default(),
            metrics: Metrics::new(cfg.metrics_latency_samples),
            trusted: TrustedProxies::parse(&cfg.forwarded_allow_ips),
            engine: engine.map(OnceLock::from).unwrap_or_default(),
            migrating: AtomicBool::new(false),
            extract,
            usage,
            started: Instant::now(),
            trend: Mutex::default(),
            trend_max: cfg.metrics_trend_samples,
            writer: OnceLock::new(),
            max_backups: cfg.max_backups,
            missed: Mutex::default(),
            stopping: AtomicBool::new(false),
            jobs: tokio::sync::Mutex::new(()),
            reload: Mutex::new(maintenance::reload_counters()),
        })
    }
}

/// The routes that this server serves. Each other route in [`ROUTES`] answers 501.
const SERVED: &[(&str, &str)] = &[
    ("GET", "/openapi.json"),
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("GET", "/api/keys/me"),
    ("POST", "/api/keys"),
    ("GET", "/api/keys"),
    ("PATCH", "/api/keys/{key_id}"),
    ("DELETE", "/api/keys/{key_id}"),
    ("GET", "/audit"),
    ("POST", "/audit/purge"),
    ("GET", "/promotions"),
    ("GET", "/promotions/{candidate_id}"),
    ("POST", "/promotions/{candidate_id}/approve"),
    ("POST", "/promotions/{candidate_id}/reject"),
    ("GET", "/memory/{memory_id}"),
    ("POST", "/memory/get-batch"),
    ("GET", "/memories"),
    ("GET", "/memories/count"),
    ("GET", "/folders"),
    ("GET", "/stats"),
    ("GET", "/memory/{memory_id}/links"),
    ("GET", "/memory/conflicts"),
    ("POST", "/memory/add"),
    ("POST", "/memory/add-batch"),
    ("POST", "/memory/upsert"),
    ("POST", "/memory/upsert-batch"),
    ("PATCH", "/memory/{memory_id}"),
    ("DELETE", "/memory/{memory_id}"),
    ("POST", "/memory/delete-batch"),
    ("POST", "/memory/delete-by-source"),
    ("POST", "/memory/delete-by-prefix"),
    ("DELETE", "/memories"),
    ("POST", "/memory/supersede"),
    ("POST", "/memory/{memory_id}/supersede"),
    ("POST", "/memory/merge"),
    ("POST", "/memory/archive-batch"),
    ("POST", "/memory/{memory_id}/link"),
    ("DELETE", "/memory/{memory_id}/link/{target_id}"),
    ("POST", "/folders/rename"),
    ("POST", "/memory/conflicts/resolve"),
    ("POST", "/memory/deduplicate"),
    ("POST", "/memory/missed"),
    ("POST", "/search"),
    ("POST", "/search/batch"),
    ("POST", "/search/explain"),
    ("POST", "/search/evidence"),
    ("POST", "/memory/is-novel"),
    ("POST", "/memory/extract"),
    ("POST", "/memory/extract/commit"),
    ("GET", "/memory/extract/{job_id}"),
    ("GET", "/extract/status"),
    ("GET", "/backups"),
    ("POST", "/backup"),
    ("POST", "/restore"),
    ("GET", "/snapshots"),
    ("POST", "/snapshots"),
    ("POST", "/snapshots/{name}/restore"),
    ("GET", "/sync/status"),
    ("POST", "/sync/upload"),
    ("POST", "/sync/download"),
    ("GET", "/sync/snapshots"),
    ("POST", "/sync/restore/{backup_name}"),
    ("GET", "/export"),
    ("POST", "/import"),
    ("GET", "/extraction/profiles"),
    ("GET", "/extraction/profiles/{*prefix}"),
    ("PUT", "/extraction/profiles/{*prefix}"),
    ("DELETE", "/extraction/profiles/{*prefix}"),
    ("GET", "/usage"),
    ("GET", "/metrics"),
    ("GET", "/metrics/search-quality"),
    ("GET", "/metrics/extraction-quality"),
    ("GET", "/metrics/quality-summary"),
    ("GET", "/metrics/failures"),
    ("GET", "/metrics/problem-queries"),
    ("GET", "/metrics/stale-memories"),
    ("GET", "/metrics/graph-search"),
    ("GET", "/metrics/temporal-search"),
    ("POST", "/search/feedback"),
    ("GET", "/search/feedback/history"),
    ("DELETE", "/search/feedback/{feedback_id}"),
    ("POST", "/maintenance/consolidate"),
    ("POST", "/maintenance/prune"),
    ("POST", "/maintenance/enforce-policies"),
    ("POST", "/maintenance/compact"),
    ("POST", "/maintenance/reembed"),
    ("POST", "/maintenance/embedder/reload"),
    ("POST", "/index/build"),
];

fn stubs(router: Router<Arc<AppState>>, public: bool) -> Router<Arc<AppState>> {
    ROUTES
        .iter()
        .filter(|(m, p)| shell::is_public(p) == public && !SERVED.contains(&(*m, *p)))
        .fold(router, |r, (m, p)| {
            let filter = MethodFilter::try_from(m.parse::<axum::http::Method>().unwrap()).unwrap();
            r.route(p, on(filter, shell::not_ported))
        })
}

pub fn app(state: Arc<AppState>) -> Router {
    let protected = Router::new()
        .route("/api/keys/me", get(keys_me))
        .route("/api/keys", post(admin::create_key).get(admin::list_keys))
        .route(
            "/api/keys/{key_id}",
            patch(admin::update_key).delete(admin::revoke_key),
        )
        .route("/audit", get(admin::get_audit))
        .route("/audit/purge", post(admin::purge_audit))
        .route("/promotions", get(promotions::list))
        .route("/promotions/{candidate_id}", get(promotions::get))
        .route(
            "/promotions/{candidate_id}/approve",
            post(promotions::approve),
        )
        .route(
            "/promotions/{candidate_id}/reject",
            post(promotions::reject),
        )
        .route("/memory/conflicts", get(reads::conflicts))
        .route("/memory/get-batch", post(reads::get_batch))
        .route("/memory/{memory_id}/links", get(reads::links))
        .route("/memories", get(reads::list))
        .route("/memories/count", get(reads::count))
        .route("/folders", get(reads::list_folders))
        .route("/stats", get(reads::stats))
        .route("/memory/add", post(writes::add))
        .route("/memory/add-batch", post(writes::add_batch))
        .route("/memory/upsert", post(writes::upsert))
        .route("/memory/upsert-batch", post(writes::upsert_batch))
        .route(
            "/memory/{memory_id}",
            get(reads::get_memory)
                .patch(writes::patch)
                .delete(writes::delete),
        )
        .route("/memory/delete-batch", post(writes::delete_batch))
        .route("/memory/delete-by-source", post(bulk::delete_by_source))
        .route("/memory/delete-by-prefix", post(bulk::delete_by_prefix))
        .route("/memories", delete(bulk::delete_memories))
        .route("/memory/supersede", post(bulk::supersede))
        .route("/memory/{memory_id}/supersede", post(bulk::supersede_by_id))
        .route("/memory/merge", post(bulk::merge))
        .route("/memory/archive-batch", post(bulk::archive_batch))
        .route("/memory/{memory_id}/link", post(bulk::add_link))
        .route(
            "/memory/{memory_id}/link/{target_id}",
            delete(bulk::remove_link),
        )
        .route("/folders/rename", post(bulk::rename_folder))
        .route("/memory/conflicts/resolve", post(bulk::resolve_conflicts))
        .route("/memory/deduplicate", post(bulk::deduplicate))
        .route("/memory/missed", post(bulk::missed))
        .route("/search", post(search::search))
        .route("/search/batch", post(search::search_batch))
        .route("/search/explain", post(search::search_explain))
        .route("/search/evidence", post(search::search_evidence))
        .route("/memory/is-novel", post(search::is_novel))
        .route("/memory/extract", post(extract::enqueue))
        .route("/memory/extract/{job_id}", get(extract::get_job))
        .route("/memory/extract/commit", post(extract::commit))
        .route("/extract/status", get(extract::status))
        .route("/extraction/profiles", get(extract::list_profiles))
        .route(
            "/extraction/profiles/{*prefix}",
            get(extract::get_profile)
                .put(extract::put_profile)
                .delete(extract::delete_profile),
        )
        .route("/maintenance/consolidate", post(maintenance::consolidate))
        .route("/maintenance/prune", post(maintenance::prune))
        .route(
            "/maintenance/enforce-policies",
            post(maintenance::enforce_policies),
        )
        .route("/maintenance/compact", post(maintenance::compact))
        .route("/maintenance/reembed", post(maintenance::reembed))
        .route(
            "/maintenance/embedder/reload",
            post(maintenance::embedder_reload),
        )
        .route("/index/build", post(maintenance::index_build))
        .route("/backups", get(backup::list_backups))
        .route("/backup", post(backup::create))
        .route("/restore", post(backup::restore))
        .route(
            "/snapshots",
            get(backup::list_snapshots).post(backup::create_snapshot),
        )
        .route("/snapshots/{name}/restore", post(backup::restore_snapshot))
        .route("/sync/status", get(sync::status))
        .route("/sync/upload", post(sync::no_query))
        .route("/sync/download", post(sync::with_confirm))
        .route("/sync/snapshots", get(sync::no_query))
        .route("/sync/restore/{backup_name}", post(sync::with_confirm))
        .route("/export", get(backup::export))
        .route("/import", post(backup::import));
    let protected = usage_routes::routes(protected);
    let protected =
        stubs(protected, false).route_layer(from_fn_with_state(state.clone(), auth::require_key));
    let public = stubs(
        Router::new()
            .route("/health", get(health))
            .route("/health/ready", get(health_ready))
            .route("/openapi.json", get(openapi)),
        true,
    );
    protected
        .merge(public)
        .fallback(shell::not_found)
        .method_not_allowed_fallback(shell::method_not_allowed)
        .layer(CatchPanicLayer::custom(shell::panic_reply))
        // Python reads bodies of any size (for example `/import`).
        .layer(DefaultBodyLimit::disable())
        .layer(from_fn_with_state(state.clone(), refuse_while_migrating))
        .layer(from_fn(shell::refuse_head))
        .layer(from_fn(shell::cors))
        .layer(from_fn_with_state(state.clone(), shell::record_metrics))
        .layer(from_fn_with_state(
            state.clone(),
            shell::client_and_access_log,
        ))
        .with_state(state)
}

async fn refuse_while_migrating(
    axum::extract::State(state): axum::extract::State<Arc<AppState>>,
    request: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    if state.migrating.load(Ordering::Acquire) && request.uri().path() != "/health" {
        return (
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail": "The server is migrating the data. Try again later."})),
        )
            .into_response();
    }
    next.run(request).await
}

/// The minimal body. For a caller that sends the `API_KEY` value (or when
/// `API_KEY` is empty), the body adds `total_memories`, `dimension` and
/// `model` of Python (when the store is open), then the extract queue depth
/// and the age of the oldest queued job. Only the env key counts, as in Python.
async fn health(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Json<Value> {
    let mut body = json!({"status": "ok", "service": "memories", "version": VERSION});
    let sent = headers
        .get("X-API-Key")
        .map(|v| v.as_bytes())
        .unwrap_or_default();
    if state.api_key.is_empty() || auth::ct_eq(sent, state.api_key.as_bytes()) {
        if let Some(engine) = state.engine.get() {
            body["total_memories"] = json!(engine.count());
            body["dimension"] = json!(engine.dim);
            body["model"] = json!(engine.embedder.config().model);
        }
        body.as_object_mut()
            .unwrap()
            .extend(extract::health_fields(&state).await);
    }
    Json(body)
}

/// shortcut: this server has no store yet, so the counts are 0. The store
/// task (P1-3, P1-4) gives the real counts and the 503 reply when they differ.
async fn health_ready() -> Json<Value> {
    Json(json!({"ready": true, "status": "ready", "qdrant_count": 0, "metadata_count": 0}))
}

async fn keys_me(Extension(auth): Extension<AuthContext>) -> Json<Value> {
    Json(auth.me())
}

/// The OpenAPI file of the Python server, with the version of this server.
static OPENAPI: LazyLock<Value> = LazyLock::new(|| {
    let mut v: Value = serde_json::from_str(include_str!("../openapi.json")).expect("openapi.json");
    v["info"]["version"] = json!(VERSION);
    v
});

async fn openapi() -> Json<Value> {
    Json(OPENAPI.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn served_routes_are_python_routes() {
        for r in SERVED {
            assert!(ROUTES.contains(r), "{r:?}");
        }
    }

    #[test]
    fn openapi_has_python_paths_and_rust_version() {
        assert_eq!(OPENAPI["info"]["version"], VERSION);
        assert!(OPENAPI["paths"]["/api/keys/me"]["get"].is_object());
    }
}
