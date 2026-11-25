//! Task P3-4 against a Qdrant server and the real embedder: the keyword
//! vector pass changes only the keyword vectors, continues after a forced
//! stop, and does not hold up adds.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 \
//!   cargo test -p memories-engine --test keyword_refresh -- --ignored --nocapture

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use memories_embed::EmbedConfig;
use memories_engine::Engine;
use memories_engine::keyword::doc_vector;
use memories_engine::rank::GraphParams;
use memories_engine::writes::refresh::RefreshRule;
use memories_engine::writes::{NewMemory, Writer};
use memories_store::{QdrantSettings, StoredVectors};
use memories_types::Record;

struct Setup {
    settings: QdrantSettings,
    dir: PathBuf,
}

impl Setup {
    fn new(name: &str) -> Self {
        let url = std::env::var("QDRANT_TEST_URL")
            .expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
        let base = format!("p34_{name}_{}", std::process::id());
        let lookup = |k: &str| match k {
            "QDRANT_URL" => Some(url.clone()),
            "QDRANT_COLLECTION" => Some(base.clone()),
            _ => None,
        };
        let dir = std::env::temp_dir().join(&base);
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Self {
            settings: QdrantSettings::from_lookup(lookup).unwrap(),
            dir,
        }
    }

    async fn start(&self) -> Arc<Writer> {
        let embed = EmbedConfig {
            cache_dir: std::env::var("MEMORIES_TEST_MODEL_CACHE")
                .ok()
                .map(PathBuf::from),
            ..EmbedConfig::default()
        };
        let engine = Engine::open(&self.settings, &self.dir, embed, GraphParams::default())
            .await
            .unwrap();
        Arc::new(
            Writer::open(Arc::new(engine), self.dir.clone(), 0.97)
                .await
                .unwrap(),
        )
    }

    async fn cleanup(self, w: Arc<Writer>) {
        let name = self.settings.v2_collection();
        let client = qdrant_client::Qdrant::from_url(&self.settings.grpc_url)
            .skip_compatibility_check()
            .build()
            .unwrap();
        drop(w);
        let _ = client.delete_collection(&name).await;
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

/// Adds `n` records in batches of 100. `words` sets the text length.
async fn add_many(w: &Writer, n: usize, words: usize, tag: &str) {
    let items: Vec<NewMemory> = (0..n)
        .map(|i| NewMemory {
            text: (0..words)
                .map(|j| format!("{tag}{}w{}", i % 97, (i * 31 + j * 7) % 509))
                .collect::<Vec<_>>()
                .join(" "),
            source: "test/refresh".into(),
            metadata: None,
        })
        .collect();
    for chunk in items.chunks(100) {
        w.add_memories(chunk.to_vec(), false, None).await.unwrap();
    }
}

/// Each record with its vectors.
async fn snapshot(w: &Writer) -> BTreeMap<u64, (Record, StoredVectors)> {
    let store = &w.engine.store;
    let ids = store.ids().await.unwrap();
    let mut vectors = store.vectors(&ids).await.unwrap();
    let mut out = BTreeMap::new();
    for chunk in ids.chunks(500) {
        for r in store.get_many(chunk).await.unwrap() {
            let v = vectors.remove(&r.id).unwrap();
            out.insert(r.id, (r, v));
        }
    }
    out
}

fn current_avgdl(w: &Writer) -> f64 {
    let (sum, n) = w.engine.state.doc_lengths().unwrap();
    sum as f64 / n as f64
}

/// The keyword part of `memories-server verify`: each keyword vector is the
/// vector of its text with the current average.
fn assert_exact(snap: &BTreeMap<u64, (Record, StoredVectors)>, avg: f64) {
    for (id, (r, v)) in snap {
        let want = doc_vector(r.text.as_deref().unwrap_or(""), avg);
        assert_eq!(v.keyword.as_ref(), Some(&want), "record {id}");
    }
}

fn p95(mut v: Vec<Duration>) -> Duration {
    v.sort();
    v[(v.len() * 95).div_ceil(100) - 1]
}

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and the embedding model"]
async fn a_pass_changes_only_keyword_vectors_continues_after_a_stop_and_does_not_hold_up_adds() {
    let s = Setup::new("pass");
    let w = s.start().await;
    let rule = RefreshRule::default();

    // Short records, then long ones: the average grows about 5 times, so the
    // first records have vectors of an old average.
    add_many(&w, 1_000, 6, "s").await;
    assert!(
        w.keyword_refresh_due(&rule).unwrap(),
        "no pass yet, 1,000 records"
    );
    add_many(&w, 300, 120, "l").await;
    let before = snapshot(&w).await;

    // 1. A pass changes only the keyword vectors.
    let ops = w.engine.store.op_counts();
    let report = w.refresh_keywords(&rule).await.unwrap().unwrap();
    let after_ops = w.engine.store.op_counts();
    let changed: BTreeMap<_, _> = after_ops
        .iter()
        .filter(|(k, n)| ops.get(*k) != Some(n))
        .map(|(k, n)| (*k, n - ops.get(k).copied().unwrap_or(0)))
        .collect();
    assert_eq!(report.records, 1_300);
    assert_eq!(report.batches, 13);
    assert_eq!(
        changed,
        BTreeMap::from([("scroll", 13), ("update_keyword_vectors", 13)]),
        "a pass makes only scroll and keyword vector calls"
    );
    let after = snapshot(&w).await;
    assert_eq!(after.len(), before.len());
    for (id, (r, v)) in &after {
        let (r0, v0) = &before[id];
        assert_eq!(r, r0, "payload of {id}");
        assert_eq!(v.dense, v0.dense, "dense vector of {id}");
    }
    let avg = current_avgdl(&w);
    assert_exact(&after, avg);
    let st = w.engine.state.keyword_pass().unwrap();
    assert_eq!((st.avgdl, st.cursor), (Some(avg), None));
    assert!(!w.keyword_refresh_due(&rule).unwrap(), "the pass just ran");

    // 2. A forced stop in the middle of a pass. The pass runs as a task and
    // stops at an await point, as at a kill.
    add_many(&w, 300, 120, "m").await;
    let small = RefreshRule {
        batch: 20,
        ..RefreshRule::default()
    };
    let task = tokio::spawn({
        let w = w.clone();
        let small = small.clone();
        async move { w.refresh_keywords(&small).await }
    });
    let deadline = Instant::now() + Duration::from_secs(60);
    while w.engine.state.keyword_pass().unwrap().cursor.unwrap_or(0) == 0 {
        assert!(Instant::now() < deadline, "the pass did not start");
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
    task.abort();
    let _ = task.await;
    let cursor = w.engine.state.keyword_pass().unwrap().cursor;
    assert!(cursor.is_some(), "the stop came after the end of the pass");
    drop(w);

    let w = s.start().await;
    assert!(
        w.keyword_refresh_due(&rule).unwrap(),
        "a pass that did not complete continues"
    );
    let report = w.refresh_keywords(&small).await.unwrap().unwrap();
    assert!(report.records < 1_600, "the pass continued from the cursor");
    assert_eq!(w.engine.state.keyword_pass().unwrap().cursor, None);
    assert_exact(&snapshot(&w).await, current_avgdl(&w));

    // 3. Adds during passes: the write lock is held for one batch only.
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let passes = tokio::spawn({
        let (w, stop) = (w.clone(), stop.clone());
        async move {
            let mut n = 0;
            while !stop.load(std::sync::atomic::Ordering::Relaxed) {
                w.refresh_keywords(&rule).await.unwrap();
                n += 1;
            }
            n
        }
    });
    let mut times = Vec::new();
    for i in 0..200 {
        let t = Instant::now();
        let item = NewMemory {
            text: format!("an add during a pass number {i}"),
            source: "test/refresh".into(),
            metadata: None,
        };
        w.add_memories(vec![item], false, None).await.unwrap();
        times.push(t.elapsed());
    }
    stop.store(true, std::sync::atomic::Ordering::Relaxed);
    let n = passes.await.unwrap();
    let p = p95(times);
    println!("add p95 during {n} passes: {p:?}");
    assert!(n >= 2, "the adds did not overlap a pass");
    assert!(p <= Duration::from_millis(250), "add p95 {p:?}");
    s.cleanup(w).await;
}
