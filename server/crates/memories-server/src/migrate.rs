//! `migrate` and `verify`: copy the records of the Python server
//! (`metadata.json`, the canonical store of v5) into `memories_v2`.
//!
//! The commands only read `metadata.json`, `config.json` and the old
//! collection. They never write them.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::future::Future;
use std::path::Path;

use memories_engine::keyword;
use memories_store::{NewPoint, QdrantStore, StateDb};
use memories_types::Record;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

/// The `state.db` value that marks a finished migration: the SHA-256 of the
/// `metadata.json` that it copied.
pub const DONE_HASH: &str = "migration_metadata_sha256";
/// The SHA-256 of the `metadata.json` that an unfinished run started from.
const STARTED_HASH: &str = "migration_started_sha256";
/// `shadow` or `normal`: the mode of the finished migration.
pub(crate) const DONE_MODE: &str = "migration_mode";
pub(crate) const BATCH: usize = 256;
/// The IDs that a report names, at most.
const REPORT_IDS: usize = 20;
/// The lowest cosine similarity for a sampled vector.
const MIN_COSINE: f64 = 0.9999;

pub trait Embed: Sync {
    fn dim(&self) -> usize;
    /// The `embedding_signature` of Python `embedding_space.py`.
    fn signature(&self) -> String;
    /// Document vectors (with `EMBED_DOC_PREFIX`), one for each text.
    fn embed_docs(
        &self,
        texts: Vec<String>,
    ) -> impl Future<Output = Result<Vec<Vec<f32>>, String>> + Send;
}

impl Embed for memories_embed::Embedder {
    fn dim(&self) -> usize {
        self.dim()
    }

    fn signature(&self) -> String {
        let c = self.config();
        let base = format!("onnx:{}:{}d", c.model, self.dim());
        if c.query_prefix.is_empty() && c.document_prefix.is_empty() {
            base
        } else {
            // shortcut: the Python prefix fingerprint is a SHA-1, which this
            // crate does not have. A mark that never matches makes the
            // migration embed each text again. Port the SHA-1 if an install
            // with prefixes must copy its vectors.
            format!("{base}+pfx-unknown")
        }
    }

    async fn embed_docs(&self, texts: Vec<String>) -> Result<Vec<Vec<f32>>, String> {
        self.embed(texts, memories_embed::TextKind::Document)
            .await
            .map_err(|e| e.to_string())
    }
}

pub struct Stores<'a> {
    /// `memories_v2`.
    pub new: &'a QdrantStore,
    /// The collection of the Python server. The commands only read it.
    pub old: &'a QdrantStore,
    pub state: &'a StateDb,
}

#[derive(Clone, Debug, Default)]
pub struct Options {
    /// Shadow mode: `metadata.json` stays the canonical store, so a run also
    /// replaces changed records and deletes removed records in `memories_v2`.
    pub shadow: bool,
    /// The count of vectors that `verify` compares.
    pub sample: usize,
    /// Records for each write. 0 gives 256.
    pub batch: usize,
    /// Test only: stop with an error after this count of written batches.
    pub stop_after_batches: Option<usize>,
}

/// The records of `metadata.json`.
pub struct Legacy {
    pub sha256: String,
    pub file_bytes: u64,
    /// One record for each ID. A repeated ID keeps the last entry, as the
    /// `_id_map` of Python does.
    pub records: BTreeMap<u64, Record>,
    pub entries: usize,
    pub duplicate_ids: Vec<u64>,
    /// `embedding_signature` and `qdrant_collection` of `config.json`.
    pub signature: Option<String>,
    pub old_collection: Option<String>,
}

pub fn metadata_path(data_dir: &Path) -> std::path::PathBuf {
    data_dir.join("metadata.json")
}

pub fn file_sha256(path: &Path) -> Result<String, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    Ok(hex(&Sha256::digest(&bytes)))
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

impl Legacy {
    pub fn read(data_dir: &Path) -> Result<Self, String> {
        let path = metadata_path(data_dir);
        let bytes =
            std::fs::read(&path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        let entries: Vec<Value> = serde_json::from_slice(&bytes).map_err(|e| {
            format!(
                "{} is not a JSON list of records ({e}). A Python float NaN or Infinity also gives this error: correct that value. Otherwise restore the file from metadata.json.bak or a backup",
                path.display()
            )
        })?;
        let count = entries.len();
        let mut records = BTreeMap::new();
        let mut duplicate_ids = Vec::new();
        for (i, entry) in entries.into_iter().enumerate() {
            let record = Record::from_json(entry)
                .map_err(|e| format!("{} entry {i}: {e}", path.display()))?;
            if let Some(old) = records.insert(record.id, record) {
                duplicate_ids.push(old.id);
            }
        }
        let config: Value = std::fs::read(data_dir.join("config.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        let text = |k: &str| config.get(k).and_then(Value::as_str).map(str::to_owned);
        Ok(Self {
            sha256: hex(&Sha256::digest(&bytes)),
            file_bytes: bytes.len() as u64,
            records,
            entries: count,
            duplicate_ids,
            signature: text("embedding_signature"),
            old_collection: text("qdrant_collection"),
        })
    }

    fn avgdl(&self) -> (f64, u64) {
        let texts = || self.records.values().map(text_of);
        let sum: usize = texts().map(|t| keyword::doc_tokens(t).len()).sum();
        (keyword::avgdl(texts()), sum as u64)
    }
}

pub(crate) fn text_of(r: &Record) -> &str {
    r.text.as_deref().unwrap_or("")
}

/// The free bytes that the migration needs on the file system of `DATA_DIR`:
/// three times `metadata.json`, plus 2 KiB for each record (vectors and
/// indexes). Qdrant can keep its files on another disk; this check does not
/// see that disk.
pub fn space_needed(legacy: &Legacy) -> u64 {
    legacy.file_bytes * 3 + legacy.records.len() as u64 * 2048
}

pub fn free_space(dir: &Path) -> Result<u64, String> {
    let s = rustix::fs::statvfs(dir).map_err(|e| format!("statvfs {}: {e}", dir.display()))?;
    Ok(s.f_bavail * s.f_frsize)
}

pub fn check_space(free: u64, need: u64) -> Result<(), String> {
    if free < need {
        return Err(format!(
            "not enough free disk space for the migration: {free} bytes free, {need} bytes needed. Make space, then start again. The old data did not change"
        ));
    }
    Ok(())
}

pub(crate) fn changed_message(path: &Path) -> String {
    format!(
        "{} changed after the migration: a Python server wrote to the old data. Run `memories-server migrate --merge` (task P7-1) to keep the records of the two sides",
        path.display()
    )
}

fn resume_changed_message(path: &Path) -> String {
    format!(
        "{} changed during an unfinished migration. Run `memories-server migrate --shadow` to write each record again from the current file",
        path.display()
    )
}

/// What the server does at start.
#[derive(Debug, PartialEq)]
pub enum StartAction {
    /// No `metadata.json`, or the migration is done and the file did not change.
    Serve,
    /// No finished migration: run (or continue) it.
    Migrate,
}

/// The start rules: migrate when `metadata.json` exists and no migration has
/// finished; refuse when the file changed after the migration.
pub fn start_action(data_dir: &Path, state: &StateDb) -> Result<StartAction, String> {
    let path = metadata_path(data_dir);
    if !path.exists() {
        return Ok(StartAction::Serve);
    }
    let meta = |k| state.get_meta(k).map_err(|e| e.to_string());
    let sha = file_sha256(&path)?;
    if meta(crate::rollback::EXPORT_PENDING)?.as_deref() == Some(sha.as_str()) {
        return Err(crate::rollback::unfinished_export_message());
    }
    match meta(DONE_HASH)? {
        Some(done) if done == sha => Ok(StartAction::Serve),
        Some(_) => Err(changed_message(&path)),
        None => match meta(STARTED_HASH)? {
            Some(started) if started != sha => Err(resume_changed_message(&path)),
            _ => Ok(StartAction::Migrate),
        },
    }
}

/// True if the vectors of the old collection come from the embedder of this
/// server. With no `embedding_signature` in `config.json`, a sample of old
/// vectors must be equal to new embeddings.
pub(crate) async fn may_copy(
    legacy: &Legacy,
    stores: &Stores<'_>,
    embed: &impl Embed,
) -> Result<bool, String> {
    if stores.old.collection() == stores.new.collection() {
        return Err(format!(
            "the old collection and the new collection have the same name {:?}",
            stores.new.collection()
        ));
    }
    if !stores.old.exists().await.map_err(|e| e.to_string())? {
        return Ok(false);
    }
    if let Some(sig) = &legacy.signature {
        return Ok(*sig == embed.signature());
    }
    let ids: Vec<u64> = legacy.records.keys().take(BATCH).copied().collect();
    let old = stores.old.vectors(&ids).await.map_err(|e| e.to_string())?;
    let pairs: Vec<(Vec<f32>, &str)> = ids
        .iter()
        .filter_map(|id| Some((old.get(id)?.dense.clone()?, text_of(&legacy.records[id]))))
        .take(8)
        .collect();
    if pairs.is_empty() {
        return Ok(false);
    }
    let new = embed
        .embed_docs(pairs.iter().map(|(_, t)| (*t).to_owned()).collect())
        .await?;
    Ok(pairs
        .iter()
        .zip(&new)
        .all(|((o, _), n)| o.len() == n.len() && cosine(o, n) >= MIN_COSINE))
}

/// Copy the records into `memories_v2`, then run [`verify`]. A run that stops
/// can run again: out of shadow mode it writes only the records that are not
/// in `memories_v2` yet. Returns the report; an error means that the
/// migration did not finish, and the old data did not change.
pub async fn migrate(
    data_dir: &Path,
    stores: &Stores<'_>,
    embed: &impl Embed,
    opts: &Options,
) -> Result<Value, String> {
    let started = std::time::Instant::now();
    let legacy = Legacy::read(data_dir)?;
    let done = stores
        .state
        .get_meta(DONE_HASH)
        .map_err(|e| e.to_string())?;
    let meta = |k| stores.state.get_meta(k).map_err(|e| e.to_string());
    let path = metadata_path(data_dir);
    if opts.shadow {
        // After a normal migration, memories_v2 is the canonical store: a
        // shadow run would delete or replace the records of the Rust server.
        if done.is_some() && meta(DONE_MODE)?.as_deref() != Some("shadow") {
            return Err(format!(
                "the migration finished out of shadow mode, so memories_v2 is the canonical store. `--shadow` would delete or replace its records. {}",
                changed_message(&path)
            ));
        }
    } else {
        match done {
            Some(h) if h == legacy.sha256 => {
                return Ok(json!({"status": "up_to_date", "metadata_sha256": legacy.sha256}));
            }
            Some(_) => return Err(changed_message(&path)),
            None => {
                if meta(STARTED_HASH)?.is_some_and(|h| h != legacy.sha256) {
                    return Err(resume_changed_message(&path));
                }
            }
        }
    }
    check_space(free_space(data_dir)?, space_needed(&legacy))?;
    if opts.sample == 0 {
        return Err("the verify sample must be 1 or more".into());
    }
    stores
        .state
        .set_meta(&[(STARTED_HASH, &legacy.sha256)])
        .map_err(|e| e.to_string())?;

    let dim = embed.dim();
    let created = stores
        .new
        .ensure_collection(dim as u64)
        .await
        .map_err(|e| e.to_string())?;
    let (avgdl, len_sum) = legacy.avgdl();
    let existing: HashSet<u64> = stores
        .new
        .ids()
        .await
        .map_err(|e| e.to_string())?
        .into_iter()
        .collect();

    let mut deleted = 0;
    if opts.shadow {
        let gone: Vec<u64> = existing
            .iter()
            .filter(|id| !legacy.records.contains_key(id))
            .copied()
            .collect();
        for chunk in gone.chunks(BATCH) {
            stores.new.delete(chunk).await.map_err(|e| e.to_string())?;
        }
        deleted = gone.len();
    }
    // shortcut: shadow mode writes every record again (vectors are copied, so
    // this is cheap); compare first if the daily sync gets slow.
    let todo: Vec<&Record> = legacy
        .records
        .values()
        .filter(|r| opts.shadow || !existing.contains(&r.id))
        .collect();

    let copy = may_copy(&legacy, stores, embed).await?;
    let size = if opts.batch == 0 { BATCH } else { opts.batch };
    let todo_pairs: Vec<(u64, &Record)> = todo.iter().map(|r| (r.id, *r)).collect();
    let (copied, embedded) = write_points(
        &todo_pairs,
        stores,
        embed,
        copy,
        avgdl,
        size,
        opts.stop_after_batches,
    )
    .await?;

    let report_verify = verify_legacy(&legacy, stores, embed, opts).await?;
    if report_verify["ok"] != json!(true) {
        return Err(format!(
            "verify failed after the migration; the server did not start. Report: {report_verify}"
        ));
    }
    let max_new = stores.new.max_id().await.map_err(|e| e.to_string())?;
    let max_id = legacy.records.keys().last().copied().max(max_new);
    stores
        .state
        .raise_id_floor(max_id.map_or(0, |m| m + 1))
        .map_err(|e| e.to_string())?;
    stores
        .state
        .set_doc_lengths(len_sum, legacy.records.len() as u64)
        .map_err(|e| e.to_string())?;
    stores
        .state
        .set_legacy_base(
            &crate::rollback::base_of(legacy.records.values()),
            &[
                (DONE_HASH, &legacy.sha256),
                (DONE_MODE, if opts.shadow { "shadow" } else { "normal" }),
            ],
        )
        .map_err(|e| e.to_string())?;
    Ok(json!({
        "status": "migrated",
        "shadow": opts.shadow,
        "collection": stores.new.collection(),
        "created_collection": created,
        "old_collection": stores.old.collection(),
        "metadata_sha256": legacy.sha256,
        "metadata_entries": legacy.entries,
        "duplicate_ids": legacy.duplicate_ids,
        "records": legacy.records.len(),
        "already_present": legacy.records.len() - todo.len(),
        "written": todo.len(),
        "copied_vectors": copied,
        "embedded_vectors": embedded,
        "deleted": deleted,
        "avgdl": avgdl,
        "next_id": stores.state.next_id().map_err(|e| e.to_string())?,
        "seconds": started.elapsed().as_secs_f64(),
        "verify": report_verify,
    }))
}

/// Write `records` to `memories_v2` in batches of `size`. Each pair is (the ID
/// of the vector in the old collection, the record to write). With `copy`,
/// the dense vector comes from the old collection; else, or if it is absent,
/// the embedder makes it. Returns (copied, embedded).
pub(crate) async fn write_points(
    records: &[(u64, &Record)],
    stores: &Stores<'_>,
    embed: &impl Embed,
    copy: bool,
    avgdl: f64,
    size: usize,
    stop_after_batches: Option<usize>,
) -> Result<(usize, usize), String> {
    let dim = embed.dim();
    let (mut copied, mut embedded) = (0usize, 0usize);
    for (batch_no, batch) in records.chunks(size.max(1)).enumerate() {
        if stop_after_batches == Some(batch_no) {
            return Err(format!("stopped after {batch_no} batches (test)"));
        }
        let old_ids: Vec<u64> = batch.iter().map(|(old, _)| *old).collect();
        let mut old = if copy {
            stores
                .old
                .vectors(&old_ids)
                .await
                .map_err(|e| e.to_string())?
        } else {
            HashMap::new()
        };
        let mut dense: Vec<Option<Vec<f32>>> = old_ids
            .iter()
            .map(|id| {
                old.remove(id)
                    .and_then(|v| v.dense)
                    .filter(|d| d.len() == dim)
            })
            .collect();
        let missing: Vec<usize> = (0..batch.len()).filter(|&i| dense[i].is_none()).collect();
        if !missing.is_empty() {
            let texts = missing
                .iter()
                .map(|&i| text_of(batch[i].1).to_owned())
                .collect();
            let vectors = embed.embed_docs(texts).await?;
            if vectors.len() != missing.len() {
                return Err(format!(
                    "the embedder gave {} vectors for {} texts",
                    vectors.len(),
                    missing.len()
                ));
            }
            for (i, v) in missing.iter().zip(vectors) {
                dense[*i] = Some(v);
            }
        }
        embedded += missing.len();
        copied += batch.len() - missing.len();
        let points = batch
            .iter()
            .zip(dense)
            .map(|((_, r), d)| NewPoint {
                record: (*r).clone(),
                dense: d.expect("filled above"),
                keyword: keyword::doc_vector(text_of(r), avgdl),
            })
            .collect();
        stores
            .new
            .upsert(points, dim as u64)
            .await
            .map_err(|e| e.to_string())?;
    }
    Ok((copied, embedded))
}

pub(crate) fn cosine(a: &[f32], b: &[f32]) -> f64 {
    let dot: f64 = a
        .iter()
        .zip(b)
        .map(|(x, y)| f64::from(*x) * f64::from(*y))
        .sum();
    let norm = |v: &[f32]| v.iter().map(|x| f64::from(*x).powi(2)).sum::<f64>().sqrt();
    dot / (norm(a) * norm(b)).max(1e-12)
}

fn first<T: Clone>(v: &[T]) -> Vec<T> {
    v.iter().take(REPORT_IDS).cloned().collect()
}

/// Compare `memories_v2` with `metadata.json`: the count, each field of each
/// record, and the vectors of a sample of the records. `ok` is false on any
/// difference. Records that the Rust server wrote after the migration count
/// as `extra`.
pub async fn verify(
    data_dir: &Path,
    stores: &Stores<'_>,
    embed: &impl Embed,
    opts: &Options,
) -> Result<Value, String> {
    verify_legacy(&Legacy::read(data_dir)?, stores, embed, opts).await
}

async fn verify_legacy(
    legacy: &Legacy,
    stores: &Stores<'_>,
    embed: &impl Embed,
    opts: &Options,
) -> Result<Value, String> {
    if opts.sample == 0 {
        return Err("the verify sample must be 1 or more".into());
    }
    if !stores.new.exists().await.map_err(|e| e.to_string())? {
        return Ok(
            json!({"ok": false, "error": format!("collection {} does not exist", stores.new.collection())}),
        );
    }
    let count = stores.new.count(None).await.map_err(|e| e.to_string())?;
    let (mut seen, mut extra, mut mismatch) = (HashSet::new(), Vec::new(), Vec::new());
    let mut offset = None;
    loop {
        let (page, next) = stores
            .new
            .scroll(None, offset, 1000)
            .await
            .map_err(|e| e.to_string())?;
        for r in page {
            match legacy.records.get(&r.id) {
                None => extra.push(r.id),
                Some(want) if want.to_json() != r.to_json() => mismatch.push(r.id),
                Some(_) => {}
            }
            seen.insert(r.id);
        }
        match next {
            Some(n) => offset = Some(n),
            None => break,
        }
    }
    let missing: Vec<u64> = legacy
        .records
        .keys()
        .filter(|id| !seen.contains(id))
        .copied()
        .collect();

    // Vectors: an even sample over the IDs present in both.
    let present: Vec<u64> = legacy
        .records
        .keys()
        .filter(|id| seen.contains(id))
        .copied()
        .collect();
    let step = (present.len() / opts.sample.max(1)).max(1);
    let sample: Vec<u64> = present
        .iter()
        .step_by(step)
        .take(opts.sample)
        .copied()
        .collect();
    let (avgdl, _) = legacy.avgdl();
    let copy = may_copy(legacy, stores, embed).await?;
    let mut bad_vectors = Vec::new();
    let mut lowest = 1.0f64;
    for chunk in sample.chunks(BATCH) {
        let new = stores.new.vectors(chunk).await.map_err(|e| e.to_string())?;
        let mut old = if copy {
            stores.old.vectors(chunk).await.map_err(|e| e.to_string())?
        } else {
            HashMap::new()
        };
        let mut want_dense: Vec<Option<Vec<f32>>> = chunk
            .iter()
            .map(|id| {
                old.remove(id)
                    .and_then(|v| v.dense)
                    .filter(|d| d.len() == embed.dim())
            })
            .collect();
        let missing_idx: Vec<usize> = (0..chunk.len())
            .filter(|&i| want_dense[i].is_none())
            .collect();
        if !missing_idx.is_empty() {
            let texts = missing_idx
                .iter()
                .map(|&i| text_of(&legacy.records[&chunk[i]]).to_owned())
                .collect();
            for (i, v) in missing_idx.iter().zip(embed.embed_docs(texts).await?) {
                want_dense[*i] = Some(v);
            }
        }
        for (id, want) in chunk.iter().zip(want_dense) {
            let got = new.get(id).cloned().unwrap_or_default();
            let want_kw = keyword::doc_vector(text_of(&legacy.records[id]), avgdl);
            let sim = match (&got.dense, &want) {
                (Some(g), Some(w)) if g.len() == w.len() => cosine(g, w),
                _ => 0.0,
            };
            lowest = lowest.min(sim);
            let kw_ok = got.keyword.unwrap_or_default() == want_kw;
            if sim < MIN_COSINE || !kw_ok {
                bad_vectors.push(*id);
            }
        }
    }

    let ok = count == legacy.records.len() as u64
        && missing.is_empty()
        && extra.is_empty()
        && mismatch.is_empty()
        && bad_vectors.is_empty();
    Ok(json!({
        "ok": ok,
        "collection": stores.new.collection(),
        "metadata_sha256": legacy.sha256,
        "metadata_entries": legacy.entries,
        "duplicate_ids": legacy.duplicate_ids,
        "records": legacy.records.len(),
        "collection_count": count,
        "missing_count": missing.len(),
        "missing_ids": first(&missing),
        "extra_count": extra.len(),
        "extra_ids": first(&extra),
        "field_mismatch_count": mismatch.len(),
        "field_mismatch_ids": first(&mismatch),
        "vectors_checked": sample.len(),
        "vector_mismatch_count": bad_vectors.len(),
        "vector_mismatch_ids": first(&bad_vectors),
        "lowest_cosine": if sample.is_empty() { Value::Null } else { json!(lowest) },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(name: &str) -> std::path::PathBuf {
        let d =
            std::env::temp_dir().join(format!("memories-migrate-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn too_little_disk_space_stops_the_migration() {
        assert!(check_space(100, 100).is_ok());
        let err = check_space(99, 100).unwrap_err();
        assert!(err.contains("not enough free disk space"), "{err}");
        assert!(err.contains("old data did not change"), "{err}");
        assert!(free_space(&std::env::temp_dir()).unwrap() > 0);
    }

    #[test]
    fn start_rules() {
        let d = dir("start");
        let state = StateDb::open(&d.join("state.db")).unwrap();
        assert_eq!(
            start_action(&d, &state).unwrap(),
            StartAction::Serve,
            "no metadata.json"
        );
        std::fs::write(metadata_path(&d), r#"[{"id": 0, "text": "a"}]"#).unwrap();
        assert_eq!(start_action(&d, &state).unwrap(), StartAction::Migrate);
        let sha = file_sha256(&metadata_path(&d)).unwrap();
        state.set_meta(&[(DONE_HASH, &sha)]).unwrap();
        assert_eq!(start_action(&d, &state).unwrap(), StartAction::Serve);
        std::fs::write(metadata_path(&d), r#"[{"id": 0, "text": "b"}]"#).unwrap();
        let err = start_action(&d, &state).unwrap_err();
        assert!(err.contains("migrate --merge"), "{err}");
        let sha = file_sha256(&metadata_path(&d)).unwrap();
        state
            .set_meta(&[(crate::rollback::EXPORT_PENDING, &sha)])
            .unwrap();
        let err = start_action(&d, &state).unwrap_err();
        assert!(err.contains("export-legacy"), "{err}");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_repeated_id_keeps_the_last_entry() {
        let d = dir("dup");
        std::fs::write(
            metadata_path(&d),
            r#"[{"id": 5, "text": "old"}, {"id": 1, "text": "x"}, {"id": 5, "text": "new"}]"#,
        )
        .unwrap();
        let l = Legacy::read(&d).unwrap();
        assert_eq!((l.entries, l.records.len()), (3, 2));
        assert_eq!(l.duplicate_ids, [5]);
        assert_eq!(l.records[&5].text.as_deref(), Some("new"));
        assert_eq!(space_needed(&l), l.file_bytes * 3 + 2 * 2048);
        std::fs::write(metadata_path(&d), "[{").unwrap();
        assert!(
            Legacy::read(&d)
                .err()
                .unwrap()
                .contains("metadata.json.bak")
        );
        let _ = std::fs::remove_dir_all(&d);
    }
}
