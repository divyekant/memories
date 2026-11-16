//! `export-legacy` and `migrate --merge` (task P7-1).
//!
//! `export-legacy` writes the records of `memories_v2` back to the data of
//! the Python server, so that a Python server (the rollback release) starts
//! with each change of the Rust server. `merge` brings the writes of a Python
//! server (a return through an old image tag) into `memories_v2`.
//!
//! The common base of the two sides is the set of record hashes of
//! `metadata.json` at the last migration, export or merge (`legacy_base` in
//! `state.db`). A record that one side changed and the other side did not
//! change takes the change.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};

use memories_engine::keyword;
use memories_store::JobQueue;
use memories_types::Record;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::migrate::{
    self, BATCH, DONE_HASH, DONE_MODE, Embed, Legacy, Options, Stores, hex, metadata_path, text_of,
    write_points,
};

/// The file in `DATA_DIR` that `export-legacy` appends the waiting extract
/// jobs to. `scripts/resend_extract_jobs.py` sends them to the Python server.
pub const EXTRACT_JOBS_FILE: &str = "rollback-extract-jobs.ndjson";
/// The IDs that a report names, at most.
const REPORT_IDS: usize = 20;

/// The `state.db` value with the SHA-256 of the `metadata.json` that an
/// unfinished `export-legacy` writes. Empty when no export is unfinished.
pub const EXPORT_PENDING: &str = "export_pending_sha256";
/// The `state.db` value with the ID numbers of an unfinished merge (JSON).
const MERGE_PENDING: &str = "merge_pending";
/// A search writes this field on each side. The merge takes the later value.
const REINFORCED: &str = "last_reinforced_at";

/// The base rows for `state.db`: each record as JSON text.
pub fn base_of<'a>(records: impl Iterator<Item = &'a Record>) -> Vec<(u64, String)> {
    records.map(|r| (r.id, r.to_json().to_string())).collect()
}

fn first(ids: &[u64]) -> Vec<u64> {
    ids.iter().take(REPORT_IDS).copied().collect()
}

#[derive(Debug, PartialEq)]
pub enum ExportAction {
    Export,
    Skip(String),
}

/// Export only when `memories_v2` is the canonical store: no `metadata.json`,
/// a `metadata.json` that did not change after the last migration, export or
/// merge, or the file of an unfinished export (`pending`). Else a Python
/// server wrote to it, and an export would replace those writes.
pub fn export_action(
    metadata_sha: Option<&str>,
    done: Option<&str>,
    pending: Option<&str>,
    mode: Option<&str>,
) -> ExportAction {
    match (metadata_sha, done) {
        _ if mode == Some("shadow") => ExportAction::Skip(
            "the migration ran in shadow mode, so metadata.json is the canonical store".into(),
        ),
        (None, _) => ExportAction::Export,
        (Some(m), _) if pending == Some(m) => ExportAction::Export,
        (Some(_), None) => ExportAction::Skip(
            "no migration finished, so metadata.json is the canonical store".into(),
        ),
        (Some(m), Some(d)) if m != d => ExportAction::Skip(
            "metadata.json changed after the last migration, export or merge: a Python server wrote to it. The export does not replace those writes. To update to the Rust server again, run `memories-server migrate --merge`".into(),
        ),
        _ => ExportAction::Export,
    }
}

/// Refuse an export that would delete most of the old data: the guard for a
/// lost or new Qdrant volume, where `memories_v2` is empty. `--force` skips it.
pub fn check_export_size(
    records: usize,
    base: usize,
    old_file_entries: usize,
    old_points_with_no_file: u64,
) -> Result<(), String> {
    let refuse = |why: String| {
        Err(format!(
            "{why}. The export would delete the old data, so it did not start. If memories_v2 is correct, run `memories-server export-legacy --force`"
        ))
    };
    if records == 0 && old_file_entries > 0 {
        return refuse(format!(
            "memories_v2 is empty and metadata.json has {old_file_entries} records"
        ));
    }
    if records * 2 < base {
        return refuse(format!(
            "memories_v2 has {records} records, less than half of the {base} records of the last migration, export or merge"
        ));
    }
    if old_points_with_no_file > 0 {
        return refuse(format!(
            "there is no metadata.json, but the old collection has {old_points_with_no_file} points"
        ));
    }
    Ok(())
}

/// The order of the records in the new `metadata.json`: the order of the old
/// file (Python lists records in file order), then each new ID in ID order.
pub fn legacy_order(old_ids: &[u64], records: &BTreeMap<u64, Record>) -> Vec<u64> {
    let mut seen = HashSet::new();
    let mut out: Vec<u64> = old_ids
        .iter()
        .filter(|id| records.contains_key(id) && seen.insert(**id))
        .copied()
        .collect();
    out.extend(records.keys().filter(|id| !seen.contains(id)));
    out
}

fn old_file_ids(path: &Path) -> Vec<u64> {
    let Ok(bytes) = std::fs::read(path) else {
        return Vec::new();
    };
    serde_json::from_slice::<Vec<Value>>(&bytes)
        .map(|v| v.iter().filter_map(|e| e.get("id")?.as_u64()).collect())
        .unwrap_or_default()
}

/// Write `bytes` to `path` as Python `_write_payloads` does: a temporary file
/// in the same folder, fsync, a copy of the old file to `<name>.bak`, then a
/// rename.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let err = |what: &str, p: &Path, e: std::io::Error| format!("{what} {}: {e}", p.display());
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let tmp = path.with_file_name(format!("{name}.export.tmp"));
    let result = (|| {
        let mut f = std::fs::File::create(&tmp).map_err(|e| err("cannot make", &tmp, e))?;
        f.write_all(bytes)
            .map_err(|e| err("cannot write", &tmp, e))?;
        f.sync_all().map_err(|e| err("cannot fsync", &tmp, e))?;
        if path.exists() {
            let bak = path.with_file_name(format!("{name}.bak"));
            std::fs::copy(path, &bak).map_err(|e| err("cannot copy to", &bak, e))?;
        }
        std::fs::rename(&tmp, path).map_err(|e| err("cannot rename to", path, e))?;
        if let Some(dir) = path.parent() {
            let _ = std::fs::File::open(dir).and_then(|d| d.sync_all());
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    result
}

/// All records of `memories_v2`.
async fn v2_records(stores: &Stores<'_>) -> Result<BTreeMap<u64, Record>, String> {
    let mut out = BTreeMap::new();
    let mut offset = None;
    loop {
        let (page, next) = stores
            .new
            .scroll(None, offset, 1000)
            .await
            .map_err(|e| e.to_string())?;
        out.extend(page.into_iter().map(|r| (r.id, r)));
        match next {
            Some(n) => offset = Some(n),
            None => return Ok(out),
        }
    }
}

/// Move the waiting extract jobs to `EXTRACT_JOBS_FILE` (appended, one JSON
/// object for each line), then end them in the queue. Returns the job count.
fn export_jobs(data_dir: &Path, now_iso: &str) -> Result<(usize, PathBuf), String> {
    let path = data_dir.join(EXTRACT_JOBS_FILE);
    let queue = JobQueue::open(&data_dir.join("state.db")).map_err(|e| e.to_string())?;
    let jobs = queue.waiting().map_err(|e| e.to_string())?;
    if jobs.is_empty() {
        return Ok((0, path));
    }
    let mut text = String::new();
    for j in &jobs {
        text.push_str(&j.to_string());
        text.push('\n');
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("cannot write {}: {e}", path.display()))?;
    let ids: Vec<String> = jobs
        .iter()
        .filter_map(|j| j["job_id"].as_str().map(str::to_owned))
        .collect();
    queue
        .fail_waiting(
            &ids,
            now_iso,
            &format!("moved to {EXTRACT_JOBS_FILE} by export-legacy"),
        )
        .map_err(|e| e.to_string())?;
    Ok((jobs.len(), path))
}

/// Write the records of `memories_v2` to `metadata.json` (Python format) and
/// to the old collection, so that the count check of the Python server
/// passes, and move the waiting extract jobs to a file. The command runs
/// again with the same result. A skip is a report with status `skipped`.
pub async fn export_legacy(
    data_dir: &Path,
    stores: &Stores<'_>,
    now_iso: &str,
    force: bool,
) -> Result<Value, String> {
    let started = std::time::Instant::now();
    let path = metadata_path(data_dir);
    let metadata_sha = if path.exists() {
        Some(migrate::file_sha256(&path)?)
    } else {
        None
    };
    let meta = |k| {
        stores
            .state
            .get_meta(k)
            .map(|v| v.filter(|v| !v.is_empty()))
            .map_err(|e| e.to_string())
    };
    let (done, mode, pending) = (meta(DONE_HASH)?, meta(DONE_MODE)?, meta(EXPORT_PENDING)?);
    let skip = |reason: String| Ok(json!({"status": "skipped", "reason": reason}));
    if let ExportAction::Skip(reason) = export_action(
        metadata_sha.as_deref(),
        done.as_deref(),
        pending.as_deref(),
        mode.as_deref(),
    ) {
        return skip(reason);
    }
    if !stores.new.exists().await.map_err(|e| e.to_string())? {
        return skip(format!(
            "collection {} does not exist: the Rust server has no records",
            stores.new.collection()
        ));
    }

    let records = v2_records(stores).await?;
    let old_ids = old_file_ids(&path);
    if !force {
        let base = stores.state.legacy_base().map_err(|e| e.to_string())?.len();
        let foreign =
            if metadata_sha.is_none() && stores.old.exists().await.map_err(|e| e.to_string())? {
                stores.old.count(None).await.map_err(|e| e.to_string())?
            } else {
                0
            };
        check_export_size(records.len(), base, old_ids.len(), foreign)?;
    }
    let ids: Vec<u64> = records.keys().copied().collect();
    // The old collection first: until the rename of metadata.json, the old
    // file stays, and the Python server refuses to start on its count check.
    let (mut created, mut dim) = (false, 0);
    for (i, chunk) in ids.chunks(BATCH).enumerate() {
        let mut vectors = stores.new.vectors(chunk).await.map_err(|e| e.to_string())?;
        let mut points = Vec::with_capacity(chunk.len());
        for id in chunk {
            let dense = vectors
                .remove(id)
                .and_then(|v| v.dense)
                .ok_or_else(|| format!("record {id} has no dense vector in memories_v2"))?;
            points.push((records[id].clone(), dense));
        }
        if i == 0 {
            dim = points[0].1.len() as u64;
            created = stores
                .old
                .ensure_legacy_collection(dim)
                .await
                .map_err(|e| e.to_string())?;
        }
        stores
            .old
            .upsert_legacy(points, dim)
            .await
            .map_err(|e| e.to_string())?;
    }
    let mut deleted = 0;
    if stores.old.exists().await.map_err(|e| e.to_string())? {
        let gone: Vec<u64> = stores
            .old
            .ids()
            .await
            .map_err(|e| e.to_string())?
            .into_iter()
            .filter(|id| !records.contains_key(id))
            .collect();
        for chunk in gone.chunks(BATCH) {
            stores.old.delete(chunk).await.map_err(|e| e.to_string())?;
        }
        deleted = gone.len();
        let count = stores.old.count(None).await.map_err(|e| e.to_string())?;
        if count != records.len() as u64 {
            return Err(format!(
                "the old collection {} has {count} points after the export, not {}. metadata.json did not change",
                stores.old.collection(),
                records.len()
            ));
        }
    }
    let (jobs, jobs_path) = export_jobs(data_dir, now_iso)?;

    let order = legacy_order(&old_ids, &records);
    let entries: Vec<Value> = order.iter().map(|id| records[id].to_json()).collect();
    let bytes = serde_json::to_vec(&entries).map_err(|e| e.to_string())?;
    let sha = hex(&Sha256::digest(&bytes));
    if let Some(old_sha) = &metadata_sha {
        // A copy that no later save replaces, unlike metadata.json.bak.
        let keep = path.with_file_name(format!("metadata.json.pre-export-{}", &old_sha[..12]));
        if !keep.exists() {
            std::fs::copy(&path, &keep)
                .map_err(|e| format!("cannot copy metadata.json to {}: {e}", keep.display()))?;
        }
    }
    // Mark the export before the rename: if the process stops after the
    // rename, the next export (or start) knows the file as its own.
    stores
        .state
        .set_meta(&[(EXPORT_PENDING, &sha)])
        .map_err(|e| e.to_string())?;
    write_atomic(&path, &bytes)?;
    stores
        .state
        .set_legacy_base(
            &base_of(records.values()),
            &[
                (DONE_HASH, &sha),
                (DONE_MODE, mode.as_deref().unwrap_or("normal")),
                (EXPORT_PENDING, ""),
            ],
        )
        .map_err(|e| e.to_string())?;
    Ok(json!({
        "status": "exported",
        "collection": stores.new.collection(),
        "old_collection": stores.old.collection(),
        "created_old_collection": created,
        "records": records.len(),
        "deleted_old_points": deleted,
        "metadata_sha256": sha,
        "extract_jobs": jobs,
        "extract_jobs_file": if jobs > 0 { json!(jobs_path) } else { Value::Null },
        "seconds": started.elapsed().as_secs_f64(),
    }))
}

/// What `migrate --merge` writes to `memories_v2`.
#[derive(Debug, Default, PartialEq)]
pub struct MergePlan {
    /// Records from `metadata.json`: (the ID in `metadata.json`, the record
    /// with its ID in `memories_v2`).
    pub upserts: Vec<(u64, Record)>,
    pub deletes: Vec<u64>,
    /// IDs where the two sides changed the same field (not `text`) to
    /// different values: a time field takes the later value, another field
    /// the value of `memories_v2`.
    pub resolved: Vec<u64>,
    /// IDs where the two sides changed `text` to different values, or that
    /// the Rust server gave out before: the record of `metadata.json` gets a
    /// new ID.
    pub renumbered: Vec<(u64, u64)>,
    /// IDs that `memories_v2` changed and `metadata.json` deleted: they stay.
    pub kept: Vec<u64>,
    /// IDs that `memories_v2` deleted and `metadata.json` changed: they come back.
    pub restored: Vec<u64>,
}

type Fields = Map<String, Value>;

fn without_reinforced(m: &Fields) -> Fields {
    let mut m = m.clone();
    m.remove(REINFORCED);
    m
}

fn later<'a>(a: Option<&'a Value>, b: Option<&'a Value>) -> Option<&'a Value> {
    match (a, b) {
        (Some(x), Some(y)) => Some(if x.as_str() > y.as_str() { x } else { y }),
        _ => a.or(b),
    }
}

/// Merge each field against the base: a field that one side changed takes
/// that change. Returns the merged fields and the fields that the two sides
/// changed to different values.
fn merge_fields(base: &Fields, py: &Fields, v2: &Fields) -> (Fields, Vec<String>) {
    let keys: std::collections::BTreeSet<&String> =
        base.keys().chain(py.keys()).chain(v2.keys()).collect();
    let (mut out, mut conflicts) = (Fields::new(), Vec::new());
    for key in keys {
        let (b, p, v) = (base.get(key), py.get(key), v2.get(key));
        let pick = if key == REINFORCED {
            later(p, v)
        } else if p == b || p == v {
            v
        } else if v == b {
            p
        } else {
            conflicts.push(key.clone());
            if key.ends_with("_at") { later(p, v) } else { v }
        };
        if let Some(x) = pick {
            out.insert(key.clone(), x.clone());
        }
    }
    (out, conflicts)
}

fn remap(r: &mut Record, ids: &HashMap<u64, u64>) {
    let map = |id: i64| {
        u64::try_from(id)
            .ok()
            .and_then(|u| ids.get(&u))
            .map_or(id, |n| *n as i64)
    };
    for field in [
        &mut r.supersedes,
        &mut r.superseded_by,
        &mut r.conflicts_with,
    ] {
        *field = field.map(map);
    }
    for link in r.links.iter_mut().flatten() {
        link.to_id = map(link.to_id);
    }
    if let Some(Value::Array(list)) = r.extra.get_mut("source_memory_ids") {
        for v in list.iter_mut() {
            if let Some(n) = v.as_i64() {
                *v = json!(map(n));
            }
        }
    }
}

/// The three-way merge of `py` (`metadata.json`) and `v2` (`memories_v2`)
/// against `base` (record JSON text by ID), field by field. A search on
/// either side only writes `last_reinforced_at`, which takes the later value
/// and is not a change. The Rust server gave out each ID below `rust_next`, so
/// a new record of `metadata.json` with such an ID gets a new ID, from
/// `first_new_id` up. With an empty base, each ID is new on the two sides, so
/// no delete comes through.
pub fn plan_merge(
    base: &HashMap<u64, String>,
    py: &BTreeMap<u64, Record>,
    v2: &BTreeMap<u64, Record>,
    rust_next: u64,
    first_new_id: u64,
) -> MergePlan {
    let mut plan = MergePlan::default();
    let mut next = first_new_id;
    let mut take = Vec::new();
    let mut renumber = |plan: &mut MergePlan, take: &mut Vec<(u64, Record)>, p: &Record| {
        plan.renumbered.push((p.id, next));
        let mut r = p.clone();
        r.id = next;
        take.push((p.id, r));
        next += 1;
    };
    let all: std::collections::BTreeSet<u64> = base
        .keys()
        .chain(py.keys())
        .chain(v2.keys())
        .copied()
        .collect();
    for id in all {
        let b: Option<Fields> = base.get(&id).and_then(|t| {
            let Ok(Value::Object(mut m)) = serde_json::from_str(t) else {
                return None;
            };
            m.remove("id");
            Some(m)
        });
        let fields = |r: Option<&Record>| r.map(Record::to_payload);
        let (pr, vr) = (py.get(&id), v2.get(&id));
        match (b, fields(pr), fields(vr)) {
            (_, None, None) | (None, None, Some(_)) => {}
            (Some(b), None, Some(v)) => {
                if without_reinforced(&v) == without_reinforced(&b) {
                    plan.deletes.push(id);
                } else {
                    plan.kept.push(id);
                }
            }
            (Some(b), Some(p), None) => {
                if without_reinforced(&p) != without_reinforced(&b) {
                    plan.restored.push(id);
                    take.push((id, pr.expect("present").clone()));
                }
            }
            // An ID that the Rust server gave out, then deleted: links of
            // memories_v2 can still point to it.
            (None, Some(_), None) if id < rust_next => {
                renumber(&mut plan, &mut take, pr.expect("present"));
            }
            (None, Some(_), None) => take.push((id, pr.expect("present").clone())),
            (b, Some(p), Some(v)) => {
                let (merged, conflicts) = merge_fields(&b.unwrap_or_default(), &p, &v);
                if conflicts.iter().any(|k| k == "text") {
                    renumber(&mut plan, &mut take, pr.expect("present"));
                    continue;
                }
                if !conflicts.is_empty() {
                    plan.resolved.push(id);
                }
                if merged != v {
                    take.push((id, Record::from_payload(id, merged)));
                }
            }
        }
    }
    // A record of metadata.json that points to a renumbered record points to
    // the record of metadata.json, not to the record of memories_v2.
    let new_ids: HashMap<u64, u64> = plan.renumbered.iter().copied().collect();
    for (_, r) in &mut take {
        remap(r, &new_ids);
    }
    plan.upserts = take;
    plan
}

/// Bring the writes that a Python server made after the last migration,
/// export or merge into `memories_v2`. The command does not change
/// `metadata.json` or the old collection. A run that stops can run again: it
/// uses the same new IDs.
pub async fn merge(
    data_dir: &Path,
    stores: &Stores<'_>,
    embed: &impl Embed,
    opts: &Options,
) -> Result<Value, String> {
    let started = std::time::Instant::now();
    let legacy = Legacy::read(data_dir)?;
    let meta = |k| {
        stores
            .state
            .get_meta(k)
            .map(|v| v.filter(|v| !v.is_empty()))
            .map_err(|e| e.to_string())
    };
    if meta(EXPORT_PENDING)?.as_deref() == Some(legacy.sha256.as_str()) {
        return Err(unfinished_export_message());
    }
    match meta(DONE_HASH)? {
        None => return Err("no migration finished: run `memories-server migrate`".into()),
        Some(h) if h == legacy.sha256 => {
            return Ok(json!({"status": "up_to_date", "metadata_sha256": legacy.sha256}));
        }
        Some(_) => {}
    }
    if meta(DONE_MODE)?.as_deref() == Some("shadow") {
        return Err(
            "the migration ran in shadow mode, so metadata.json is the canonical store: run `memories-server migrate --shadow`".into(),
        );
    }
    let base = stores.state.legacy_base().map_err(|e| e.to_string())?;
    let v2 = v2_records(stores).await?;
    // The ID numbers of an unfinished merge of the same file, else new ones.
    let pending: Option<Value> = meta(MERGE_PENDING)?
        .and_then(|t| serde_json::from_str(&t).ok())
        .filter(|p: &Value| p["sha"] == json!(legacy.sha256));
    let (rust_next, first_new) = match pending {
        Some(p) => (
            p["rust_next"].as_u64().unwrap_or(0),
            p["first_new"].as_u64().unwrap_or(0),
        ),
        None => {
            let rust_next = stores.state.next_id().map_err(|e| e.to_string())?;
            let max_id = legacy
                .records
                .keys()
                .chain(v2.keys())
                .max()
                .map_or(0, |m| m + 1);
            let first_new = rust_next.max(max_id);
            let p = json!({"sha": legacy.sha256, "rust_next": rust_next, "first_new": first_new});
            stores
                .state
                .set_meta(&[(MERGE_PENDING, &p.to_string())])
                .map_err(|e| e.to_string())?;
            (rust_next, first_new)
        }
    };
    let plan = plan_merge(&base, &legacy.records, &v2, rust_next, first_new);
    stores
        .state
        .raise_id_floor(first_new + plan.renumbered.len() as u64)
        .map_err(|e| e.to_string())?;

    let mut after = v2.clone();
    for id in &plan.deletes {
        after.remove(id);
    }
    for (_, r) in &plan.upserts {
        after.insert(r.id, r.clone());
    }
    let len_sum: usize = after
        .values()
        .map(|r| keyword::doc_tokens(text_of(r)).len())
        .sum();
    let avgdl = keyword::avgdl(after.values().map(text_of));
    for chunk in plan.deletes.chunks(BATCH) {
        stores.new.delete(chunk).await.map_err(|e| e.to_string())?;
    }
    let copy = migrate::may_copy(&legacy, stores, embed).await?;
    let pairs: Vec<(u64, &Record)> = plan.upserts.iter().map(|(o, r)| (*o, r)).collect();
    let size = if opts.batch == 0 { BATCH } else { opts.batch };
    let (copied, embedded) = write_points(
        &pairs,
        stores,
        embed,
        copy,
        avgdl,
        size,
        opts.stop_after_batches,
    )
    .await?;
    let count = stores.new.count(None).await.map_err(|e| e.to_string())?;
    if count != after.len() as u64 {
        return Err(format!(
            "memories_v2 has {count} records after the merge, not {}. Run the merge again",
            after.len()
        ));
    }
    stores
        .state
        .set_doc_lengths(len_sum as u64, after.len() as u64)
        .map_err(|e| e.to_string())?;
    stores
        .state
        .set_legacy_base(
            &base_of(legacy.records.values()),
            &[(DONE_HASH, &legacy.sha256), (MERGE_PENDING, "")],
        )
        .map_err(|e| e.to_string())?;
    let ids = |v: &[(u64, Record)]| v.iter().map(|(o, _)| *o).collect::<Vec<_>>();
    Ok(json!({
        "status": "merged",
        "collection": stores.new.collection(),
        "metadata_sha256": legacy.sha256,
        "base_records": base.len(),
        "records": count,
        "written": plan.upserts.len(),
        "written_ids": first(&ids(&plan.upserts)),
        "deleted": plan.deletes.len(),
        "deleted_ids": first(&plan.deletes),
        "resolved": plan.resolved.len(),
        "resolved_ids": first(&plan.resolved),
        "renumbered": plan.renumbered.len(),
        "renumbered_ids": plan.renumbered.iter().take(REPORT_IDS).map(|(o, n)| json!([o, n])).collect::<Vec<_>>(),
        "kept": plan.kept.len(),
        "kept_ids": first(&plan.kept),
        "restored": plan.restored.len(),
        "restored_ids": first(&plan.restored),
        "copied_vectors": copied,
        "embedded_vectors": embedded,
        "next_id": stores.state.next_id().map_err(|e| e.to_string())?,
        "seconds": started.elapsed().as_secs_f64(),
    }))
}

pub fn unfinished_export_message() -> String {
    "an export-legacy run stopped after it wrote metadata.json. Run `memories-server export-legacy` again to finish it".into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use memories_types::Link;

    fn rec(id: u64, text: &str, updated: &str) -> Record {
        Record {
            id,
            text: Some(text.into()),
            updated_at: Some(updated.into()),
            ..Default::default()
        }
    }

    fn map(records: &[Record]) -> BTreeMap<u64, Record> {
        records.iter().map(|r| (r.id, r.clone())).collect()
    }

    #[test]
    fn export_runs_only_when_memories_v2_is_the_canonical_store() {
        use ExportAction::*;
        assert_eq!(
            export_action(None, None, None, None),
            Export,
            "a new Rust install"
        );
        assert_eq!(
            export_action(Some("a"), Some("a"), None, Some("normal")),
            Export
        );
        assert_eq!(
            export_action(Some("b"), Some("a"), Some("b"), None),
            Export,
            "an export that stopped after the rename runs again"
        );
        for (m, d, mode) in [
            (Some("a"), None, None),
            (Some("b"), Some("a"), Some("normal")),
            (Some("a"), Some("a"), Some("shadow")),
            (None, Some("a"), Some("shadow")),
        ] {
            assert!(
                matches!(export_action(m, d, None, mode), Skip(_)),
                "{m:?} {d:?} {mode:?}"
            );
        }
        let Skip(reason) = export_action(Some("b"), Some("a"), Some("c"), None) else {
            panic!()
        };
        assert!(reason.contains("migrate --merge"), "{reason}");
    }

    #[test]
    fn an_export_that_deletes_most_of_the_data_needs_force() {
        assert!(check_export_size(100, 120, 120, 0).is_ok());
        assert!(check_export_size(0, 0, 0, 0).is_ok(), "a new install");
        for (records, base, entries, old_points) in [(0, 0, 5, 0), (49, 100, 100, 0), (10, 0, 0, 3)]
        {
            let err = check_export_size(records, base, entries, old_points).unwrap_err();
            assert!(err.contains("--force"), "{err}");
        }
    }

    #[test]
    fn the_new_file_keeps_the_old_order_then_adds_new_ids() {
        let records = map(&[
            rec(1, "a", ""),
            rec(3, "c", ""),
            rec(5, "e", ""),
            rec(9, "i", ""),
        ]);
        assert_eq!(legacy_order(&[5, 2, 1, 5], &records), [5, 1, 3, 9]);
        assert_eq!(legacy_order(&[], &records), [1, 3, 5, 9]);
    }

    #[test]
    fn an_atomic_write_keeps_the_old_file_as_bak() {
        let dir = std::env::temp_dir().join(format!("memories-rollback-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("metadata.json");
        write_atomic(&path, b"[1]").unwrap();
        assert!(!dir.join("metadata.json.bak").exists());
        write_atomic(&path, b"[2]").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"[2]");
        assert_eq!(
            std::fs::read(dir.join("metadata.json.bak")).unwrap(),
            b"[1]"
        );
        let names: Vec<_> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(names.len(), 2, "no temporary file stays: {names:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_change_on_one_side_wins_over_no_change() {
        let base_records = [
            rec(1, "a", "t0"),
            rec(2, "b", "t0"),
            rec(3, "c", "t0"),
            rec(4, "d", "t0"),
        ];
        let base: HashMap<u64, String> = base_of(base_records.iter()).into_iter().collect();
        // Python: changed 1, deleted 2, no change to 3 and 4.
        let py = map(&[rec(1, "a2", "t1"), rec(3, "c", "t0"), rec(4, "d", "t0")]);
        // Rust: no change to 1 and 2, changed 3, deleted 4.
        let v2 = map(&[rec(1, "a", "t0"), rec(2, "b", "t0"), rec(3, "c3", "t1")]);
        let plan = plan_merge(&base, &py, &v2, 0, 100);
        assert_eq!(plan.upserts, [(1, rec(1, "a2", "t1"))]);
        assert_eq!(plan.deletes, [2]);
        assert!(plan.renumbered.is_empty() && plan.kept.is_empty() && plan.restored.is_empty());
    }

    #[test]
    fn a_change_wins_over_a_delete_on_the_other_side() {
        let base: HashMap<u64, String> = base_of([rec(1, "a", "t0"), rec(2, "b", "t0")].iter())
            .into_iter()
            .collect();
        let py = map(&[rec(1, "a2", "t1")]);
        let v2 = map(&[rec(2, "b2", "t1")]);
        let plan = plan_merge(&base, &py, &v2, 0, 100);
        assert_eq!(plan.restored, [1]);
        assert_eq!(plan.upserts, [(1, rec(1, "a2", "t1"))]);
        assert_eq!(plan.kept, [2]);
        assert!(plan.deletes.is_empty());
    }

    #[test]
    fn the_same_new_id_on_the_two_sides_gives_the_python_record_a_new_id() {
        let base: HashMap<u64, String> = base_of([rec(1, "a", "t0")].iter()).into_iter().collect();
        let mut py_new = rec(2, "python new", "t1");
        py_new.supersedes = Some(1);
        let mut py_link = rec(3, "python links to 2", "t1");
        py_link.links = Some(vec![Link {
            to_id: 2,
            link_type: "related_to".into(),
            created_at: None,
        }]);
        let py = map(&[
            rec(1, "a", "t0"),
            py_new.clone(),
            py_link,
            rec(4, "same", "t1"),
        ]);
        let v2 = map(&[
            rec(1, "a", "t0"),
            rec(2, "rust new", "t1"),
            rec(4, "same", "t1"),
        ]);
        let plan = plan_merge(&base, &py, &v2, 0, 50);
        assert_eq!(plan.renumbered, [(2, 50)]);
        let got: BTreeMap<u64, &Record> = plan.upserts.iter().map(|(o, r)| (*o, r)).collect();
        assert_eq!(got.len(), 2, "4 is equal on the two sides: {got:?}");
        assert_eq!(got[&2].id, 50);
        assert_eq!(got[&2].supersedes, Some(1));
        assert_eq!(got[&3].id, 3);
        assert_eq!(got[&3].links.as_ref().unwrap()[0].to_id, 50);
    }

    #[test]
    fn a_field_conflict_keeps_the_later_time_and_no_field_change_is_lost() {
        let base: HashMap<u64, String> = base_of([rec(1, "a", "t0"), rec(2, "b", "t0")].iter())
            .into_iter()
            .collect();
        let mut p1 = rec(1, "a", "t2");
        p1.last_reinforced_at = Some("r1".into());
        let mut v1 = rec(1, "a", "t1");
        v1.last_reinforced_at = Some("r3".into());
        // Python pins 2; Rust only reinforces 2.
        let mut p2 = rec(2, "b", "t0");
        p2.pinned = Some(true);
        let mut v2_2 = rec(2, "b", "t0");
        v2_2.last_reinforced_at = Some("r9".into());
        let plan = plan_merge(
            &base,
            &map(&[p1.clone(), p2.clone()]),
            &map(&[v1, v2_2]),
            0,
            9,
        );
        assert_eq!(plan.resolved, [1], "only 1 has a conflict (updated_at)");
        let mut want1 = p1;
        want1.last_reinforced_at = Some("r3".into());
        let mut want2 = p2;
        want2.last_reinforced_at = Some("r9".into());
        assert_eq!(plan.upserts, [(1, want1), (2, want2)]);
    }

    #[test]
    fn a_reinforcement_is_not_a_change() {
        let base: HashMap<u64, String> =
            base_of([rec(1, "a", "t0"), rec(2, "b", "t0"), rec(3, "c", "t0")].iter())
                .into_iter()
                .collect();
        let reinforced = |r: Record| Record {
            last_reinforced_at: Some("r5".into()),
            ..r
        };
        // 1: only Rust reinforced. 2: only Python reinforced. 3: Python
        // deleted, Rust only reinforced.
        let py = map(&[rec(1, "a", "t0"), reinforced(rec(2, "b", "t0"))]);
        let v2 = map(&[
            reinforced(rec(1, "a", "t0")),
            rec(2, "b", "t0"),
            reinforced(rec(3, "c", "t0")),
        ]);
        let plan = plan_merge(&base, &py, &v2, 0, 9);
        assert_eq!(plan.upserts, [(2, reinforced(rec(2, "b", "t0")))]);
        assert_eq!(plan.deletes, [3]);
        assert!(plan.kept.is_empty() && plan.resolved.is_empty());
    }

    #[test]
    fn source_memory_ids_point_to_the_renumbered_record() {
        let mut p = rec(3, "summary", "t1");
        p.extra.insert("source_memory_ids".into(), json!([2, 1]));
        let py = map(&[rec(2, "py two", "t1"), p]);
        let v2 = map(&[rec(2, "rust two", "t1")]);
        let plan = plan_merge(&HashMap::new(), &py, &v2, 0, 20);
        assert_eq!(plan.renumbered, [(2, 20)]);
        let three = &plan.upserts.iter().find(|(o, _)| *o == 3).unwrap().1;
        assert_eq!(three.extra["source_memory_ids"], json!([20, 1]));
    }

    #[test]
    fn a_python_record_on_an_id_that_rust_gave_out_gets_a_new_id() {
        let base: HashMap<u64, String> = base_of([rec(1, "a", "t0")].iter()).into_iter().collect();
        // Rust gave out 2 and 3, then deleted 2. Python added 2 and 4.
        let py = map(&[
            rec(1, "a", "t0"),
            rec(2, "py two", "t1"),
            rec(4, "py four", "t1"),
        ]);
        let v2 = map(&[rec(1, "a", "t0"), rec(3, "rust three", "t1")]);
        let plan = plan_merge(&base, &py, &v2, 4, 10);
        assert_eq!(plan.renumbered, [(2, 10)]);
        let ids: Vec<(u64, u64)> = plan.upserts.iter().map(|(o, r)| (*o, r.id)).collect();
        assert_eq!(ids, [(2, 10), (4, 4)]);
    }

    #[test]
    fn with_no_base_no_delete_comes_through() {
        let base = HashMap::new();
        let py = map(&[rec(1, "a", "t0"), rec(5, "py only", "t0")]);
        let v2 = map(&[rec(1, "a", "t0"), rec(7, "rust only", "t0")]);
        let plan = plan_merge(&base, &py, &v2, 0, 8);
        assert_eq!(plan.upserts, [(5, rec(5, "py only", "t0"))]);
        assert!(plan.deletes.is_empty());
    }
}
