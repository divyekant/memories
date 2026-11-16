//! HTTP routes, auth, middleware and the `memories-server` binary.

pub mod auth;
pub mod error;
pub mod healthcheck;
pub mod keys;
pub mod shell;

use std::path::PathBuf;
use std::sync::{Arc, LazyLock};

use axum::{
    Extension, Json, Router,
    extract::DefaultBodyLimit,
    middleware::from_fn,
    middleware::from_fn_with_state,
    routing::{MethodFilter, get, on},
};
use memories_types::config;
use serde_json::{Value, json};
use tower_http::catch_panic::CatchPanicLayer;

use crate::{
    auth::{AuthContext, Failures},
    keys::KeyStore,
    shell::{Metrics, ROUTES, TrustedProxies},
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
        })
    }
}

pub struct AppState {
    pub api_key: String,
    pub auth_rate_limit: i64,
    pub keys: KeyStore,
    pub failures: Failures,
    pub metrics: Metrics,
    pub trusted: TrustedProxies,
}

impl AppState {
    pub fn new(cfg: &Config, keys: KeyStore) -> Arc<Self> {
        Arc::new(Self {
            api_key: cfg.api_key.clone(),
            auth_rate_limit: cfg.auth_rate_limit,
            keys,
            failures: Failures::default(),
            metrics: Metrics::new(cfg.metrics_latency_samples),
            trusted: TrustedProxies::parse(&cfg.forwarded_allow_ips),
        })
    }
}

/// The routes that this server serves. Each other route in [`ROUTES`] answers 501.
const SERVED: &[(&str, &str)] = &[
    ("GET", "/openapi.json"),
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("GET", "/api/keys/me"),
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
    let protected = stubs(Router::new().route("/api/keys/me", get(keys_me)), false)
        .route_layer(from_fn_with_state(state.clone(), auth::require_key));
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
        .layer(from_fn(shell::cors))
        .layer(from_fn_with_state(state.clone(), shell::record_metrics))
        .layer(from_fn_with_state(
            state.clone(),
            shell::client_and_access_log,
        ))
        .with_state(state)
}

/// The minimal body. The Python server adds index stats when the caller sends
/// the `API_KEY` value (or when `API_KEY` is empty); the engine task adds them.
async fn health() -> Json<Value> {
    Json(json!({"status": "ok", "service": "memories", "version": VERSION}))
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
