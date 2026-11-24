//! A snapshot that takes longer than the 5 s default gRPC deadline must not fail.

use memories_store::{QdrantSettings, QdrantStore};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::test(flavor = "multi_thread")]
async fn a_slow_snapshot_does_not_time_out() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (mut sock, _) = listener.accept().await.unwrap();
        let mut buf = vec![0u8; 4096];
        let n = sock.read(&mut buf).await.unwrap();
        let head = String::from_utf8_lossy(&buf[..n]).to_string();
        tokio::time::sleep(std::time::Duration::from_secs(6)).await;
        let body = r#"{"result":{"name":"slow.snapshot","size":1},"status":"ok","time":6.0}"#;
        let resp = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
            body.len()
        );
        sock.write_all(resp.as_bytes()).await.unwrap();
        head
    });
    let settings =
        QdrantSettings::from_lookup(|k| (k == "QDRANT_URL").then(|| url.clone())).unwrap();
    let store = QdrantStore::new(&settings, "memories_v2").unwrap();
    assert_eq!(store.create_snapshot().await.unwrap(), "slow.snapshot");
    let head = server.await.unwrap();
    assert!(
        head.starts_with("POST /collections/memories_v2/snapshots?wait=true "),
        "{head}"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_failed_snapshot_is_tried_again() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        for (status, body) in [
            (
                "500 Internal Server Error",
                r#"{"status":{"error":"busy"}}"#,
            ),
            (
                "200 OK",
                r#"{"result":{"name":"second.snapshot","size":1},"status":"ok"}"#,
            ),
        ] {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 4096];
            let _ = sock.read(&mut buf).await.unwrap();
            let resp = format!(
                "HTTP/1.1 {status}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            sock.write_all(resp.as_bytes()).await.unwrap();
        }
    });
    let settings =
        QdrantSettings::from_lookup(|k| (k == "QDRANT_URL").then(|| url.clone())).unwrap();
    let store = QdrantStore::new(&settings, "memories_v2").unwrap();
    assert_eq!(store.create_snapshot().await.unwrap(), "second.snapshot");
    server.await.unwrap();
}
