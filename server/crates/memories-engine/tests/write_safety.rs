//! Task P3-3 against a Qdrant server and the real embedder: a start completes
//! each write of two steps that a stop interrupted after its first step, and
//! two writes to the same key run one after the other.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=.../libonnxruntime.so \
//!   cargo test -p memories-engine --test write_safety -- --ignored

use std::path::PathBuf;
use std::sync::Arc;

use memories_embed::EmbedConfig;
use memories_engine::Engine;
use memories_engine::rank::GraphParams;
use memories_engine::writes::{NewMemory, Patch, UpsertEntry, Writer};
use memories_store::{Condition, Filter, QdrantSettings};
use serde_json::{Map, Value, json};

struct Setup {
    settings: QdrantSettings,
    dir: PathBuf,
}

impl Setup {
    fn new(name: &str) -> Self {
        let url = std::env::var("QDRANT_TEST_URL")
            .expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
        let base = format!("p33_{name}_{}", std::process::id());
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

    /// A start of the server: a new engine (the catalog loads from Qdrant) and
    /// a new writer (the repair runs).
    async fn start(&self) -> Writer {
        let embed = EmbedConfig {
            cache_dir: std::env::var("MEMORIES_TEST_MODEL_CACHE")
                .ok()
                .map(PathBuf::from),
            ..EmbedConfig::default()
        };
        let engine = Engine::open(&self.settings, &self.dir, embed, GraphParams::default())
            .await
            .unwrap();
        Writer::open(Arc::new(engine), self.dir.clone(), 0.97)
            .await
            .unwrap()
    }

    async fn cleanup(self, w: Writer) {
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

async fn add(w: &Writer, text: &str, meta: Option<Value>) -> u64 {
    let metadata = meta.map(|m| m.as_object().cloned().unwrap_or_default());
    let item = NewMemory {
        text: text.into(),
        source: "test/safety".into(),
        metadata,
    };
    w.add_memories(vec![item], false, None).await.unwrap()[0]
}

async fn field(w: &Writer, id: u64, name: &str) -> Option<Value> {
    w.get(id as i64).await.unwrap().field(name)
}

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and the embedding model"]
async fn a_start_completes_a_supersede_after_its_first_step() {
    let s = Setup::new("supersede");
    let w = s.start().await;
    let old = add(&w, "The deploy runs on Fridays.", None).await;
    // The first step: the new record with its `supersedes` link. Then a stop.
    let new = add(
        &w,
        "The deploy runs on Mondays.",
        Some(json!({"supersedes": old})),
    )
    .await;
    let op = json!({"op": "supersede", "old": old, "new": new}).to_string();
    w.engine.state.begin_op(&op).unwrap();
    // A supersede whose first step did not happen changes nothing.
    let lost = json!({"op": "supersede", "old": old, "new": new + 100}).to_string();
    w.engine.state.begin_op(&lost).unwrap();
    // An entry older than the last supersede of the record does not win.
    let stale = json!({"op": "supersede", "old": old, "new": old}).to_string();
    w.engine.state.begin_op(&stale).unwrap();
    drop(w);

    let w = s.start().await;
    assert!(w.engine.state.pending_ops().unwrap().is_empty());
    assert_eq!(field(&w, old, "archived").await, Some(json!(true)));
    assert_eq!(field(&w, old, "superseded_by").await, Some(json!(new)));
    s.cleanup(w).await;
}

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and the embedding model"]
async fn a_start_completes_a_merge_after_its_first_step() {
    let s = Setup::new("merge");
    let w = s.start().await;
    let a = add(&w, "Use pnpm for the web app.", None).await;
    let b = add(&w, "The web app installs with pnpm.", None).await;
    let m = add(&w, "The web app uses pnpm.", None).await;
    let op = json!({"op": "merge", "ids": [a, b], "new": m}).to_string();
    w.engine.state.begin_op(&op).unwrap();
    drop(w);

    let w = s.start().await;
    assert!(w.engine.state.pending_ops().unwrap().is_empty());
    for id in [a, b] {
        assert_eq!(field(&w, id, "archived").await, Some(json!(true)));
        assert_eq!(field(&w, id, "superseded_by").await, None);
    }
    assert_eq!(field(&w, m, "archived").await, None);
    s.cleanup(w).await;
}

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and the embedding model"]
async fn a_start_completes_a_delete_after_its_first_step() {
    let s = Setup::new("delete");
    let w = s.start().await;
    let a = add(&w, "The cache lives in Redis.", None).await;
    let b = add(&w, "Redis runs on port 6379.", None).await;
    w.add_link(b as i64, a as i64, "related_to").await.unwrap();
    let op = json!({"op": "delete", "id": a}).to_string();
    w.engine.state.begin_op(&op).unwrap();
    drop(w);

    let w = s.start().await;
    assert!(w.engine.state.pending_ops().unwrap().is_empty());
    assert!(w.get(a as i64).await.is_err(), "the record is deleted");
    assert_eq!(field(&w, b, "links").await, None);
    s.cleanup(w).await;
}

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and the embedding model"]
async fn completed_writes_leave_no_journal_entry_and_set_last_updated() {
    let s = Setup::new("complete");
    let w = s.start().await;
    assert_eq!(w.engine.state.get_meta("last_updated").unwrap(), None);
    let a = add(&w, "Tests run with pytest.", None).await;
    let first = w.engine.state.get_meta("last_updated").unwrap();
    assert!(
        first.as_deref().is_some_and(|t| t.ends_with("+00:00")),
        "{first:?}"
    );
    let mut last = first.unwrap();
    let mut changed = |w: &Writer, step: &str| {
        let now = w.engine.state.get_meta("last_updated").unwrap().unwrap();
        assert!(now > last, "{step} did not set last_updated");
        last = now;
    };
    let b = add(&w, "Lint runs with ruff.", None).await;
    changed(&w, "add");
    let r = w
        .supersede(a as i64, "Tests run with pytest -q.".into(), "", None, None)
        .await
        .unwrap();
    changed(&w, "supersede");
    let new = r["new_id"].as_i64().unwrap();
    let patch = Patch {
        pinned: Some(false),
        ..Patch::default()
    };
    w.update_memory(b as i64, patch, None, false).await.unwrap();
    changed(&w, "PATCH");
    let m = w
        .merge_memories(
            &[new, b as i64],
            "Checks: pytest -q and ruff.".into(),
            "test/safety",
            None,
        )
        .await
        .unwrap();
    changed(&w, "merge");
    w.delete_memory(m["id"].as_i64().unwrap(), false)
        .await
        .unwrap();
    changed(&w, "delete");
    assert!(w.engine.state.pending_ops().unwrap().is_empty());
    s.cleanup(w).await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs QDRANT_TEST_URL and the embedding model"]
async fn writes_to_the_same_key_run_one_after_the_other() {
    let s = Setup::new("samekey");
    let w = Arc::new(s.start().await);
    let mut tasks = Vec::new();
    for i in 0..20 {
        let w = w.clone();
        tasks.push(tokio::spawn(async move {
            let entry = UpsertEntry {
                text: format!("The release owner is person {i}."),
                source: "test/safety".into(),
                key: "release-owner".into(),
                metadata: Some(Map::new()),
            };
            w.upsert_memory(entry, None).await.unwrap()
        }));
    }
    let mut created = 0;
    for t in tasks {
        if t.await.unwrap()["action"] == "created" {
            created += 1;
        }
    }
    assert_eq!(created, 1, "only the first upsert creates the record");
    let filter = Filter::must([Condition::matches("entity_key", "release-owner".to_owned())]);
    assert_eq!(w.engine.store.count(Some(filter)).await.unwrap(), 1);
    let w = Arc::try_unwrap(w).ok().unwrap();
    s.cleanup(w).await;
}
