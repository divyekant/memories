//! Exit check of task P1-3 against a Qdrant server (1.15.4 in CI).
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 cargo test -p memories-store --test qdrant_store -- --ignored

use std::collections::HashMap;

use memories_store::{NewPoint, QdrantSettings, QdrantStore, StateDb, StoreError};
use memories_types::Record;
use qdrant_client::Qdrant;
use qdrant_client::qdrant::{Condition, Filter};
use serde_json::{Value, json};

const DIM: u64 = 8;

fn settings() -> QdrantSettings {
    let url =
        std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
    QdrantSettings::from_lookup(|k| (k == "QDRANT_URL").then(|| url.clone())).unwrap()
}

fn fixture(name: &str) -> Vec<Record> {
    let path = format!("{}/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"));
    let values: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    values
        .into_iter()
        .map(|v| Record::from_json(v).unwrap())
        .collect()
}

fn point(record: Record) -> NewPoint {
    let seed = record.id as f32 + 1.0;
    NewPoint {
        dense: (0..DIM).map(|i| seed + i as f32).collect(),
        keyword: vec![(record.id as u32, 1.0), (u32::MAX, 0.5)],
        record,
    }
}

async fn store(name: &str) -> QdrantStore {
    let collection = format!("p13_{name}_{}", std::process::id());
    let store = QdrantStore::new(&settings(), &collection).unwrap();
    let _ = store.delete_collection().await;
    store
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn each_field_of_each_seed_record_is_equal_after_a_write_and_a_read() {
    let store = store("roundtrip").await;
    assert!(store.ensure_collection(DIM).await.unwrap());
    assert!(
        !store.ensure_collection(DIM).await.unwrap(),
        "second call makes nothing"
    );

    let mut records = fixture("seed_records.json");
    records.extend(fixture("edge_records.json"));
    assert!(records.len() > 240);
    store
        .upsert(records.iter().cloned().map(point).collect(), DIM)
        .await
        .unwrap();

    for r in &records {
        let got = store.get(r.id).await.unwrap().expect("record exists");
        assert_eq!(got.to_json(), r.to_json(), "record {}", r.id);
        assert_eq!(&got, r, "typed fields of record {}", r.id);
    }

    let wanted = [records[3].id, 999_999, records[0].id, records[3].id];
    let many = store.get_many(&wanted).await.unwrap();
    assert_eq!(
        many.iter().map(|r| r.id).collect::<Vec<_>>(),
        [records[3].id, records[0].id, records[3].id]
    );
    assert!(store.get(999_999).await.unwrap().is_none());

    let mut scrolled = Vec::new();
    let mut offset = None;
    loop {
        let (page, next) = store.scroll(None, offset, 50).await.unwrap();
        scrolled.extend(page);
        match next {
            Some(n) => offset = Some(n),
            None => break,
        }
    }
    let mut want = records.clone();
    want.sort_by_key(|r| r.id);
    assert_eq!(scrolled, want);

    assert_eq!(store.count(None).await.unwrap(), records.len() as u64);
    let pinned = Filter::must([Condition::matches("pinned", true)]);
    let n_pinned = records.iter().filter(|r| r.pinned == Some(true)).count() as u64;
    assert!(n_pinned > 0);
    assert_eq!(store.count(Some(pinned.clone())).await.unwrap(), n_pinned);
    let (page, _) = store.scroll(Some(pinned), None, 100).await.unwrap();
    assert!(page.iter().all(|r| r.pinned == Some(true)));

    let linked = records
        .iter()
        .find_map(|r| r.links.as_ref().and_then(|l| l.first()).map(|l| l.to_id))
        .unwrap();
    let by_link = Filter::must([Condition::matches("links[].to_id", linked)]);
    let n_link = records
        .iter()
        .filter(|r| r.links.iter().flatten().any(|l| l.to_id == linked))
        .count() as u64;
    assert_eq!(store.count(Some(by_link)).await.unwrap(), n_link);

    let mut sources: HashMap<String, u64> = HashMap::new();
    for r in &records {
        if let Some(s) = &r.source {
            *sources.entry(s.clone()).or_default() += 1;
        }
    }
    let facet: HashMap<String, u64> = store
        .facet("source", None, 10_000)
        .await
        .unwrap()
        .into_iter()
        .collect();
    assert_eq!(facet, sources);

    store.delete_collection().await.unwrap();
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn the_collection_has_the_vectors_and_payload_indexes() {
    let store = store("schema").await;
    store.ensure_collection(DIM).await.unwrap();
    let client = Qdrant::from_url(&settings().grpc_url)
        .skip_compatibility_check()
        .build()
        .unwrap();
    let info = client
        .collection_info(store.collection())
        .await
        .unwrap()
        .result
        .unwrap();
    let mut fields: Vec<_> = info.payload_schema.keys().cloned().collect();
    fields.sort();
    assert_eq!(
        fields,
        [
            "archived",
            "conflicts_with",
            "document_at",
            "entity_key",
            "is_latest",
            "links[].to_id",
            "pinned",
            "source"
        ]
    );
    assert!(matches!(
        store.ensure_collection(DIM + 1).await,
        Err(StoreError::BadCollection { .. })
    ));
    let params = info.config.unwrap().params.unwrap();
    let sparse = params.sparse_vectors_config.unwrap().map;
    assert_eq!(sparse["keyword"].modifier, Some(1), "IDF modifier");
    let dense = format!("{:?}", params.vectors_config.unwrap());
    assert!(
        dense.contains("\"dense\"") && dense.contains("size: 8"),
        "{dense}"
    );
    store.delete_collection().await.unwrap();
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn payload_writes() {
    let store = store("payload").await;
    store.ensure_collection(DIM).await.unwrap();
    let record = fixture("edge_records.json").remove(0);
    store
        .upsert(vec![point(record.clone())], DIM)
        .await
        .unwrap();

    let fields = json!({"pinned": true, "note": {"a": 1}});
    store
        .set_payload(&[record.id], fields.as_object().unwrap().clone())
        .await
        .unwrap();
    let got = store.get(record.id).await.unwrap().unwrap();
    assert_eq!(got.pinned, Some(true));
    assert_eq!(got.extra["note"], json!({"a": 1}));
    assert_eq!(got.text, record.text);

    store.replace_payload(&record).await.unwrap();
    assert_eq!(store.get(record.id).await.unwrap().unwrap(), record);

    let mut big = record.clone();
    big.extra.insert("n".into(), json!(u64::MAX));
    assert!(matches!(
        store.upsert(vec![point(big.clone())], DIM).await,
        Err(StoreError::IntegerTooLarge { .. })
    ));
    assert!(matches!(
        store.replace_payload(&big).await,
        Err(StoreError::IntegerTooLarge { .. })
    ));
    assert!(matches!(
        store
            .set_payload(&[record.id], json!({"id": 5}).as_object().unwrap().clone())
            .await,
        Err(StoreError::IdInPayload { .. })
    ));
    let mut with_id = record.clone();
    with_id.extra.insert("id".into(), json!(5));
    assert!(matches!(
        store.upsert(vec![point(with_id)], DIM).await,
        Err(StoreError::IdInPayload { .. })
    ));
    let mut short = point(record.clone());
    short.dense.pop();
    assert!(matches!(
        store.upsert(vec![short], DIM).await,
        Err(StoreError::BadDimension { .. })
    ));
    assert_eq!(store.get(record.id).await.unwrap().unwrap(), record);
    store.delete_collection().await.unwrap();
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server: set QDRANT_TEST_URL"]
async fn the_id_counter_gives_a_new_id_after_a_restart_and_after_the_delete_of_the_highest_id() {
    let store = store("ids").await;
    store.ensure_collection(DIM).await.unwrap();
    assert_eq!(store.max_id().await.unwrap(), None);
    let dir = std::env::temp_dir().join(format!("p13-ids-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let db_path = dir.join("state.db");

    // Records that a migration wrote: IDs 0..246 and the edge IDs up to 903.
    let mut records = fixture("seed_records.json");
    records.extend(fixture("edge_records.json"));
    store
        .upsert(records.iter().cloned().map(point).collect(), DIM)
        .await
        .unwrap();
    let highest = store.max_id().await.unwrap().unwrap();
    assert_eq!(highest, 903);

    let db = StateDb::open(&db_path).unwrap();
    db.raise_id_floor(highest + 1).unwrap();
    let new_ids = db.reserve_ids(2).unwrap();
    assert_eq!(new_ids, 904..906);
    let added: Vec<NewPoint> = new_ids
        .clone()
        .map(|id| {
            point(Record {
                id,
                text: Some(format!("new {id}")),
                ..Record::default()
            })
        })
        .collect();
    store.upsert(added, DIM).await.unwrap();

    // Delete the highest ID, then restart: the store max is 904 now.
    store.delete(&[905]).await.unwrap();
    assert!(store.get(905).await.unwrap().is_none());
    drop(db);
    let db = StateDb::open(&db_path).unwrap();
    db.raise_id_floor(store.max_id().await.unwrap().unwrap() + 1)
        .unwrap();
    let id = db.reserve_ids(1).unwrap().start;
    assert_eq!(id, 906, "905 is not given again");

    // The same after a restart with no write in between.
    drop(db);
    let db = StateDb::open(&db_path).unwrap();
    db.raise_id_floor(store.max_id().await.unwrap().unwrap() + 1)
        .unwrap();
    assert_eq!(db.reserve_ids(1).unwrap().start, 907);

    store.delete_collection().await.unwrap();
    let _ = std::fs::remove_dir_all(&dir);
}
