//! Task P3-3 benchmark: the latency of an add at 100,000 and at 300,000
//! records, and the search latency with and without a constant write load.
//!
//! Run, from `server/`:
//!   QDRANT_TEST_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=.../libonnxruntime.so \
//!     cargo bench -p memories-engine --bench write_load
//!
//! `BENCH_SIZES` (default `100000,300000`) sets the corpus sizes and
//! `BENCH_SAMPLES` (default 200) the samples of each measure. The run fails if
//! the add p95 is more than 250 ms at a size (exit check of P3-3).

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use memories_embed::{EmbedConfig, TextKind};
use memories_engine::Engine;
use memories_engine::keyword::{doc_tokens, doc_vector};
use memories_engine::rank::GraphParams;
use memories_engine::search::SearchArgs;
use memories_engine::writes::{NewMemory, Writer};
use memories_store::{NewPoint, QdrantSettings};
use memories_types::Record;
use qdrant_client::Qdrant;
use serde_json::{Map, json};

const ADD_P95_LIMIT_MS: f64 = 250.0;
const POOL: usize = 2_000;
const WORDS: &[&str] = &[
    "deploy",
    "server",
    "cache",
    "token",
    "release",
    "branch",
    "review",
    "test",
    "client",
    "query",
    "index",
    "vector",
    "backup",
    "schema",
    "worker",
    "queue",
    "config",
    "metric",
    "alert",
    "build",
    "docker",
    "python",
    "rust",
    "memory",
    "search",
    "project",
    "owner",
    "decision",
    "rollback",
    "latency",
    "budget",
    "contract",
    "sprint",
    "incident",
    "migration",
    "storage",
    "network",
];

/// A small xorshift generator: the benchmark needs no random crate.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
    fn unit(&mut self) -> f32 {
        (self.next() % 2_000_001) as f32 / 1_000_000.0 - 1.0
    }
    fn text(&mut self) -> String {
        let n = 8 + self.below(30);
        (0..n)
            .map(|_| WORDS[self.below(WORDS.len())])
            .collect::<Vec<_>>()
            .join(" ")
    }
}

fn pct(v: &mut [f64], p: f64) -> f64 {
    v.sort_by(f64::total_cmp);
    v[((v.len() as f64 * p).ceil() as usize).clamp(1, v.len()) - 1]
}

fn row(name: &str, v: &mut [f64]) -> f64 {
    let p95 = pct(v, 0.95);
    println!(
        "| {name} | {} | {:.1} | {:.1} | {:.1} | {:.1} |",
        v.len(),
        pct(v, 0.50),
        p95,
        pct(v, 0.99),
        v[v.len() - 1]
    );
    p95
}

async fn open(settings: &QdrantSettings, dir: &Path) -> Writer {
    let embed = EmbedConfig {
        cache_dir: std::env::var("MEMORIES_TEST_MODEL_CACHE")
            .ok()
            .map(PathBuf::from),
        ..EmbedConfig::default()
    };
    let engine = Engine::open(settings, dir, embed, GraphParams::default())
        .await
        .unwrap();
    Writer::open(Arc::new(engine), dir.to_path_buf(), 0.97)
        .await
        .unwrap()
}

/// Seeds `size` records: each one is a real embedding of a pool text with a
/// small change, so the vectors spread as real records do.
async fn seed(w: &Writer, size: usize, rng: &mut Rng) {
    let pool_texts: Vec<String> = (0..POOL).map(|_| rng.text()).collect();
    let pool = w
        .engine
        .embedder
        .embed(pool_texts.clone(), TextKind::Document)
        .await
        .unwrap();
    let ids = w.engine.state.reserve_ids(size as u64).unwrap();
    let avg = 23.0;
    let mut batch = Vec::with_capacity(1_000);
    for id in ids {
        let k = rng.below(POOL);
        let text = format!("{} {}", pool_texts[k], WORDS[rng.below(WORDS.len())]);
        let mut dense: Vec<f32> = pool[k].iter().map(|x| x + 0.02 * rng.unit()).collect();
        let norm = dense.iter().map(|x| x * x).sum::<f32>().sqrt();
        dense.iter_mut().for_each(|x| *x /= norm);
        let mut p = Map::new();
        p.insert("text".into(), json!(text));
        p.insert("source".into(), json!(format!("bench/s{}", id % 50)));
        for f in ["created_at", "updated_at", "timestamp"] {
            p.insert(f.into(), json!("2026-11-16T00:00:00+00:00"));
        }
        let keyword = doc_vector(&text, avg);
        batch.push(NewPoint {
            record: Record::from_payload(id, p),
            dense,
            keyword,
        });
        if batch.len() == 1_000 {
            w.engine
                .store
                .upsert(std::mem::take(&mut batch), w.engine.dim)
                .await
                .unwrap();
        }
    }
    w.engine.store.upsert(batch, w.engine.dim).await.unwrap();
}

async fn wait_green(client: &Qdrant, name: &str) {
    loop {
        let info = client.collection_info(name).await.unwrap();
        let r = info.result.unwrap();
        // 1 = green: the optimizer has built the index.
        if r.status == 1 {
            return;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn timed_search(w: &Writer, rng: &mut Rng) -> f64 {
    let args = SearchArgs {
        query: rng.text(),
        k: 5,
        ..SearchArgs::default()
    };
    let t = Instant::now();
    w.engine.search(&args, true).await.unwrap();
    t.elapsed().as_secs_f64() * 1e3
}

async fn timed_add(w: &Writer, rng: &mut Rng, dedupe: bool) -> f64 {
    let item = NewMemory {
        text: rng.text(),
        source: "bench/add".into(),
        metadata: None,
    };
    let t = Instant::now();
    w.add_memories(vec![item], dedupe, None).await.unwrap();
    t.elapsed().as_secs_f64() * 1e3
}

#[tokio::main]
async fn main() {
    let url = std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL");
    let sizes: Vec<usize> = std::env::var("BENCH_SIZES")
        .unwrap_or_else(|_| "100000,300000".into())
        .split(',')
        .map(|s| s.trim().parse().unwrap())
        .collect();
    let samples: usize = std::env::var("BENCH_SAMPLES").map_or(200, |s| s.parse().unwrap());
    let mut failed = false;
    for size in sizes {
        let base = format!("p33_bench_{size}_{}", std::process::id());
        let lookup = |k: &str| match k {
            "QDRANT_URL" => Some(url.clone()),
            "QDRANT_COLLECTION" => Some(base.clone()),
            _ => None,
        };
        let settings = QdrantSettings::from_lookup(lookup).unwrap();
        let client = Qdrant::from_url(&settings.grpc_url)
            .skip_compatibility_check()
            .build()
            .unwrap();
        let name = settings.v2_collection();
        let _ = client.delete_collection(&name).await;
        let dir = std::env::temp_dir().join(&base);
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mut rng = Rng(0x9E37_79B9_7F4A_7C15 ^ size as u64);

        let t = Instant::now();
        let w = open(&settings, &dir).await;
        seed(&w, size, &mut rng).await;
        drop(w);
        wait_green(&client, &name).await;
        let seeded = t.elapsed().as_secs_f64();
        let t = Instant::now();
        let w = Arc::new(open(&settings, &dir).await);
        let started = t.elapsed().as_secs_f64();
        assert_eq!(w.engine.count(), size);
        let (sum, n) = w.engine.state.doc_lengths().unwrap();
        assert_eq!(n as usize, size);
        assert!(sum > 0 && doc_tokens("a b").len() == 2);

        println!("\n## {size} records (seed and index {seeded:.0} s, start {started:.1} s)\n");
        println!("| Measure | Samples | p50 ms | p95 ms | p99 ms | max ms |");
        println!("|---|---|---|---|---|---|");
        for _ in 0..10 {
            timed_add(&w, &mut rng, false).await; // warm-up
        }
        let mut v = Vec::new();
        for _ in 0..samples {
            v.push(timed_add(&w, &mut rng, false).await);
        }
        let p95 = row("Add", &mut v);
        let mut v = Vec::new();
        for _ in 0..samples {
            v.push(timed_add(&w, &mut rng, true).await);
        }
        let p95_dedupe = row("Add with `deduplicate`", &mut v);
        let mut v = Vec::new();
        for _ in 0..samples {
            v.push(timed_search(&w, &mut rng).await);
        }
        row("Search, no write load", &mut v);

        // A constant write load: one client adds records with no pause.
        let stop = Arc::new(AtomicBool::new(false));
        let load = {
            let (w, stop) = (w.clone(), stop.clone());
            tokio::spawn(async move {
                let mut rng = Rng(42);
                let mut lat = Vec::new();
                while !stop.load(Ordering::Relaxed) {
                    lat.push(timed_add(&w, &mut rng, false).await);
                }
                lat
            })
        };
        let mut v = Vec::new();
        for _ in 0..samples {
            v.push(timed_search(&w, &mut rng).await);
        }
        stop.store(true, Ordering::Relaxed);
        row("Search, constant write load", &mut v);
        let mut lat = load.await.unwrap();
        row("Add, during the searches", &mut lat);

        for (name, p) in [("Add", p95), ("Add with deduplicate", p95_dedupe)] {
            if p > ADD_P95_LIMIT_MS {
                println!(
                    "FAIL: {name} p95 {p:.1} ms at {size} records is more than {ADD_P95_LIMIT_MS} ms"
                );
                failed = true;
            }
        }
        drop(w);
        let _ = client.delete_collection(&name).await;
        let _ = std::fs::remove_dir_all(&dir);
    }
    if failed {
        std::process::exit(1);
    }
}
