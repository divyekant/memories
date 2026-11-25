//! Prints the speed and the memory of the embedder (VmRSS and VmHWM from
//! /proc, Linux only).
//! Run: `cargo run --release -p memories-embed --example embed_memory`

use memories_embed::{EmbedConfig, Embedder, TextKind};

fn mem() -> String {
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    status
        .lines()
        .filter(|l| l.starts_with("VmRSS") || l.starts_with("VmHWM"))
        .map(|l| l.split_whitespace().collect::<Vec<_>>().join(" "))
        .collect::<Vec<_>>()
        .join(", ")
}

#[tokio::main]
async fn main() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/embed_texts.json"
    );
    let fixture: serde_json::Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let texts: Vec<String> = fixture["texts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| t.as_str().unwrap().to_owned())
        .collect();
    println!("start: {}", mem());
    let embedder = Embedder::load(EmbedConfig {
        cache_dir: std::env::var("MEMORIES_TEST_MODEL_CACHE")
            .ok()
            .map(Into::into),
        ..EmbedConfig::default()
    })
    .await
    .unwrap();
    println!("loaded: {}", mem());
    let mut times: Vec<f64> = Vec::new();
    for text in texts.iter().cycle().take(400) {
        // The first 12 words: the length of a search query.
        let query = text
            .split_whitespace()
            .take(12)
            .collect::<Vec<_>>()
            .join(" ");
        let t = std::time::Instant::now();
        embedder.embed(vec![query], TextKind::Query).await.unwrap();
        times.push(t.elapsed().as_secs_f64() * 1000.0);
    }
    times.sort_by(f64::total_cmp);
    println!(
        "after 400 single queries (p50 {:.2} ms, p95 {:.2} ms): {}",
        times[200],
        times[380],
        mem()
    );
    for round in 1..=2 {
        let t = std::time::Instant::now();
        embedder
            .embed(texts.clone(), TextKind::Document)
            .await
            .unwrap();
        println!(
            "round {round} ({} texts, {:?}): {}",
            texts.len(),
            t.elapsed(),
            mem()
        );
    }
}
