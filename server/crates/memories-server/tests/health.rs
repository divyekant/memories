mod common;

use memories_server::{app, healthcheck};
use tokio::net::TcpListener;

async fn spawn_server() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app(common::state(&common::config("", 50))))
            .await
            .unwrap()
    });
    format!("http://{addr}/health")
}

#[tokio::test]
async fn health_returns_minimal_body_as_json() {
    let url = spawn_server().await;
    let res = tokio::task::spawn_blocking(move || healthcheck::get(&url))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(res.status, 200);
    assert!(
        res.content_type.starts_with("application/json"),
        "{}",
        res.content_type
    );
    let body: serde_json::Value = serde_json::from_str(&res.body).unwrap();
    assert_eq!(
        body,
        serde_json::json!({
            "status": "ok",
            "service": "memories",
            "version": env!("CARGO_PKG_VERSION"),
            // API_KEY is empty, so each caller gets the full body.
            "extract_queue_depth": 0,
            "extract_oldest_job_age_sec": null,
        })
    );
}

#[tokio::test]
async fn python_shim_passes_on_live_server_and_fails_on_closed_port() {
    let url = spawn_server().await;
    let cmd = format!(
        "import sys,urllib.request; sys.exit(0 if 200 <= urllib.request.urlopen('{url}', timeout=3).getcode() < 400 else 1)"
    );
    let args = vec!["python".to_string(), "-c".to_string(), cmd];
    assert_eq!(
        tokio::task::spawn_blocking(move || healthcheck::run(&args))
            .await
            .unwrap(),
        0
    );

    let closed = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = closed.local_addr().unwrap().port();
    drop(closed);
    let args = vec![
        "python".to_string(),
        "-c".to_string(),
        format!("urlopen('http://127.0.0.1:{port}/health')"),
    ];
    assert_eq!(
        tokio::task::spawn_blocking(move || healthcheck::run(&args))
            .await
            .unwrap(),
        1
    );
}

#[tokio::test]
async fn healthcheck_command_passes_on_live_server_and_fails_on_closed_port() {
    let url = spawn_server().await;
    let closed = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = closed.local_addr().unwrap().port();
    drop(closed);
    let run = |u: String| {
        std::process::Command::new(env!("CARGO_BIN_EXE_memories-server"))
            .args(["healthcheck", &u])
            .status()
            .unwrap()
            .code()
    };
    let (live, dead) = tokio::task::spawn_blocking(move || {
        (run(url), run(format!("http://127.0.0.1:{port}/health")))
    })
    .await
    .unwrap();
    assert_eq!((live, dead), (Some(0), Some(1)));
}

#[test]
fn shim_reads_url_from_python_command_or_uses_default() {
    let compose_cmd = "import sys,urllib.request; sys.exit(0 if 200 <= urllib.request.urlopen('http://localhost:8000/health', timeout=3).getcode() < 400 else 1)";
    assert_eq!(
        healthcheck::url_from_args(&["python".into(), "-c".into(), compose_cmd.into()]),
        "http://localhost:8000/health"
    );
    assert_eq!(
        healthcheck::url_from_args(&["python".into()]),
        "http://localhost:8000/health"
    );
}

#[test]
fn shim_refuses_calls_that_are_not_the_health_check() {
    for args in [
        vec!["python", "-c", "import app"],
        vec!["python3", "--version"],
        vec!["python", "/app/script.py"],
        vec!["python"],
    ] {
        let args: Vec<String> = args.into_iter().map(String::from).collect();
        assert_eq!(healthcheck::run(&args), 127, "{args:?}");
    }
}
