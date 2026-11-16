//! Search routes with no engine: the checks that run before the search.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use memories_server::{AppState, app};
use rusqlite::Connection;
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";
const RO: &str = "mem_0123456789abcdef0123456789abcdef";

async fn post(state: &Arc<AppState>, path: &str, key: &str, body: Value) -> (StatusCode, Value) {
    let req = Request::post(path)
        .header("X-API-Key", key)
        .header("Content-Type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    let res = app(state.clone()).oneshot(req).await.unwrap();
    let status = res.status();
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn state_with_ro_key() -> Arc<AppState> {
    let cfg = common::config(ADMIN, 50);
    let st = common::state(&cfg);
    let conn = Connection::open(cfg.data_dir.join("keys.db")).unwrap();
    conn.execute(
        "INSERT INTO api_keys (id, name, principal_id, key_hash, key_prefix, role, prefixes, created_at, revoked)
         VALUES ('id-ro', 'ro', 'ro', ?1, ?2, 'read-only', '[\"claude-code/\"]', '2026-11-16T00:00:00Z', 0)",
        rusqlite::params![memories_server::keys::hash_key(RO), &RO[..8]],
    )
    .unwrap();
    st
}

#[tokio::test]
async fn scope_errors_come_before_the_engine() {
    let st = state_with_ro_key();
    let both = json!({"query": "q", "source_prefix": "a/", "source_prefixes": ["b/"]});
    for path in ["/search", "/search/evidence", "/search/explain"] {
        let (s, b) = post(&st, path, ADMIN, both.clone()).await;
        assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY, "{path}");
        assert_eq!(
            b,
            json!({"detail": "source_prefix and source_prefixes are mutually exclusive"})
        );
    }
    // An empty source_prefix is falsy in Python: no conflict.
    let (s, _) = post(
        &st,
        "/search",
        ADMIN,
        json!({"query": "q", "source_prefix": "", "source_prefixes": ["b/"]}),
    )
    .await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);

    let (s, b) = post(
        &st,
        "/search",
        ADMIN,
        json!({"query": "q", "source_prefixes": ["ok", ""]}),
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        b["detail"],
        "source_prefixes must contain non-empty paths up to 500 characters"
    );

    let (s, b) = post(
        &st,
        "/search",
        RO,
        json!({"query": "q", "source_prefixes": ["claude-code/x", "codex/"]}),
    )
    .await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    assert_eq!(
        b,
        json!({"detail": "Key does not have read access to source: codex/"})
    );

    // A batch checks each item before any search runs.
    let batch = json!({"queries": [{"query": "a"}, {"query": "b", "source_prefixes": ["codex/"]}]});
    let (s, b) = post(&st, "/search/batch", RO, batch).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    assert_eq!(
        b["detail"],
        "Key does not have read access to source: codex/"
    );
}

#[tokio::test]
async fn explain_needs_an_admin_key_and_routes_need_the_engine() {
    let st = state_with_ro_key();
    let (s, b) = post(&st, "/search/explain", RO, json!({"query": "q"})).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    assert_eq!(b, json!({"detail": "Admin key required"}));
    for (path, body) in [
        ("/search", json!({"query": "q"})),
        ("/search/evidence", json!({"query": "q"})),
        ("/search/explain", json!({"query": "q"})),
        ("/search/batch", json!({"queries": [{"query": "q"}]})),
        ("/memory/is-novel", json!({"text": "t"})),
    ] {
        let (s, b) = post(&st, path, ADMIN, body).await;
        assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE, "{path}");
        assert!(b["detail"].as_str().unwrap().contains("QDRANT_URL"));
    }
}

#[tokio::test]
async fn intent_error_is_a_plain_500() {
    let st = state_with_ro_key();
    let req = Request::post("/search")
        .header("X-API-Key", ADMIN)
        .body(Body::from(
            json!({"query": "what happened 1000000000 days ago"}).to_string(),
        ))
        .unwrap();
    let res = app(st).oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    assert_eq!(&bytes[..], b"Internal Server Error");
}
