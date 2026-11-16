//! The write routes without a store: the body checks, the key checks and the
//! 400 reply, against the replies of the Python server v5.19.0. The flow with
//! a store is in `memories-engine/tests/writes.rs` and the parity script.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use memories_server::{AppState, app};
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";

async fn call(
    st: &Arc<AppState>,
    method: &str,
    path: &str,
    key: &str,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut b = Request::builder()
        .method(method)
        .uri(path)
        .header(header::HOST, "testserver")
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
    (status, serde_json::from_slice(&bytes).unwrap())
}

fn state() -> Arc<AppState> {
    common::state(&common::config(ADMIN, 50))
}

async fn key(st: &Arc<AppState>, role: &str, prefixes: Value) -> String {
    let body = json!({"name": format!("k-{role}"), "role": role, "prefixes": prefixes});
    let (s, v) = call(st, "POST", "/api/keys", ADMIN, Some(body)).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    v["key"].as_str().unwrap().to_owned()
}

fn detail(v: Value) -> Value {
    v["detail"].clone()
}

#[tokio::test]
async fn body_errors_match_pydantic() {
    let st = state();
    let cases = [
        // Golden case add-422.
        (
            "POST",
            "/memory/add",
            json!({"source": "x", "text": ""}),
            json!([{"ctx": {"min_length": 1}, "input": "", "loc": ["body", "text"], "msg": "String should have at least 1 character", "type": "string_too_short"}]),
        ),
        (
            "POST",
            "/memory/add-batch",
            json!({"memories": [5, {"text": "", "source": "s"}]}),
            json!([
                {"type": "model_attributes_type", "loc": ["body", "memories", 0], "msg": "Input should be a valid dictionary or object to extract fields from", "input": 5},
                {"type": "string_too_short", "loc": ["body", "memories", 1, "text"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}}
            ]),
        ),
        (
            "POST",
            "/memory/delete-batch",
            json!({"ids": ["x", 1.5, true]}),
            json!([
                {"type": "int_parsing", "loc": ["body", "ids", 0], "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "x"},
                {"type": "int_from_float", "loc": ["body", "ids", 1], "msg": "Input should be a valid integer, got a number with a fractional part", "input": 1.5}
            ]),
        ),
        (
            "POST",
            "/memory/upsert",
            json!({"text": "t", "source": "s"}),
            json!([{"type": "missing", "loc": ["body", "key"], "msg": "Field required", "input": {"text": "t", "source": "s"}}]),
        ),
        (
            "POST",
            "/memory/upsert-batch",
            json!({"memories": []}),
            json!([{"type": "too_short", "loc": ["body", "memories"], "msg": "List should have at least 1 item after validation, not 0", "input": [], "ctx": {"field_type": "List", "min_length": 1, "actual_length": 0}}]),
        ),
        (
            "PATCH",
            "/memory/1",
            json!({"text": "", "pinned": null, "metadata_patch": 5}),
            json!([
                {"type": "string_too_short", "loc": ["body", "text"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}},
                {"type": "dict_type", "loc": ["body", "metadata_patch"], "msg": "Input should be a valid dictionary", "input": 5}
            ]),
        ),
        (
            "PATCH",
            "/memory/abc",
            json!({"pinned": "x"}),
            json!([
                {"type": "int_parsing", "loc": ["path", "memory_id"], "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "abc"},
                {"type": "bool_parsing", "loc": ["body", "pinned"], "msg": "Input should be a valid boolean, unable to interpret input", "input": "x"}
            ]),
        ),
    ];
    for (method, path, body, want) in cases {
        let (s, v) = call(&st, method, path, ADMIN, Some(body)).await;
        assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY, "{method} {path}");
        assert_eq!(detail(v), want, "{method} {path}");
    }
    let (s, v) = call(&st, "DELETE", "/memory/abc?force=maybe", ADMIN, None).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        detail(v),
        json!([
            {"type": "int_parsing", "loc": ["path", "memory_id"], "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "abc"},
            {"type": "bool_parsing", "loc": ["query", "force"], "msg": "Input should be a valid boolean, unable to interpret input", "input": "maybe"}
        ])
    );
}

#[tokio::test]
async fn key_checks_and_the_empty_patch() {
    let st = state();
    let ro = key(&st, "read-only", json!(["claude-code/"])).await;
    let rw = key(&st, "read-write", json!(["codex/"])).await;
    let denied = json!("Key does not have write access to source: claude-code/voltis");
    // Golden cases add-ro-403 and add-rw-out-of-scope.
    for k in [&ro, &rw] {
        let body = json!({"source": "claude-code/voltis", "text": "x"});
        let (s, v) = call(&st, "POST", "/memory/add", k, Some(body)).await;
        assert_eq!((s, detail(v)), (StatusCode::FORBIDDEN, denied.clone()));
    }
    let body =
        json!({"memories": [{"source": "codex/a", "text": "x"}, {"source": "wip/b", "text": "y"}]});
    let (s, v) = call(&st, "POST", "/memory/add-batch", &rw, Some(body)).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    assert_eq!(detail(v), "Key does not have write access to source: wip/b");
    let (s, v) = call(&st, "DELETE", "/memory/1", &ro, None).await;
    assert_eq!(
        (s, detail(v)),
        (
            StatusCode::FORBIDDEN,
            json!("Read-only keys cannot delete memories")
        )
    );
    // Golden case patch-empty-400: before the store is read.
    for body in [json!({}), json!({"metadata_patch": {}, "text": null})] {
        let (s, v) = call(&st, "PATCH", "/memory/1", ADMIN, Some(body)).await;
        assert_eq!(
            (s, detail(v)),
            (
                StatusCode::BAD_REQUEST,
                json!("At least one field must be provided")
            )
        );
    }
    // With no store, a valid write answers 503.
    let (s, _) = call(
        &st,
        "POST",
        "/memory/add",
        ADMIN,
        Some(json!({"source": "a", "text": "b"})),
    )
    .await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);
    let (s, _) = call(&st, "GET", "/memory/1", ADMIN, None).await;
    assert_eq!(
        s,
        StatusCode::SERVICE_UNAVAILABLE,
        "GET on the same path is the read route of P2-3"
    );
}
