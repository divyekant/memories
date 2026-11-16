//! The maintenance jobs of `MemoryEngine` and `consolidator.py` v5.19.0
//! (task P5-3): `enforce_policies`, `find_similar_clusters`, the clusters and
//! the locked replacement of a consolidation, the prune candidates and
//! `reembed`. Each job reads the records in pages of the store.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use memories_embed::TextKind;
use memories_store::NewPoint;
use memories_types::Record;
use serde_json::{Map, Value, json};

use super::{CHUNK, NewMemory, Result, Writer, avgdl, is_pinned, source_of, text_of};
use crate::keyword::doc_vector;
use crate::policy::{
    TrustedAuthorship, is_project_source, is_reserved_namespace_source, parse_memory_source,
    validate_namespace_preserving_replacement, validate_project_write,
};
use crate::rank::PyDateTime;
use crate::rank::scores::py_round;
use crate::records::truthy;
use crate::scope::Scope;
use crate::search::SearchArgs;

const DAY_US: i64 = 86_400_000_000;
/// Records in one store read.
const PAGE: usize = 1_000;
/// `find_clusters` defaults.
const CLUSTER_SIMILARITY: f64 = 0.75;
const CLUSTER_MIN: usize = 3;
const CLUSTER_MAX_CANDIDATES: usize = 500;
const CLUSTER_NEIGHBORS: usize = 10;
/// `find_prune_candidates` defaults.
const PRUNE_DETAIL_DAYS: i64 = 60;
const PRUNE_DECISION_DAYS: i64 = 120;
pub const CHANGED: &str = "cluster changed while consolidation was running";

/// `(now - ts).days` of two aware values.
fn age_days(now: &PyDateTime, ts: &PyDateTime) -> i64 {
    (now.utc_us() - ts.utc_us()).div_euclid(DAY_US)
}

fn parse_utc(text: &str) -> Option<PyDateTime> {
    PyDateTime::fromisoformat(&text.replace('Z', "+00:00")).map(PyDateTime::or_utc)
}

fn flag(r: &Record, key: &str) -> bool {
    r.field(key).is_some_and(|v| truthy(&v))
}

/// `meta.get(a) or meta.get(b) or ...` as a string.
fn first_str(r: &Record, keys: &[&str]) -> Option<String> {
    keys.iter()
        .filter_map(|k| r.field(k).filter(truthy))
        .next()
        .and_then(|v| v.as_str().map(str::to_owned))
}

// ---- promotion ---------------------------------------------------------------

const PROMOTION_KEYS: [&str; 18] = [
    "status",
    "owner",
    "project_id",
    "declaration_fingerprint",
    "classifier_provider",
    "classifier_model",
    "reviewer_provider",
    "reviewer_model",
    "reviewer_version",
    "capture_mode",
    "route",
    "proposal",
    "review",
    "evidence_fingerprint",
    "captured_at",
    "attempt_count",
    "target_memory_id",
    "rejected_until",
];

/// `is_promotion_maintenance_protected`.
/// shortcut: checks the envelope keys and the status, not the proposal and
/// the review, so a bad proposal is protected where Python is not (it keeps
/// more records). Use the full parse of task P5-6 when it is ported.
pub fn promotion_protected(r: &Record, now: &PyDateTime, retention_days: i64) -> bool {
    let Some(Value::Object(p)) = r.field("promotion") else {
        return false;
    };
    if p.len() != PROMOTION_KEYS.len() || !PROMOTION_KEYS.iter().all(|k| p.contains_key(*k)) {
        return false;
    }
    match p["status"].as_str() {
        Some("candidate" | "shadow_approved" | "deferred" | "failed" | "unreviewable") => true,
        Some("rejected") => {
            if retention_days < 0 {
                return true;
            }
            if let Some(until) = p.get("rejected_until").filter(|v| !v.is_null()) {
                return match until.as_str().and_then(parse_utc) {
                    Some(u) => now.utc_us() <= u.utc_us(),
                    None => true,
                };
            }
            let stamp = match &p["review"] {
                Value::Null => r
                    .field("updated_at")
                    .filter(truthy)
                    .unwrap_or_else(|| p["captured_at"].clone()),
                review => review.get("reviewed_at").cloned().unwrap_or(Value::Null),
            };
            match stamp.as_str().and_then(parse_utc) {
                Some(at) => now.utc_us() - at.utc_us() <= retention_days * DAY_US,
                None => true,
            }
        }
        _ => false,
    }
}

// ---- policies ----------------------------------------------------------------

/// The lifecycle fields of an extraction profile after the cascade of
/// `ExtractionProfiles.resolve`. `Null` is a field that no profile sets.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Policy {
    pub ttl_days: Value,
    pub confidence_threshold: Value,
    pub min_age_days: Value,
    pub confidence_half_life_days: Value,
}

/// The profiles of `extraction_profiles.json`.
#[derive(Clone, Debug, Default)]
pub struct Profiles(Map<String, Value>);

impl Profiles {
    /// Reads the file. A file that is absent gives no profile.
    pub fn load(path: &Path) -> std::io::Result<Self> {
        match std::fs::read(path) {
            Ok(bytes) => match serde_json::from_slice(&bytes) {
                Ok(Value::Object(m)) => Ok(Self(m)),
                Ok(_) => Ok(Self::default()),
                Err(e) => Err(std::io::Error::other(e)),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(e) => Err(e),
        }
    }

    pub fn from_value(v: Value) -> Self {
        match v {
            Value::Object(m) => Self(m),
            _ => Self::default(),
        }
    }

    /// The policy fields of `resolve(source)`: the explicit fields of each
    /// matching profile, from the least specific prefix to the most specific.
    pub fn resolve(&self, source: &str) -> Policy {
        let parts: Vec<&str> = source.split('/').collect();
        let matched: Vec<&Map<String, Value>> = (1..=parts.len())
            .rev()
            .filter_map(|i| self.0.get(&format!("{}/", parts[..i].join("/"))))
            .filter_map(Value::as_object)
            .collect();
        let mut p = Policy::default();
        for profile in matched.iter().rev() {
            let explicit = profile
                .get("_explicit_fields")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            for f in explicit.iter().filter_map(Value::as_str) {
                let Some(v) = profile.get(f) else { continue };
                match f {
                    "ttl_days" => p.ttl_days = v.clone(),
                    "confidence_threshold" => p.confidence_threshold = v.clone(),
                    "min_age_days" => p.min_age_days = v.clone(),
                    "confidence_half_life_days" => p.confidence_half_life_days = v.clone(),
                    _ => {}
                }
            }
        }
        p
    }
}

/// `MemoryEngine.compute_confidence(anchor, half_life_days)`.
fn confidence(anchor: Option<&str>, half_life: f64, now: &PyDateTime) -> f64 {
    let Some(ts) = anchor.and_then(PyDateTime::fromisoformat) else {
        return 0.0;
    };
    let age = now.seconds_since(&ts.or_utc()) / 86_400.0;
    if age < 0.0 {
        return 1.0;
    }
    let hl = if half_life <= 0.0 { 90.0 } else { half_life };
    0.5f64.powf(age / hl)
}

/// The reasons of the policy rules for one record: `(age_days, confidence, reasons)`.
fn evaluate(r: &Record, profiles: &Profiles, now: &PyDateTime) -> (i64, f64, Vec<Value>) {
    let source = source_of(r);
    let policy = profiles.resolve(&source);
    let anchor = first_str(r, &["updated_at", "created_at", "timestamp"]);
    let age = anchor
        .as_deref()
        .and_then(parse_utc)
        .map_or(0, |ts| age_days(now, &ts));
    // `policy.get("confidence_half_life_days") or 90.0`.
    let half_life = policy
        .confidence_half_life_days
        .as_f64()
        .filter(|v| *v != 0.0)
        .unwrap_or(90.0);
    let conf = confidence(anchor.as_deref(), half_life, now);
    let mut reasons = Vec::new();
    if let Some(ttl) = policy.ttl_days.as_f64()
        && age as f64 > ttl
    {
        reasons.push(json!({"rule": "ttl", "ttl_days": policy.ttl_days,
                            "age_days": age, "prefix": source}));
    }
    if let (Some(t), Some(m)) = (
        policy.confidence_threshold.as_f64(),
        policy.min_age_days.as_f64(),
    ) && conf < t
        && age as f64 > m
    {
        reasons.push(
            json!({"rule": "confidence", "threshold": policy.confidence_threshold,
                            "confidence": py_round(conf, 4),
                            "min_age_days": policy.min_age_days, "prefix": source}),
        );
    }
    (age, conf, reasons)
}

fn action(r: &Record, age: i64, conf: f64, reasons: Vec<Value>, kind: &str) -> Value {
    json!({"memory_id": r.id, "source": source_of(r), "age_days": age,
           "confidence": py_round(conf, 4), "reasons": reasons, "action": kind})
}

// ---- prune -------------------------------------------------------------------

/// `find_prune_candidates`: the IDs of the stale records that no search
/// returned, in record order.
pub fn prune_candidates(
    records: &[Record],
    unretrieved: &HashSet<i64>,
    now: &PyDateTime,
    retention_days: i64,
) -> Vec<i64> {
    records
        .iter()
        .filter(|r| {
            let source = r.field("source");
            !source
                .as_ref()
                .and_then(Value::as_str)
                .is_some_and(|s| s.starts_with("project/"))
        })
        .filter(|r| !promotion_protected(r, now, retention_days))
        .filter(|r| !flag(r, "pinned") && !flag(r, "archived"))
        .filter(|r| unretrieved.contains(&(r.id as i64)))
        .filter(|r| {
            let Some(created) = first_str(r, &["created_at", "timestamp"]) else {
                return false;
            };
            let Some(created) = parse_utc(&created) else {
                return false;
            };
            let category = r
                .field("category")
                .and_then(|v| v.as_str().map(str::to_lowercase))
                .unwrap_or_else(|| "detail".into());
            let limit = if category == "decision" || category == "learning" {
                PRUNE_DECISION_DAYS
            } else {
                PRUNE_DETAIL_DAYS
            };
            age_days(now, &created) > limit
        })
        .map(|r| r.id as i64)
        .collect()
}

// ---- Python set order --------------------------------------------------------

/// A CPython `set` of ints, for its iteration order.
struct PySet {
    table: Vec<Option<i64>>,
    used: usize,
}

impl PySet {
    fn new() -> Self {
        Self {
            table: vec![None; 8],
            used: 0,
        }
    }

    fn mask(&self) -> usize {
        self.table.len() - 1
    }

    /// The slot of `key` (or the empty slot for it): `set_add_entry` probing.
    fn slot(table: &[Option<i64>], key: i64) -> (usize, bool) {
        let mask = table.len() - 1;
        let hash = if key == -1 { -2i64 } else { key } as u64;
        let mut perturb = hash;
        let mut i = (hash as usize) & mask;
        loop {
            let probes = if i + 9 <= mask { 9 } else { 0 };
            for j in 0..=probes {
                match table[i + j] {
                    None => return (i + j, false),
                    Some(k) if k == key => return (i + j, true),
                    _ => {}
                }
            }
            perturb >>= 5;
            i = (i
                .wrapping_mul(5)
                .wrapping_add(1)
                .wrapping_add(perturb as usize))
                & mask;
        }
    }

    fn resize(&mut self, minused: usize) {
        let mut size = 8;
        while size <= minused {
            size <<= 1;
        }
        let old = std::mem::replace(&mut self.table, vec![None; size]);
        for k in old.into_iter().flatten() {
            let (s, _) = Self::slot(&self.table, k);
            self.table[s] = Some(k);
        }
    }

    fn add(&mut self, key: i64) {
        let (s, found) = Self::slot(&self.table, key);
        if found {
            return;
        }
        self.table[s] = Some(key);
        self.used += 1;
        if self.used * 5 >= self.mask() * 3 {
            let n = if self.used > 50_000 {
                self.used * 2
            } else {
                self.used * 4
            };
            self.resize(n);
        }
    }

    /// `set_merge(self, other)`.
    fn merge(&mut self, other: &PySet) {
        if other.used == 0 {
            return;
        }
        if (self.used + other.used) * 5 >= self.mask() * 3 {
            self.resize((self.used + other.used) * 2);
        }
        if self.used == 0 && self.mask() == other.mask() {
            self.table = other.table.clone();
            self.used = other.used;
            return;
        }
        for k in other.table.iter().flatten() {
            let (s, found) = Self::slot(&self.table, *k);
            if !found {
                self.table[s] = Some(*k);
                self.used += 1;
            }
        }
    }

    fn iter(&self) -> impl Iterator<Item = i64> + '_ {
        self.table.iter().flatten().copied()
    }
}

/// The iteration order of `{a...} | {b...}` in CPython.
pub fn py_union_order(a: &[i64], b: &[i64]) -> Vec<i64> {
    let build = |keys: &[i64]| {
        let mut s = PySet::new();
        keys.iter().for_each(|&k| s.add(k));
        s
    };
    let (sa, sb) = (build(a), build(b));
    let mut out = PySet::new();
    out.merge(&sa);
    out.merge(&sb);
    out.iter().collect()
}

/// `find_similar_clusters` from the pairs of `find_duplicates`
/// (`id_a`, `id_b`, rounded `similarity`), in the Python order.
pub fn similar_clusters(pairs: &[(i64, i64, f64)], threshold: f64) -> Vec<Vec<i64>> {
    fn find(parent: &mut HashMap<i64, i64>, mut x: i64) -> i64 {
        while parent.get(&x).copied().unwrap_or(x) != x {
            let p = parent[&x];
            let gp = parent.get(&p).copied().unwrap_or(p);
            parent.insert(x, gp);
            x = gp;
        }
        x
    }
    let mut parent = HashMap::new();
    for &(a, b, _) in pairs {
        let (ra, rb) = (find(&mut parent, a), find(&mut parent, b));
        if ra != rb {
            parent.insert(ra, rb);
        }
    }
    let ids_a: Vec<i64> = pairs.iter().map(|p| p.0).collect();
    let ids_b: Vec<i64> = pairs.iter().map(|p| p.1).collect();
    let mut groups: Vec<(i64, Vec<i64>)> = Vec::new();
    for mid in py_union_order(&ids_a, &ids_b) {
        let root = find(&mut parent, mid);
        match groups.iter_mut().find(|(r, _)| *r == root) {
            Some((_, g)) => g.push(mid),
            None => groups.push((root, vec![mid])),
        }
    }
    let mut sim: HashMap<(i64, i64), f64> = HashMap::new();
    for &(a, b, s) in pairs {
        sim.insert((a, b), s);
        sim.insert((b, a), s);
    }
    let mut out = Vec::new();
    for (_, ids) in groups {
        if ids.len() < 2 {
            continue;
        }
        let mut kept: Vec<i64> = ids
            .iter()
            .copied()
            .filter(|&m| {
                let similar = ids
                    .iter()
                    .filter(|&&o| o != m && sim.get(&(m, o)).copied().unwrap_or(0.0) >= threshold)
                    .count();
                similar as f64 >= (ids.len() - 1) as f64 / 2.0
            })
            .collect();
        if kept.len() >= 2 {
            kept.sort_unstable();
            out.push(kept);
        }
    }
    out
}

/// A random sample of `n` items (Python `random.sample`, not the same picks).
fn sample<T>(mut items: Vec<T>, n: usize, mut seed: u64) -> Vec<T> {
    seed |= 1;
    for i in 0..n.min(items.len()) {
        // xorshift64
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        let j = i + (seed % (items.len() - i) as u64) as usize;
        items.swap(i, j);
    }
    items.truncate(n);
    items
}

fn member_str(m: &Value, key: &str) -> String {
    match m.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(v) => crate::records::py_str(v),
    }
}

fn member_flag(m: &Value, key: &str) -> bool {
    m.get(key).is_some_and(truthy)
}

impl Writer {
    /// The records in ID order, one page at a time. `f` gets each page.
    async fn each_page(&self, mut f: impl FnMut(Vec<Record>)) -> Result<()> {
        let ids: Vec<u64> = self.e().catalog().iter().map(|(id, _)| id).collect();
        for page in ids.chunks(PAGE) {
            let mut records = self.e().store.get_many(page).await?;
            records.sort_by_key(|r| r.id);
            for r in &mut records {
                self.e().overlay(r);
            }
            f(records);
        }
        Ok(())
    }

    /// `enforce_policies`: archive each record that a TTL or confidence rule
    /// of its extraction profile selects. Each candidate is evaluated again
    /// under the write lock before the archive.
    pub async fn enforce_policies(
        &self,
        dry_run: bool,
        profiles: &Profiles,
        now: &PyDateTime,
    ) -> Result<Value> {
        let (mut excl_pinned, mut excl_archived, mut excl_promotion, mut scanned) = (0, 0, 0, 0);
        let mut actions = Vec::new();
        let kind = if dry_run { "would_archive" } else { "archived" };
        self.each_page(|page| {
            for r in page {
                if flag(&r, "archived") {
                    excl_archived += 1;
                    continue;
                }
                if flag(&r, "pinned") {
                    excl_pinned += 1;
                    continue;
                }
                // Python uses the default retention (90 days) here.
                if promotion_protected(&r, now, 90) {
                    excl_promotion += 1;
                    continue;
                }
                scanned += 1;
                let (age, conf, reasons) = evaluate(&r, profiles, now);
                if !reasons.is_empty() {
                    actions.push(action(&r, age, conf, reasons, kind));
                }
            }
        })
        .await?;
        let mut stale = 0;
        if !dry_run && !actions.is_empty() {
            let _g = self.lock.lock().await;
            let archived_at = now.isoformat_utc();
            let mut done = Vec::new();
            for a in &actions {
                let id = a["memory_id"].as_i64().unwrap_or(-1);
                let Some(mut r) = self.record(id).await? else {
                    stale += 1;
                    continue;
                };
                if flag(&r, "archived") || flag(&r, "pinned") || promotion_protected(&r, now, 90) {
                    stale += 1;
                    continue;
                }
                let (age, conf, reasons) = evaluate(&r, profiles, now);
                let Some(first) = reasons.first() else {
                    stale += 1;
                    continue;
                };
                let rule = first["rule"].as_str().unwrap_or_default().to_owned();
                let prefix = first["prefix"].as_str().unwrap_or_default();
                let mut fields = Map::new();
                fields.insert("archived".into(), json!(true));
                fields.insert("_policy_archived_reason".into(), json!(rule));
                fields.insert(
                    "_policy_archived_policy".into(),
                    json!(format!("{prefix} {rule}")),
                );
                fields.insert("_policy_archived_at".into(), json!(archived_at));
                fields.insert(
                    "_policy_archived_confidence".into(),
                    json!(py_round(conf, 4)),
                );
                fields.insert("_policy_archived_age_days".into(), json!(age));
                self.e().store.set_payload(&[r.id], fields.clone()).await?;
                let mut p = r.to_payload();
                p.extend(fields);
                r = Record::from_payload(r.id, p);
                self.e().catalog_mut().put(&r);
                done.push(action(&r, age, conf, reasons, "archived"));
            }
            actions = done;
        }
        let mut by_rule = Map::from_iter([
            ("ttl".to_owned(), json!(0)),
            ("confidence".into(), json!(0)),
        ]);
        for a in &actions {
            for reason in a["reasons"].as_array().into_iter().flatten() {
                if let Some(n) = reason["rule"].as_str().and_then(|k| by_rule.get_mut(k)) {
                    *n = json!(n.as_i64().unwrap_or(0) + 1);
                }
            }
        }
        let mut summary = json!({
            "candidates_scanned": scanned, "by_rule": by_rule,
            "excluded_promotion": excl_promotion, "excluded_pinned": excl_pinned,
            "excluded_already_archived": excl_archived,
        });
        summary[kind] = json!(actions.len());
        if stale > 0 {
            summary["skipped_stale"] = json!(stale);
        }
        Ok(json!({"dry_run": dry_run, "actions": actions, "summary": summary}))
    }

    /// `find_similar_clusters(threshold)`: clusters of record IDs.
    pub async fn similar_clusters(&self, threshold: f64) -> Result<Vec<Vec<i64>>> {
        if self.e().count() < 2 {
            return Ok(Vec::new());
        }
        let pairs: Vec<(i64, i64, f64)> = self
            .find_duplicates(threshold)
            .await?
            .iter()
            .map(|p| {
                (
                    p["id_a"].as_i64().unwrap_or(-1),
                    p["id_b"].as_i64().unwrap_or(-1),
                    p["similarity"].as_f64().unwrap_or(0.0),
                )
            })
            .collect();
        Ok(similar_clusters(&pairs, threshold))
    }

    /// The records of `find_prune_candidates`, read in pages.
    pub async fn prune_candidates(
        &self,
        unretrieved: &HashSet<i64>,
        now: &PyDateTime,
        retention_days: i64,
    ) -> Result<Vec<i64>> {
        let mut out = Vec::new();
        self.each_page(|page| {
            out.extend(prune_candidates(&page, unretrieved, now, retention_days))
        })
        .await?;
        Ok(out)
    }

    /// `find_clusters`: groups of at least 3 similar records (vector
    /// similarity 0.75 or more) for consolidation. Each member is the record
    /// as JSON. `seed` picks the sample when there are more than 500 candidates.
    pub async fn consolidation_clusters(
        &self,
        source_prefix: &str,
        retention_days: i64,
        seed: u64,
    ) -> Result<Vec<Vec<Value>>> {
        let now = PyDateTime::now_utc();
        let mut candidates: Vec<Record> = Vec::new();
        self.each_page(|page| {
            candidates.extend(page.into_iter().filter(|r| {
                let source = source_of(r);
                !flag(r, "pinned")
                    && !flag(r, "archived")
                    && !parse_memory_source(&source).is_some_and(|p| p.namespace == "project")
                    && !promotion_protected(r, &now, retention_days)
                    && (source_prefix.is_empty() || source.starts_with(source_prefix))
            }));
        })
        .await?;
        if candidates.len() > CLUSTER_MAX_CANDIDATES {
            tracing::info!(
                count = candidates.len(),
                "Consolidation: capping candidates to {CLUSTER_MAX_CANDIDATES}"
            );
            candidates = sample(candidates, CLUSTER_MAX_CANDIDATES, seed);
        }
        let mut clustered: HashSet<i64> = HashSet::new();
        let mut clusters = Vec::new();
        for seed_rec in candidates {
            let id = seed_rec.id as i64;
            if clustered.contains(&id) {
                continue;
            }
            let seed_source = source_of(&seed_rec);
            let reserved = is_reserved_namespace_source(&seed_source);
            let args = SearchArgs {
                query: text_of(&seed_rec).unwrap_or_default(),
                k: CLUSTER_NEIGHBORS,
                scope: Scope {
                    source_exact: reserved.then(|| seed_source.clone()),
                    source_prefix: (!source_prefix.is_empty()).then(|| source_prefix.to_owned()),
                    ..Scope::default()
                },
                ..SearchArgs::default()
            };
            let hits = self.e().search(&args, true).await?;
            let mut cluster = vec![seed_rec.to_json()];
            let mut ids = vec![id];
            for hit in hits {
                let hid = hit["id"].as_i64().unwrap_or(-1);
                if hid == id
                    || clustered.contains(&hid)
                    || member_flag(&hit, "pinned")
                    || member_flag(&hit, "archived")
                {
                    continue;
                }
                let hit_source = member_str(&hit, "source");
                if reserved {
                    if hit_source != seed_source {
                        continue;
                    }
                } else if is_reserved_namespace_source(&hit_source) {
                    continue;
                }
                if hit["similarity"].as_f64().unwrap_or(0.0) >= CLUSTER_SIMILARITY {
                    ids.push(hid);
                    cluster.push(hit);
                }
            }
            if cluster.len() >= CLUSTER_MIN {
                clustered.extend(ids);
                clusters.push(cluster);
            }
        }
        Ok(clusters)
    }

    /// `replace_consolidation_cluster`: under the write lock, check that no
    /// member changed, add the new texts, then delete the members. A failed
    /// check gives `{"error": ...}` and changes nothing.
    pub async fn replace_consolidation_cluster(
        &self,
        cluster: &[Value],
        new_texts: &[String],
        source: &str,
        category: &str,
    ) -> Result<Value> {
        let old_ids: Vec<i64> = cluster.iter().filter_map(|m| m["id"].as_i64()).collect();
        if old_ids.is_empty() {
            return Ok(json!({"error": "consolidation cluster is empty"}));
        }
        let _g = self.lock.lock().await;
        let mut sources = Vec::with_capacity(cluster.len());
        for m in cluster {
            let id = m["id"].as_i64().unwrap_or(-1);
            let Some(current) = self.record(id).await? else {
                return Ok(json!({"error": CHANGED}));
            };
            let current = current.to_json();
            for f in ["text", "source", "pinned", "archived", "updated_at"] {
                if current.get(f) != m.get(f) {
                    return Ok(json!({"error": CHANGED}));
                }
            }
            sources.push(member_str(&current, "source"));
        }
        let refs: Vec<&str> = sources.iter().map(String::as_str).collect();
        validate_namespace_preserving_replacement(&refs, source, "consolidation")?;
        let trusted = TrustedAuthorship::system(old_ids.clone());
        for text in new_texts {
            for s in &sources {
                if is_project_source(s) {
                    validate_project_write(text, s, Some(&trusted))?;
                }
            }
            validate_project_write(text, source, Some(&trusted))?;
        }
        let items = new_texts
            .iter()
            .map(|t| NewMemory {
                text: t.clone(),
                source: source.to_owned(),
                metadata: Some(Map::from_iter([
                    ("category".to_owned(), json!(category)),
                    ("consolidated_from".to_owned(), json!(old_ids)),
                ])),
            })
            .collect();
        let added = self
            .add_locked(items, false, Some(&trusted), Vec::new())
            .await?;
        if added.is_empty() {
            return Ok(json!({"error": "add_memories stored nothing; originals left untouched"}));
        }
        // `delete_memories(old_ids)`: pinned records stay; more than 10 make a snapshot.
        let unique: Vec<u64> = old_ids
            .iter()
            .map(|&i| i as u64)
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect();
        let delete: Vec<Record> = self
            .records(&unique)
            .await?
            .into_iter()
            .filter(|r| !is_pinned(r))
            .collect();
        if !delete.is_empty() && unique.len() > super::SNAPSHOT_OVER {
            self.snapshot("pre_delete_batch").await?;
        }
        self.remove(&delete).await?;
        if delete.len() != old_ids.len() {
            return Ok(
                json!({"error": "consolidation replacement did not delete every original",
                             "added_ids": added}),
            );
        }
        Ok(json!({"added_ids": added, "deleted_ids": old_ids}))
    }

    /// `reembed` with the current model: a new dense vector for each record,
    /// one page at a time. The keyword vectors and the payloads stay.
    /// Returns the record count.
    pub async fn reembed(&self) -> Result<usize> {
        let _g = self.lock.lock().await;
        self.snapshot("pre_reembed").await?;
        let total = self.e().count();
        let (sum, count) = self.e().state.doc_lengths()?;
        let avg = avgdl(sum as i64, count as i64);
        let mut offset = None;
        loop {
            let (records, next) = self.e().store.scroll(None, offset, CHUNK as u32).await?;
            if !records.is_empty() {
                let ids: Vec<u64> = records.iter().map(|r| r.id).collect();
                let mut stored = self.e().store.vectors(&ids).await?;
                let texts: Vec<String> = records
                    .iter()
                    .map(|r| text_of(r).unwrap_or_default())
                    .collect();
                let dense = self
                    .e()
                    .embedder
                    .embed(texts.clone(), TextKind::Document)
                    .await?;
                let points = records
                    .into_iter()
                    .zip(dense)
                    .zip(texts)
                    .map(|((record, dense), text)| {
                        let keyword = stored
                            .remove(&record.id)
                            .and_then(|v| v.keyword)
                            .unwrap_or_else(|| doc_vector(&text, avg));
                        NewPoint {
                            record,
                            dense,
                            keyword,
                        }
                    })
                    .collect();
                self.e().store.upsert(points, self.e().dim).await?;
            }
            match next {
                Some(o) => offset = Some(o),
                None => break,
            }
        }
        Ok(total)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn now() -> PyDateTime {
        PyDateTime::fromisoformat("2026-11-16T12:00:00+00:00").unwrap()
    }

    fn rec(v: Value) -> Record {
        Record::from_json(v).unwrap()
    }

    #[test]
    fn prune_takes_old_unretrieved_records_by_category() {
        let records = vec![
            rec(json!({"id": 1, "source": "a/x", "created_at": "2026-09-01T00:00:00Z"})),
            rec(
                json!({"id": 2, "source": "a/x", "created_at": "2026-09-01T00:00:00Z", "category": "Decision"}),
            ),
            rec(json!({"id": 3, "source": "a/x", "created_at": "2026-01-01T00:00:00"})),
            rec(
                json!({"id": 4, "source": "a/x", "created_at": "2026-01-01T00:00:00Z", "pinned": true}),
            ),
            rec(
                json!({"id": 5, "source": "project/p/notes", "created_at": "2026-01-01T00:00:00Z"}),
            ),
            rec(json!({"id": 6, "source": "a/x", "timestamp": "2026-01-01T00:00:00Z"})),
            rec(json!({"id": 7, "source": "a/x", "created_at": "bad"})),
            rec(json!({"id": 8, "source": "a/x", "created_at": "2026-01-01T00:00:00Z"})),
            rec(json!({"id": 9, "source": "a/x", "created_at": "2026-09-17T11:00:00Z"})),
        ];
        let unretrieved: HashSet<i64> = [1, 2, 3, 4, 5, 6, 7, 9].into_iter().collect();
        // 1: 76 days > 60. 2: a decision, 76 <= 120. 8: retrieved. 9: 60 days, not more.
        assert_eq!(
            prune_candidates(&records, &unretrieved, &now(), 90),
            vec![1, 3, 6]
        );
    }

    fn promo(status: &str, extra: Value) -> Record {
        let mut p: Map<String, Value> = PROMOTION_KEYS
            .iter()
            .map(|k| ((*k).to_owned(), Value::Null))
            .collect();
        p.insert("status".into(), json!(status));
        p.insert("captured_at".into(), json!("2026-11-01T00:00:00Z"));
        if let Value::Object(m) = extra {
            p.extend(m);
        }
        rec(json!({"id": 1, "promotion": p}))
    }

    #[test]
    fn promotion_protection_follows_python() {
        let n = now();
        assert!(promotion_protected(&promo("candidate", json!({})), &n, 90));
        assert!(!promotion_protected(&promo("promoted", json!({})), &n, 90));
        assert!(!promotion_protected(&promo("bogus", json!({})), &n, 90));
        // Rejected 15 days ago: inside 90 days, outside 10 days.
        assert!(promotion_protected(&promo("rejected", json!({})), &n, 90));
        assert!(!promotion_protected(&promo("rejected", json!({})), &n, 10));
        assert!(promotion_protected(&promo("rejected", json!({})), &n, -1));
        let until = json!({"rejected_until": "2026-12-01T00:00:00"});
        assert!(promotion_protected(&promo("rejected", until), &n, 0));
        let review = json!({"review": {"reviewed_at": "2026-01-01T00:00:00Z"}});
        assert!(!promotion_protected(&promo("rejected", review), &n, 90));
        // A wrong key set is not an envelope.
        let mut r = promo("candidate", json!({}));
        r.extra
            .get_mut("promotion")
            .and_then(Value::as_object_mut)
            .unwrap()
            .insert("more".into(), json!(1));
        assert!(!promotion_protected(&r, &n, 90));
    }

    #[test]
    fn profiles_cascade_explicit_fields() {
        let p = Profiles::from_value(json!({
            "a/": {"ttl_days": 30, "min_age_days": 5, "_explicit_fields": ["source_prefix", "ttl_days", "min_age_days"]},
            "a/b/": {"ttl_days": 10, "min_age_days": null, "confidence_threshold": 0.4,
                     "_explicit_fields": ["confidence_threshold", "ttl_days"]},
        }));
        let r = p.resolve("a/b/c");
        assert_eq!(r.ttl_days, json!(10));
        assert_eq!(r.min_age_days, json!(5));
        assert_eq!(r.confidence_threshold, json!(0.4));
        assert_eq!(p.resolve("z/y"), Policy::default());
        // "a" matches the profile "a/", as in Python.
        assert_eq!(p.resolve("a").ttl_days, json!(30));
    }

    #[test]
    fn policy_rules_give_python_reasons() {
        let p = Profiles::from_value(json!({
            "w/": {"ttl_days": 30, "confidence_threshold": 0.5, "min_age_days": 10,
                   "_explicit_fields": ["ttl_days", "confidence_threshold", "min_age_days"]},
        }));
        let r = rec(json!({"id": 3, "source": "w/x", "created_at": "2026-08-18T12:00:00+00:00"}));
        let (age, conf, reasons) = evaluate(&r, &p, &now());
        assert_eq!(age, 90);
        assert_eq!(py_round(conf, 4), 0.5);
        assert_eq!(
            reasons,
            vec![json!({"rule": "ttl", "ttl_days": 30, "age_days": 90, "prefix": "w/x"})]
        );
        let r = rec(json!({"id": 3, "source": "w/x", "updated_at": "2026-08-01T12:00:00Z"}));
        let (_, _, reasons) = evaluate(&r, &p, &now());
        assert_eq!(reasons.len(), 2);
        assert_eq!(reasons[1]["confidence"], json!(0.4386));
        // No anchor: age 0, confidence 0.
        let (age, conf, reasons) = evaluate(&rec(json!({"id": 1, "source": "w/x"})), &p, &now());
        assert_eq!((age, conf, reasons.len()), (0, 0.0, 0));
    }

    #[test]
    fn union_order_is_cpython_order() {
        // The golden `compact` capture: pairs sorted by similarity.
        let a = [255, 187, 246, 72, 250, 246, 250, 251];
        let b = [256, 228, 247, 243, 251, 262, 252, 252];
        let order = py_union_order(&a, &b);
        assert_eq!(order.len(), 12);
        assert_eq!(&order[..3], &[256, 228, 262]);
        // Small IDs in a large table come in ascending order.
        assert_eq!(py_union_order(&[3, 1], &[2]), vec![1, 2, 3]);
        let many: Vec<i64> = (0..100).rev().collect();
        let mut sorted = many.clone();
        sorted.sort_unstable();
        assert_eq!(py_union_order(&many, &[]), sorted);
    }

    #[test]
    fn similar_clusters_drop_chain_members() {
        // 1~2, 2~3, 3~4: 1 and 4 have one similar member of three.
        let pairs = [
            (1, 2, 0.9),
            (2, 3, 0.9),
            (3, 4, 0.9),
            (7, 8, 0.95),
            (9, 10, 0.8),
        ];
        let c = similar_clusters(&pairs, 0.85);
        assert_eq!(c, vec![vec![2, 3], vec![7, 8]]);
    }

    #[test]
    fn sample_takes_n_distinct_items() {
        let s = sample((0..1000).collect::<Vec<i32>>(), 500, 42);
        let set: HashSet<i32> = s.iter().copied().collect();
        assert_eq!((s.len(), set.len()), (500, 500));
    }
}
