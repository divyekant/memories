//! Prints the memory of the embedder (VmRSS and VmHWM from /proc, Linux only).
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
    for _ in 0..200 {
        embedder
            .embed(vec!["a short search query".into()], TextKind::Query)
            .await
            .unwrap();
    }
    println!("after 200 single queries: {}", mem());
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
