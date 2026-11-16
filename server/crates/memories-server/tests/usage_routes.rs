//! The usage, metrics and feedback routes with no engine. The 422 replies
//! are the replies of the Python server v5.19.0.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use memories_server::{AppState, app, usage::Row};
use rusqlite::Connection;
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";
const RO: &str = "mem_0123456789abcdef0123456789abcdef";

async fn send(
    st: &Arc<AppState>,
    method: &str,
    path: &str,
    key: &str,
    body: Option<Value>,
) -> (StatusCode, String, String) {
    let mut b = Request::builder()
        .method(method)
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
    let ct = res
        .headers()
        .get(header::CONTENT_TYPE)
        .map(|v| v.to_str().unwrap().to_string())
        .unwrap_or_default();
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    (status, ct, String::from_utf8_lossy(&bytes).into_owned())
}

async fn get(st: &Arc<AppState>, path: &str, key: &str) -> (StatusCode, Value) {
    let (s, _, b) = send(st, "GET", path, key, None).await;
    (s, serde_json::from_str(&b).unwrap_or(Value::Null))
}

fn state(usage: bool, audit: bool) -> Arc<AppState> {
    let mut cfg = common::config(ADMIN, 50);
    cfg.usage_tracking = usage;
    cfg.audit_log = audit;
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
async fn query_errors_are_the_python_replies() {
    let st = state(true, false);
    let long = "x".repeat(201);
    let cases = [
        (
            "/usage?period=daily".to_string(),
            json!([{"type":"string_pattern_mismatch","loc":["query","period"],"msg":"String should match pattern '^(today|7d|30d|all)$'","input":"daily","ctx":{"pattern":"^(today|7d|30d|all)$"}}]),
        ),
        (
            format!("/usage?session_id={long}"),
            json!([{"type":"string_too_long","loc":["query","session_id"],"msg":"String should have at most 200 characters","input":long,"ctx":{"max_length":200}}]),
        ),
        (
            "/search/feedback/history".into(),
            json!([{"type":"missing","loc":["query","memory_id"],"msg":"Field required","input":null}]),
        ),
        (
            "/search/feedback/history?memory_id=abc&limit=0".into(),
            json!([{"type":"int_parsing","loc":["query","memory_id"],"msg":"Input should be a valid integer, unable to parse string as an integer","input":"abc"},{"type":"greater_than_equal","loc":["query","limit"],"msg":"Input should be greater than or equal to 1","input":"0","ctx":{"ge":1}}]),
        ),
        (
            "/metrics/failures?type=x&limit=101".into(),
            json!([{"type":"string_pattern_mismatch","loc":["query","type"],"msg":"String should match pattern '^(retrieval|extraction)$'","input":"x","ctx":{"pattern":"^(retrieval|extraction)$"}},{"type":"less_than_equal","loc":["query","limit"],"msg":"Input should be less than or equal to 100","input":"101","ctx":{"le":100}}]),
        ),
        (
            "/metrics/problem-queries?min_feedback=0".into(),
            json!([{"type":"greater_than_equal","loc":["query","min_feedback"],"msg":"Input should be greater than or equal to 1","input":"0","ctx":{"ge":1}}]),
        ),
    ];
    for (path, want) in cases {
        // A read-only key: the query check comes before the admin check.
        let (s, b) = get(&st, &path, RO).await;
        assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY, "{path}");
        assert_eq!(b, json!({"detail": want}), "{path}");
    }
}

#[tokio::test]
async fn feedback_body_errors_are_the_python_replies() {
    let st = state(true, false);
    let (s, _, b) = send(
        &st,
        "POST",
        "/search/feedback",
        ADMIN,
        Some(json!({"memory_id":"x","signal":"meh","query":5})),
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        serde_json::from_str::<Value>(&b).unwrap(),
        json!({"detail":[{"type":"int_parsing","loc":["body","memory_id"],"msg":"Input should be a valid integer, unable to parse string as an integer","input":"x"},{"type":"string_type","loc":["body","query"],"msg":"Input should be a valid string","input":5},{"type":"string_pattern_mismatch","loc":["body","signal"],"msg":"String should match pattern '^(useful|not_useful)$'","input":"meh","ctx":{"pattern":"^(useful|not_useful)$"}}]})
    );
    let (s, _, b) = send(&st, "POST", "/search/feedback", ADMIN, Some(json!({}))).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        serde_json::from_str::<Value>(&b).unwrap(),
        json!({"detail":[{"type":"missing","loc":["body","memory_id"],"msg":"Field required","input":{}},{"type":"missing","loc":["body","signal"],"msg":"Field required","input":{}}]})
    );
    // A valid body needs the engine for the memory check.
    let (s, _, _) = send(
        &st,
        "POST",
        "/search/feedback",
        ADMIN,
        Some(json!({"memory_id": 5, "signal": "useful"})),
    )
    .await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn admin_routes_refuse_a_read_only_key() {
    let st = state(true, false);
    for path in [
        "/usage",
        "/metrics",
        "/metrics/prometheus",
        "/metrics/extraction-quality",
        "/metrics/quality-summary",
        "/metrics/failures",
        "/metrics/problem-queries",
        "/metrics/stale-memories",
        "/metrics/graph-search",
        "/metrics/temporal-search",
    ] {
        let (s, b) = get(&st, path, RO).await;
        assert_eq!(s, StatusCode::FORBIDDEN, "{path}");
        assert_eq!(b, json!({"detail": "Admin key required"}), "{path}");
    }
    let (s, _, _) = send(&st, "DELETE", "/search/feedback/1", RO, None).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    // search-quality has no admin check; a scoped key needs the catalog.
    let (s, _) = get(&st, "/metrics/search-quality", RO).await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn usage_off_gives_the_null_tracker_replies() {
    let st = state(false, false);
    for (path, want) in [
        ("/usage", json!({"enabled": false})),
        ("/metrics/search-quality", json!({"enabled": false})),
        ("/metrics/extraction-quality", json!({"enabled": false})),
        ("/metrics/quality-summary", json!({"enabled": false})),
        ("/metrics/failures", json!({"enabled": false})),
        ("/metrics/problem-queries", json!({"queries": []})),
        ("/metrics/stale-memories", json!({"memories": []})),
        ("/metrics/graph-search", json!({})),
        ("/metrics/temporal-search", json!({})),
    ] {
        let (s, b) = get(&st, path, ADMIN).await;
        assert_eq!((s, b), (StatusCode::OK, want), "{path}");
    }
    let (s, _, b) = send(&st, "DELETE", "/search/feedback/7", ADMIN, None).await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    assert_eq!(b, r#"{"detail":"Feedback 7 not found"}"#);
}

#[tokio::test]
async fn empty_usage_db_gives_the_python_zero_replies() {
    let st = state(true, false);
    let (_, b) = get(&st, "/metrics/graph-search", ADMIN).await;
    assert_eq!(
        b,
        json!({"total_graph_searches":0,"total_graph_results":0,"total_direct_results":0,"avg_graph_results":0.0,"period":"7d"})
    );
    let (_, b) = get(&st, "/metrics/temporal-search", ADMIN).await;
    assert_eq!(
        b,
        json!({"total_temporal_searches":0,"since_only":0,"until_only":0,"range_queries":0,"auto_detected":0,"explicit":0,"period":"7d"})
    );
    // An empty source_prefix is no scope (falsy in Python).
    let (_, b) = get(
        &st,
        "/metrics/search-quality?period=all&source_prefix=",
        ADMIN,
    )
    .await;
    assert_eq!(
        b,
        json!({"period":"all","total_searches":0,"rank_distribution":{"top_3":0,"rank_4_plus":0},"feedback":{"useful":0,"not_useful":0,"useful_ratio":0.0},"unique_memories_retrieved":0})
    );
}

#[tokio::test]
async fn reads_see_the_rows_logged_before_them_and_retract_writes_an_audit_row() {
    let st = state(true, true);
    let u = st.usage.as_ref().unwrap();
    u.log(Row::Feedback {
        memory_id: 9,
        query: "q".into(),
        signal: "not_useful".into(),
        search_id: String::new(),
    });
    u.log(Row::Api {
        operation: "search".into(),
        source: String::new(),
        count: 1,
        client: "codex".into(),
        session_id: "s".into(),
        invocation: String::new(),
    });
    let (_, b) = get(&st, "/usage?period=all&session_id=s", ADMIN).await;
    assert_eq!(b["operations"]["search"]["by_client"]["codex"], 1);
    assert_eq!(b["session_id"], "s");
    let (_, b) = get(&st, "/metrics/failures", ADMIN).await;
    assert_eq!(b["failures"][0]["memory_id"], 9);

    let (s, _, b) = send(&st, "DELETE", "/search/feedback/1", ADMIN, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(
        serde_json::from_str::<Value>(&b).unwrap(),
        json!({"status": "retracted", "id": 1})
    );
    let (_, b) = get(&st, "/audit?action=feedback.retracted", ADMIN).await;
    assert_eq!(b["count"], 1);
    assert_eq!(b["entries"][0]["resource_id"], "1");
    assert_eq!(b["entries"][0]["key_id"], "env");
    let (s, _, _) = send(&st, "DELETE", "/search/feedback/1", ADMIN, None).await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    let (s, _, _) = send(&st, "DELETE", "/search/feedback/abc", ADMIN, None).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn metrics_has_the_python_shape() {
    let st = state(true, false);
    get(&st, "/usage", ADMIN).await;
    let (s, m) = get(&st, "/metrics", ADMIN).await;
    assert_eq!(s, StatusCode::OK);
    let keys = |v: &Value| {
        let mut k: Vec<String> = v.as_object().unwrap().keys().cloned().collect();
        k.sort();
        k
    };
    assert_eq!(
        keys(&m),
        [
            "embedder_reload",
            "extract",
            "memory",
            "requests",
            "routes",
            "uptime_sec"
        ]
    );
    assert_eq!(
        keys(&m["extract"]),
        [
            "jobs_tracked",
            "queue_depth",
            "queue_max",
            "queue_remaining",
            "workers"
        ]
    );
    assert_eq!(m["memory"]["current_total"], 0);
    assert_eq!(m["memory"]["trend"]["window_size"], 120);
    assert_eq!(m["memory"]["trend"]["samples"].as_array().unwrap().len(), 1);
    assert_eq!(
        keys(&m["memory"]["process"]),
        [
            "rss_anon_kb",
            "rss_file_kb",
            "rss_high_water_kb",
            "rss_kb",
            "vmsize_kb"
        ]
    );
    assert_eq!(m["embedder_reload"]["enabled"], false);
    assert_eq!(m["embedder_reload"]["policy"]["check_sec"], json!(15.0));
    assert_eq!(
        m["embedder_reload"]["policy"]["rss_kb_threshold"],
        1_200_000
    );
    assert_eq!(m["routes"]["GET /usage"]["count"], 1);
    assert_eq!(m["requests"]["active_http_requests"], 1);
    let (_, m) = get(&st, "/metrics", ADMIN).await;
    assert_eq!(m["memory"]["trend"]["samples"].as_array().unwrap().len(), 2);
}

#[tokio::test]
async fn prometheus_text() {
    let st = state(false, false);
    get(&st, "/usage", ADMIN).await;
    get(&st, "/nothing\"here", ADMIN).await;
    let (s, ct, text) = send(&st, "GET", "/metrics/prometheus", ADMIN, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(ct, "text/plain; version=0.0.4; charset=utf-8");
    assert!(
        text.contains("# TYPE memories_http_requests_total counter\n"),
        "{text}"
    );
    assert!(
        text.contains("memories_http_requests_total{route=\"GET /usage\"} 1\n"),
        "{text}"
    );
    assert!(
        text.contains("memories_http_request_errors_total{route=\"GET /nothing\\\"here\"} 1\n"),
        "{text}"
    );
    assert!(text.contains("\nmemories_records 0\n"), "{text}");
    assert!(
        text.contains("\nmemories_http_active_requests 1\n"),
        "{text}"
    );
    for line in text.lines().filter(|l| !l.starts_with('#')) {
        let v = line.rsplit(' ').next().unwrap();
        assert!(v.parse::<f64>().is_ok(), "{line}");
    }
}
