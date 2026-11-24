//! `/events/*`, `/webhooks` and `/ui` against the replies of the Python server
//! v5.19.0 (`event_bus.py`, `app.py`).

mod common;

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use futures_util::StreamExt;
use memories_server::{AppState, app};
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";

fn request(method: &str, path: &str, key: &str, body: Option<Value>) -> Request<Body> {
    let mut b = Request::builder()
        .method(method)
        .uri(path)
        .header(header::HOST, "testserver");
    if !key.is_empty() {
        b = b.header("X-API-Key", key);
    }
    match body {
        Some(v) => b
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(v.to_string()))
            .unwrap(),
        None => b.body(Body::empty()).unwrap(),
    }
}

async fn raw(
    st: &Arc<AppState>,
    method: &str,
    path: &str,
    key: &str,
) -> (StatusCode, String, Vec<u8>) {
    let res = app(st.clone())
        .oneshot(request(method, path, key, None))
        .await
        .unwrap();
    let ct = res
        .headers()
        .get(header::CONTENT_TYPE)
        .map(|v| v.to_str().unwrap().to_string())
        .unwrap_or_default();
    let status = res.status();
    (
        status,
        ct,
        to_bytes(res.into_body(), usize::MAX)
            .await
            .unwrap()
            .to_vec(),
    )
}

async fn call(
    st: &Arc<AppState>,
    method: &str,
    path: &str,
    key: &str,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let res = app(st.clone())
        .oneshot(request(method, path, key, body))
        .await
        .unwrap();
    let status = res.status();
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

fn state() -> (Arc<AppState>, memories_server::Config) {
    let cfg = common::config(ADMIN, 50);
    (common::state(&cfg), cfg)
}

async fn ro_key(st: &Arc<AppState>) -> String {
    let (s, v) = call(
        st,
        "POST",
        "/api/keys",
        ADMIN,
        Some(json!({"name": "ro", "role": "read-only", "prefixes": ["claude-code/"]})),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    v["key"].as_str().unwrap().to_string()
}

#[tokio::test]
async fn recent_events_use_the_python_slice_and_the_read_scope() {
    let (st, _) = state();
    for (i, src) in ["claude-code/a", "codex/b", "", "claude-code/c"]
        .iter()
        .enumerate()
    {
        st.events
            .emit("memory.added", json!({"id": i, "source": src}));
    }
    st.events
        .emit("extraction.completed", json!({"job_id": "j"}));

    let (s, v) = call(&st, "GET", "/events/recent?limit=2", ADMIN, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["count"], 2);
    assert_eq!(
        v["events"][0]["data"],
        json!({"id": 3, "source": "claude-code/c"})
    );
    let e = &v["events"][1];
    assert_eq!(e["type"], "extraction.completed");
    assert!(e["id"].is_string() && e["timestamp"].as_str().unwrap().ends_with("+00:00"));

    // Python events[-limit:]: 0 gives all, -1 drops the first event.
    let (_, v) = call(&st, "GET", "/events/recent?limit=0", ADMIN, None).await;
    assert_eq!(v["count"], 5);
    let (_, v) = call(&st, "GET", "/events/recent?limit=-1", ADMIN, None).await;
    assert_eq!(v["count"], 4);
    assert_eq!(v["events"][0]["data"]["id"], 1);
    let (_, v) = call(&st, "GET", "/events/recent", ADMIN, None).await;
    assert_eq!(v["count"], 5);

    // A scoped key sees its sources and the events with no source.
    let ro = ro_key(&st).await;
    let (_, v) = call(&st, "GET", "/events/recent", &ro, None).await;
    let ids: Vec<Value> = v["events"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["data"]["id"].clone())
        .collect();
    assert_eq!(ids, vec![json!(0), json!(2), json!(3), Value::Null]);
    assert_eq!(v["count"], 4);

    let (s, v) = call(&st, "GET", "/events/recent?limit=x", ADMIN, None).await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        v,
        json!({"detail": [{"type": "int_parsing", "loc": ["query", "limit"],
            "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "x"}]})
    );
    let (s, _) = call(&st, "GET", "/events/recent", "", None).await;
    assert_eq!(s, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn stream_sends_retry_then_the_visible_events_of_the_type() {
    let (st, _) = state();
    let ro = ro_key(&st).await;
    let res = app(st.clone())
        .oneshot(request(
            "GET",
            "/events/stream?event_type=memory.added",
            &ro,
            None,
        ))
        .await
        .unwrap();
    assert_eq!(res.status(), StatusCode::OK);
    assert_eq!(
        res.headers()[header::CONTENT_TYPE],
        "text/event-stream; charset=utf-8"
    );
    let mut body = res.into_body().into_data_stream();
    assert_eq!(next(&mut body).await, "retry: 5000\n\n");
    st.events.emit(
        "memory.deleted",
        json!({"id": 1, "source": "claude-code/x"}),
    );
    st.events
        .emit("memory.added", json!({"id": 2, "source": "codex/x"}));
    st.events.emit(
        "memory.added",
        json!({"id": 3, "source": "claude-code/x", "text": "é"}),
    );
    let got = next(&mut body).await;
    let id = st.events.recent(1)[0].id.clone();
    assert_eq!(
        got,
        format!(
            "id: {id}\nevent: memory.added\ndata: {{\"id\": 3, \"source\": \"claude-code/x\", \"text\": \"\\u00e9\"}}\n\n"
        )
    );
}

#[tokio::test]
async fn webhook_routes_need_admin_and_keep_the_python_shapes() {
    let (st, cfg) = state();
    let ro = ro_key(&st).await;
    let (s, v) = call(&st, "GET", "/webhooks", &ro, None).await;
    assert_eq!(
        (s, v),
        (
            StatusCode::FORBIDDEN,
            json!({"detail": "Admin key required"})
        )
    );
    // FastAPI checks the body before the handler: 422 before 403.
    let (s, v) = call(
        &st,
        "POST",
        "/webhooks",
        &ro,
        Some(json!({"events": "memory.added"})),
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        v,
        json!({"detail": [
            {"type": "missing", "loc": ["body", "url"], "msg": "Field required", "input": {"events": "memory.added"}},
            {"type": "list_type", "loc": ["body", "events"], "msg": "Input should be a valid list", "input": "memory.added"}
        ]})
    );
    let (s, _) = call(
        &st,
        "POST",
        "/webhooks",
        &ro,
        Some(json!({"url": "http://x"})),
    )
    .await;
    assert_eq!(s, StatusCode::FORBIDDEN);

    let (s, v) = call(
        &st,
        "POST",
        "/webhooks",
        ADMIN,
        Some(json!({"url": "http://127.0.0.1:9/hook", "events": ["memory.added"]})),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["id"], "1");
    assert_eq!(v["url"], "http://127.0.0.1:9/hook");
    assert_eq!(v["events"], json!(["memory.added"]));
    assert!(v["created_at"].as_str().unwrap().ends_with("+00:00"));
    // `events or list(EVENT_TYPES)`: null and [] give all five types.
    for events in [Value::Null, json!([])] {
        let (_, v) = call(
            &st,
            "POST",
            "/webhooks",
            ADMIN,
            Some(json!({"url": "u", "events": events})),
        )
        .await;
        assert_eq!(v["events"].as_array().unwrap().len(), 5);
    }
    let (_, v) = call(&st, "GET", "/webhooks", ADMIN, None).await;
    let ids: Vec<&str> = v["webhooks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|w| w["id"].as_str().unwrap())
        .collect();
    assert_eq!(ids, ["1", "2", "3"]);

    let (s, v) = call(&st, "DELETE", "/webhooks/2", &ro, None).await;
    assert_eq!(
        (s, v),
        (
            StatusCode::FORBIDDEN,
            json!({"detail": "Admin key required"})
        )
    );
    let (_, v) = call(&st, "DELETE", "/webhooks/2", ADMIN, None).await;
    assert_eq!(v, json!({"deleted": true}));
    let (_, v) = call(&st, "DELETE", "/webhooks/2", ADMIN, None).await;
    assert_eq!(v, json!({"deleted": false}));

    // The webhooks stay in state.db after a restart.
    drop(st);
    let st = common::state(&cfg);
    let (_, v) = call(&st, "GET", "/webhooks", ADMIN, None).await;
    assert_eq!(v["webhooks"].as_array().unwrap().len(), 2);
}

/// A receiver that answers `fail_status` to the first `fail` calls, then 200.
async fn receiver(
    fail: usize,
    fail_status: StatusCode,
) -> (String, Arc<AtomicUsize>, Arc<std::sync::Mutex<Vec<Value>>>) {
    let hits = Arc::new(AtomicUsize::new(0));
    let bodies = Arc::new(std::sync::Mutex::new(Vec::new()));
    let (h, b) = (hits.clone(), bodies.clone());
    let route = axum::routing::post(move |axum::Json(v): axum::Json<Value>| {
        let (h, b) = (h.clone(), b.clone());
        async move {
            b.lock().unwrap().push(v);
            if h.fetch_add(1, Ordering::SeqCst) < fail {
                fail_status
            } else {
                StatusCode::OK
            }
        }
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(
        async move { axum::serve(listener, axum::Router::new().route("/hook", route)).await },
    );
    (format!("http://{addr}/hook"), hits, bodies)
}

async fn wait_for(hits: &AtomicUsize, n: usize) {
    for _ in 0..100 {
        if hits.load(Ordering::SeqCst) >= n {
            return;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

#[tokio::test]
async fn webhooks_get_the_events_of_their_types_with_limited_retries() {
    let (st, _) = state();
    let _dispatch = memories_server::events::spawn_webhooks(st.clone());
    let (ok_url, ok_hits, ok_bodies) = receiver(1, StatusCode::INTERNAL_SERVER_ERROR).await;
    let (bad_url, bad_hits, _) = receiver(usize::MAX, StatusCode::BAD_GATEWAY).await;
    call(
        &st,
        "POST",
        "/webhooks",
        ADMIN,
        Some(json!({"url": ok_url, "events": ["memory.added"]})),
    )
    .await;
    call(
        &st,
        "POST",
        "/webhooks",
        ADMIN,
        Some(json!({"url": bad_url})),
    )
    .await;

    st.events
        .emit("memory.deleted", json!({"id": 1, "source": "s"}));
    st.events
        .emit("memory.added", json!({"id": 2, "source": "s"}));
    wait_for(&bad_hits, 6).await;
    wait_for(&ok_hits, 2).await;
    tokio::time::sleep(Duration::from_millis(500)).await;
    // One failed try and one retry for the receiver of memory.added only.
    assert_eq!(ok_hits.load(Ordering::SeqCst), 2);
    let b = ok_bodies.lock().unwrap();
    assert_eq!(b[0]["type"], "memory.added");
    assert_eq!(b[0]["data"], json!({"id": 2, "source": "s"}));
    assert_eq!(b[0], b[1]);
    // Two events, three tries each, then the dispatcher stops.
    assert_eq!(bad_hits.load(Ordering::SeqCst), 6);
}

#[tokio::test]
async fn ui_page_and_static_files_need_no_key() {
    let (_, mut cfg) = state();
    let ui = cfg.data_dir.join("webui");
    std::fs::create_dir_all(ui.join("sub")).unwrap();
    std::fs::write(ui.join("index.html"), "<html></html>").unwrap();
    std::fs::write(ui.join("app.js"), "export {}").unwrap();
    std::fs::write(ui.join("sub/styles.css"), "a{}").unwrap();
    std::fs::write(ui.join("favicon.svg"), "<svg/>").unwrap();
    std::fs::write(cfg.data_dir.join("secret.txt"), "no").unwrap();
    cfg.ui_dir = ui;
    let st = common::state(&cfg);

    let (s, ct, body) = raw(&st, "GET", "/ui", "").await;
    assert_eq!(
        (s, ct.as_str(), body.as_slice()),
        (
            StatusCode::OK,
            "text/html; charset=utf-8",
            &b"<html></html>"[..]
        )
    );
    for (path, want) in [
        ("/ui/static/app.js", "text/javascript; charset=utf-8"),
        ("/ui/static/sub/styles.css", "text/css; charset=utf-8"),
        ("/ui/static/favicon.svg", "image/svg+xml"),
        ("/ui/static/index.html", "text/html; charset=utf-8"),
    ] {
        let (s, ct, _) = raw(&st, "GET", path, "").await;
        assert_eq!((s, ct.as_str()), (StatusCode::OK, want), "{path}");
    }
    for path in [
        "/ui/static/missing.js",
        "/ui/static/sub",
        "/ui/static/../secret.txt",
        "/ui/static/sub/../../secret.txt",
        "/ui/static/..%2Fsecret.txt",
        "/ui/static/%2Fetc%2Fpasswd",
    ] {
        let (s, ct, body) = raw(&st, "GET", path, "").await;
        assert_eq!(s, StatusCode::NOT_FOUND, "{path}");
        assert_eq!(ct, "application/json", "{path}");
        assert_eq!(body, br#"{"detail":"Not Found"}"#, "{path}");
    }
    let (s, _, body) = raw(&st, "HEAD", "/ui/static/app.js", "").await;
    assert_eq!(s, StatusCode::OK);
    assert!(body.is_empty());

    // With no UI files, as Python: /ui is 404 "UI not found".
    std::fs::remove_dir_all(&cfg.ui_dir).unwrap();
    let (s, _, body) = raw(&st, "GET", "/ui", "").await;
    assert_eq!(
        (s, body.as_slice()),
        (StatusCode::NOT_FOUND, &br#"{"detail":"UI not found"}"#[..])
    );
}

async fn next(b: &mut axum::body::BodyDataStream) -> String {
    let chunk = tokio::time::timeout(Duration::from_secs(5), b.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    String::from_utf8(chunk.to_vec()).unwrap()
}

#[tokio::test]
async fn a_4xx_reply_and_a_bad_url_get_one_try() {
    let (st, _) = state();
    let _dispatch = memories_server::events::spawn_webhooks(st.clone());
    let (url, hits, _) = receiver(usize::MAX, StatusCode::NOT_FOUND).await;
    call(&st, "POST", "/webhooks", ADMIN, Some(json!({"url": url}))).await;
    call(
        &st,
        "POST",
        "/webhooks",
        ADMIN,
        Some(json!({"url": "not a url"})),
    )
    .await;
    st.events.emit("memory.added", json!({"id": 1}));
    wait_for(&hits, 1).await;
    tokio::time::sleep(Duration::from_millis(1500)).await;
    assert_eq!(hits.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn the_stream_ends_when_the_shutdown_starts() {
    let (st, _) = state();
    let res = app(st.clone())
        .oneshot(request("GET", "/events/stream", ADMIN, None))
        .await
        .unwrap();
    let mut body = res.into_body().into_data_stream();
    assert_eq!(next(&mut body).await, "retry: 5000\n\n");
    st.shutdown.send_replace(true);
    let end = tokio::time::timeout(Duration::from_secs(5), body.next())
        .await
        .unwrap();
    assert!(end.is_none());
}
