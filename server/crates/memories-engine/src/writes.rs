//! The write flow of `MemoryEngine` v5.19.0: `add_memories`,
//! `add_with_doctrine`, `supersede`, `update_memory`, `upsert_memory(s)`,
//! `delete_memory` and `delete_memories`.
//!
//! Each write changes only the Qdrant points of the records that it affects.
//! It also updates the catalog, the document length sums of `state.db` and
//! the event bus. One lock puts the writes in sequence (Python has a write
//! lock and a lock for each source). A write of two steps has a journal entry
//! in `state.db` until its last step (`journal`), so a start after a forced
//! stop completes it.

use std::collections::{BTreeSet, HashSet};
use std::ops::Range;
use std::path::PathBuf;
use std::sync::Arc;

use memories_embed::TextKind;
use memories_store::{Condition, Filter, NewPoint};
use memories_types::Record;
use serde_json::{Map, Value, json};
use tokio::sync::Mutex;

use crate::catalog::Entry;
use crate::engine::{Engine, EngineError};
use crate::events::EventBus;
use crate::keyword::{doc_tokens, doc_vector};
use crate::policy::{
    MSG_CROSSING, MSG_NEEDS_AUTHOR, PolicyError, RESERVED_METADATA_FIELDS, TrustedAuthorship,
    is_namespace_crossing_source_move, is_project_source, is_reserved_namespace_source,
    is_substantive_replacement, normalize_origin_client, validate_namespace_preserving_replacement,
    validate_project_write,
};
use crate::rank::PyDateTime;
use crate::rank::scores::py_round;
use crate::records::truthy;
use crate::scope::Scope;
use crate::search::SearchArgs;

mod bulk;
mod journal;
pub mod maintenance;

/// The duplicate threshold of `/memory/add` (dedupe and doctrine).
pub const DEDUP_THRESHOLD: f64 = 0.90;
/// `DOCTRINE_IDENTICAL_THRESHOLD` when it is not set.
pub const DEFAULT_IDENTICAL_THRESHOLD: f64 = 0.97;
/// Points in one upsert call (`_chunk_size`).
const CHUNK: usize = 100;
/// A batch delete of more IDs than this makes a snapshot first.
const SNAPSHOT_OVER: usize = 10;

#[derive(Debug, thiserror::Error)]
pub enum WriteError {
    /// Python `ValueError` "not found": HTTP 404.
    #[error("{0}")]
    NotFound(String),
    /// A Python `ValueError` of a link or merge check: HTTP 400 on the link
    /// and merge routes.
    #[error("{0}")]
    Invalid(String),
    /// A pinned record without `force`: HTTP 409.
    #[error("{0}")]
    Pinned(String),
    /// `ProjectMemoryPolicyError`: HTTP 422.
    #[error(transparent)]
    Policy(#[from] PolicyError),
    #[error(transparent)]
    Engine(#[from] EngineError),
    #[error("snapshot manifest: {0}")]
    Io(#[from] std::io::Error),
}

impl From<memories_store::StoreError> for WriteError {
    fn from(e: memories_store::StoreError) -> Self {
        Self::Engine(e.into())
    }
}

impl From<memories_store::StateError> for WriteError {
    fn from(e: memories_store::StateError) -> Self {
        Self::Engine(e.into())
    }
}

impl From<memories_embed::EmbedError> for WriteError {
    fn from(e: memories_embed::EmbedError) -> Self {
        Self::Engine(e.into())
    }
}

type Result<T> = std::result::Result<T, WriteError>;

/// One new memory of an add.
#[derive(Clone, Debug, Default)]
pub struct NewMemory {
    pub text: String,
    pub source: String,
    pub metadata: Option<Map<String, Value>>,
}

/// The fields of `update_memory`. `None` does not change the field.
#[derive(Clone, Debug, Default)]
pub struct Patch {
    pub text: Option<String>,
    pub source: Option<String>,
    pub metadata_patch: Option<Map<String, Value>>,
    pub pinned: Option<bool>,
    pub archived: Option<bool>,
}

/// One entry of `upsert_memories`.
#[derive(Clone, Debug, Default)]
pub struct UpsertEntry {
    pub text: String,
    pub source: String,
    pub key: String,
    pub metadata: Option<Map<String, Value>>,
}

pub struct Writer {
    pub engine: Arc<Engine>,
    pub events: EventBus,
    lock: Mutex<()>,
    identical_threshold: f64,
    /// `DATA_DIR`: the snapshot manifest is in `snapshots/manifest.json`.
    data_dir: PathBuf,
}

/// `str(meta.get("source", ""))`.
pub fn source_of(r: &Record) -> String {
    Entry::of(r).source
}

/// The stored text, if it is a string.
pub fn text_of(r: &Record) -> Option<String> {
    match &r.text {
        Some(t) => Some(t.clone()),
        None => r
            .extra
            .get("text")
            .and_then(Value::as_str)
            .map(str::to_owned),
    }
}

fn is_pinned(r: &Record) -> bool {
    r.field("pinned").is_some_and(|v| truthy(&v))
}

fn chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn now() -> String {
    PyDateTime::now_utc().isoformat_utc()
}

fn doc_len(text: &str) -> i64 {
    doc_tokens(text).len() as i64
}

/// `sum / count`, or 1.0 when there is no length (`keyword::avgdl`).
fn avgdl(sum: i64, count: i64) -> f64 {
    if sum <= 0 || count <= 0 {
        1.0
    } else {
        sum as f64 / count as f64
    }
}

fn not_found(id: i64) -> WriteError {
    WriteError::NotFound(format!("Memory ID {id} not found"))
}

impl Writer {
    /// Makes the writer. Moves the ID counter above the highest stored ID, and
    /// counts the document lengths again if `state.db` does not agree with
    /// the store (for example, a new `state.db`).
    pub async fn open(
        engine: Arc<Engine>,
        data_dir: PathBuf,
        identical_threshold: f64,
    ) -> Result<Self> {
        if let Some(max) = engine.store.max_id().await? {
            engine.state.raise_id_floor(max + 1)?;
        }
        // A crash between a point write and the sum update can leave the sums
        // wrong, so count them again at each start (one scroll).
        let (mut sum, mut n, mut offset) = (0u64, 0u64, None);
        loop {
            let (records, next) = engine.store.scroll(None, offset, 1_000).await?;
            for r in &records {
                sum += doc_tokens(text_of(r).as_deref().unwrap_or("")).len() as u64;
                n += 1;
            }
            match next {
                Some(o) => offset = Some(o),
                None => break,
            }
        }
        engine.state.set_doc_lengths(sum, n)?;
        let writer = Self {
            engine,
            events: EventBus::default(),
            lock: Mutex::new(()),
            identical_threshold,
            data_dir,
        };
        writer.repair().await?;
        Ok(writer)
    }

    fn e(&self) -> &Engine {
        &self.engine
    }

    /// `is_novel(text, threshold, source_exact=...)`: a vector search for the
    /// top hit (it reinforces the hit, as Python does).
    pub async fn is_novel(
        &self,
        text: &str,
        threshold: f64,
        source_exact: Option<&str>,
    ) -> Result<(bool, Option<Value>)> {
        let args = SearchArgs {
            query: text.to_owned(),
            k: 1,
            scope: Scope {
                source_exact: source_exact.map(str::to_owned),
                ..Scope::default()
            },
            ..SearchArgs::default()
        };
        let top = self.e().search(&args, true).await?.into_iter().next();
        Ok(match top {
            None => (true, None),
            Some(t) => {
                let s = t["similarity"].as_f64().unwrap_or(0.0);
                (s < threshold, Some(t))
            }
        })
    }

    /// The record of `id` from the store, with its pending reinforcement.
    /// `None` if the catalog does not have it.
    async fn record(&self, id: i64) -> Result<Option<Record>> {
        if !self.e().catalog().contains(id) {
            return Ok(None);
        }
        let mut r = self.e().store.get(id as u64).await?;
        if let Some(r) = r.as_mut() {
            self.e().overlay(r);
        }
        Ok(r)
    }

    /// `get_memory` for the routes: the record or "Memory ID X not found".
    pub async fn get(&self, id: i64) -> Result<Record> {
        self.record(id).await?.ok_or_else(|| not_found(id))
    }

    /// Writes the pending reinforcements under the write lock. A write reads a
    /// record, then writes its whole payload; a flush between the two would
    /// lose the flushed value.
    pub async fn flush_reinforcements(&self) -> Result<()> {
        let _g = self.lock.lock().await;
        self.e().flush_reinforcements().await?;
        Ok(())
    }

    // ---- add -------------------------------------------------------------------

    /// `add_memories`. Returns the new IDs. With `deduplicate`, a text with a
    /// similar record (0.90) is not added.
    pub async fn add_memories(
        &self,
        items: Vec<NewMemory>,
        deduplicate: bool,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Vec<u64>> {
        for m in &items {
            validate_project_write(&m.text, &m.source, trusted)?;
        }
        let _g = self.lock.lock().await;
        self.add_locked(items, deduplicate, trusted, Vec::new())
            .await
    }

    /// The add, with the lock held. `extra` adds fields to each record after
    /// the caller fields (for `supersede`).
    async fn add_locked(
        &self,
        mut items: Vec<NewMemory>,
        deduplicate: bool,
        trusted: Option<&TrustedAuthorship>,
        extra: Vec<Map<String, Value>>,
    ) -> Result<Vec<u64>> {
        if items.is_empty() {
            return Ok(Vec::new());
        }
        if deduplicate && self.e().count() > 0 {
            let mut keep = Vec::with_capacity(items.len());
            for m in items {
                let exact = trusted.map(|_| m.source.as_str());
                if self.is_novel(&m.text, DEDUP_THRESHOLD, exact).await?.0 {
                    keep.push(m);
                }
            }
            items = keep;
            if items.is_empty() {
                return Ok(Vec::new());
            }
        }
        let ids = self.e().state.reserve_ids(items.len() as u64)?;
        self.put_new(items, trusted, extra, ids).await
    }

    /// Stores new records with the reserved `ids` (one ID for each item).
    async fn put_new(
        &self,
        items: Vec<NewMemory>,
        trusted: Option<&TrustedAuthorship>,
        extra: Vec<Map<String, Value>>,
        ids: Range<u64>,
    ) -> Result<Vec<u64>> {
        let texts: Vec<String> = items.iter().map(|m| m.text.clone()).collect();
        let dense = self.e().embedder.embed(texts, TextKind::Document).await?;
        let lens: Vec<i64> = items.iter().map(|m| doc_len(&m.text)).collect();
        let (sum, count) = self.e().state.doc_lengths()?;
        let added: i64 = lens.iter().sum();
        let avg = avgdl(sum as i64 + added, count as i64 + items.len() as i64);
        let reserved = [
            "id",
            "text",
            "source",
            "timestamp",
            "created_at",
            "updated_at",
        ];
        let mut points = Vec::with_capacity(items.len());
        for (i, (m, vector)) in items.into_iter().zip(dense).enumerate() {
            let id = ids.start + i as u64;
            let t = now();
            let mut p = Map::new();
            p.insert("text".into(), json!(m.text));
            p.insert("source".into(), json!(m.source));
            p.insert("created_at".into(), json!(t));
            p.insert("updated_at".into(), json!(t));
            p.insert("timestamp".into(), json!(t));
            let meta = m.metadata.unwrap_or_default();
            for (k, v) in &meta {
                if !reserved.contains(&k.as_str())
                    && !RESERVED_METADATA_FIELDS.contains(&k.as_str())
                {
                    p.insert(k.clone(), v.clone());
                }
            }
            if trusted.is_none()
                && let Some(oc) = meta.get("origin_client")
            {
                p.insert("origin_client".into(), json!(normalize_origin_client(oc)));
            }
            if let Some(t) = trusted {
                p.extend(t.as_metadata());
            }
            if let Some(x) = extra.get(i) {
                p.extend(x.clone());
            }
            let keyword = doc_vector(&m.text, avg);
            points.push(NewPoint {
                record: Record::from_payload(id, p),
                dense: vector,
                keyword,
            });
        }
        let records: Vec<Record> = points.iter().map(|p| p.record.clone()).collect();
        let mut rest = points;
        // Each stored chunk goes into the catalog and the sums at once, so a
        // later failed chunk leaves no stored point that the catalog hides.
        while !rest.is_empty() {
            let tail = rest.split_off(rest.len().min(CHUNK));
            let len: i64 = rest
                .iter()
                .map(|p| doc_len(p.record.text.as_deref().unwrap_or("")))
                .sum();
            let chunk: Vec<Record> = rest.iter().map(|p| p.record.clone()).collect();
            self.e().store.upsert(rest, self.e().dim).await?;
            {
                let mut c = self.e().catalog_mut();
                for r in &chunk {
                    c.put(r);
                }
            }
            self.e().state.add_doc_lengths(len, chunk.len() as i64)?;
            rest = tail;
        }
        self.touch()?;
        for r in &records {
            let text = text_of(r).unwrap_or_default();
            self.events.emit(
                "memory.added",
                json!({"id": r.id, "source": source_of(r), "text": chars(&text, 200)}),
            );
        }
        Ok(records.iter().map(|r| r.id).collect())
    }

    /// `add_with_doctrine`: add, skip or supersede on a similar record.
    /// `on_duplicate` is "add", "skip" or "supersede" (the route checks it).
    pub async fn add_with_doctrine(
        &self,
        m: NewMemory,
        on_duplicate: &str,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Value> {
        validate_project_write(&m.text, &m.source, trusted)?;
        let _g = self.lock.lock().await;
        if on_duplicate != "add" && self.e().count() > 0 {
            let exact = trusted.map(|_| m.source.as_str());
            let (novel, top) = self.is_novel(&m.text, DEDUP_THRESHOLD, exact).await?;
            if let (false, Some(top)) = (novel, top) {
                let top_id = top["id"].clone();
                let sim = top["similarity"].as_f64().unwrap_or(0.0);
                let similarity = py_round(sim, 4);
                if top.get("pinned").is_some_and(truthy) {
                    return Ok(json!({
                        "action": "skipped", "reason": "pinned_blocker", "blocked_by": top_id,
                        "similarity": similarity,
                        "hint": format!("memory {top_id} is pinned; unpin it or supersede deliberately"),
                    }));
                }
                let identical = sim >= self.identical_threshold;
                if on_duplicate == "skip" || identical {
                    return Ok(json!({
                        "action": "skipped",
                        "reason": if identical { "identical" } else { "duplicate" },
                        "blocked_by": top_id, "similarity": similarity,
                        "hint": format!("memory {top_id} already covers this; supersede it to replace the content"),
                    }));
                }
                let old = top_id.as_i64().unwrap_or(-1);
                let r = self
                    .supersede_locked(old, m.text, &m.source, m.metadata, trusted)
                    .await?;
                return Ok(json!({
                    "action": "superseded", "id": r["new_id"], "superseded": top_id,
                    "similarity": similarity, "previous_text": r["previous_text"],
                }));
            }
        }
        let ids = self.add_locked(vec![m], false, trusted, Vec::new()).await?;
        Ok(json!({"action": "added", "id": ids.first()}))
    }

    /// `supersede`: add the new text with a `supersedes` link, then archive
    /// the old record. An empty `source` keeps the old source.
    pub async fn supersede(
        &self,
        old_id: i64,
        new_text: String,
        source: &str,
        metadata: Option<Map<String, Value>>,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Value> {
        let _g = self.lock.lock().await;
        self.supersede_locked(old_id, new_text, source, metadata, trusted)
            .await
    }

    async fn supersede_locked(
        &self,
        old_id: i64,
        new_text: String,
        source: &str,
        metadata: Option<Map<String, Value>>,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Value> {
        let Some(old) = self.record(old_id).await? else {
            return Err(WriteError::NotFound(format!("Memory {old_id} not found")));
        };
        let old_source = source_of(&old);
        let use_source = if source.is_empty() {
            old_source.clone()
        } else {
            source.to_owned()
        };
        let previous_text = text_of(&old).unwrap_or_default();
        validate_namespace_preserving_replacement(&[&old_source], &use_source, "supersede")?;
        if is_project_source(&old_source) {
            validate_project_write(&new_text, &old_source, trusted)?;
        }
        validate_project_write(&new_text, &use_source, trusted)?;
        // Python adds the record, then sets `supersedes`, `previous_text` and
        // the link. This server writes them with the new point.
        let mut links: Vec<Value> = metadata
            .as_ref()
            .and_then(|m| m.get("links"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        links.push(json!({"to_id": old_id, "type": "supersedes", "created_at": now()}));
        let mut extra = Map::new();
        extra.insert("supersedes".into(), json!(old_id));
        extra.insert("previous_text".into(), json!(previous_text));
        extra.insert("links".into(), Value::Array(links));
        let item = NewMemory {
            text: new_text,
            source: use_source.clone(),
            metadata,
        };
        let ids = self.e().state.reserve_ids(1)?;
        let new_id = ids.start;
        let op = json!({"op": "supersede", "old": old_id, "new": new_id});
        let entry = self.begin(&op)?;
        let steps = async {
            self.put_new(vec![item], trusted, vec![extra], ids).await?;
            self.events.emit(
                "memory.linked",
                json!({"from_id": new_id, "to_id": old_id, "type": "supersedes", "source": use_source}),
            );
            self.archive(vec![old], Some(new_id)).await
        };
        let done = steps.await;
        self.close(entry, &op, done).await?;
        Ok(json!({"old_id": old_id, "new_id": new_id,
                  "previous_text": previous_text, "archived_old": true}))
    }

    // ---- update ----------------------------------------------------------------

    /// `update_memory`. The text gets a new embedding only when it changes;
    /// other changes write the payload only.
    pub async fn update_memory(
        &self,
        id: i64,
        patch: Patch,
        trusted: Option<&TrustedAuthorship>,
        apply_trusted: bool,
    ) -> Result<Value> {
        let _g = self.lock.lock().await;
        self.update_locked(id, patch, trusted, apply_trusted).await
    }

    async fn update_locked(
        &self,
        id: i64,
        patch: Patch,
        trusted: Option<&TrustedAuthorship>,
        apply_trusted: bool,
    ) -> Result<Value> {
        let current = self.record(id).await?.ok_or_else(|| not_found(id))?;
        let cur_source = source_of(&current);
        let cur_text = text_of(&current);
        let source = patch.source.as_deref();
        let target = source.unwrap_or(&cur_source).to_owned();
        let has_patch = patch.metadata_patch.as_ref().is_some_and(|m| !m.is_empty());
        let touches_project = is_project_source(&cur_source) || is_project_source(&target);
        let replaces = apply_trusted
            || is_substantive_replacement(
                cur_text.as_deref(),
                patch.text.as_deref(),
                &cur_source,
                source,
            );
        let content = patch.text.is_some() || source.is_some() || has_patch;
        if source.is_some() || (is_reserved_namespace_source(&target) && content) {
            let text = patch.text.as_deref().or(cur_text.as_deref()).unwrap_or("");
            validate_project_write(text, &target, trusted)?;
        }
        let crossing = is_namespace_crossing_source_move(&cur_source, source);
        if crossing && trusted.is_none() {
            return Err(PolicyError(MSG_CROSSING.into()).into());
        }
        if (apply_trusted || (touches_project && replaces)) && trusted.is_none() {
            return Err(PolicyError(MSG_NEEDS_AUTHOR.into()).into());
        }
        let restamp = (apply_trusted || crossing || (touches_project && replaces))
            .then_some(trusted)
            .flatten();
        let mut p = current.to_payload();
        let restamp_fields = |p: &mut Map<String, Value>| {
            if let Some(t) = restamp {
                for f in RESERVED_METADATA_FIELDS {
                    p.remove(f);
                }
                p.extend(t.as_metadata());
            }
        };

        let source_only = source.is_some_and(|s| s != cur_source)
            && patch.text.is_none()
            && !has_patch
            && patch.pinned.is_none()
            && patch.archived.is_none();
        if source_only {
            p.insert("source".into(), json!(target));
            restamp_fields(&mut p);
            p.insert("updated_at".into(), json!(now()));
            let r = Record::from_payload(current.id, p);
            self.e().store.replace_payload(&r).await?;
            self.e().catalog_mut().put(&r);
            self.touch()?;
            return Ok(json!({"id": id, "updated_fields": ["source"]}));
        }

        let mut fields: Vec<&str> = Vec::new();
        let mut new_text = None;
        if let Some(t) = &patch.text
            && p.get("text") != Some(&json!(t))
        {
            p.insert("text".into(), json!(t));
            new_text = Some(t.clone());
            fields.push("text");
        }
        if let Some(s) = source
            && p.get("source") != Some(&json!(s))
        {
            p.insert("source".into(), json!(s));
            fields.push("source");
        }
        if let Some(m) = patch.metadata_patch.as_ref().filter(|m| !m.is_empty()) {
            let fixed = [
                "id",
                "text",
                "source",
                "timestamp",
                "created_at",
                "updated_at",
                "entity_key",
            ];
            for (k, v) in m {
                if fixed.contains(&k.as_str())
                    || RESERVED_METADATA_FIELDS.contains(&k.as_str())
                    || k.starts_with("_policy_")
                {
                    continue;
                }
                p.insert(k.clone(), v.clone());
            }
            fields.push("metadata");
        }
        restamp_fields(&mut p);
        if let Some(b) = patch.pinned {
            p.insert("pinned".into(), json!(b));
            fields.push("pinned");
        }
        if let Some(b) = patch.archived {
            p.insert("archived".into(), json!(b));
            fields.push("archived");
        }
        p.insert("updated_at".into(), json!(now()));
        let r = Record::from_payload(current.id, p);
        match &new_text {
            Some(text) => {
                let old_len = doc_len(cur_text.as_deref().unwrap_or(""));
                let delta = doc_len(text) - old_len;
                let (sum, count) = self.e().state.doc_lengths()?;
                let avg = avgdl(sum as i64 + delta, count as i64);
                let dense = self
                    .e()
                    .embedder
                    .embed(vec![text.clone()], TextKind::Document)
                    .await?
                    .pop()
                    .unwrap_or_default();
                let point = NewPoint {
                    record: r.clone(),
                    dense,
                    keyword: doc_vector(text, avg),
                };
                self.e().store.upsert(vec![point], self.e().dim).await?;
                self.e().catalog_mut().put(&r);
                self.e().state.add_doc_lengths(delta, 0)?;
            }
            None => {
                self.e().store.replace_payload(&r).await?;
                self.e().catalog_mut().put(&r);
            }
        }
        self.touch()?;
        let source = source_of(&r);
        self.events.emit(
            "memory.updated",
            json!({"id": id, "updated_fields": fields, "source": source}),
        );
        Ok(json!({"id": id, "updated_fields": fields}))
    }

    // ---- upsert ----------------------------------------------------------------

    /// The first record (in ID order) with this `source` and `entity_key`.
    async fn find_entity(&self, source: &str, key: &str) -> Result<Option<u64>> {
        let filter = Filter::must([
            Condition::matches("source", source.to_owned()),
            Condition::matches("entity_key", key.to_owned()),
        ]);
        let (found, _) = self.e().store.scroll(Some(filter), None, 1).await?;
        Ok(found.first().map(|r| r.id))
    }

    /// `upsert_memory`: update the record of (`source`, `key`), or add it.
    pub async fn upsert_memory(
        &self,
        entry: UpsertEntry,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Value> {
        let _g = self.lock.lock().await;
        self.upsert_locked(entry, trusted).await
    }

    async fn upsert_locked(
        &self,
        entry: UpsertEntry,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Value> {
        let mut metadata = entry.metadata.unwrap_or_default();
        metadata.insert("entity_key".into(), json!(entry.key));
        match self.find_entity(&entry.source, &entry.key).await? {
            None => {
                validate_project_write(&entry.text, &entry.source, trusted)?;
                let item = NewMemory {
                    text: entry.text,
                    source: entry.source,
                    metadata: Some(metadata),
                };
                let ids = self
                    .add_locked(vec![item], false, trusted, Vec::new())
                    .await?;
                Ok(json!({"id": ids[0], "action": "created"}))
            }
            Some(id) => {
                let patch = Patch {
                    text: Some(entry.text),
                    source: Some(entry.source),
                    metadata_patch: Some(metadata),
                    ..Patch::default()
                };
                let r = self
                    .update_locked(id as i64, patch, trusted, trusted.is_some())
                    .await?;
                Ok(json!({"id": r["id"], "action": "updated"}))
            }
        }
    }

    /// `upsert_memories`: check the policy of all entries first, then upsert
    /// each entry. A failed entry counts in `errors`.
    pub async fn upsert_memories(
        &self,
        entries: Vec<UpsertEntry>,
        trusted: Option<&TrustedAuthorship>,
    ) -> Result<Value> {
        for e in &entries {
            validate_project_write(&e.text, &e.source, trusted)?;
        }
        let _g = self.lock.lock().await;
        let (mut created, mut updated, mut errors) = (0, 0, 0);
        let mut results = Vec::new();
        for e in entries {
            match self.upsert_locked(e, trusted).await {
                Ok(r) => {
                    if r["action"] == "created" {
                        created += 1;
                    } else {
                        updated += 1;
                    }
                    results.push(r);
                }
                Err(err) => {
                    tracing::warn!(error = %err, "upsert entry failed");
                    errors += 1;
                }
            }
        }
        Ok(json!({"created": created, "updated": updated, "errors": errors, "results": results}))
    }

    // ---- delete ----------------------------------------------------------------

    /// `delete_memory`: remove the links to the record, then the record.
    pub async fn delete_memory(&self, id: i64, force: bool) -> Result<Value> {
        let _g = self.lock.lock().await;
        let rec = self.record(id).await?.ok_or_else(|| not_found(id))?;
        if !force && is_pinned(&rec) {
            return Err(WriteError::Pinned(format!(
                "Memory ID {id} is pinned; pass force=true to delete it"
            )));
        }
        let op = json!({"op": "delete", "id": id});
        let entry = self.begin(&op)?;
        let done = self.delete_steps(id, Some(&rec)).await;
        self.close(entry, &op, done).await?;
        let text = text_of(&rec).unwrap_or_default();
        self.events.emit(
            "memory.deleted",
            json!({"id": id, "source": source_of(&rec)}),
        );
        Ok(json!({"deleted_id": id, "deleted_text": chars(&text, 100)}))
    }

    /// The steps of `delete_memory`: remove the links to `id`, then the record.
    async fn delete_steps(&self, id: i64, rec: Option<&Record>) -> Result<()> {
        let linking: Vec<u64> = {
            let c = self.e().catalog();
            c.ids_linking_to(id)
        };
        for mut r in self.e().store.get_many(&linking).await? {
            self.e().overlay(&mut r);
            if let Some(links) = r.links.as_mut() {
                links.retain(|l| l.to_id != id);
                if links.is_empty() {
                    r.links = None;
                }
            }
            self.e().store.replace_payload(&r).await?;
            self.e().catalog_mut().put(&r);
        }
        if let Some(rec) = rec {
            self.remove(std::slice::from_ref(rec)).await?;
        }
        Ok(())
    }

    /// Sets `archived` (and `superseded_by`) on the stored records with one
    /// payload call, then updates the catalog.
    async fn archive(&self, records: Vec<Record>, superseded_by: Option<u64>) -> Result<()> {
        if records.is_empty() {
            return Ok(());
        }
        let ids: Vec<u64> = records.iter().map(|r| r.id).collect();
        let mut fields = Map::new();
        fields.insert("archived".into(), json!(true));
        if let Some(n) = superseded_by {
            fields.insert("superseded_by".into(), json!(n));
        }
        self.e().store.set_payload(&ids, fields).await?;
        let mut c = self.e().catalog_mut();
        for mut r in records {
            r.archived = Some(true);
            r.extra.remove("archived");
            if let Some(n) = superseded_by {
                r.superseded_by = Some(n as i64);
                r.extra.remove("superseded_by");
            }
            c.put(&r);
        }
        Ok(())
    }

    /// Deletes the points, then updates the catalog, the reinforcement buffer
    /// and the length sums.
    async fn remove(&self, records: &[Record]) -> Result<()> {
        let ids: Vec<u64> = records.iter().map(|r| r.id).collect();
        self.e().store.delete(&ids).await?;
        let mut len = 0;
        {
            let mut c = self.e().catalog_mut();
            for r in records {
                c.remove(r.id);
                self.e().reinforce.forget(r.id);
                len += doc_len(text_of(r).as_deref().unwrap_or(""));
            }
        }
        self.e()
            .state
            .add_doc_lengths(-len, -(records.len() as i64))?;
        self.touch()
    }

    /// `config["last_updated"]` of Python: set by each add, delete and update,
    /// as in v5.19.0. `/stats` reads it.
    fn touch(&self) -> Result<()> {
        Ok(self.e().state.set_meta(&[("last_updated", &now())])?)
    }

    /// `delete_memories`: pinned records stay. More than 10 IDs make a Qdrant
    /// snapshot first.
    pub async fn delete_memories(&self, ids: &[i64]) -> Result<Value> {
        self.delete_memories_with(ids, false).await
    }

    /// `delete_memories` with `skip_snapshot`.
    pub async fn delete_memories_with(&self, ids: &[i64], skip_snapshot: bool) -> Result<Value> {
        let unique: Vec<i64> = ids
            .iter()
            .copied()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let _g = self.lock.lock().await;
        let existing: Vec<u64> = {
            let c = self.e().catalog();
            unique
                .iter()
                .filter(|&&id| c.contains(id))
                .map(|&id| id as u64)
                .collect()
        };
        let found: HashSet<i64> = existing.iter().map(|&id| id as i64).collect();
        let missing: Vec<i64> = unique
            .iter()
            .copied()
            .filter(|id| !found.contains(id))
            .collect();
        if existing.is_empty() {
            return Ok(json!({"deleted_count": 0, "deleted_ids": [],
                             "missing_ids": missing, "skipped_pinned": []}));
        }
        let records = self.e().store.get_many(&existing).await?;
        let (pinned, delete): (Vec<Record>, Vec<Record>) = records.into_iter().partition(is_pinned);
        let pinned_ids: Vec<u64> = pinned.iter().map(|r| r.id).collect();
        if delete.is_empty() {
            return Ok(json!({"deleted_count": 0, "deleted_ids": [],
                             "missing_ids": missing, "skipped_pinned": pinned_ids}));
        }
        if !pinned_ids.is_empty() {
            tracing::info!(
                count = pinned_ids.len(),
                "delete_memories: skipping pinned ids"
            );
        }
        if unique.len() > SNAPSHOT_OVER && !skip_snapshot {
            self.snapshot("pre_delete_batch").await?;
        }
        self.remove(&delete).await?;
        let deleted: Vec<u64> = delete.iter().map(|r| r.id).collect();
        Ok(json!({"deleted_count": deleted.len(), "deleted_ids": deleted, "missing_ids": missing}))
    }

    /// `_snapshot_before_delete`: a Qdrant snapshot and a manifest entry.
    pub async fn snapshot(&self, reason: &str) -> Result<String> {
        let name = self.e().store.create_snapshot().await?;
        let count = self.e().store.count(None).await?;
        let dir = self.data_dir.join("snapshots");
        std::fs::create_dir_all(&dir)?;
        let path = dir.join("manifest.json");
        let mut entries: Vec<Value> = match std::fs::read(&path) {
            Ok(b) => serde_json::from_slice(&b).map_err(std::io::Error::other)?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(e.into()),
        };
        entries.push(json!({"name": name, "reason": reason,
                            "timestamp": now(), "point_count": count}));
        std::fs::write(
            &path,
            serde_json::to_vec(&entries).map_err(std::io::Error::other)?,
        )?;
        Ok(name)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn avgdl_has_a_floor() {
        assert_eq!(avgdl(0, 0), 1.0);
        assert_eq!(avgdl(10, 4), 2.5);
        assert_eq!(avgdl(-3, 2), 1.0);
    }

    #[test]
    fn pinned_reads_python_truthiness() {
        let r = |v: Value| Record::from_json(v).unwrap();
        assert!(is_pinned(&r(json!({"id": 1, "pinned": true}))));
        assert!(is_pinned(&r(json!({"id": 1, "pinned": "yes"}))));
        assert!(!is_pinned(&r(json!({"id": 1, "pinned": 0}))));
        assert!(!is_pinned(&r(json!({"id": 1}))));
    }
}
