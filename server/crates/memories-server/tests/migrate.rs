//! Exit check of task P1-4 against a Qdrant server (1.15.4 in CI).
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 cargo test -p memories-server --test migrate -- --ignored

use std::collections::BTreeMap;
use std::path::PathBuf;

use memories_server::migrate::{self, DONE_HASH, Embed, Options, Stores};
use memories_store::{QdrantSettings, QdrantStore, StateDb, StoredVectors};
use memories_types::Record;
use qdrant_client::Qdrant;
use qdrant_client::qdrant::{
    CreateCollectionBuilder, Distance, PointStruct, UpsertPointsBuilder, VectorParamsBuilder,
};
use serde_json::{Map, Value, json};

const DIM: usize = 8;
const SIGNATURE: &str = "onnx:fake:8d";

/// A fake embedder: a unit vector from the bytes of the text.
struct Fake;

fn fake_vector(text: &str) -> Vec<f32> {
    let mut v = [1.0f32; DIM];
    for (i, b) in text.bytes().enumerate() {
        v[i % DIM] += f32::from(b) / 255.0;
    }
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    v.iter().map(|x| x / n).collect()
}

impl Embed for Fake {
    fn dim(&self) -> usize {
        DIM
    }
    fn signature(&self) -> String {
        SIGNATURE.into()
    }
    async fn embed_docs(&self, texts: Vec<String>) -> Result<Vec<Vec<f32>>, String> {
        Ok(texts.iter().map(|t| fake_vector(t)).collect())
    }
}

/// The vector in the old collection: different from the fake embedder, so a
/// test can see that the migration copied it.
fn old_vector(id: u64) -> Vec<f32> {
    let mut v: Vec<f32> = (0..DIM)
        .map(|i| (id as f32 + i as f32 + 1.0).sin())
        .collect();
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    v.iter_mut().for_each(|x| *x /= n);
    v
}

fn settings() -> QdrantSettings {
    let url =
        std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
    QdrantSettings::from_lookup(|k| (k == "QDRANT_URL").then(|| url.clone())).unwrap()
}

fn fixture(name: &str) -> Vec<Value> {
    let path = format!(
        "{}/../memories-store/tests/fixtures/{name}",
        env!("CARGO_MANIFEST_DIR")
    );
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

struct Setup {
    dir: PathBuf,
    new: QdrantStore,
    old: QdrantStore,
    state: StateDb,
    /// IDs with a vector in the old collection.
    with_vector: Vec<u64>,
}

impl Setup {
    fn stores(&self) -> Stores<'_> {
        Stores {
            new: &self.new,
            old: &self.old,
            state: &self.state,
        }
    }

    async fn migrate(&self, opts: &Options) -> Result<Value, String> {
        migrate::migrate(&self.dir, &self.stores(), &Fake, opts).await
    }

    async fn verify(&self) -> Value {
        migrate::verify(&self.dir, &self.stores(), &Fake, &all())
            .await
            .unwrap()
    }

    fn metadata_sha(&self) -> String {
        migrate::file_sha256(&migrate::metadata_path(&self.dir)).unwrap()
    }

    /// Each record and each vector of `memories_v2`.
    async fn snapshot(&self) -> BTreeMap<u64, (Value, StoredVectors)> {
        let ids = self.new.ids().await.unwrap();
        let records = self.new.get_many(&ids).await.unwrap();
        let mut vectors = self.new.vectors(&ids).await.unwrap();
        records
            .into_iter()
            .map(|r| (r.id, (r.to_json(), vectors.remove(&r.id).unwrap())))
            .collect()
    }

    /// Forget the finished migration (a new `state.db`).
    fn new_state(&mut self, name: &str) {
        self.state = StateDb::open(&self.dir.join(format!("{name}.db"))).unwrap();
    }
}

impl Drop for Setup {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn all() -> Options {
    Options {
        sample: 10_000,
        ..Default::default()
    }
}

/// A Python data directory: `metadata.json` (the seed corpus, the edge
/// records and one repeated ID), `config.json`, and the old collection with
/// a vector for 4 of 5 records.
async fn setup(name: &str) -> Setup {
    let dir = std::env::temp_dir().join(format!("memories-p14-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let mut entries = fixture("seed_records.json");
    entries.extend(fixture("edge_records.json"));
    // The production file has a repeated ID (108742). The last entry wins.
    entries.insert(
        1,
        json!({"id": 3, "text": "an older entry with id 3", "source": "dup"}),
    );
    std::fs::write(
        migrate::metadata_path(&dir),
        serde_json::to_vec(&entries).unwrap(),
    )
    .unwrap();

    let s = settings();
    let old_name = format!("p14_{name}_{}", std::process::id());
    std::fs::write(
        dir.join("config.json"),
        serde_json::to_vec(
            &json!({"embedding_signature": SIGNATURE, "qdrant_collection": old_name}),
        )
        .unwrap(),
    )
    .unwrap();

    // The old collection as the Python server makes it: one unnamed vector.
    let client = Qdrant::from_url(&s.grpc_url).build().unwrap();
    let _ = client.delete_collection(&old_name).await;
    client
        .create_collection(
            CreateCollectionBuilder::new(&old_name)
                .vectors_config(VectorParamsBuilder::new(DIM as u64, Distance::Cosine)),
        )
        .await
        .unwrap();
    let mut with_vector = Vec::new();
    let mut points = Vec::new();
    for e in &entries {
        let id = e["id"].as_u64().unwrap();
        if id % 5 != 0 && !with_vector.contains(&id) {
            with_vector.push(id);
            let mut payload: Map<String, Value> = e.as_object().unwrap().clone();
            payload.remove("id");
            points.push(PointStruct::new(id, old_vector(id), payload));
        }
    }
    client
        .upsert_points(UpsertPointsBuilder::new(&old_name, points).wait(true))
        .await
        .unwrap();

    let new = QdrantStore::new(&s, &format!("{old_name}_v2")).unwrap();
    let _ = new.delete_collection().await;
    Setup {
        state: StateDb::open(&dir.join("state.db")).unwrap(),
        old: QdrantStore::new(&s, &old_name).unwrap(),
        new,
        dir,
        with_vector,
    }
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn two_runs_give_the_same_collection_and_the_old_data_does_not_change() {
    let mut t = setup("same").await;
    let sha = t.metadata_sha();
    let old_count = t.old.count(None).await.unwrap();

    let report = t.migrate(&all()).await.unwrap();
    assert_eq!(report["status"], "migrated", "{report}");
    assert_eq!(report["verify"]["ok"], true, "{report}");
    assert_eq!(report["duplicate_ids"], json!([3]));
    let records = report["records"].as_u64().unwrap();
    assert_eq!(records, report["metadata_entries"].as_u64().unwrap() - 1);
    assert_eq!(
        report["copied_vectors"].as_u64().unwrap(),
        t.with_vector.len() as u64
    );
    assert_eq!(
        report["embedded_vectors"].as_u64().unwrap(),
        records - t.with_vector.len() as u64
    );
    assert_eq!(t.new.count(None).await.unwrap(), records);

    // A copied vector is the old vector; the others come from the embedder.
    let v = t.new.vectors(&[1, 5]).await.unwrap();
    assert_eq!(v[&1].dense.as_deref(), Some(&old_vector(1)[..]));
    let r5 = t.new.get(5).await.unwrap().unwrap();
    assert_eq!(
        v[&5].dense.as_deref(),
        Some(&fake_vector(r5.text.as_deref().unwrap())[..])
    );
    assert!(!v[&1].keyword.as_ref().unwrap().is_empty());
    // The last entry with ID 3 wins.
    assert_ne!(
        t.new.get(3).await.unwrap().unwrap().source.as_deref(),
        Some("dup")
    );

    let max = t.new.max_id().await.unwrap().unwrap();
    assert_eq!(t.state.next_id().unwrap(), max + 1);
    assert_eq!(t.state.doc_lengths().unwrap().1, records);
    assert_eq!(t.state.get_meta(DONE_HASH).unwrap(), Some(sha.clone()));
    let first = t.snapshot().await;

    // Second run: nothing to do.
    let again = t.migrate(&all()).await.unwrap();
    assert_eq!(again["status"], "up_to_date");
    assert_eq!(t.snapshot().await, first);

    // Second full run with no record of the first: the same collection.
    t.new_state("second");
    let again = t.migrate(&all()).await.unwrap();
    assert_eq!(again["written"], 0, "{again}");
    assert_eq!(t.snapshot().await, first);

    // A shadow run writes each record again: still the same collection.
    let shadow = t
        .migrate(&Options {
            shadow: true,
            ..all()
        })
        .await
        .unwrap();
    assert_eq!(shadow["written"].as_u64().unwrap(), records);
    assert_eq!(t.snapshot().await, first);

    assert_eq!(t.metadata_sha(), sha, "metadata.json changed");
    assert_eq!(t.old.count(None).await.unwrap(), old_count);
    assert_eq!(
        t.old.vectors(&[1]).await.unwrap()[&1].dense.as_deref(),
        Some(&old_vector(1)[..])
    );
    let _ = t.new.delete_collection().await;
    let _ = t.old.delete_collection().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn a_stopped_run_continues_and_verify_passes() {
    let t = setup("stop").await;
    let sha = t.metadata_sha();
    let old_count = t.old.count(None).await.unwrap();
    let opts = Options {
        batch: 50,
        stop_after_batches: Some(2),
        ..all()
    };
    let err = t.migrate(&opts).await.unwrap_err();
    assert!(err.contains("stopped"), "{err}");
    assert_eq!(t.new.count(None).await.unwrap(), 100);
    assert_eq!(t.state.get_meta(DONE_HASH).unwrap(), None);
    let state = t.verify().await;
    assert_eq!(state["ok"], false);
    assert!(state["missing_count"].as_u64().unwrap() > 0);

    let report = t.migrate(&Options { batch: 50, ..all() }).await.unwrap();
    assert_eq!(report["already_present"], 100, "{report}");
    assert_eq!(report["verify"]["ok"], true, "{report}");
    assert_eq!(t.verify().await["ok"], true);
    assert_eq!(t.metadata_sha(), sha);
    assert_eq!(t.old.count(None).await.unwrap(), old_count);
    let _ = t.new.delete_collection().await;
    let _ = t.old.delete_collection().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn only_shadow_mode_replaces_or_deletes_records() {
    let mut t = setup("shadow").await;
    t.migrate(&all()).await.unwrap();

    // Changes in memories_v2 that metadata.json does not have.
    let mut changed = t.new.get(1).await.unwrap().unwrap();
    changed.text = Some("changed in memories_v2".into());
    t.new.replace_payload(&changed).await.unwrap();
    let extra = Record::from_json(json!({"id": 999_999, "text": "new in memories_v2"})).unwrap();
    t.new
        .upsert(
            vec![memories_store::NewPoint {
                record: extra,
                dense: fake_vector("x"),
                keyword: vec![(1, 1.0)],
            }],
            DIM as u64,
        )
        .await
        .unwrap();

    t.new_state("again");
    let err = t.migrate(&all()).await.unwrap_err();
    assert!(err.contains("verify failed"), "{err}");
    assert!(err.contains("999999"), "{err}");
    assert_eq!(
        t.new.get(1).await.unwrap().unwrap().text.as_deref(),
        Some("changed in memories_v2"),
        "out of shadow mode, migrate does not replace a record"
    );
    assert!(t.new.get(999_999).await.unwrap().is_some());
    assert_eq!(t.state.get_meta(DONE_HASH).unwrap(), None);

    let report = t
        .migrate(&Options {
            shadow: true,
            ..all()
        })
        .await
        .unwrap();
    assert_eq!(report["deleted"], 1, "{report}");
    assert_ne!(
        t.new.get(1).await.unwrap().unwrap().text.as_deref(),
        Some("changed in memories_v2")
    );
    assert!(t.new.get(999_999).await.unwrap().is_none());
    let _ = t.new.delete_collection().await;
    let _ = t.old.delete_collection().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn verify_finds_each_kind_of_difference_and_a_changed_file_stops_the_start() {
    let t = setup("verify").await;
    t.migrate(&all()).await.unwrap();
    assert_eq!(
        migrate::start_action(&t.dir, &t.state).unwrap(),
        migrate::StartAction::Serve
    );

    let mut fields = Map::new();
    fields.insert("pinned".into(), json!(true));
    t.new.set_payload(&[2], fields).await.unwrap();
    let mut r4 = t.new.get(4).await.unwrap().unwrap();
    let text4 = r4.text.clone().unwrap();
    r4.text = Some(text4.clone());
    t.new
        .upsert(
            vec![memories_store::NewPoint {
                record: r4,
                dense: fake_vector("another text"),
                keyword: memories_engine::keyword::doc_vector(&text4, 1.0),
            }],
            DIM as u64,
        )
        .await
        .unwrap();
    t.new.delete(&[6]).await.unwrap();

    let report = t.verify().await;
    assert_eq!(report["ok"], false);
    assert_eq!(report["field_mismatch_ids"], json!([2]), "{report}");
    assert_eq!(report["vector_mismatch_ids"], json!([4]), "{report}");
    assert_eq!(report["missing_ids"], json!([6]), "{report}");

    // A Python server wrote to metadata.json after the migration.
    let path = migrate::metadata_path(&t.dir);
    let mut entries: Vec<Value> = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    entries.push(json!({"id": 5000, "text": "written by Python"}));
    std::fs::write(&path, serde_json::to_vec(&entries).unwrap()).unwrap();
    let err = migrate::start_action(&t.dir, &t.state).unwrap_err();
    assert!(err.contains("migrate --merge"), "{err}");
    let err = t.migrate(&all()).await.unwrap_err();
    assert!(err.contains("migrate --merge"), "{err}");
    let _ = t.new.delete_collection().await;
    let _ = t.old.delete_collection().await;
}
