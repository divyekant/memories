//! Test loader: copies the records of a Python `metadata.json` into a new
//! `memories_v2` collection, with dense vectors from the Rust embedder and
//! keyword vectors. The parity check of the search routes uses it.
//!
//! It deletes the collection first. Do not run it against a store with data
//! to keep. Task P1-4 adds `memories-server migrate`, the real migration.
//!
//! Usage: QDRANT_URL=http://127.0.0.1:6333 cargo run --example load_metadata -- <metadata.json>

use memories_embed::{EmbedConfig, Embedder, TextKind};
use memories_engine::keyword::{avgdl, doc_vector};
use memories_store::{NewPoint, QdrantSettings, QdrantStore};
use memories_types::Record;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .ok_or("usage: load_metadata <metadata.json>")?;
    let entries: Vec<serde_json::Value> = serde_json::from_str(&std::fs::read_to_string(&path)?)?;
    let records = entries
        .into_iter()
        .map(Record::from_json)
        .collect::<Result<Vec<_>, _>>()?;
    let settings = QdrantSettings::from_env()?;
    let store = QdrantStore::new(&settings, &settings.v2_collection())?;
    let embedder = Embedder::load(EmbedConfig::from_env(None)?).await?;
    let dim = embedder.dim() as u64;
    let _ = store.delete_collection().await;
    store.ensure_collection(dim).await?;

    let texts: Vec<String> = records
        .iter()
        .map(|r| r.text.clone().unwrap_or_default())
        .collect();
    let avg = avgdl(texts.iter().map(String::as_str));
    for (chunk, chunk_texts) in records.chunks(64).zip(texts.chunks(64)) {
        let dense = embedder
            .embed(chunk_texts.to_vec(), TextKind::Document)
            .await?;
        let points = chunk
            .iter()
            .zip(dense)
            .zip(chunk_texts)
            .map(|((r, dense), text)| NewPoint {
                record: r.clone(),
                dense,
                keyword: doc_vector(text, avg),
            })
            .collect();
        store.upsert(points, dim).await?;
    }
    println!(
        "loaded {} records into {}",
        records.len(),
        store.collection()
    );
    Ok(())
}
