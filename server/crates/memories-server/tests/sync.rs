//! The five `/sync` routes of a server with no cloud sync configuration, with
//! no engine. Each expected reply is the reply of the Python server v5.19.0.

mod common;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use memories_server::app;
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";

async fn send(
    st: &std::sync::Arc<memories_server::AppState>,
    method: &str,
    path: &str,
    key: &str,
) -> (StatusCode, Value) {
    let req = Request::builder()
        .method(method)
        .uri(path)
        .header("X-API-Key", key)
        .body(Body::empty())
        .unwrap();
    let res = app(st.clone()).oneshot(req).await.unwrap();
    let status = res.status();
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn not_configured() -> (StatusCode, Value) {
    (
        StatusCode::BAD_REQUEST,
        json!({"detail": "Cloud sync not configured"}),
    )
}

fn bad_bool(input: &str) -> (StatusCode, Value) {
    (
        StatusCode::UNPROCESSABLE_ENTITY,
        json!({"detail": [{"type": "bool_parsing", "loc": ["query", "confirm"],
               "msg": "Input should be a valid boolean, unable to interpret input", "input": input}]}),
    )
}

#[tokio::test]
async fn admin_gets_the_replies_of_a_server_with_no_sync() {
    let st = common::state(&common::config(ADMIN, 50));
    assert_eq!(
        send(&st, "GET", "/sync/status", ADMIN).await,
        (
            StatusCode::OK,
            json!({"enabled": false, "message": "Cloud sync not configured"})
        )
    );
    for (m, p) in [
        ("POST", "/sync/upload"),
        ("GET", "/sync/snapshots"),
        ("POST", "/sync/download"),
        ("POST", "/sync/download?confirm=true&backup_name=x"),
        ("POST", "/sync/restore/b1"),
        ("POST", "/sync/restore/b1?confirm=yes"),
    ] {
        assert_eq!(send(&st, m, p, ADMIN).await, not_configured(), "{m} {p}");
    }
    assert_eq!(
        send(&st, "POST", "/sync/download?confirm=maybe", ADMIN).await,
        bad_bool("maybe")
    );
    // Starlette takes the last value of a repeated query parameter.
    assert_eq!(
        send(&st, "POST", "/sync/restore/b1?confirm=1&confirm=x", ADMIN).await,
        bad_bool("x")
    );
}

#[tokio::test]
async fn other_keys_get_403_after_the_query_check() {
    let st = common::state(&common::config(ADMIN, 50));
    let ro = st
        .keys
        .create("ro", "read-only", vec!["a/".into()], None)
        .unwrap()
        .key;
    let rw = st
        .keys
        .create("rw", "read-write", vec!["a/".into()], None)
        .unwrap()
        .key;
    let forbidden = (
        StatusCode::FORBIDDEN,
        json!({"detail": "Admin key required"}),
    );
    for key in [&ro, &rw] {
        for (m, p) in [
            ("GET", "/sync/status"),
            ("POST", "/sync/upload"),
            ("GET", "/sync/snapshots"),
            ("POST", "/sync/download?confirm=true"),
            ("POST", "/sync/restore/b1"),
        ] {
            assert_eq!(send(&st, m, p, key).await, forbidden, "{m} {p}");
        }
        assert_eq!(
            send(&st, "POST", "/sync/download?confirm=nope", key).await,
            bad_bool("nope")
        );
    }
    assert_eq!(
        send(&st, "GET", "/sync/status", "wrong").await.0,
        StatusCode::UNAUTHORIZED
    );
}
