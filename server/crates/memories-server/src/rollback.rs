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
use serde_json::{Value, json};
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

/// The SHA-256 of the record JSON. The keys are in sorted order, so equal
/// records give equal hashes.
pub fn record_sha(r: &Record) -> String {
    hex(&Sha256::digest(r.to_json().to_string().as_bytes()))
}

pub fn base_of<'a>(records: impl Iterator<Item = &'a Record>) -> Vec<(u64, String)> {
    records.map(|r| (r.id, record_sha(r))).collect()
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
/// or a `metadata.json` that did not change after the last migration, export
/// or merge. Else a Python server wrote to it, and an export would replace
/// those writes.
pub fn export_action(
    metadata_sha: Option<&str>,
    done: Option<&str>,
    mode: Option<&str>,
) -> ExportAction {
    match (metadata_sha, done) {
        _ if mode == Some("shadow") => ExportAction::Skip(
            "the migration ran in shadow mode, so metadata.json is the canonical store".into(),
        ),
        (None, _) => ExportAction::Export,
        (Some(_), None) => ExportAction::Skip(
            "no migration finished, so metadata.json is the canonical store".into(),
        ),
        (Some(m), Some(d)) if m != d => ExportAction::Skip(
            "metadata.json changed after the last migration, export or merge: a Python server wrote to it. The export does not replace those writes. To update to the Rust server again, run `memories-server migrate --merge`".into(),
        ),
        _ => ExportAction::Export,
    }
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
/// passes. Then move the waiting extract jobs to a file. The command runs
/// again with the same result. A skip is a report with status `skipped`.
pub async fn export_legacy(
    data_dir: &Path,
    stores: &Stores<'_>,
    now_iso: &str,
) -> Result<Value, String> {
    let started = std::time::Instant::now();
    let path = metadata_path(data_dir);
    let metadata_sha = if path.exists() {
        Some(migrate::file_sha256(&path)?)
    } else {
        None
    };
    let meta = |k| stores.state.get_meta(k).map_err(|e| e.to_string());
    let (done, mode) = (meta(DONE_HASH)?, meta(DONE_MODE)?);
    let skip = |reason: String| Ok(json!({"status": "skipped", "reason": reason}));
    if let ExportAction::Skip(reason) =
        export_action(metadata_sha.as_deref(), done.as_deref(), mode.as_deref())
    {
        return skip(reason);
    }
    if !stores.new.exists().await.map_err(|e| e.to_string())? {
        return skip(format!(
            "collection {} does not exist: the Rust server has no records",
            stores.new.collection()
        ));
    }

    let records = v2_records(stores).await?;
    let ids: Vec<u64> = records.keys().copied().collect();
    // The old collection first: if a step fails, metadata.json is unchanged,
    // and the Python server refuses to start on the count check.
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

    let order = legacy_order(&old_file_ids(&path), &records);
    let entries: Vec<Value> = order.iter().map(|id| records[id].to_json()).collect();
    let bytes = serde_json::to_vec(&entries).map_err(|e| e.to_string())?;
    write_atomic(&path, &bytes)?;
    let sha = hex(&Sha256::digest(&bytes));
    let (jobs, jobs_path) = export_jobs(data_dir, now_iso)?;
    stores
        .state
        .set_legacy_base(
            &base_of(records.values()),
            &[
                (DONE_HASH, &sha),
                (DONE_MODE, mode.as_deref().unwrap_or("normal")),
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
    /// IDs that the two sides changed to the same text: the record with the
    /// later `updated_at` stays, with the later `last_reinforced_at`.
    pub resolved: Vec<u64>,
    /// IDs that the two sides changed to different texts, or that the two
    /// sides used for different new records: the record of `metadata.json`
    /// gets a new ID.
    pub renumbered: Vec<(u64, u64)>,
    /// IDs that `memories_v2` changed and `metadata.json` deleted: they stay.
    pub kept: Vec<u64>,
    /// IDs that `memories_v2` deleted and `metadata.json` changed: they come back.
    pub restored: Vec<u64>,
}

#[derive(Clone, Copy, PartialEq)]
enum Side {
    Same,
    Changed,
    Deleted,
    New,
}

fn side(base: Option<&String>, record: Option<&Record>) -> Side {
    match (base, record) {
        (_, None) => Side::Deleted,
        (None, Some(_)) => Side::New,
        (Some(b), Some(r)) if *b == record_sha(r) => Side::Same,
        _ => Side::Changed,
    }
}

/// The record that stays when the two sides changed a record to the same
/// text: the later `updated_at` (a tie keeps `v2`), with the later
/// `last_reinforced_at` of the two.
fn resolve(py: &Record, v2: &Record) -> Record {
    let mut out = if py.updated_at > v2.updated_at {
        py.clone()
    } else {
        v2.clone()
    };
    out.last_reinforced_at = py
        .last_reinforced_at
        .clone()
        .max(v2.last_reinforced_at.clone());
    out
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
}

/// The three-way merge of `py` (`metadata.json`) and `v2` (`memories_v2`)
/// against `base`. The Rust server gave out each ID below `rust_next`, so a
/// new record of `metadata.json` with such an ID gets a new ID, from
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
        let (p, v) = (py.get(&id), v2.get(&id));
        let (ps, vs) = (side(base.get(&id), p), side(base.get(&id), v));
        match (ps, vs) {
            // memories_v2 keeps its change, its delete or its new record.
            (Side::Same, _) | (Side::Deleted, Side::Deleted | Side::New) => {}
            (Side::Deleted, Side::Same) => plan.deletes.push(id),
            (Side::Deleted, Side::Changed) => plan.kept.push(id),
            // An ID that the Rust server gave out, then deleted: links of
            // memories_v2 can still point to it.
            (Side::New, Side::Deleted) if id < rust_next => {
                renumber(&mut plan, &mut take, p.expect("not deleted"));
            }
            (Side::Changed, Side::Same) | (Side::New, Side::Deleted) => {
                take.push((id, p.expect("not deleted").clone()));
            }
            (Side::Changed, Side::Deleted) => {
                plan.restored.push(id);
                take.push((id, p.expect("not deleted").clone()));
            }
            // Changed on the two sides, or a new record on the two sides.
            (Side::Changed | Side::New, Side::Changed | Side::New | Side::Same) => {
                let (p, v) = (p.expect("not deleted"), v.expect("not deleted"));
                if record_sha(p) == record_sha(v) {
                    continue;
                }
                if p.text == v.text {
                    plan.resolved.push(id);
                    let r = resolve(p, v);
                    if r != *v {
                        take.push((id, r));
                    }
                } else {
                    renumber(&mut plan, &mut take, p);
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
/// `metadata.json` or the old collection.
pub async fn merge(
    data_dir: &Path,
    stores: &Stores<'_>,
    embed: &impl Embed,
    opts: &Options,
) -> Result<Value, String> {
    let started = std::time::Instant::now();
    let legacy = Legacy::read(data_dir)?;
    let meta = |k| stores.state.get_meta(k).map_err(|e| e.to_string());
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
    let rust_next = stores.state.next_id().map_err(|e| e.to_string())?;
    let max_id = legacy
        .records
        .keys()
        .chain(v2.keys())
        .max()
        .map_or(0, |m| m + 1);
    let first_new = stores
        .state
        .raise_id_floor(max_id)
        .map_err(|e| e.to_string())?;
    let plan = plan_merge(&base, &legacy.records, &v2, rust_next, first_new);
    if !plan.renumbered.is_empty() {
        stores
            .state
            .reserve_ids(plan.renumbered.len() as u64)
            .map_err(|e| e.to_string())?;
    }

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
            &[(DONE_HASH, &legacy.sha256)],
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
            export_action(None, None, None),
            Export,
            "a new Rust install"
        );
        assert_eq!(export_action(Some("a"), Some("a"), Some("normal")), Export);
        for (m, d, mode) in [
            (Some("a"), None, None),
            (Some("b"), Some("a"), Some("normal")),
            (Some("a"), Some("a"), Some("shadow")),
            (None, Some("a"), Some("shadow")),
        ] {
            assert!(
                matches!(export_action(m, d, mode), Skip(_)),
                "{m:?} {d:?} {mode:?}"
            );
        }
        let Skip(reason) = export_action(Some("b"), Some("a"), None) else {
            panic!()
        };
        assert!(reason.contains("migrate --merge"), "{reason}");
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
    fn the_same_text_on_the_two_sides_keeps_the_later_update() {
        let base: HashMap<u64, String> = base_of([rec(1, "a", "t0"), rec(2, "b", "t0")].iter())
            .into_iter()
            .collect();
        let mut p1 = rec(1, "a", "t2");
        p1.last_reinforced_at = Some("r1".into());
        let mut v1 = rec(1, "a", "t1");
        v1.last_reinforced_at = Some("r3".into());
        let mut p2 = rec(2, "b", "t1");
        p2.pinned = Some(true);
        let mut v2_2 = rec(2, "b", "t1");
        v2_2.last_reinforced_at = Some("r9".into());
        let plan = plan_merge(&base, &map(&[p1.clone(), p2]), &map(&[v1, v2_2]), 0, 9);
        assert_eq!(plan.resolved, [1, 2]);
        let mut want = p1;
        want.last_reinforced_at = Some("r3".into());
        assert_eq!(
            plan.upserts,
            [(1, want)],
            "2: a tie keeps memories_v2, so no write"
        );
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
