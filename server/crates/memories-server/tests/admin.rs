//! `/api/keys` and `/audit` against the replies of the Python server v5.19.0.
//! The 422 bodies come from a probe of the Python request models.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use memories_server::{AppState, app, audit::Entry, auth::AuthContext};
use rusqlite::Connection;
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

fn state(audit: bool) -> (Arc<AppState>, std::path::PathBuf) {
    let mut cfg = common::config(ADMIN, 50);
    cfg.audit_log = audit;
    (common::state(&cfg), cfg.data_dir)
}

async fn new_key(st: &Arc<AppState>, body: Value) -> Value {
    let (s, v) = call(st, "POST", "/api/keys", ADMIN, Some(body)).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    v
}

#[tokio::test]
async fn key_lifecycle() {
    let (st, dir) = state(false);
    let ro = new_key(
        &st,
        json!({"name": "parity-ro", "role": "read-only", "prefixes": ["claude-code/"]}),
    )
    .await;
    let raw = ro["key"].as_str().unwrap();
    assert!(raw.starts_with("mem_") && raw.len() == 36);
    assert!(
        raw[4..]
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    );
    assert_eq!(ro["key_prefix"], &raw[..8]);
    assert_eq!(ro["principal_id"], "parity-ro");
    assert_eq!(ro["prefixes"], json!(["claude-code/"]));
    assert_eq!(ro["created_at"].as_str().unwrap().len(), 20);
    let id = ro["id"].as_str().unwrap().to_string();

    // The new key works at once; the list shows its use at once.
    let (s, me) = call(&st, "GET", "/api/keys/me", raw, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(me["id"], id.as_str());
    let (s, list) = call(&st, "GET", "/api/keys", ADMIN, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(list["count"], 1);
    let k = &list["keys"][0];
    assert_eq!(k["usage_count"], 1);
    assert!(k["last_used_at"].is_string());
    assert_eq!(k["revoked"], 0);
    assert!(k.get("key").is_none() && k.get("key_hash").is_none());
    let mut fields: Vec<_> = k.as_object().unwrap().keys().cloned().collect();
    fields.sort();
    assert_eq!(
        fields,
        [
            "created_at",
            "id",
            "key_prefix",
            "last_used_at",
            "name",
            "prefixes",
            "principal_id",
            "revoked",
            "role",
            "usage_count"
        ]
    );

    // A key that is not admin cannot manage keys.
    for (m, p) in [
        ("GET", "/api/keys".to_string()),
        ("DELETE", format!("/api/keys/{id}")),
    ] {
        let (s, v) = call(&st, m, &p, raw, None).await;
        assert_eq!(
            (s, v),
            (
                StatusCode::FORBIDDEN,
                json!({"detail": "Admin key required"})
            )
        );
    }

    let (s, v) = call(
        &st,
        "PATCH",
        &format!("/api/keys/{id}"),
        ADMIN,
        Some(json!({"name": "renamed", "prefixes": ["codex/", "wip/é"], "unknown": 1})),
    )
    .await;
    assert_eq!((s, v), (StatusCode::OK, json!({"success": true, "id": id})));
    let conn = Connection::open(dir.join("keys.db")).unwrap();
    let (name, prefixes): (String, String) = conn
        .query_row(
            "SELECT name, prefixes FROM api_keys WHERE id = ?1",
            [&id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap();
    assert_eq!(name, "renamed");
    // The text that Python json.dumps writes.
    assert_eq!(prefixes, r#"["codex/", "wip/\u00e9"]"#);

    let (s, v) = call(&st, "DELETE", &format!("/api/keys/{id}"), ADMIN, None).await;
    assert_eq!((s, v), (StatusCode::OK, json!({"id": id, "revoked": true})));
    let (s, _) = call(&st, "GET", "/api/keys/me", raw, None).await;
    assert_eq!(s, StatusCode::UNAUTHORIZED);
    let (s, v) = call(&st, "DELETE", &format!("/api/keys/{id}"), ADMIN, None).await;
    assert_eq!(
        (s, v),
        (
            StatusCode::NOT_FOUND,
            json!({"detail": format!("Key '{id}' is already revoked")})
        )
    );
    let (s, v) = call(
        &st,
        "PATCH",
        &format!("/api/keys/{id}"),
        ADMIN,
        Some(json!({})),
    )
    .await;
    assert_eq!(
        (s, v),
        (
            StatusCode::NOT_FOUND,
            json!({"detail": format!("Key '{id}' is revoked")})
        )
    );
    for m in ["PATCH", "DELETE"] {
        let body = (m == "PATCH").then(|| json!({}));
        let (s, v) = call(&st, m, "/api/keys/nope", ADMIN, body).await;
        assert_eq!(
            (s, v),
            (
                StatusCode::NOT_FOUND,
                json!({"detail": "Key 'nope' not found"})
            )
        );
    }
    let (_, list) = call(&st, "GET", "/api/keys", ADMIN, None).await;
    assert_eq!(list["keys"][0]["revoked"], 1);
    // Python v5.19.0 gave 422 here: its status test looked for "principal_id" in the message.
    let (s, v) = call(
        &st,
        "PATCH",
        "/api/keys/principal_id-x",
        ADMIN,
        Some(json!({})),
    )
    .await;
    assert_eq!(
        (s, v),
        (
            StatusCode::NOT_FOUND,
            json!({"detail": "Key 'principal_id-x' not found"})
        )
    );
}

#[tokio::test]
async fn create_rules() {
    let (st, _) = state(false);
    let k = new_key(
        &st,
        json!({"name": "Ops Team", "role": "admin", "prefixes": ["x/"]}),
    )
    .await;
    assert_eq!(k["prefixes"], json!([]));
    assert_eq!(k["principal_id"], Value::Null);
    let k = new_key(&st, json!({"name": "Ops Team", "principal_id": "ops", "role": "read-write", "prefixes": ["x/"]})).await;
    assert_eq!(k["principal_id"], "ops");
    let (s, v) = call(
        &st,
        "POST",
        "/api/keys",
        ADMIN,
        Some(json!({"name": "n", "role": "read-only"})),
    )
    .await;
    assert_eq!(
        (s, v),
        (
            StatusCode::BAD_REQUEST,
            json!({"detail": "Non-admin keys must have at least one prefix"})
        )
    );
}

#[tokio::test]
async fn body_errors_match_pydantic() {
    let (st, _) = state(false);
    let long = "A".repeat(201);
    let cases = [
        (
            json!({}),
            json!([
            {"type": "missing", "loc": ["body", "name"], "msg": "Field required", "input": {}},
            {"type": "missing", "loc": ["body", "role"], "msg": "Field required", "input": {}}]),
        ),
        (
            json!({"name": "", "role": "owner", "principal_id": long}),
            json!([
            {"type": "string_too_short", "loc": ["body", "name"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}},
            {"type": "string_too_long", "loc": ["body", "principal_id"], "msg": "String should have at most 200 characters", "input": long, "ctx": {"max_length": 200}},
            {"type": "string_pattern_mismatch", "loc": ["body", "role"], "msg": "String should match pattern '^(read-only|read-write|admin)$'", "input": "owner", "ctx": {"pattern": "^(read-only|read-write|admin)$"}}]),
        ),
        (
            json!({"name": 1, "role": null, "prefixes": null, "principal_id": null}),
            json!([
            {"type": "string_type", "loc": ["body", "name"], "msg": "Input should be a valid string", "input": 1},
            {"type": "string_type", "loc": ["body", "role"], "msg": "Input should be a valid string", "input": null},
            {"type": "list_type", "loc": ["body", "prefixes"], "msg": "Input should be a valid list", "input": null}]),
        ),
        (
            json!({"name": "n", "role": "admin", "prefixes": [1, "a", null]}),
            json!([
            {"type": "string_type", "loc": ["body", "prefixes", 0], "msg": "Input should be a valid string", "input": 1},
            {"type": "string_type", "loc": ["body", "prefixes", 2], "msg": "Input should be a valid string", "input": null}]),
        ),
        (
            json!({"name": "n", "role": "admin\n"}),
            json!([
            {"type": "string_pattern_mismatch", "loc": ["body", "role"], "msg": "String should match pattern '^(read-only|read-write|admin)$'", "input": "admin\n", "ctx": {"pattern": "^(read-only|read-write|admin)$"}}]),
        ),
        // `role` has a pattern and no length limit.
        (
            json!({"name": "n", "role": ""}),
            json!([
            {"type": "string_pattern_mismatch", "loc": ["body", "role"], "msg": "String should match pattern '^(read-only|read-write|admin)$'", "input": "", "ctx": {"pattern": "^(read-only|read-write|admin)$"}}]),
        ),
        (
            json!({"name": "n", "role": "read-only", "principal_id": "-bad"}),
            json!([
            {"type": "string_pattern_mismatch", "loc": ["body", "principal_id"], "msg": "String should match pattern '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$'", "input": "-bad", "ctx": {"pattern": "^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"}}]),
        ),
    ];
    for (body, detail) in cases {
        let (s, v) = call(&st, "POST", "/api/keys", ADMIN, Some(body.clone())).await;
        assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
        assert_eq!(v, json!({"detail": detail}), "{body}");
    }
    // 200 characters of a 2-byte character is not too long: the limit counts characters.
    new_key(&st, json!({"name": "é".repeat(200), "role": "admin"})).await;

    let (s, v) = call(
        &st,
        "PATCH",
        "/api/keys/x",
        ADMIN,
        Some(json!({"name": "", "prefixes": [2]})),
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        v,
        json!({"detail": [
        {"type": "string_too_short", "loc": ["body", "name"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}},
        {"type": "string_type", "loc": ["body", "prefixes", 0], "msg": "Input should be a valid string", "input": 2}]})
    );
    // PATCH fields may be null.
    let (s, _) = call(
        &st,
        "PATCH",
        "/api/keys/x",
        ADMIN,
        Some(json!({"name": null, "role": null, "principal_id": null, "prefixes": null})),
    )
    .await;
    assert_eq!(s, StatusCode::NOT_FOUND);

    // FastAPI checks the body before the handler checks the role.
    let ro = new_key(
        &st,
        json!({"name": "ro", "role": "read-only", "prefixes": ["a/"]}),
    )
    .await;
    let (s, _) = call(
        &st,
        "POST",
        "/api/keys",
        ro["key"].as_str().unwrap(),
        Some(json!({})),
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
}

fn ctx(key_id: Option<&str>) -> AuthContext {
    AuthContext {
        role: "admin".into(),
        prefixes: None,
        key_type: if key_id.is_some() { "managed" } else { "env" },
        key_id: key_id.map(Into::into),
        key_name: key_id.map(|_| "k".into()),
        principal_id: None,
    }
}

#[tokio::test]
async fn audit_off_is_empty() {
    let (st, _) = state(false);
    st.audit(Entry::from_request(&ctx(None), None, "memory.created"))
        .await;
    let (s, v) = call(&st, "GET", "/audit", ADMIN, None).await;
    assert_eq!(
        (s, v),
        (
            StatusCode::OK,
            json!({"entries": [], "count": 0, "total": 0})
        )
    );
    let (s, v) = call(&st, "POST", "/audit/purge", ADMIN, None).await;
    assert_eq!(
        (s, v),
        (StatusCode::OK, json!({"purged": 0, "retention_days": 90}))
    );
}

#[tokio::test]
async fn audit_rows_query_and_purge() {
    let (st, dir) = state(true);
    for i in 0..5 {
        let mut e = Entry::from_request(
            &ctx(if i % 2 == 0 { None } else { Some("kid") }),
            None,
            "memory.created",
        );
        e.resource_id = i.to_string();
        e.source_prefix = "codex/x".into();
        if i == 4 {
            e.action = "memory.deleted".into();
            e.metadata.insert("z".into(), json!(1));
            e.metadata.insert("a".into(), json!("é"));
        }
        st.audit(e).await;
    }
    let (s, v) = call(&st, "GET", "/audit?limit=2&offset=1", ADMIN, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["count"], 2);
    assert_eq!(v["total"], 5);
    let e = &v["entries"][0];
    assert_eq!(e["id"], 4);
    assert_eq!(e["key_id"], "kid");
    assert_eq!(e["key_name"], "k");
    assert_eq!(e["metadata"], json!({}));
    assert!(e.get("metadata_json").is_none());
    let mut fields: Vec<_> = e.as_object().unwrap().keys().cloned().collect();
    fields.sort();
    assert_eq!(
        fields,
        [
            "action",
            "id",
            "ip",
            "key_id",
            "key_name",
            "metadata",
            "resource_id",
            "source_prefix",
            "ts"
        ]
    );

    let (_, v) = call(&st, "GET", "/audit?action=memory.deleted", ADMIN, None).await;
    assert_eq!(v["total"], 1);
    assert_eq!(v["entries"][0]["metadata"], json!({"a": "é", "z": 1}));
    assert_eq!(v["entries"][0]["key_id"], "env");
    let (_, v) = call(&st, "GET", "/audit?key_id=kid&resource_id=3", ADMIN, None).await;
    assert_eq!(
        (v["count"].clone(), v["entries"][0]["id"].clone()),
        (json!(1), json!(4))
    );
    // An empty filter is no filter.
    let (_, v) = call(&st, "GET", "/audit?action=", ADMIN, None).await;
    assert_eq!(v["total"], 5);

    // Python writes json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
    let conn = Connection::open(dir.join("audit.db")).unwrap();
    let meta: String = conn
        .query_row(
            "SELECT metadata_json FROM audit_log WHERE id = 5",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(meta, r#"{"a":"é","z":1}"#);

    let (s, v) = call(&st, "GET", "/audit?limit=abc&offset=-1", ADMIN, None).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        v,
        json!({"detail": [
        {"type": "int_parsing", "loc": ["query", "limit"], "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "abc"},
        {"type": "greater_than_equal", "loc": ["query", "offset"], "msg": "Input should be greater than or equal to 0", "input": "-1", "ctx": {"ge": 0}}]})
    );
    let (s, v) = call(
        &st,
        "GET",
        "/audit?limit=99999999999999999999999",
        ADMIN,
        None,
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(v["detail"][0]["ctx"], json!({"le": 500}));
    let (s, v) = call(&st, "GET", "/audit?limit=1&limit=+3", ADMIN, None).await;
    assert_eq!((s, v["count"].clone()), (StatusCode::OK, json!(3)));
    let (s, v) = call(&st, "POST", "/audit/purge?retention_days=366", ADMIN, None).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        v["detail"][0]["msg"],
        "Input should be less than or equal to 365"
    );

    // A row older than the period goes; the new rows stay.
    conn.execute(
        "UPDATE audit_log SET ts = '2020-01-01T00:00:00Z' WHERE id = 1",
        [],
    )
    .unwrap();
    let (s, v) = call(&st, "POST", "/audit/purge?retention_days=30", ADMIN, None).await;
    assert_eq!(
        (s, v),
        (StatusCode::OK, json!({"purged": 1, "retention_days": 30}))
    );
    let (_, v) = call(&st, "GET", "/audit", ADMIN, None).await;
    assert_eq!(v["total"], 4);
}
