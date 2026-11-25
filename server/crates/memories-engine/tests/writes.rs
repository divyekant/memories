//! Exit check of task P3-1 against a Qdrant server and the real embedder:
//! a write makes the same Qdrant calls at each corpus size, and it changes
//! only the points of the records that it affects.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 \
//!   cargo test -p memories-engine --test writes -- --ignored

// The step macro updates the counters after the last step too.
#![allow(unused_assignments)]
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::PathBuf;
use std::sync::Arc;

use memories_embed::EmbedConfig;
use memories_engine::Engine;
use memories_engine::rank::GraphParams;
use memories_engine::writes::{NewMemory, Patch, UpsertEntry, Writer};
use memories_store::QdrantSettings;
use qdrant_client::Qdrant;
use qdrant_client::qdrant::ScrollPointsBuilder;
use qdrant_client::qdrant::vectors_output::VectorsOptions;
use serde_json::{Value, json};

struct Setup {
    writer: Writer,
    client: Qdrant,
    collection: String,
    dir: PathBuf,
}

async fn setup(name: &str) -> Setup {
    let url =
        std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
    let base = format!("p31_{name}_{}", std::process::id());
    let lookup = |k: &str| match k {
        "QDRANT_URL" => Some(url.clone()),
        "QDRANT_COLLECTION" => Some(base.clone()),
        _ => None,
    };
    let settings = QdrantSettings::from_lookup(lookup).unwrap();
    let collection = settings.v2_collection();
    let client = Qdrant::from_url(&settings.grpc_url)
        .skip_compatibility_check()
        .build()
        .unwrap();
    let _ = client.delete_collection(&collection).await;
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
    let writer = Writer::open(Arc::new(engine), dir.clone(), 0.97)
        .await
        .unwrap();
    Setup {
        writer,
        client,
        collection,
        dir,
    }
}

impl Setup {
    async fn cleanup(self) {
        let _ = self.client.delete_collection(&self.collection).await;
        let _ = std::fs::remove_dir_all(&self.dir);
    }

    /// Each point as text: the payload and the vectors.
    async fn dump(&self) -> BTreeMap<u64, String> {
        let mut out = BTreeMap::new();
        let mut offset = None;
        loop {
            let mut req = ScrollPointsBuilder::new(&self.collection)
                .limit(500)
                .with_payload(true)
                .with_vectors(true);
            if let Some(o) = offset.take() {
                req = req.offset(o);
            }
            let r = self.client.scroll(req).await.unwrap();
            for p in r.result {
                let id = match p.id.as_ref().and_then(|i| i.point_id_options.clone()) {
                    Some(qdrant_client::qdrant::point_id::PointIdOptions::Num(n)) => n,
                    _ => panic!("uuid point"),
                };
                let payload: BTreeMap<_, _> = p
                    .payload
                    .into_iter()
                    .map(|(k, v)| (k, v.into_json().to_string()))
                    .collect();
                // Maps print in a random order: sort the named vectors.
                let vectors: BTreeMap<String, String> =
                    match p.vectors.and_then(|v| v.vectors_options) {
                        Some(VectorsOptions::Vectors(named)) => named
                            .vectors
                            .into_iter()
                            .map(|(k, v)| (k, format!("{v:?}")))
                            .collect(),
                        other => BTreeMap::from([(String::new(), format!("{other:?}"))]),
                    };
                out.insert(id, format!("{payload:?} {vectors:?}"));
            }
            match r.next_page_offset {
                Some(o) => offset = Some(o),
                None => return out,
            }
        }
    }

    fn ops(&self) -> BTreeMap<&'static str, u64> {
        self.writer.engine.store.op_counts()
    }
}

fn changed(before: &BTreeMap<u64, String>, after: &BTreeMap<u64, String>) -> BTreeSet<u64> {
    let ids: BTreeSet<u64> = before.keys().chain(after.keys()).copied().collect();
    ids.into_iter()
        .filter(|id| before.get(id) != after.get(id))
        .collect()
}

fn delta(
    before: &BTreeMap<&'static str, u64>,
    after: &BTreeMap<&'static str, u64>,
) -> BTreeMap<&'static str, u64> {
    after
        .iter()
        .map(|(k, v)| (*k, v - before.get(k).copied().unwrap_or(0)))
        .filter(|(_, v)| *v > 0)
        .collect()
}

fn mem(text: &str, source: &str) -> NewMemory {
    NewMemory {
        text: text.into(),
        source: source.into(),
        metadata: None,
    }
}

/// Runs a fixed list of writes on a corpus of `n` records. Returns the Qdrant
/// calls of each write. Checks that each write changes only its records.
async fn run(n: usize) -> Vec<(&'static str, BTreeMap<&'static str, u64>)> {
    let s = setup(&format!("n{n}")).await;
    let w = &s.writer;
    let corpus: Vec<NewMemory> = (0..n)
        .map(|i| {
            mem(
                &format!("Corpus fact {i}: service s{} uses port {}", i % 9, 4000 + i),
                &format!("test/corpus/{}", i % 5),
            )
        })
        .collect();
    let base = w.add_memories(corpus, false, None).await.unwrap();
    assert_eq!(base.len(), n);
    let mut calls = Vec::new();
    let mut ops = s.ops();
    let mut points = s.dump().await;

    macro_rules! step {
        ($name:expr, $expect:expr, $body:expr) => {{
            let out = $body;
            let after = s.dump().await;
            let expect: BTreeSet<u64> = $expect(&out).into_iter().collect();
            for id in changed(&points, &after).difference(&expect) {
                eprintln!(
                    "{id} before: {:?}\n{id} after: {:?}",
                    points.get(id),
                    after.get(id)
                );
            }
            assert_eq!(changed(&points, &after), expect, "{} at n={n}", $name);
            points = after;
            let now = s.ops();
            calls.push(($name, delta(&ops, &now)));
            ops = now;
            out
        }};
    }

    let a = step!(
        "add",
        |ids: &Vec<u64>| ids.clone(),
        w.add_memories(
            vec![mem(
                "A new fact about the gateway timeout of 30 s.",
                "test/new"
            )],
            false,
            None
        )
        .await
        .unwrap()
    );
    let a = a[0];
    step!(
        "add-dedupe-skip",
        |_: &Vec<u64>| Vec::<u64>::new(),
        w.add_memories(
            vec![mem(
                "A new fact about the gateway timeout of 30 s.",
                "test/new"
            )],
            true,
            None
        )
        .await
        .unwrap()
    );
    step!(
        "patch-text",
        |_: &Value| vec![a],
        w.update_memory(
            a as i64,
            Patch {
                text: Some("The gateway timeout is 45 s.".into()),
                ..Patch::default()
            },
            None,
            false
        )
        .await
        .unwrap()
    );
    step!(
        "patch-meta-pin",
        |_: &Value| vec![a],
        w.update_memory(
            a as i64,
            Patch {
                metadata_patch: Some(json!({"owner": "ops"}).as_object().unwrap().clone()),
                pinned: Some(true),
                ..Patch::default()
            },
            None,
            false
        )
        .await
        .unwrap()
    );
    let old = base[3];
    let sup = step!(
        "supersede",
        |r: &Value| vec![old, r["new_id"].as_u64().unwrap()],
        w.supersede(
            old as i64,
            "Corpus fact 3 changed: port 9999".into(),
            "",
            None,
            None
        )
        .await
        .unwrap()
    );
    let new_id = sup["new_id"].as_u64().unwrap();
    let up = step!(
        "upsert-create",
        |r: &Value| vec![r["id"].as_u64().unwrap()],
        w.upsert_memory(
            UpsertEntry {
                text: "Upsert value one.".into(),
                source: "test/upsert".into(),
                key: "k1".into(),
                metadata: None
            },
            None
        )
        .await
        .unwrap()
    );
    step!(
        "upsert-update",
        |_: &Value| vec![up["id"].as_u64().unwrap()],
        w.upsert_memory(
            UpsertEntry {
                text: "Upsert value two.".into(),
                source: "test/upsert".into(),
                key: "k1".into(),
                metadata: None
            },
            None
        )
        .await
        .unwrap()
    );
    // The new record links to `old`: the delete removes that link.
    step!(
        "delete-linked",
        |_: &Value| vec![old, new_id],
        w.delete_memory(old as i64, false).await.unwrap()
    );
    let pinned = w.delete_memory(a as i64, false).await;
    assert!(pinned.is_err(), "a pinned record needs force");
    let batch = vec![base[5] as i64, base[6] as i64, 999_999];
    step!(
        "delete-batch",
        |_: &Value| vec![base[5], base[6]],
        w.delete_memories(&batch).await.unwrap()
    );

    // The length sums and the catalog agree with the store.
    let (sum, count) = w.engine.state.doc_lengths().unwrap();
    assert_eq!(count as usize, points.len());
    assert_eq!(w.engine.count(), points.len());
    let mut expect_sum = 0;
    for id in points.keys() {
        let r = w.engine.store.get(*id).await.unwrap().unwrap();
        expect_sum += r.text.unwrap().to_lowercase().split_whitespace().count() as u64;
    }
    assert_eq!(sum, expect_sum);

    let kinds: Vec<&str> = w.events.recent(100).iter().map(|e| e.event_type).collect();
    let tail = &kinds[kinds.len() - 8..];
    assert_eq!(
        tail,
        [
            "memory.added",
            "memory.updated",
            "memory.updated",
            "memory.added",
            "memory.linked",
            "memory.added",
            "memory.updated",
            "memory.deleted"
        ]
    );
    // The delete removed the link to `old` from the link map too.
    assert!(w.engine.catalog().ids_linking_to(old as i64).is_empty());

    // Doctrine: an identical text is skipped, a pinned blocker wins, "add" adds.
    let text = format!("Corpus fact 10: service s1 uses port {}", 4010);
    let r = w
        .add_with_doctrine(mem(&text, "test/corpus/0"), "supersede", None)
        .await
        .unwrap();
    assert_eq!(
        (r["action"].clone(), r["reason"].clone()),
        (json!("skipped"), json!("identical"))
    );
    assert_eq!(r["blocked_by"], json!(base[10]));
    let pin = Patch {
        pinned: Some(true),
        ..Patch::default()
    };
    w.update_memory(base[10] as i64, pin, None, false)
        .await
        .unwrap();
    let r = w
        .add_with_doctrine(mem(&text, "test/corpus/0"), "skip", None)
        .await
        .unwrap();
    assert_eq!(r["reason"], "pinned_blocker");
    let r = w
        .add_with_doctrine(mem(&text, "test/corpus/0"), "add", None)
        .await
        .unwrap();
    assert_eq!(r["action"], "added");

    // A start counts the length sums again.
    let (sum, count) = w.engine.state.doc_lengths().unwrap();
    w.engine.state.set_doc_lengths(1, 1).unwrap();
    Writer::open(w.engine.clone(), s.dir.clone(), 0.97)
        .await
        .unwrap();
    assert_eq!(w.engine.state.doc_lengths().unwrap(), (sum, count));
    s.cleanup().await;
    calls
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server (QDRANT_TEST_URL) and the model"]
async fn writes_touch_only_their_records_and_cost_the_same_at_each_size() {
    let small = run(20).await;
    let large = run(200).await;
    for ((name, a), (_, b)) in small.iter().zip(&large) {
        println!("{name}: {a:?}");
        assert_eq!(a, b, "{name}: Qdrant calls change with the corpus size");
    }
    let calls: HashMap<_, _> = small.into_iter().collect();
    // A text change writes one whole point; other changes write the payload.
    assert_eq!(calls["patch-text"].get("upsert"), Some(&1));
    assert_eq!(calls["patch-meta-pin"].get("upsert"), None);
    assert_eq!(calls["patch-meta-pin"].get("replace_payload"), Some(&1));
    assert_eq!(calls["delete-batch"].get("delete"), Some(&1));
}
