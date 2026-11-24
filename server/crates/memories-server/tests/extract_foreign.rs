//! An extract decision that picks a memory of another source keeps the fact
//! as a new memory (`EXTRACT_FOREIGN_TARGET_ADD`), against a Qdrant server and
//! the real embedder.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=.../libonnxruntime.so \
//!   cargo test -p memories-server --test extract_foreign -- --ignored

use std::path::PathBuf;
use std::sync::Arc;

use memories_embed::EmbedConfig;
use memories_engine::Engine;
use memories_engine::policy::TrustedAuthorship;
use memories_engine::rank::GraphParams;
use memories_engine::writes::{NewMemory, Writer};
use memories_server::pipeline::Exec;
use memories_store::QdrantSettings;
use serde_json::{Map, Value, json};

async fn writer(name: &str) -> (Writer, Arc<Engine>, PathBuf) {
    let url =
        std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
    let base = format!("xf_{name}_{}", std::process::id());
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
    let _ = engine.store.delete_collection().await;
    engine.store.ensure_collection(engine.dim).await.unwrap();
    let engine = Arc::new(engine);
    let writer = Writer::open(engine.clone(), dir.clone(), 0.97)
        .await
        .unwrap();
    (writer, engine, dir)
}

async fn add(w: &Writer, text: &str, source: &str) -> i64 {
    let item = NewMemory {
        text: text.into(),
        source: source.into(),
        metadata: None,
    };
    w.add_memories(vec![item], false, None).await.unwrap()[0] as i64
}

fn actions(v: Value) -> Vec<Map<String, Value>> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|a| a.as_object().unwrap().clone())
        .collect()
}

#[tokio::test]
#[ignore = "needs a Qdrant server and the embedder: set QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn a_foreign_target_keeps_the_fact_as_a_new_memory() {
    let (w, engine, dir) = writer("keep").await;
    let other = add(&w, "The other project deploys on Fridays.", "other/project").await;
    let mine = add(&w, "This project deploys on Mondays.", "eval/mine").await;
    let facts = vec![
        json!({"text": "This project now deploys on Tuesdays.", "category": "decision"}),
        json!({"text": "Deploys need a second reviewer.", "category": "decision"}),
        json!({"text": "Deploys now happen on Wednesdays.", "category": "decision"}),
    ];
    let decisions = actions(json!([
        {"action": "UPDATE", "fact_index": 0, "old_id": other,
         "new_text": "The other project deploys on Fridays. This project deploys on Tuesdays."},
        {"action": "CONFLICT", "fact_index": 1, "old_id": other},
        {"action": "UPDATE", "fact_index": 2, "old_id": mine},
    ]));
    let exec = |foreign_add| Exec {
        writer: &w,
        source: "eval/mine",
        allowed: None,
        trusted: None,
        job_id: None,
        document_at: None,
        gate: false,
        threshold: 0.0,
        foreign_add,
    };

    // Python: each foreign target is an error and the fact is lost.
    let before = engine.catalog().len();
    let r = exec(false).run(&decisions[..2], &facts).await.unwrap();
    let errors: Vec<_> = r["actions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a["error"].as_str().unwrap_or_default().to_owned())
        .collect();
    assert_eq!(
        errors,
        [
            format!("old_id not authorized for update: {other}"),
            format!("old_id not authorized for conflict: {other}"),
        ]
    );
    assert_eq!(engine.catalog().len(), before);

    let r = exec(true).run(&decisions, &facts).await.unwrap();
    let acts = r["actions"].as_array().unwrap();
    assert_eq!(acts.len(), 3, "{r}");
    for (a, fact) in acts[..2].iter().zip(&facts) {
        assert_eq!(a["action"], "fallback_add", "{a}");
        assert_eq!(a["foreign_old_id"], other);
        // The fact text, never the merged new_text with the other source's words.
        assert_eq!(a["text"], fact["text"]);
        let rec = w.get(a["id"].as_i64().unwrap()).await.unwrap();
        assert_eq!(rec.source.as_deref(), Some("eval/mine"));
        assert_eq!(rec.text.as_deref(), fact["text"].as_str());
        assert!(rec.field("supersedes").is_none() && rec.field("conflicts_with").is_none());
    }
    assert_eq!(
        acts[2]["action"], "update",
        "a same-source update stays an update"
    );
    assert_eq!(
        (r["stored_count"].clone(), r["fallback_count"].clone()),
        (json!(2), json!(0)),
        "fallback_count stays the count of AUDN failures"
    );
    assert_eq!(r["updated_count"], 1);
    let old = w.get(other).await.unwrap();
    assert_eq!(
        old.text.as_deref(),
        Some("The other project deploys on Fridays.")
    );
    assert!(old.field("archived").is_none_or(|v| v != json!(true)));

    // A project job: the policy check reads the fact text that the fallback
    // writes, not a `new_text` with a credential of the other source.
    let secret = add(&w, "The CI key is AKIAABCDEFGHIJKLMNOP.", "other/ci").await;
    let author = TrustedAuthorship::principal("dk", "claude-code").unwrap();
    let facts = vec![json!({"text": "CI keys rotate each month.", "category": "decision"})];
    let decisions = actions(
        json!([{"action": "UPDATE", "fact_index": 0, "old_id": secret,
        "new_text": "The CI key is AKIAABCDEFGHIJKLMNOP. CI keys rotate each month."}]),
    );
    let project = |foreign_add| Exec {
        source: "project/a/decisions",
        trusted: Some(&author),
        ..exec(foreign_add)
    };
    assert!(project(false).run(&decisions, &facts).await.is_err());
    let r = project(true).run(&decisions, &facts).await.unwrap();
    let a = &r["actions"][0];
    assert_eq!(
        (&a["action"], &a["foreign_old_id"]),
        (&json!("fallback_add"), &json!(secret)),
        "{r}"
    );
    let rec = w.get(a["id"].as_i64().unwrap()).await.unwrap();
    assert_eq!(rec.text.as_deref(), Some("CI keys rotate each month."));
    assert_eq!(rec.source.as_deref(), Some("project/a/decisions"));

    let _ = engine.store.delete_collection().await;
    let _ = std::fs::remove_dir_all(&dir);
}
