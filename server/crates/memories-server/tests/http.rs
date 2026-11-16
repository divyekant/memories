//! The HTTP shell and auth against the replies of the Python server v5.19.0.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use memories_server::{AppState, app, keys::KeyStore};
use rusqlite::Connection;
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";

struct Reply {
    status: StatusCode,
    headers: axum::http::HeaderMap,
    body: Vec<u8>,
}

impl Reply {
    fn json(&self) -> Value {
        assert_eq!(self.headers[header::CONTENT_TYPE], "application/json");
        serde_json::from_slice(&self.body).unwrap()
    }
}

async fn send(state: &Arc<AppState>, req: Request<Body>) -> Reply {
    let res = app(state.clone()).oneshot(req).await.unwrap();
    let status = res.status();
    let headers = res.headers().clone();
    let body = to_bytes(res.into_body(), usize::MAX)
        .await
        .unwrap()
        .to_vec();
    Reply {
        status,
        headers,
        body,
    }
}

fn get(path: &str, key: Option<&str>) -> Request<Body> {
    let mut b = Request::get(path).header(header::HOST, "testserver");
    if let Some(k) = key {
        b = b.header("X-API-Key", k);
    }
    b.body(Body::empty()).unwrap()
}

fn admin_state() -> Arc<AppState> {
    common::state(&common::config(ADMIN, 50))
}

/// Adds a managed key row as the Python `KeyStore.create_key` writes it.
fn add_key(
    state_dir: &std::path::Path,
    raw: &str,
    name: &str,
    role: &str,
    prefixes: &str,
    revoked: bool,
) {
    let conn = Connection::open(state_dir.join("keys.db")).unwrap();
    conn.execute(
        "INSERT INTO api_keys (id, name, principal_id, key_hash, key_prefix, role, prefixes, created_at, revoked)
         VALUES (?1, ?2, ?2, ?3, ?4, ?5, ?6, '2026-11-16T00:00:00Z', ?7)",
        rusqlite::params![
            format!("id-{name}"),
            name,
            memories_server::keys::hash_key(raw),
            &raw[..8],
            role,
            prefixes,
            revoked as i64
        ],
    )
    .unwrap();
}

#[tokio::test]
async fn health_needs_no_key() {
    let st = admin_state();
    for key in [None, Some(""), Some("wrong")] {
        let r = send(&st, get("/health", key)).await;
        assert_eq!(r.status, StatusCode::OK);
        assert_eq!(
            r.json(),
            json!({"status": "ok", "service": "memories", "version": env!("CARGO_PKG_VERSION")})
        );
    }
    let r = send(&st, get("/health/ready", None)).await;
    assert_eq!(
        r.json(),
        json!({"metadata_count": 0, "qdrant_count": 0, "ready": true, "status": "ready"})
    );
}

#[tokio::test]
async fn wrong_missing_and_empty_key_are_401() {
    let st = admin_state();
    for key in [Some("wrong-key"), None, Some("")] {
        let r = send(&st, get("/stats", key)).await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED, "{key:?}");
        assert_eq!(r.json(), json!({"detail": "Invalid or missing API key"}));
    }
}

#[tokio::test]
async fn keys_me_for_env_managed_and_revoked_keys() {
    let cfg = common::config(ADMIN, 50);
    let st = common::state(&cfg);
    let ro = "mem_0123456789abcdef0123456789abcdef";
    let gone = "mem_fedcba9876543210fedcba9876543210";
    add_key(
        &cfg.data_dir,
        ro,
        "parity-ro",
        "read-only",
        r#"["claude-code/"]"#,
        false,
    );
    add_key(
        &cfg.data_dir,
        gone,
        "parity-rw",
        "read-write",
        r#"["codex/"]"#,
        true,
    );

    let r = send(&st, get("/api/keys/me", Some(ADMIN))).await;
    assert_eq!(r.json(), json!({"role": "admin", "type": "env"}));
    let r = send(&st, get("/api/keys/me", Some(ro))).await;
    assert_eq!(
        r.json(),
        json!({"id": "id-parity-ro", "name": "parity-ro", "prefixes": ["claude-code/"],
               "principal_id": "parity-ro", "role": "read-only", "type": "managed"})
    );
    let r = send(&st, get("/api/keys/me", Some(gone))).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);

    // The use goes to the database only at the flush.
    let used = |c: &Connection| -> (i64, Option<String>) {
        c.query_row(
            "SELECT usage_count, last_used_at FROM api_keys WHERE name = 'parity-ro'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap()
    };
    let conn = Connection::open(cfg.data_dir.join("keys.db")).unwrap();
    assert_eq!(used(&conn), (0, None));
    send(&st, get("/api/keys/me", Some(ro))).await;
    assert_eq!(st.keys.flush_usage().unwrap(), 1);
    let (count, last) = used(&conn);
    assert_eq!(count, 2);
    assert_eq!(last.unwrap().len(), "2026-11-16T00:00:00Z".len());
    assert_eq!(st.keys.flush_usage().unwrap(), 0);
}

#[tokio::test]
async fn no_api_key_means_no_key_is_admin_but_a_wrong_key_is_401() {
    let st = common::state(&common::config("", 50));
    let r = send(&st, get("/api/keys/me", None)).await;
    assert_eq!(r.json(), json!({"role": "admin", "type": "none"}));
    let r = send(&st, get("/api/keys/me", Some(""))).await;
    assert_eq!(r.json(), json!({"role": "admin", "type": "none"}));
    let r = send(&st, get("/api/keys/me", Some("wrong"))).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn failed_attempts_limit_blocks_even_a_good_key() {
    let st = common::state(&common::config(ADMIN, 3));
    for _ in 0..3 {
        assert_eq!(
            send(&st, get("/stats", Some("bad"))).await.status,
            StatusCode::UNAUTHORIZED
        );
    }
    let r = send(&st, get("/api/keys/me", Some(ADMIN))).await;
    assert_eq!(r.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(
        r.json(),
        json!({"detail": "Too many failed authentication attempts"})
    );
    // Health has no key check, so no limit.
    assert_eq!(send(&st, get("/health", None)).await.status, StatusCode::OK);
}

#[tokio::test]
async fn not_found_method_not_allowed_and_not_ported() {
    let st = admin_state();
    let r = send(&st, get("/nope", Some("wrong"))).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    assert_eq!(r.json(), json!({"detail": "Not Found"}));

    for key in [ADMIN, "wrong"] {
        let req = Request::put("/search")
            .header("X-API-Key", key)
            .body(Body::empty())
            .unwrap();
        let r = send(&st, req).await;
        assert_eq!(r.status, StatusCode::METHOD_NOT_ALLOWED, "{key}");
        assert_eq!(r.json(), json!({"detail": "Method Not Allowed"}));
        assert_eq!(r.headers[header::ALLOW], "POST");
    }

    let r = send(&st, get("/stats", Some(ADMIN))).await;
    assert_eq!(r.status, StatusCode::NOT_IMPLEMENTED);
    assert_eq!(
        r.json(),
        json!({"detail": "Not implemented in the Rust server yet"})
    );
}

#[tokio::test]
async fn trailing_slash_redirects_with_307() {
    let st = admin_state();
    for (path, location) in [
        ("/health/", "http://testserver/health"),
        ("/stats/?a=1", "http://testserver/stats?a=1"),
        ("/memory/12/", "http://testserver/memory/12"),
    ] {
        let r = send(&st, get(path, Some("wrong"))).await;
        assert_eq!(r.status, StatusCode::TEMPORARY_REDIRECT, "{path}");
        assert_eq!(r.headers[header::LOCATION], location);
        assert!(r.body.is_empty());
    }
    assert_eq!(
        send(&st, get("/nope/", None)).await.status,
        StatusCode::NOT_FOUND
    );
}

#[tokio::test]
async fn cors_follows_starlette() {
    let st = admin_state();
    let pre = |origin: &str, method: &str| {
        Request::options("/search")
            .header("Origin", origin)
            .header("Access-Control-Request-Method", method)
            .header("Access-Control-Request-Headers", "x-api-key,content-type")
            .body(Body::empty())
            .unwrap()
    };
    let r = send(&st, pre("http://localhost:8900", "POST")).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.body, b"OK");
    assert_eq!(
        r.headers["access-control-allow-origin"],
        "http://localhost:8900"
    );
    assert_eq!(
        r.headers["access-control-allow-methods"],
        "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"
    );
    assert_eq!(
        r.headers["access-control-allow-headers"],
        "x-api-key,content-type"
    );
    assert_eq!(r.headers["access-control-max-age"], "600");
    assert_eq!(r.headers["vary"], "Origin");

    let r = send(&st, pre("http://evil.com", "POST")).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    assert_eq!(r.body, b"Disallowed CORS origin");
    assert!(!r.headers.contains_key("access-control-allow-origin"));
    let r = send(&st, pre("http://127.0.0.1:8000", "FOO")).await;
    assert_eq!(r.body, b"Disallowed CORS method");

    let simple = |origin: &str| {
        Request::get("/api/keys/me")
            .header("Origin", origin)
            .header("X-API-Key", ADMIN)
            .body(Body::empty())
            .unwrap()
    };
    let r = send(&st, simple("http://127.0.0.1:8900")).await;
    assert_eq!(
        r.headers["access-control-allow-origin"],
        "http://127.0.0.1:8900"
    );
    assert_eq!(r.headers["vary"], "Origin");
    let r = send(&st, simple("http://evil.com")).await;
    assert_eq!(r.status, StatusCode::OK);
    assert!(!r.headers.contains_key("access-control-allow-origin"));
}

#[tokio::test]
async fn route_metrics_count_each_request() {
    let st = admin_state();
    send(&st, get("/health", None)).await;
    send(&st, get("/memory/42", Some("wrong"))).await;
    let s = st.metrics.snapshot();
    assert_eq!(s["routes"]["GET /health"]["count"], 1);
    assert_eq!(s["routes"]["GET /memory/{id}"]["error_count"], 1);
    assert_eq!(s["requests"]["total_count"], 2);
    assert_eq!(s["requests"]["active_http_requests"], 0);
}

#[tokio::test]
async fn openapi_is_public_and_has_the_health_version() {
    let st = admin_state();
    let api = send(&st, get("/openapi.json", None)).await.json();
    let health = send(&st, get("/health", None)).await.json();
    assert_eq!(api["info"]["version"], health["version"]);
    assert_eq!(api["info"]["title"], "Memories API");
}

/// A `keys.db` from before principals (no `principal_id` column) gets the
/// Python migration: a valid slug name becomes the principal.
#[tokio::test]
async fn old_keys_db_is_migrated_like_python() {
    let dir = common::temp_dir();
    let conn = Connection::open(dir.join("keys.db")).unwrap();
    conn.execute_batch(
        "CREATE TABLE api_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL, role TEXT NOT NULL, prefixes TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL, last_used_at TEXT, usage_count INTEGER DEFAULT 0, revoked INTEGER DEFAULT 0);
         INSERT INTO api_keys VALUES ('a', 'good-name', 'h1', 'mem_0000', 'read-only', '[\"x/\"]', 't', NULL, 0, 0);
         INSERT INTO api_keys VALUES ('b', 'Bad Name', 'h2', 'mem_0000', 'read-only', '[\"x/\"]', 't', NULL, 0, 0);",
    )
    .unwrap();
    drop(conn);
    KeyStore::open(&dir.join("keys.db")).unwrap();
    let conn = Connection::open(dir.join("keys.db")).unwrap();
    let p = |id: &str| -> Option<String> {
        conn.query_row(
            "SELECT principal_id FROM api_keys WHERE id = ?1",
            [id],
            |r| r.get(0),
        )
        .unwrap()
    };
    assert_eq!(p("a").as_deref(), Some("good-name"));
    assert_eq!(p("b"), None);

    // A stored principal that is not a valid slug is cleared at each start.
    conn.execute(
        "UPDATE api_keys SET principal_id = 'Not Valid' WHERE id = 'a'",
        [],
    )
    .unwrap();
    KeyStore::open(&dir.join("keys.db")).unwrap();
    assert_eq!(p("a"), None);
}
