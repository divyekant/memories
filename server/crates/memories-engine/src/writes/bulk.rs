//! The bulk writes and relation writes of `MemoryEngine` v5.19.0:
//! `delete_by_source`, `delete_by_prefix`, `merge_memories`, `add_link`,
//! `remove_link`, `resolve_conflicts` and `deduplicate`.
//!
//! As in Python, the bulk deletes do not remove the links to the deleted
//! records, and they emit no event.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use memories_store::{Condition, Filter};
use memories_types::{Link, Record};
use serde_json::{Map, Value, json};

use super::{NewMemory, Result, WriteError, Writer, chars, is_pinned, now, source_of, text_of};
use crate::catalog::Entry;
use crate::policy::{
    TrustedAuthorship, is_project_source, validate_namespace_preserving_replacement,
    validate_project_write,
};
use crate::rank::PyDateTime;
use crate::rank::scores::py_round;
use crate::records::{py_str, truthy};

/// `VALID_LINK_TYPES`, sorted as Python prints it.
const LINK_TYPES: [&str; 5] = [
    "blocked_by",
    "caused_by",
    "reinforces",
    "related_to",
    "supersedes",
];
/// IDs in one store read.
const PAGE: usize = 1_000;
/// Neighbors of each record in `find_duplicates` (`search_k`).
const DUP_NEIGHBORS: usize = 5;

fn invalid(msg: impl Into<String>) -> WriteError {
    WriteError::Invalid(msg.into())
}

/// Adds the link to `from` with the checks of `add_link` that need no store.
fn push_link(from: &mut Record, to_id: i64, link_type: &str) -> Result<String> {
    let links = from.links.get_or_insert_with(Vec::new);
    if links
        .iter()
        .any(|l| l.to_id == to_id && l.link_type == link_type)
    {
        return Err(invalid(format!(
            "Link {} --{link_type}--> {to_id} already exists",
            from.id
        )));
    }
    let created_at = now();
    links.push(Link {
        to_id,
        link_type: link_type.to_owned(),
        created_at: Some(created_at.clone()),
    });
    Ok(created_at)
}

fn check_link(from_id: i64, to_id: i64, link_type: &str) -> Result<()> {
    if !LINK_TYPES.contains(&link_type) {
        return Err(invalid(format!(
            "Invalid link type: {link_type}. Valid types: ['blocked_by', 'caused_by', 'reinforces', 'related_to', 'supersedes']"
        )));
    }
    if from_id == to_id {
        return Err(invalid("A memory cannot link to itself"));
    }
    Ok(())
}

fn set_field(r: &mut Record, key: &str, v: Value) {
    let mut p = r.to_payload();
    p.insert(key.to_owned(), v);
    *r = Record::from_payload(r.id, p);
}

fn pop_field(r: &mut Record, key: &str) {
    let mut p = r.to_payload();
    if p.remove(key).is_some() {
        *r = Record::from_payload(r.id, p);
    }
}

/// `_when` of `resolve_conflicts`: the first date of `document_at`,
/// `created_at` and `timestamp` that parses. A date with no zone is UTC.
fn when(r: &Record) -> Option<PyDateTime> {
    ["document_at", "created_at", "timestamp"]
        .iter()
        .filter_map(|k| r.field(k).filter(truthy))
        .find_map(|v| {
            let text = match &v {
                Value::String(s) => s.clone(),
                other => py_str(other),
            };
            PyDateTime::fromisoformat(&text.replace('Z', "+00:00")).map(PyDateTime::or_utc)
        })
}

/// The near-duplicate pairs of `find_duplicates`, from the dense vectors in
/// record order. Each record compares only with records of the same source;
/// its `k` most similar records give pairs at `threshold` or more.
/// Returns `(i, j, similarity)` in the order that Python finds them.
pub fn duplicate_pairs(
    sources: &[String],
    vectors: &[Option<Vec<f32>>],
    threshold: f64,
    k: usize,
) -> Vec<(usize, usize, f32)> {
    let mut groups: HashMap<&str, Vec<usize>> = HashMap::new();
    for (i, s) in sources.iter().enumerate() {
        if vectors[i].is_some() {
            groups.entry(s.as_str()).or_default().push(i);
        }
    }
    let dot = |a: &[f32], b: &[f32]| a.iter().zip(b).map(|(x, y)| x * y).sum::<f32>();
    let mut neighbors: Vec<Vec<(usize, f32)>> = vec![Vec::new(); sources.len()];
    for members in groups.values() {
        for &i in members {
            let vi = vectors[i].as_deref().unwrap_or_default();
            let mut row: Vec<(usize, f32)> = members
                .iter()
                .filter(|&&j| j != i)
                .map(|&j| (j, dot(vi, vectors[j].as_deref().unwrap_or_default())))
                .collect();
            // np.argsort(row)[::-1]: high first; a tie puts the higher index first.
            let order = |a: &(usize, f32), b: &(usize, f32)| {
                b.1.total_cmp(&a.1).then_with(|| b.0.cmp(&a.0))
            };
            if row.len() > k {
                row.select_nth_unstable_by(k, order);
                row.truncate(k);
            }
            row.sort_by(order);
            neighbors[i] = row;
        }
    }
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for (i, row) in neighbors.iter().enumerate() {
        for &(j, sim) in row {
            let key = (i.min(j), i.max(j));
            if f64::from(sim) >= threshold && seen.insert(key) {
                out.push((key.0, key.1, sim));
            }
        }
    }
    out
}

impl Writer {
    /// The records of `ids` from the store, in pages.
    pub(super) async fn records(&self, ids: &[u64]) -> Result<Vec<Record>> {
        let mut out = Vec::with_capacity(ids.len());
        for page in ids.chunks(PAGE) {
            out.extend(self.e().store.get_many(page).await?);
        }
        Ok(out)
    }

    /// The records whose source matches, in ID order. Pinned records stay.
    async fn delete_matching(
        &self,
        matches: impl Fn(&str) -> bool,
        skip_snapshot: bool,
        dry_run: bool,
        reason: &str,
    ) -> Result<Value> {
        let _g = self.lock.lock().await;
        if self.e().catalog().iter().any(|(_, e)| !e.source_is_str) {
            // Python: `pattern in None` raises, so nothing is deleted (HTTP 500).
            return Err(invalid("a record has a source that is not a string"));
        }
        let candidates: Vec<u64> = self
            .e()
            .catalog()
            .iter()
            .filter(|(_, e)| matches(&e.source))
            .map(|(id, _)| id)
            .collect();
        let matching: Vec<Record> = self
            .records(&candidates)
            .await?
            .into_iter()
            .filter(|r| !is_pinned(r))
            .collect();
        let ids: Vec<u64> = matching.iter().map(|r| r.id).collect();
        if dry_run {
            return Ok(json!({"count": ids.len(), "would_delete": ids}));
        }
        if ids.is_empty() {
            return Ok(json!({"deleted_count": 0}));
        }
        if !skip_snapshot {
            self.snapshot(reason).await?;
        }
        self.remove(&matching).await?;
        Ok(json!({"deleted_count": ids.len()}))
    }

    /// `delete_by_source`: each record with `pattern` in its source (a
    /// substring match).
    pub async fn delete_by_source(
        &self,
        pattern: &str,
        skip_snapshot: bool,
        dry_run: bool,
    ) -> Result<Value> {
        self.delete_matching(
            |s| s.contains(pattern),
            skip_snapshot,
            dry_run,
            "pre_delete_source",
        )
        .await
    }

    /// `delete_by_prefix`: each record whose source starts with `prefix` (a
    /// plain prefix, with no "/" boundary).
    pub async fn delete_by_prefix(
        &self,
        prefix: &str,
        skip_snapshot: bool,
        dry_run: bool,
    ) -> Result<Value> {
        self.delete_matching(
            |s| s.starts_with(prefix),
            skip_snapshot,
            dry_run,
            "pre_delete_prefix",
        )
        .await
    }

    // ---- links -----------------------------------------------------------------

    /// `add_link`: a typed link from `from_id` to `to_id`.
    pub async fn add_link(&self, from_id: i64, to_id: i64, link_type: &str) -> Result<Value> {
        let _g = self.lock.lock().await;
        self.add_link_locked(from_id, to_id, link_type).await
    }

    async fn add_link_locked(&self, from_id: i64, to_id: i64, link_type: &str) -> Result<Value> {
        check_link(from_id, to_id, link_type)?;
        let Some(mut from) = self.record(from_id).await? else {
            return Err(invalid(format!("Source memory {from_id} not found")));
        };
        if !self.e().catalog().contains(to_id) {
            return Err(invalid(format!("Target memory {to_id} not found")));
        }
        let created_at = push_link(&mut from, to_id, link_type)?;
        self.e().store.replace_payload(&from).await?;
        self.e().catalog_mut().put(&from);
        self.linked(from_id, to_id, link_type, &source_of(&from));
        Ok(json!({"from_id": from_id, "to_id": to_id, "type": link_type, "created_at": created_at}))
    }

    fn linked(&self, from_id: i64, to_id: i64, link_type: &str, source: &str) {
        self.events.emit(
            "memory.linked",
            json!({"from_id": from_id, "to_id": to_id, "type": link_type, "source": source}),
        );
    }

    /// `remove_link`. As in Python, a record with no `links` gets an empty
    /// list when nothing is removed.
    pub async fn remove_link(&self, from_id: i64, to_id: i64, link_type: &str) -> Result<Value> {
        let _g = self.lock.lock().await;
        let Some(mut from) = self.record(from_id).await? else {
            return Ok(json!({"removed": false}));
        };
        let had_links = from.links.is_some();
        let links = from.links.get_or_insert_with(Vec::new);
        let before = links.len();
        links.retain(|l| !(l.to_id == to_id && l.link_type == link_type));
        let removed = links.len() < before;
        if removed && links.is_empty() {
            from.links = None;
        }
        if removed || !had_links {
            self.e().store.replace_payload(&from).await?;
            self.e().catalog_mut().put(&from);
        }
        Ok(json!({"removed": removed}))
    }

    // ---- merge -----------------------------------------------------------------

    /// `merge_memories`: add the merged text with a `supersedes` link to each
    /// record, then archive the records.
    pub async fn merge_memories(
        &self,
        ids: &[i64],
        merged_text: String,
        source: &str,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Value> {
        if ids.len() < 2 {
            return Err(invalid("merge_memories requires at least 2 IDs"));
        }
        let _g = self.lock.lock().await;
        let sources: Vec<String> = {
            let c = self.e().catalog();
            let mut out = Vec::with_capacity(ids.len());
            for &mid in ids {
                match c.get(mid) {
                    Some(e) => out.push(e.source.clone()),
                    None => return Err(WriteError::NotFound(format!("Memory {mid} not found"))),
                }
            }
            out
        };
        let refs: Vec<&str> = sources.iter().map(String::as_str).collect();
        validate_namespace_preserving_replacement(&refs, source, "merge")?;
        for s in &sources {
            if is_project_source(s) {
                validate_project_write(&merged_text, s, trusted)?;
            }
        }
        validate_project_write(&merged_text, source, trusted)?;
        // Python adds the record, then adds each link. This server writes the
        // links with the new point. A repeated ID fails as the second
        // `add_link` fails in Python: after the add, with no record archived.
        let mut holder = Record::default();
        let mut duplicate = None;
        for &mid in ids {
            if let Err(e) = push_link(&mut holder, mid, "supersedes") {
                duplicate = Some(e);
                break;
            }
        }
        let links = holder.links.unwrap_or_default();
        let mut extra = Map::new();
        extra.insert(
            "links".into(),
            serde_json::to_value(&links).unwrap_or_default(),
        );
        let item = NewMemory {
            text: merged_text,
            source: source.to_owned(),
            metadata: None,
        };
        let unique: Vec<u64> = ids.iter().map(|&i| i as u64).collect();
        let reserved = self.e().state.reserve_ids(1)?;
        let new_id = reserved.start as i64;
        // A repeated ID has no second step, so it needs no journal entry.
        if let Some(WriteError::Invalid(m)) = duplicate {
            self.put_new(vec![item], trusted, vec![extra], reserved)
                .await?;
            for l in &links {
                self.linked(new_id, l.to_id, "supersedes", source);
            }
            let to = m.split("--> ").nth(1).unwrap_or_default().to_owned();
            return Err(invalid(format!("Link {new_id} --supersedes--> {to}")));
        }
        let op = json!({"op": "merge", "ids": unique, "new": new_id});
        let entry = self.begin(&op)?;
        let steps = async {
            self.put_new(vec![item], trusted, vec![extra], reserved)
                .await?;
            for l in &links {
                self.linked(new_id, l.to_id, "supersedes", source);
            }
            let records = self.records(&unique).await?;
            self.archive(records, None).await
        };
        let done = steps.await;
        self.close(entry, &op, done).await?;
        Ok(json!({"id": new_id, "archived": ids}))
    }

    // ---- conflicts -------------------------------------------------------------

    /// `resolve_conflicts` with the `newest_wins` policy. `max` 0 has no limit.
    pub async fn resolve_conflicts(&self, dry_run: bool, max: usize) -> Result<Value> {
        let _g = self.lock.lock().await;
        let policy = "newest_wins";
        let now_iso = now();
        // The flagging records, in ID order. Python reads all records; only a
        // record with a `conflicts_with` value can start a resolution.
        let mut cache: BTreeMap<u64, Record> = BTreeMap::new();
        let filter = Filter {
            must_not: vec![Condition::is_empty("conflicts_with")],
            ..Default::default()
        };
        let mut offset = None;
        loop {
            let (page, next) = self
                .e()
                .store
                .scroll(Some(filter.clone()), offset, PAGE as u32)
                .await?;
            for mut r in page {
                self.e().overlay(&mut r);
                cache.insert(r.id, r);
            }
            match next {
                Some(o) => offset = Some(o),
                None => break,
            }
        }
        let order: Vec<u64> = cache.keys().copied().collect();
        let (mut resolved, mut review, mut orphaned) = (Vec::new(), Vec::new(), Vec::new());
        let mut dirty = BTreeSet::new();
        for mid in order {
            let (skip, cw) = {
                let m = &cache[&mid];
                let skip = Entry::of(m).archived || m.field("deferred").is_some_and(|v| truthy(&v));
                (skip, m.field("conflicts_with").filter(|v| !v.is_null()))
            };
            let (true, Some(cw)) = (!skip, cw) else {
                continue;
            };
            if max > 0 && resolved.len() + orphaned.len() >= max {
                break;
            }
            // `12.0 in _id_map` is true in Python.
            let as_id = cw
                .as_i64()
                .or_else(|| cw.as_f64().filter(|f| f.fract() == 0.0).map(|f| f as i64));
            let other_id = as_id
                .filter(|&id| self.e().catalog().contains(id))
                .map(|id| id as u64);
            if let Some(o) = other_id
                && !cache.contains_key(&o)
                && let Some(r) = self.record(o as i64).await?
            {
                cache.insert(o, r);
            }
            let m = &cache[&mid];
            let other = other_id.and_then(|o| cache.get(&o));
            let orphan = match other {
                None => Some("missing"),
                Some(o) if Entry::of(o).archived => Some("already_archived"),
                Some(_) => None,
            };
            if let Some(reason) = orphan {
                orphaned.push(json!({"id": mid, "conflicts_with": cw, "reason": reason}));
                if !dry_run {
                    let m = cache.get_mut(&mid).expect("in cache");
                    let outcome = format!("orphaned_{reason}");
                    set_field(
                        m,
                        "conflict_resolution",
                        json!({"policy": policy, "outcome": outcome, "other": cw, "at": now_iso}),
                    );
                    pop_field(m, "conflicts_with");
                    dirty.insert(mid);
                }
                continue;
            }
            let other = other.expect("not an orphan");
            let mut pair = None;
            let review_reason = if m.field("source") != other.field("source") {
                Some("cross_source")
            } else if let (Some(tm), Some(to)) = (when(m), when(other)) {
                // A tie goes to the flagging record: it was stored later.
                let m_wins = !tm.lt(&to).unwrap_or(false);
                let (winner, loser) = if m_wins { (m, other) } else { (other, m) };
                if is_pinned(loser) {
                    Some("pinned_loser")
                } else {
                    pair = Some((winner.id, loser.id));
                    None
                }
            } else {
                Some("undated")
            };
            let Some((winner_id, loser_id)) = pair else {
                let reason = review_reason.expect("set when there is no pair");
                review.push(json!({"id": mid, "conflicts_with": cw, "reason": reason}));
                let m = cache.get_mut(&mid).expect("in cache");
                if !dry_run && m.field("conflict_review") != Some(json!(reason)) {
                    set_field(m, "conflict_review", json!(reason));
                    dirty.insert(mid);
                }
                continue;
            };
            if !dry_run {
                let mut loser = cache.remove(&loser_id).expect("in cache");
                loser.archived = Some(true);
                loser.extra.remove("archived");
                loser.superseded_by = Some(winner_id as i64);
                loser.extra.remove("superseded_by");
                cache.insert(loser_id, loser);
                dirty.insert(loser_id);
                let winner = cache.get_mut(&winner_id).expect("in cache");
                let link = check_link(winner_id as i64, loser_id as i64, "supersedes")
                    .and_then(|_| push_link(winner, loser_id as i64, "supersedes"));
                if let Err(e) = link {
                    // Python archives the loser before `add_link` fails, and keeps the
                    // earlier changes: store them, so that the next call goes on.
                    self.store_all(&cache, &dirty).await?;
                    return Err(e);
                }
                let winner = cache.get_mut(&winner_id).expect("in cache");
                let source = source_of(winner);
                set_field(
                    winner,
                    "conflict_resolution",
                    json!({"policy": policy, "outcome": "won", "archived": loser_id, "at": now_iso}),
                );
                self.linked(winner_id as i64, loser_id as i64, "supersedes", &source);
                let m = cache.get_mut(&mid).expect("in cache");
                pop_field(m, "conflicts_with");
                pop_field(m, "conflict_review");
                dirty.extend([mid, winner_id, loser_id]);
            }
            resolved.push(json!({"kept": winner_id, "archived": loser_id, "flagger": mid}));
        }
        self.store_all(&cache, &dirty).await?;
        Ok(json!({
            "dry_run": dry_run, "policy": policy,
            "resolved": resolved, "needs_review": review, "orphaned": orphaned,
            "resolved_count": resolved.len(), "needs_review_count": review.len(),
            "orphaned_count": orphaned.len(),
        }))
    }

    /// Writes the changed records of `resolve_conflicts`.
    async fn store_all(&self, cache: &BTreeMap<u64, Record>, dirty: &BTreeSet<u64>) -> Result<()> {
        for id in dirty {
            let r = &cache[id];
            self.e().store.replace_payload(r).await?;
            self.e().catalog_mut().put(r);
        }
        Ok(())
    }

    // ---- deduplicate -----------------------------------------------------------

    /// `find_duplicates` from the stored dense vectors.
    async fn find_duplicates(&self, threshold: f64) -> Result<Vec<Value>> {
        if self.e().count() < 2 {
            return Ok(Vec::new());
        }
        let mut records = Vec::new();
        let mut offset = None;
        loop {
            let (page, next) = self.e().store.scroll(None, offset, PAGE as u32).await?;
            records.extend(page);
            match next {
                Some(o) => offset = Some(o),
                None => break,
            }
        }
        let ids: Vec<u64> = records.iter().map(|r| r.id).collect();
        let mut dense = HashMap::with_capacity(ids.len());
        for page in ids.chunks(PAGE) {
            for (id, v) in self.e().store.vectors(page).await? {
                dense.insert(id, v.dense);
            }
        }
        let sources: Vec<String> = records.iter().map(source_of).collect();
        let vectors: Vec<Option<Vec<f32>>> =
            ids.iter().map(|id| dense.remove(id).flatten()).collect();
        let k = DUP_NEIGHBORS.min(records.len());
        // shortcut: compares each pair of one source (Python compares all
        // pairs); a source with very many records makes this slow. Use a
        // filtered Qdrant search for each record if that becomes a problem.
        let pairs = {
            let sources = sources.clone();
            tokio::task::spawn_blocking(move || duplicate_pairs(&sources, &vectors, threshold, k))
                .await
                .map_err(std::io::Error::other)?
        };
        let text = |i: usize| chars(&text_of(&records[i]).unwrap_or_default(), 120);
        let mut out: Vec<(f64, Value)> = pairs
            .into_iter()
            .map(|(i, j, sim)| {
                let s = py_round(f64::from(sim), 4);
                let v = json!({"id_a": ids[i], "id_b": ids[j], "source": sources[i],
                               "similarity": s, "text_a": text(i), "text_b": text(j)});
                (s, v)
            })
            .collect();
        // Python sorts the rounded values, high first, with a stable sort.
        out.sort_by(|a, b| b.0.total_cmp(&a.0));
        Ok(out.into_iter().map(|(_, v)| v).collect())
    }

    /// `deduplicate`: remove the later record of each same-source pair.
    pub async fn deduplicate(&self, threshold: f64, dry_run: bool) -> Result<Value> {
        let pairs = self.find_duplicates(threshold).await?;
        if pairs.is_empty() {
            return Ok(json!({"duplicate_pairs": 0, "removed": 0, "dry_run": dry_run}));
        }
        let pair_ids = |p: &Value| {
            (
                p["id_a"].as_i64().unwrap_or(-1),
                p["id_b"].as_i64().unwrap_or(-1),
            )
        };
        if dry_run {
            let remove: HashSet<i64> = pairs
                .iter()
                .map(|p| {
                    let (x, y) = pair_ids(p);
                    x.max(y)
                })
                .collect();
            let shown: Vec<&Value> = pairs.iter().take(20).collect();
            return Ok(
                json!({"duplicate_pairs": pairs.len(), "would_remove": remove.len(),
                             "dry_run": true, "pairs": shown}),
            );
        }
        let _g = self.lock.lock().await;
        // The scan ran with no lock: check the pairs again.
        let pairs: Vec<(i64, i64)> = {
            let c = self.e().catalog();
            pairs
                .iter()
                .map(pair_ids)
                .filter(|&(a, b)| match (c.get(a), c.get(b)) {
                    (Some(x), Some(y)) => x.source == y.source,
                    _ => false,
                })
                .collect()
        };
        let remove: BTreeSet<u64> = pairs.iter().map(|&(a, b)| a.max(b) as u64).collect();
        if remove.is_empty() {
            return Ok(json!({"duplicate_pairs": 0, "removed": 0,
                             "remaining": self.e().count(), "dry_run": false}));
        }
        let ids: Vec<u64> = remove.into_iter().collect();
        // Python writes `_backup("pre_dedup")` first; a snapshot is the recovery point here.
        self.snapshot("pre_dedup").await?;
        let records = self.records(&ids).await?;
        self.remove(&records).await?;
        Ok(json!({"duplicate_pairs": pairs.len(), "removed": ids.len(),
                  "remaining": self.e().count(), "dry_run": false}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_pairs_stay_inside_a_source_and_keep_python_order() {
        let v = |x: f32, y: f32| {
            let n = (x * x + y * y).sqrt();
            Some(vec![x / n, y / n])
        };
        let sources: Vec<String> = ["a", "a", "b", "a", "a"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let vectors = vec![
            v(1.0, 0.0),
            v(1.0, 0.01),
            v(1.0, 0.0),
            v(0.0, 1.0),
            v(1.0, 0.05),
        ];
        let pairs = duplicate_pairs(&sources, &vectors, 0.9, 5);
        let ids: Vec<(usize, usize)> = pairs.iter().map(|p| (p.0, p.1)).collect();
        // Record 0 finds 1, then 4; record 1 finds 4. Source "b" is alone.
        assert_eq!(ids, vec![(0, 1), (0, 4), (1, 4)]);
        assert!(pairs.iter().all(|p| p.2 >= 0.9));
        // Only the k most similar of each record count.
        let near = duplicate_pairs(&sources, &vectors, 0.9, 1);
        assert_eq!(
            near.iter().map(|p| (p.0, p.1)).collect::<Vec<_>>(),
            vec![(0, 1), (1, 4)]
        );
    }

    #[test]
    fn when_reads_python_dates() {
        let r = |v: Value| Record::from_json(v).unwrap();
        let a = when(&r(json!({"id": 1, "document_at": "2025-01-01T00:00:00Z"}))).unwrap();
        let b = when(&r(
            json!({"id": 1, "document_at": "", "created_at": "2025-01-01T00:00:00"}),
        ))
        .unwrap();
        assert_eq!(a.lt(&b), Some(false));
        assert!(
            when(&r(
                json!({"id": 1, "document_at": "bad", "timestamp": "2024-01-01"})
            ))
            .is_some()
        );
        assert!(when(&r(json!({"id": 1, "document_at": "bad"}))).is_none());
    }

    #[test]
    fn link_checks_use_python_messages() {
        let mut from = Record::from_json(json!({"id": 3})).unwrap();
        assert_eq!(
            push_link(&mut from, 4, "related_to").map(|_| ()).ok(),
            Some(())
        );
        match push_link(&mut from, 4, "related_to") {
            Err(WriteError::Invalid(m)) => assert_eq!(m, "Link 3 --related_to--> 4 already exists"),
            other => panic!("{other:?}"),
        }
        assert!(matches!(
            check_link(1, 1, "related_to"),
            Err(WriteError::Invalid(_))
        ));
        assert!(check_link(1, 2, "supersedes").is_ok());
    }
}
