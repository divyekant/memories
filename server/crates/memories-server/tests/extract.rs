//! The extract job API against the replies of the Python server v5.19.0.
//! The 422 bodies come from `record_extract_validation.py`.

mod common;

use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use memories_extract::provider::Provider;
use memories_server::{
    AppState, Config, app,
    extract::{Extract, ExtractConfig, Pipeline, PipelineFuture, spawn_workers},
    keys::KeyStore,
};
use rusqlite::Connection;
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";

struct Reply {
    status: StatusCode,
    retry_after: Option<String>,
    body: Value,
}

async fn send(
    st: &Arc<AppState>,
    method: &str,
    path: &str,
    key: &str,
    body: Option<Value>,
) -> Reply {
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
    let retry_after = res
        .headers()
        .get(header::RETRY_AFTER)
        .map(|v| v.to_str().unwrap().to_string());
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    Reply {
        status,
        retry_after,
        body: serde_json::from_slice(&bytes).unwrap(),
    }
}

/// A pipeline for the tests: it counts the tries and gives `reply`.
struct Stub {
    calls: AtomicU32,
    reply: fn(u32, &Value) -> Result<Value, String>,
}

impl Pipeline for Stub {
    fn run(&self, request: Value) -> PipelineFuture {
        let n = self.calls.fetch_add(1, Ordering::SeqCst) + 1;
        let r = (self.reply)(n, &request);
        Box::pin(async move {
            if r.as_ref().is_err_and(|e| e == "panic") {
                panic!("stub pipeline panic");
            }
            r
        })
    }
}

fn stub(reply: fn(u32, &Value) -> Result<Value, String>) -> Arc<Stub> {
    Arc::new(Stub {
        calls: AtomicU32::new(0),
        reply,
    })
}

fn echo(_: u32, req: &Value) -> Result<Value, String> {
    Ok(json!({"stored_count": 0, "echo": req}))
}

fn config(audit: bool) -> Config {
    let mut cfg = common::config(ADMIN, 50);
    cfg.audit_log = audit;
    cfg
}

fn state_with(
    cfg: &Config,
    ecfg: ExtractConfig,
    provider: Option<Arc<Provider>>,
    pipeline: Option<Arc<dyn Pipeline>>,
) -> Arc<AppState> {
    let audit = cfg
        .audit_log
        .then(|| memories_server::audit::AuditLog::open(&cfg.data_dir.join("audit.db")).unwrap());
    let extract = Extract::open(&cfg.data_dir, ecfg, provider, pipeline).unwrap();
    AppState::with_extract(
        cfg,
        KeyStore::open(&cfg.data_dir.join("keys.db")).unwrap(),
        audit,
        extract,
        None,
    )
}

async fn new_key(st: &Arc<AppState>, body: Value) -> (String, String) {
    let r = send(st, "POST", "/api/keys", ADMIN, Some(body)).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    (
        r.body["key"].as_str().unwrap().into(),
        r.body["id"].as_str().unwrap().into(),
    )
}

async fn wait_done(st: &Arc<AppState>, job_id: &str) -> Value {
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        let r = send(st, "GET", &format!("/memory/extract/{job_id}"), ADMIN, None).await;
        assert_eq!(r.status, StatusCode::OK, "{}", r.body);
        if matches!(r.body["status"].as_str(), Some("completed" | "failed")) {
            return r.body;
        }
        assert!(Instant::now() < deadline, "job did not finish: {}", r.body);
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

const MESSAGES: &str = "User: the route-optimizer cache TTL is 5 minutes.\nAssistant: Noted.";

/// The data of the `extraction.completed` events, after a short wait for the worker.
async fn completed_events(st: &Arc<AppState>) -> Vec<Value> {
    tokio::time::sleep(Duration::from_millis(200)).await;
    st.events
        .recent(100)
        .into_iter()
        .filter(|e| e.event_type == "extraction.completed")
        .map(|e| e.data)
        .collect()
}

#[tokio::test]
async fn body_errors_match_pydantic() {
    let st = common::state(&config(false));
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/extract_validation.json")).unwrap();
    assert!(cases.len() >= 20);
    for c in cases {
        let r = send(
            &st,
            "POST",
            "/memory/extract",
            ADMIN,
            Some(c["body"].clone()),
        )
        .await;
        assert_eq!(r.status.as_u16(), c["status"], "{}", c["name"]);
        assert_eq!(r.body, c["reply"], "{}", c["name"]);
    }
}

#[tokio::test]
async fn access_rules_and_501_follow_python() {
    let st = common::state(&config(false));
    let (ro, _) = new_key(
        &st,
        json!({"name": "ro", "role": "read-only", "prefixes": ["claude-code/"]}),
    )
    .await;
    let (rw, _) = new_key(
        &st,
        json!({"name": "rw", "role": "read-write", "prefixes": ["codex/"]}),
    )
    .await;
    let cases = [
        (
            ro.as_str(),
            json!({"messages": "m"}),
            403,
            json!("Read-only keys cannot trigger extraction"),
        ),
        (
            ro.as_str(),
            json!({"messages": "m", "source": "claude-code/x"}),
            403,
            json!("Key does not have write access to source: claude-code/x"),
        ),
        (
            rw.as_str(),
            json!({"messages": "m"}),
            400,
            json!("source is required for scoped keys"),
        ),
        (
            rw.as_str(),
            json!({"messages": "m", "source": "wip/x"}),
            403,
            json!("Key does not have write access to source: wip/x"),
        ),
        // No provider: 501 after the access checks.
        (
            rw.as_str(),
            json!({"messages": "m", "source": "codex/x"}),
            501,
            json!("Extraction not configured. Set EXTRACT_PROVIDER env var."),
        ),
        (
            ADMIN,
            json!({"messages": "m"}),
            501,
            json!("Extraction not configured. Set EXTRACT_PROVIDER env var."),
        ),
    ];
    for (key, body, status, detail) in cases {
        let r = send(&st, "POST", "/memory/extract", key, Some(body.clone())).await;
        assert_eq!(
            (r.status.as_u16(), &r.body),
            (status, &json!({"detail": detail})),
            "{body}"
        );
    }
    let r = send(&st, "GET", "/memory/extract/nojob", ADMIN, None).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    assert_eq!(r.body, json!({"detail": "Extraction job not found: nojob"}));
}

#[tokio::test]
async fn a_job_runs_and_keeps_the_python_fields() {
    let cfg = config(true);
    let pipe = stub(echo);
    let st = state_with(&cfg, ExtractConfig::default(), None, Some(pipe.clone()));
    let body = json!({"messages": MESSAGES, "source": "claude-code/voltis/extract", "context": "stop",
                      "debug": "yes", "dry_run": 1, "document_at": "2026-01-02"});
    let r = send(&st, "POST", "/memory/extract", ADMIN, Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{}", r.body);
    let job_id = r.body["job_id"].as_str().unwrap().to_string();
    assert!(job_id.len() == 32 && job_id.bytes().all(|b| b.is_ascii_hexdigit()));
    assert_eq!(
        r.body,
        json!({"job_id": job_id, "status": "queued", "queue_depth": 1,
               "result_url": format!("/memory/extract/{job_id}")})
    );
    // Queued and not running: no worker yet.
    let queued = send(
        &st,
        "GET",
        &format!("/memory/extract/{job_id}"),
        ADMIN,
        None,
    )
    .await
    .body;
    assert_eq!(queued["status"], "queued");

    let workers = spawn_workers(&st);
    let job = wait_done(&st, &job_id).await;
    let mut keys: Vec<&str> = job
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort();
    assert_eq!(
        keys,
        [
            "auth_key_id",
            "completed_at",
            "context",
            "created_at",
            "job_id",
            "message_length",
            "queue_depth",
            "result",
            "source",
            "started_at",
            "status",
            "trusted_authorship"
        ]
    );
    assert_eq!(job["status"], "completed");
    assert_eq!(job["message_length"], MESSAGES.chars().count());
    assert_eq!(job["queue_depth"], 0);
    assert_eq!(job["auth_key_id"], Value::Null);
    assert_eq!(job["trusted_authorship"], Value::Null);
    assert!(job["created_at"].as_str().unwrap().ends_with("+00:00"));
    assert_eq!(
        job["result"]["echo"],
        json!({"messages": MESSAGES, "source": "claude-code/voltis/extract", "context": "stop",
               "allowed_prefixes": null, "debug": true, "dry_run": true, "document_at": "2026-01-02",
               "trusted_authorship": null, "promotion_context": null})
    );
    assert_eq!(pipe.calls.load(Ordering::SeqCst), 1);

    // Python `event_bus.emit("extraction.completed", ...)` after the job completes.
    let events = completed_events(&st).await;
    assert_eq!(events.len(), 1);
    assert_eq!(
        events[0],
        json!({"job_id": job_id, "source": "claude-code/voltis/extract", "stored_count": 0,
               "updated_count": 0, "conflict_count": 0})
    );

    // Python `_audit(request, "extract", source=...)`.
    let conn = Connection::open(cfg.data_dir.join("audit.db")).unwrap();
    let row: (String, String, String) = conn
        .query_row(
            "SELECT action, key_id, source_prefix FROM audit_log",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .unwrap();
    assert_eq!(
        row,
        (
            "extract".into(),
            "env".into(),
            "claude-code/voltis/extract".into()
        )
    );
    workers.iter().for_each(|w| w.abort());
}

#[tokio::test]
async fn job_access_follows_python() {
    let pipe = stub(echo);
    let st = state_with(&config(false), ExtractConfig::default(), None, Some(pipe));
    let (rw, rw_id) = new_key(
        &st,
        json!({"name": "writer", "principal_id": "dk", "role": "read-write", "prefixes": ["codex/"]}),
    )
    .await;
    let (ro_other, _) = new_key(
        &st,
        json!({"name": "other", "role": "read-only", "prefixes": ["wip/"]}),
    )
    .await;
    let (ro_same, _) = new_key(
        &st,
        json!({"name": "reader", "role": "read-only", "prefixes": ["codex/"]}),
    )
    .await;
    let r = send(
        &st,
        "POST",
        "/memory/extract",
        &rw,
        Some(json!({"messages": "m", "source": "codex/a"})),
    )
    .await;
    assert_eq!(r.status, StatusCode::ACCEPTED);
    let path = format!("/memory/extract/{}", r.body["job_id"].as_str().unwrap());

    let own = send(&st, "GET", &path, &rw, None).await;
    assert_eq!(own.status, StatusCode::OK);
    assert_eq!(own.body["auth_key_id"], rw_id.as_str());
    assert_eq!(
        own.body["trusted_authorship"],
        json!({"author": "dk", "origin_client": "other", "contributors": [], "source_memory_ids": []})
    );
    assert_eq!(
        send(&st, "GET", &path, &ro_same, None).await.status,
        StatusCode::OK
    );
    let denied = send(&st, "GET", &path, &ro_other, None).await;
    assert_eq!(denied.status, StatusCode::FORBIDDEN);
    assert_eq!(
        denied.body,
        json!({"detail": "Access denied to extraction job"})
    );
}

#[tokio::test]
async fn a_full_queue_gives_429_with_retry_after() {
    let ecfg = ExtractConfig {
        queue_max: 3,
        ..ExtractConfig::default()
    };
    let st = state_with(&config(false), ecfg, None, Some(stub(echo)));
    for depth in 1..=3 {
        let r = send(
            &st,
            "POST",
            "/memory/extract",
            ADMIN,
            Some(json!({"messages": "m"})),
        )
        .await;
        assert_eq!(
            (r.status, &r.body["queue_depth"]),
            (StatusCode::ACCEPTED, &json!(depth))
        );
    }
    let r = send(
        &st,
        "POST",
        "/memory/extract",
        ADMIN,
        Some(json!({"messages": "m"})),
    )
    .await;
    assert_eq!(r.status, StatusCode::TOO_MANY_REQUESTS);
    // Python: max(1, min(30, depth // workers + 1)) with 2 workers.
    assert_eq!(r.retry_after.as_deref(), Some("2"));
    assert_eq!(
        r.body,
        json!({"detail": {"error": "extract_queue_full", "message": "Extraction queue is full. Retry later.",
                          "queue_depth": 3, "queue_max": 3, "retry_after_sec": 2}})
    );
    let s = send(&st, "GET", "/extract/status", ADMIN, None).await.body;
    assert_eq!(
        (&s["queue_depth"], &s["jobs_tracked"], &s["queue_remaining"]),
        (&json!(3), &json!(3), &json!(0))
    );
}

#[tokio::test]
async fn a_failing_job_gets_three_tries() {
    fn flaky(n: u32, _: &Value) -> Result<Value, String> {
        if n < 3 {
            Err(format!("try {n} failed"))
        } else {
            Ok(json!({"ok": n}))
        }
    }
    fn broken(n: u32, _: &Value) -> Result<Value, String> {
        Err(if n == 2 {
            "panic".into()
        } else {
            format!("try {n} failed")
        })
    }
    for (reply, status, field, expected) in [
        (
            flaky as fn(u32, &Value) -> Result<Value, String>,
            "completed",
            "result",
            json!({"ok": 3}),
        ),
        (broken, "failed", "error", json!("try 3 failed")),
    ] {
        let pipe = stub(reply);
        let ecfg = ExtractConfig {
            retry_delay: Duration::from_millis(50),
            ..ExtractConfig::default()
        };
        let st = state_with(&config(false), ecfg, None, Some(pipe.clone()));
        let r = send(
            &st,
            "POST",
            "/memory/extract",
            ADMIN,
            Some(json!({"messages": "m"})),
        )
        .await;
        let workers = spawn_workers(&st);
        let job = wait_done(&st, r.body["job_id"].as_str().unwrap()).await;
        assert_eq!(
            (job["status"].as_str().unwrap(), &job[field]),
            (status, &expected)
        );
        assert_eq!(pipe.calls.load(Ordering::SeqCst), 3);
        // Only a completed job sends `extraction.completed`, once.
        let n = completed_events(&st).await.len();
        assert_eq!(n, usize::from(status == "completed"));
        workers.iter().for_each(|w| w.abort());
    }
}

#[tokio::test]
async fn status_reports_the_queue_and_the_provider() {
    let st = common::state(&config(false));
    let r = send(&st, "GET", "/extract/status", ADMIN, None).await;
    assert_eq!(
        r.body,
        json!({"enabled": false, "fallback_add_enabled": false, "jobs_tracked": 0, "queue_depth": 0,
               "queue_max": 10000, "queue_remaining": 10000, "workers": 2})
    );
    // An Ollama provider on a closed port, as in the parity settings.
    let port = std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port();
    let url = format!("http://127.0.0.1:{port}");
    let provider = Provider::from_env(Some("ollama"), Some("gemma3:4b"), |k| {
        (k == "OLLAMA_URL").then(|| url.clone())
    })
    .unwrap()
    .unwrap();
    let ecfg = ExtractConfig {
        queue_max: 40,
        ..ExtractConfig::default()
    };
    let st = state_with(&config(false), ecfg, Some(Arc::new(provider)), None);
    let r = send(&st, "GET", "/extract/status", ADMIN, None).await;
    assert_eq!(
        r.body,
        json!({"enabled": true, "fallback_add_enabled": false, "jobs_tracked": 0, "model": "gemma3:4b",
               "provider": "ollama", "queue_depth": 0, "queue_max": 40, "queue_remaining": 40,
               "status": "unhealthy", "workers": 2})
    );
}

#[tokio::test]
async fn health_shows_the_queue_only_for_the_admin_key() {
    let st = state_with(
        &config(false),
        ExtractConfig::default(),
        None,
        Some(stub(echo)),
    );
    send(
        &st,
        "POST",
        "/memory/extract",
        ADMIN,
        Some(json!({"messages": "m"})),
    )
    .await;
    let minimal = send(&st, "GET", "/health", "wrong", None).await.body;
    assert_eq!(minimal.as_object().unwrap().len(), 3);
    let full = send(&st, "GET", "/health", ADMIN, None).await.body;
    assert_eq!(full["extract_queue_depth"], 1);
    assert!(full["extract_oldest_job_age_sec"].as_f64().unwrap() >= 0.0);
}

/// Exit check: the enqueue p95 is 50 ms or less (a hook stops the request after 2 s).
#[tokio::test]
async fn enqueue_p95_is_50_ms_or_less() {
    let st = state_with(
        &config(false),
        ExtractConfig::default(),
        None,
        Some(stub(echo)),
    );
    let body = json!({"messages": "x".repeat(20_000), "source": "claude-code/p95"});
    let mut times = Vec::new();
    for _ in 0..200 {
        let t = Instant::now();
        let r = send(&st, "POST", "/memory/extract", ADMIN, Some(body.clone())).await;
        times.push(t.elapsed());
        assert_eq!(r.status, StatusCode::ACCEPTED);
    }
    times.sort();
    let p95 = times[times.len() * 95 / 100];
    eprintln!("enqueue p50 {:?} p95 {:?}", times[times.len() / 2], p95);
    assert!(p95 <= Duration::from_millis(50), "p95 {p95:?}");
}
