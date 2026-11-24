//! Exit check of task P4-1: a forced stop (SIGKILL) with 100 queued jobs loses
//! no job. The test runs the real binary two times on one `DATA_DIR`.

use std::net::TcpListener;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::{Value, json};

const KEY: &str = "forced-stop-key";

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn start(data: &std::path::Path, port: u16, ollama_url: &str, workers: u32) -> Child {
    Command::new(env!("CARGO_BIN_EXE_memories-server"))
        .args(["serve", "--port", &port.to_string()])
        .env_clear()
        .env("API_KEY", KEY)
        .env("DATA_DIR", data)
        .env("EXTRACT_PROVIDER", "ollama")
        .env("EXTRACT_MODEL", "gemma3:4b")
        .env("OLLAMA_URL", ollama_url)
        .env("EXTRACT_MAX_INFLIGHT", workers.to_string())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap()
}

async fn wait_up(http: &reqwest::Client, base: &str) {
    let deadline = Instant::now() + Duration::from_secs(30);
    while http.get(format!("{base}/health")).send().await.is_err() {
        assert!(Instant::now() < deadline, "the server did not start");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

async fn get(http: &reqwest::Client, url: String) -> (u16, Value) {
    let r = http.get(url).header("X-API-Key", KEY).send().await.unwrap();
    (r.status().as_u16(), r.json().await.unwrap())
}

#[tokio::test]
async fn a_kill_with_100_queued_jobs_loses_no_job() {
    let data = std::env::temp_dir().join(format!("memories-forced-stop-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&data);
    let http = reqwest::Client::builder().no_proxy().build().unwrap();

    // A provider that accepts the connection and never answers: the one worker
    // waits on its first job, so the other jobs stay queued.
    let black_hole = TcpListener::bind("127.0.0.1:0").unwrap();
    let hole_url = format!("http://{}", black_hole.local_addr().unwrap());
    let port = free_port();
    let base = format!("http://127.0.0.1:{port}");
    let mut child = start(&data, port, &hole_url, 1);
    wait_up(&http, &base).await;

    let mut ids = Vec::new();
    for i in 0..101 {
        let r = http
            .post(format!("{base}/memory/extract"))
            .header("X-API-Key", KEY)
            .json(&json!({"messages": format!("User: note {i}"), "source": "claude-code/stop"}))
            .send()
            .await
            .unwrap();
        assert_eq!(r.status().as_u16(), 202);
        let body: Value = r.json().await.unwrap();
        ids.push(body["job_id"].as_str().unwrap().to_string());
    }
    let (_, status) = get(&http, format!("{base}/extract/status")).await;
    assert!(status["queue_depth"].as_u64().unwrap() >= 100, "{status}");

    child.kill().unwrap(); // SIGKILL: no shutdown code runs.
    child.wait().unwrap();

    // Restart with a provider on a closed port: each job ends as in Python (rule W7).
    // Each provider call on a closed port takes about 1.5 s (two retries), so
    // more workers run the 101 jobs.
    let closed = format!("http://127.0.0.1:{}", free_port());
    let port = free_port();
    let base = format!("http://127.0.0.1:{port}");
    let mut child = start(&data, port, &closed, 16);
    wait_up(&http, &base).await;
    let deadline = Instant::now() + Duration::from_secs(120);
    for id in &ids {
        loop {
            let (code, job) = get(&http, format!("{base}/memory/extract/{id}")).await;
            assert_eq!(code, 200, "job {id} is lost: {job}");
            if job["status"] == "completed" {
                assert_eq!(job["result"]["error"], "provider_runtime_failure");
                break;
            }
            assert!(Instant::now() < deadline, "job {id} did not finish: {job}");
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }
    child.kill().unwrap();
    child.wait().unwrap();
    drop(black_hole);
    let _ = std::fs::remove_dir_all(&data);
}
