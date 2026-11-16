//! The maintenance routes with no engine (task P5-3): request checks, the
//! admin check and the replies before the store. The 422 replies are the
//! replies of the Python server v5.19.0.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use memories_server::{AppState, app};
use rusqlite::Connection;
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";
const RO: &str = "mem_0123456789abcdef0123456789abcdef";

async fn post(
    st: &Arc<AppState>,
    path: &str,
    key: &str,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut b = Request::builder()
        .method("POST")
        .uri(path)
        .header("X-API-Key", key);
    let body = match body {
        Some(v) => {
            b = b.header(header::CONTENT_TYPE, "application/json");
            Body::from(v.to_string())
        }
        None => Body::empty(),
    };
    let res = app(st.clone())
        .oneshot(b.body(body).unwrap())
        .await
        .unwrap();
    let status = res.status();
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn state() -> Arc<AppState> {
    let cfg = common::config(ADMIN, 50);
    let st = common::state(&cfg);
    let conn = Connection::open(cfg.data_dir.join("keys.db")).unwrap();
    conn.execute(
        "INSERT INTO api_keys (id, name, principal_id, key_hash, key_prefix, role, prefixes, created_at, revoked)
         VALUES ('id-ro', 'ro', 'ro', ?1, ?2, 'read-write', '[\"codex/\"]', '2026-11-16T00:00:00Z', 0)",
        rusqlite::params![memories_server::keys::hash_key(RO), &RO[..8]],
    )
    .unwrap();
    st
}

#[tokio::test]
async fn query_and_body_errors_are_the_python_replies() {
    let st = state();
    let (s, b) = post(&st, "/maintenance/prune?dry_run=maybe", ADMIN, None).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        b["detail"],
        json!([{"type": "bool_parsing", "loc": ["query", "dry_run"],
                "msg": "Input should be a valid boolean, unable to interpret input", "input": "maybe"}])
    );
    let (s, b) = post(
        &st,
        "/maintenance/compact",
        ADMIN,
        Some(json!({"threshold": 0.3})),
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(b["detail"][0]["type"], "greater_than_equal");
    assert_eq!(b["detail"][0]["loc"], json!(["body", "threshold"]));
    // A body model with no body: "Field required" (as FastAPI).
    let (s, b) = post(&st, "/index/build", ADMIN, None).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(b["detail"][0]["type"], "missing");
    let (s, b) = post(&st, "/index/build", ADMIN, Some(json!({"sources": "x"}))).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(b["detail"][0]["loc"], json!(["body", "sources"]));
}

#[tokio::test]
async fn each_route_needs_an_admin_key() {
    let st = state();
    let routes = [
        ("/maintenance/consolidate", None),
        ("/maintenance/prune", None),
        ("/maintenance/enforce-policies", None),
        ("/maintenance/compact", Some(json!({}))),
        ("/maintenance/reembed", Some(json!({}))),
        ("/maintenance/embedder/reload", None),
        ("/index/build", Some(json!({}))),
    ];
    for (path, body) in routes {
        let (s, b) = post(&st, path, RO, body).await;
        assert_eq!(s, StatusCode::FORBIDDEN, "{path}");
        assert_eq!(b, json!({"detail": "Admin key required"}), "{path}");
    }
}

#[tokio::test]
async fn consolidate_needs_a_provider_and_the_others_need_the_store() {
    let st = state();
    let (s, b) = post(&st, "/maintenance/consolidate?dry_run=true", ADMIN, None).await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        b,
        json!({"detail": "No LLM provider configured for consolidation"})
    );
    for (path, body) in [
        ("/maintenance/prune", None),
        ("/maintenance/enforce-policies", None),
        ("/maintenance/compact", Some(json!({}))),
        ("/maintenance/reembed", Some(json!({}))),
        ("/maintenance/embedder/reload", None),
        ("/index/build", Some(json!({}))),
    ] {
        let (s, _) = post(&st, path, ADMIN, body).await;
        assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE, "{path}");
    }
}
