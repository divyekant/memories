//! Task P5-3 against a Qdrant server and the real embedder: the maintenance
//! jobs of `Writer`.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=.../libonnxruntime.so \
//!   cargo test -p memories-engine --test maintenance -- --ignored

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;

use memories_embed::EmbedConfig;
use memories_engine::Engine;
use memories_engine::rank::{GraphParams, PyDateTime};
use memories_engine::writes::maintenance::{CHANGED, Profiles};
use memories_engine::writes::{NewMemory, Patch, Writer};
use memories_store::QdrantSettings;
use serde_json::{Map, Value, json};

struct Setup {
    writer: Writer,
    dir: PathBuf,
}

async fn setup(name: &str) -> Setup {
    let url =
        std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
    let base = format!("p53_{name}_{}", std::process::id());
    let lookup = |k: &str| match k {
        "QDRANT_URL" => Some(url.clone()),
        "QDRANT_COLLECTION" => Some(base.clone()),
        _ => None,
    };
    let settings = QdrantSettings::from_lookup(lookup).unwrap();
    let dir = std::env::temp_dir().join(&base);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let embed = EmbedConfig {
        cache_dir: std::env::var("MEMORIES_TEST_MODEL_CACHE")
            .ok()
            .map(PathBuf::from),
        ..EmbedConfig::default()
    };
    let engine = Engine::open(&settings, &dir, embed, GraphParams::default())
        .await
        .unwrap();
    engine.store.delete_collection().await.unwrap();
    engine.store.ensure_collection(engine.dim).await.unwrap();
    engine.load_catalog().await.unwrap();
    let writer = Writer::open(Arc::new(engine), dir.clone(), 0.97)
        .await
        .unwrap();
    Setup { writer, dir }
}

impl Setup {
    async fn cleanup(self) {
        let _ = self.writer.engine.store.delete_collection().await;
        let _ = std::fs::remove_dir_all(&self.dir);
    }

    async fn add(&self, items: &[(&str, &str)]) -> Vec<i64> {
        let items = items
            .iter()
            .map(|(t, s)| NewMemory {
                text: (*t).into(),
                source: (*s).into(),
                metadata: None,
            })
            .collect();
        let ids = self.writer.add_memories(items, false, None).await.unwrap();
        ids.into_iter().map(|i| i as i64).collect()
    }

    /// Sets stored fields with no catalog change (old dates for the tests).
    async fn set(&self, id: i64, fields: Value) {
        let m: Map<String, Value> = fields.as_object().unwrap().clone();
        self.writer
            .engine
            .store
            .set_payload(&[id as u64], m)
            .await
            .unwrap();
    }

    async fn field(&self, id: i64, name: &str) -> Option<Value> {
        self.writer.get(id).await.unwrap().field(name)
    }
}

const OLD: &str = "2025-01-01T00:00:00+00:00";

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn enforce_policies_archives_with_evidence() {
    let s = setup("enforce").await;
    let ids = s
        .add(&[
            ("Old wip note one.", "wip/a/notes"),
            ("Old wip note two.", "wip/a/notes"),
            ("Old learning.", "learning/a/x"),
            ("New wip note.", "wip/a/notes"),
        ])
        .await;
    for &id in &ids[..3] {
        s.set(id, json!({"updated_at": OLD, "created_at": OLD}))
            .await;
    }
    let pin = Patch {
        pinned: Some(true),
        ..Patch::default()
    };
    s.writer
        .update_memory(ids[1], pin, None, false)
        .await
        .unwrap();
    s.set(ids[1], json!({"updated_at": OLD})).await;
    let profiles = Profiles::from_value(json!({
        "wip/": {"ttl_days": 30, "_explicit_fields": ["ttl_days", "source_prefix"]},
    }));
    let now = PyDateTime::now_utc();
    let dry = s
        .writer
        .enforce_policies(true, &profiles, &now)
        .await
        .unwrap();
    assert_eq!(dry["summary"]["would_archive"], 1);
    assert_eq!(dry["summary"]["excluded_pinned"], 1);
    assert_eq!(dry["summary"]["candidates_scanned"], 3);
    assert_eq!(dry["actions"][0]["memory_id"], ids[0]);
    assert_eq!(dry["actions"][0]["action"], "would_archive");
    assert!(s.field(ids[0], "archived").await.is_none());

    let done = s
        .writer
        .enforce_policies(false, &profiles, &now)
        .await
        .unwrap();
    assert_eq!(done["summary"]["archived"], 1);
    assert_eq!(done["summary"]["by_rule"]["ttl"], 1);
    assert_eq!(s.field(ids[0], "archived").await, Some(json!(true)));
    assert_eq!(
        s.field(ids[0], "_policy_archived_policy").await,
        Some(json!("wip/a/notes ttl"))
    );
    assert!(
        s.writer
            .engine
            .catalog()
            .get(ids[0])
            .is_some_and(|e| e.archived)
    );
    let again = s
        .writer
        .enforce_policies(true, &profiles, &now)
        .await
        .unwrap();
    assert_eq!(again["summary"]["excluded_already_archived"], 1);
    s.cleanup().await;
}

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn consolidation_finds_clusters_and_replaces_them() {
    let s = setup("consolidate").await;
    let ids = s
        .add(&[
            (
                "The deploy pipeline runs on GitHub Actions.",
                "claude-code/a/ops",
            ),
            (
                "The deploy pipeline runs on GitHub Actions workflows.",
                "claude-code/a/ops",
            ),
            (
                "Our deploy pipeline runs on GitHub Actions.",
                "claude-code/a/ops",
            ),
            ("Bananas are yellow fruit.", "claude-code/a/food"),
        ])
        .await;
    let clusters = s.writer.consolidation_clusters("", 90, 1).await.unwrap();
    assert_eq!(clusters.len(), 1);
    let members: HashSet<i64> = clusters[0]
        .iter()
        .map(|m| m["id"].as_i64().unwrap())
        .collect();
    assert_eq!(members, ids[..3].iter().copied().collect());
    assert!(
        s.writer
            .consolidation_clusters("other/", 90, 1)
            .await
            .unwrap()
            .is_empty()
    );

    // A member that changed after the scan stops the replacement.
    let mut stale = clusters[0].clone();
    stale[1]["text"] = json!("Something else.");
    let r = s
        .writer
        .replace_consolidation_cluster(&stale, &["x".into()], "claude-code/a/ops", "detail")
        .await
        .unwrap();
    assert_eq!(r["error"], CHANGED);
    assert_eq!(s.writer.engine.count(), 4);

    let texts = vec!["The deploy pipeline runs on GitHub Actions.".to_string()];
    let r = s
        .writer
        .replace_consolidation_cluster(&clusters[0], &texts, "claude-code/a/ops", "detail")
        .await
        .unwrap();
    let added = r["added_ids"][0].as_i64().unwrap();
    assert_eq!(s.writer.engine.count(), 2);
    assert!(
        ids[..3]
            .iter()
            .all(|&i| !s.writer.engine.catalog().contains(i))
    );
    assert_eq!(s.field(added, "author").await, Some(json!("system")));
    assert_eq!(s.field(added, "category").await, Some(json!("detail")));
    let from = s.field(added, "consolidated_from").await.unwrap();
    let from: HashSet<i64> = from
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_i64().unwrap())
        .collect();
    assert_eq!(from, ids[..3].iter().copied().collect());
    s.cleanup().await;
}

#[tokio::test]
#[ignore = "needs QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn compact_prune_and_reembed() {
    let s = setup("compact").await;
    let ids = s
        .add(&[
            ("Parity upsert batch 1.", "learning/a/x"),
            ("Parity upsert batch 2.", "learning/a/x"),
            ("Cats sleep a lot during the day.", "learning/a/y"),
        ])
        .await;
    let clusters = s.writer.similar_clusters(0.95).await.unwrap();
    assert_eq!(clusters, vec![vec![ids[0], ids[1]]]);

    s.set(ids[2], json!({"created_at": OLD})).await;
    let unretrieved: HashSet<i64> = ids.iter().copied().collect();
    let now = PyDateTime::now_utc();
    let c = s
        .writer
        .prune_candidates(&unretrieved, &now, 90)
        .await
        .unwrap();
    assert_eq!(c, vec![ids[2]]);

    let before = s
        .writer
        .engine
        .store
        .vectors(&[ids[0] as u64])
        .await
        .unwrap();
    assert_eq!(s.writer.reembed().await.unwrap(), 3);
    let after = s
        .writer
        .engine
        .store
        .vectors(&[ids[0] as u64])
        .await
        .unwrap();
    // The same model gives the same vectors; the keyword vector stays.
    assert_eq!(before, after);
    assert_eq!(
        s.field(ids[2], "created_at").await,
        Some(json!(OLD)),
        "reembed keeps the payload"
    );
    s.cleanup().await;
}
