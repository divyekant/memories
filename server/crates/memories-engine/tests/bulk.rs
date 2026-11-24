//! Task P3-2 against a Qdrant server and the real embedder: the bulk deletes,
//! merge, links, conflict resolution and deduplicate of `Writer`.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=.../libonnxruntime.so \
//!   cargo test -p memories-engine --test bulk -- --ignored

use std::path::PathBuf;
use std::sync::Arc;

use memories_embed::EmbedConfig;
use memories_engine::Engine;
use memories_engine::rank::GraphParams;
use memories_engine::writes::{NewMemory, Patch, WriteError, Writer, source_of};
use memories_store::QdrantSettings;
use serde_json::{Map, Value, json};

struct Setup {
    writer: Writer,
    dir: PathBuf,
}

async fn setup(name: &str) -> Setup {
    let url =
        std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
    let base = format!("p32_{name}_{}", std::process::id());
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
    // A collection left by a failed run would change the IDs.
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

    async fn add_meta(&self, text: &str, source: &str, meta: Value) -> i64 {
        let m: Map<String, Value> = meta.as_object().unwrap().clone();
        let item = NewMemory {
            text: text.into(),
            source: source.into(),
            metadata: Some(m),
        };
        self.writer
            .add_memories(vec![item], false, None)
            .await
            .unwrap()[0] as i64
    }

    async fn pin(&self, id: i64) {
        let patch = Patch {
            pinned: Some(true),
            ..Patch::default()
        };
        self.writer
            .update_memory(id, patch, None, false)
            .await
            .unwrap();
    }

    fn exists(&self, id: i64) -> bool {
        self.writer.engine.catalog().contains(id)
    }

    async fn field(&self, id: i64, name: &str) -> Option<Value> {
        self.writer.get(id).await.unwrap().field(name)
    }
}

fn ids(v: &Value) -> Vec<i64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_i64().unwrap())
        .collect()
}

fn invalid(e: WriteError) -> String {
    match e {
        WriteError::Invalid(m) => m,
        e => panic!("not Invalid: {e:?}"),
    }
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn bulk_deletes_keep_the_python_match_rules() {
    let s = setup("del").await;
    let w = &s.writer;
    let r = s
        .add(&[
            ("Alpha one.", "team/alpha"),
            ("Alpha two.", "team/alpha/sub"),
            ("Alpha three.", "x/team/alpha"),
            ("Beta one.", "team/beta"),
            ("Pinned alpha.", "team/alpha"),
            ("Alphabet.", "team/alphabet"),
        ])
        .await;
    s.pin(r[4]).await;

    // Substring: "team/alpha" in the source.
    let dry = w.delete_by_source("team/alpha", false, true).await.unwrap();
    assert_eq!(ids(&dry["would_delete"]), vec![r[0], r[1], r[2], r[5]]);
    assert_eq!(dry["count"], 4);
    // Prefix: the source starts with "team/alpha" (no "/" boundary).
    let dry = w.delete_by_prefix("team/alpha", false, true).await.unwrap();
    assert_eq!(ids(&dry["would_delete"]), vec![r[0], r[1], r[5]]);
    let none = w.delete_by_prefix("nothing", false, true).await.unwrap();
    assert_eq!(none, json!({"count": 0, "would_delete": []}));
    assert_eq!(
        w.delete_by_prefix("nothing", true, false).await.unwrap(),
        json!({"deleted_count": 0})
    );

    let done = w.delete_by_prefix("team/alpha", true, false).await.unwrap();
    assert_eq!(done, json!({"deleted_count": 3}));
    assert!(!s.exists(r[0]) && !s.exists(r[1]) && !s.exists(r[5]));
    assert!(
        s.exists(r[2]) && s.exists(r[4]),
        "substring match and pinned stay"
    );

    // A snapshot goes into the manifest unless skip_snapshot.
    let done = w.delete_by_source("alpha", false, false).await.unwrap();
    assert_eq!(done, json!({"deleted_count": 1}));
    assert!(!s.exists(r[2]) && s.exists(r[4]) && s.exists(r[3]));
    let manifest: Value =
        serde_json::from_slice(&std::fs::read(s.dir.join("snapshots/manifest.json")).unwrap())
            .unwrap();
    assert_eq!(manifest[0]["reason"], "pre_delete_source");

    // The scoped route deletes listed IDs with no snapshot; pinned IDs stay.
    let r2 = s.add(&[("Gamma.", "team/gamma")]).await;
    let out = w
        .delete_memories_with(&[r2[0], r[4], 999_999], true)
        .await
        .unwrap();
    assert_eq!(ids(&out["deleted_ids"]), vec![r2[0]]);
    assert_eq!(ids(&out["missing_ids"]), vec![999_999]);
    s.cleanup().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn links_merge_and_their_errors() {
    let s = setup("links").await;
    let w = &s.writer;
    let r = s
        .add(&[
            ("Link from.", "team/a"),
            ("Link to.", "team/a"),
            ("Merge one.", "team/m"),
            ("Merge two.", "team/m"),
        ])
        .await;

    let l = w.add_link(r[0], r[1], "related_to").await.unwrap();
    assert_eq!(
        (&l["from_id"], &l["to_id"], &l["type"]),
        (&json!(r[0]), &json!(r[1]), &json!("related_to"))
    );
    assert!(l["created_at"].is_string());
    let links = s.field(r[0], "links").await.unwrap();
    assert_eq!(links[0]["to_id"], r[1]);
    assert_eq!(
        invalid(w.add_link(r[0], r[1], "related_to").await.unwrap_err()),
        format!("Link {} --related_to--> {} already exists", r[0], r[1])
    );
    assert_eq!(
        invalid(w.add_link(r[0], r[1], "likes").await.unwrap_err()),
        "Invalid link type: likes. Valid types: ['blocked_by', 'caused_by', 'reinforces', 'related_to', 'supersedes']"
    );
    assert_eq!(
        invalid(w.add_link(r[0], r[0], "related_to").await.unwrap_err()),
        "A memory cannot link to itself"
    );
    assert_eq!(
        invalid(w.add_link(999_999, r[0], "related_to").await.unwrap_err()),
        "Source memory 999999 not found"
    );
    assert_eq!(
        invalid(w.add_link(r[0], 999_999, "related_to").await.unwrap_err()),
        "Target memory 999999 not found"
    );
    let adj = w.engine.catalog().adjacency();
    assert!(adj.get(r[0]).is_some(), "the catalog has the new link");

    assert_eq!(
        w.remove_link(r[0], r[1], "supersedes").await.unwrap(),
        json!({"removed": false})
    );
    assert_eq!(
        w.remove_link(r[0], r[1], "related_to").await.unwrap(),
        json!({"removed": true})
    );
    assert_eq!(
        s.field(r[0], "links").await,
        None,
        "an empty list goes away"
    );
    assert_eq!(
        w.remove_link(r[0], r[1], "related_to").await.unwrap(),
        json!({"removed": false})
    );
    assert_eq!(
        s.field(r[0], "links").await,
        None,
        "a call that removes nothing adds no empty list"
    );
    assert_eq!(
        w.remove_link(999_999, r[1], "related_to").await.unwrap(),
        json!({"removed": false})
    );

    let m = w
        .merge_memories(&[r[2], r[3]], "Merged.".into(), "team/m", None)
        .await
        .unwrap();
    let new_id = m["id"].as_i64().unwrap();
    assert_eq!(m["archived"], json!([r[2], r[3]]));
    let links = s.field(new_id, "links").await.unwrap();
    assert_eq!(links[0]["to_id"], r[2]);
    assert_eq!(links[1]["to_id"], r[3]);
    assert_eq!(links[1]["type"], "supersedes");
    for id in [r[2], r[3]] {
        assert_eq!(s.field(id, "archived").await, Some(json!(true)));
        assert_eq!(
            s.field(id, "superseded_by").await,
            None,
            "merge sets no pointer"
        );
    }
    let before = w.engine.catalog().len();
    assert_eq!(
        invalid(
            w.merge_memories(&[r[2], r[3], r[2]], "x".into(), "team/m", None)
                .await
                .unwrap_err()
        ),
        format!("Memory {} is listed more than once", r[2])
    );
    assert_eq!(
        w.engine.catalog().len(),
        before,
        "a repeated ID adds no record"
    );
    match w
        .merge_memories(&[r[2], 999_999], "x".into(), "team/m", None)
        .await
    {
        Err(WriteError::NotFound(m)) => assert_eq!(m, "Memory 999999 not found"),
        other => panic!("{other:?}"),
    }
    match w
        .merge_memories(&[r[0], r[1]], "x".into(), "project/p1/notes", None)
        .await
    {
        Err(WriteError::Policy(p)) => assert!(p.0.contains("merge source"), "{}", p.0),
        other => panic!("{other:?}"),
    }
    s.cleanup().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn conflicts_resolve_newest_wins() {
    let s = setup("conf").await;
    let w = &s.writer;
    let old = s
        .add_meta(
            "Port is 80.",
            "team/c",
            json!({"document_at": "2025-01-01T00:00:00Z"}),
        )
        .await;
    let flag = s
        .add_meta(
            "Port is 8080.",
            "team/c",
            json!({"conflicts_with": old, "document_at": "2025-02-01T00:00:00"}),
        )
        .await;
    let other = s.add(&[("Other source.", "team/d")]).await[0];
    let cross = s
        .add_meta("Cross.", "team/c", json!({"conflicts_with": other}))
        .await;
    let orphan = s
        .add_meta("Orphan.", "team/c", json!({"conflicts_with": 999_999}))
        .await;
    let pinned_old = s
        .add_meta(
            "Pinned old.",
            "team/p",
            json!({"document_at": "2024-01-01"}),
        )
        .await;
    s.pin(pinned_old).await;
    let pin_flag = s
        .add_meta("Pin flag.", "team/p", json!({"conflicts_with": pinned_old}))
        .await;

    let dry = w.resolve_conflicts(true, 0).await.unwrap();
    assert_eq!(
        dry["resolved"],
        json!([{"kept": flag, "archived": old, "flagger": flag}])
    );
    assert_eq!(
        dry["needs_review"],
        json!([
            {"id": cross, "conflicts_with": other, "reason": "cross_source"},
            {"id": pin_flag, "conflicts_with": pinned_old, "reason": "pinned_loser"}
        ])
    );
    assert_eq!(
        dry["orphaned"],
        json!([{"id": orphan, "conflicts_with": 999_999, "reason": "missing"}])
    );
    assert_eq!(s.field(flag, "conflicts_with").await, Some(json!(old)));

    let limited = w.resolve_conflicts(true, 1).await.unwrap();
    assert_eq!(limited["resolved_count"], 1);
    assert_eq!(limited["needs_review_count"], 0);

    let done = w.resolve_conflicts(false, 0).await.unwrap();
    assert_eq!(done["resolved"], dry["resolved"]);
    assert_eq!(s.field(old, "archived").await, Some(json!(true)));
    assert_eq!(s.field(old, "superseded_by").await, Some(json!(flag)));
    assert_eq!(s.field(flag, "conflicts_with").await, None);
    let won = s.field(flag, "conflict_resolution").await.unwrap();
    assert_eq!(
        (&won["outcome"], &won["archived"]),
        (&json!("won"), &json!(old))
    );
    assert_eq!(s.field(flag, "links").await.unwrap()[0]["to_id"], old);
    assert_eq!(
        s.field(cross, "conflict_review").await,
        Some(json!("cross_source"))
    );
    assert_eq!(s.field(orphan, "conflicts_with").await, None);
    let o = s.field(orphan, "conflict_resolution").await.unwrap();
    assert_eq!(o["outcome"], "orphaned_missing");
    let again = w.resolve_conflicts(false, 0).await.unwrap();
    assert_eq!(again["resolved_count"], 0);
    assert_eq!(again["needs_review_count"], 2);
    s.cleanup().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs QDRANT_TEST_URL and ORT_DYLIB_PATH"]
async fn deduplicate_removes_the_later_record_of_same_source_pairs() {
    let s = setup("dedup").await;
    let w = &s.writer;
    let r = s
        .add(&[
            ("The gateway timeout is 30 seconds.", "team/a"),
            ("The gateway timeout is 30 seconds!", "team/a"),
            ("The gateway timeout is 30 seconds.", "team/b"),
            ("Bananas are yellow fruit.", "team/a"),
        ])
        .await;
    let dry = w.deduplicate(0.9, true).await.unwrap();
    assert_eq!(dry["duplicate_pairs"], 1, "{dry}");
    assert_eq!(dry["would_remove"], 1);
    let pair = &dry["pairs"][0];
    assert_eq!((&pair["id_a"], &pair["id_b"]), (&json!(r[0]), &json!(r[1])));
    assert_eq!(pair["source"], "team/a");
    assert!(pair["similarity"].as_f64().unwrap() >= 0.9);
    assert!(s.exists(r[1]));

    let done = w.deduplicate(0.9, false).await.unwrap();
    assert_eq!(
        done,
        json!({"duplicate_pairs": 1, "removed": 1, "remaining": 3, "dry_run": false})
    );
    assert!(!s.exists(r[1]) && s.exists(r[0]) && s.exists(r[2]));
    assert_eq!(
        w.deduplicate(0.9, true).await.unwrap(),
        json!({"duplicate_pairs": 0, "removed": 0, "dry_run": true})
    );
    let src = source_of(&w.get(r[2]).await.unwrap());
    assert_eq!(src, "team/b");
    s.cleanup().await;
}
