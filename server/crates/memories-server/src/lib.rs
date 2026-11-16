//! HTTP routes, auth, middleware and the `memories-server` binary.

pub mod healthcheck;

use axum::{Json, Router, routing::get};
use serde_json::{Value, json};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn app() -> Router {
    Router::new().route("/health", get(health))
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok", "service": "memories", "version": VERSION}))
}
