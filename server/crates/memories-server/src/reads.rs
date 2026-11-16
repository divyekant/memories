//! The read routes (task P2-3): `/memory/{id}`, `/memory/get-batch`,
//! `/memories`, `/memories/count`, `/folders`, `/stats`,
//! `/memory/{id}/links` and `/memory/conflicts`. Each route reads Qdrant with
//! a get, a page, a count or a scroll. No route reads all records into memory.
//! The replies copy the Python server v5.19.0.

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use std::collections::BTreeMap;
use std::path::Path as FsPath;
use std::sync::Arc;

use axum::{
    Extension, Json,
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use memories_engine::{Engine, rank::PyDateTime, records, scope::source_matches_scope};
use memories_store::{Condition, Filter, QdrantStore, StoreError};
use memories_types::Record;
use serde_json::{Map, Value, json};

use crate::{
    AppState,
    admin::{last, py_int, query_int},
    auth::AuthContext,
    error::{ApiError, JsonBody, int_parsing, item, list_too_short, missing, path_int},
    shell::internal_error,
};

/// The scroll page of `/memory/conflicts`.
const CONFLICT_PAGE: u32 = 256;

// ---- What the routes read --------------------------------------------------------

/// The engine and a copy of its source map: each stored source (Python
/// `str(m.get("source", ""))`) and its number of records.
pub struct Reader<'a> {
    pub engine: &'a Engine,
    pub sources: Vec<(String, u64)>,
}

impl<'a> Reader<'a> {
    fn new(engine: &'a Engine) -> Self {
        let sources = engine
            .catalog()
            .sources()
            .iter()
            .map(|(s, n)| (s.clone(), *n))
            .collect();
        Self { engine, sources }
    }

    fn store(&self) -> &QdrantStore {
        &self.engine.store
    }

    fn total(&self) -> u64 {
        self.engine.count() as u64
    }

    async fn get(&self, id: i64) -> Result<Option<Record>, StoreError> {
        let Ok(id) = u64::try_from(id) else {
            return Ok(None);
        };
        Ok(self.get_many(&[id]).await?.pop())
    }

    /// The records with a pending reinforcement show it, as Python shows its
    /// in-memory value.
    async fn get_many(&self, ids: &[u64]) -> Result<Vec<Record>, StoreError> {
        let mut records = self.store().get_many(ids).await?;
        records.iter_mut().for_each(|r| self.engine.overlay(r));
        Ok(records)
    }

    async fn page(
        &self,
        filter: Option<Filter>,
        offset: u64,
        limit: u64,
    ) -> Result<Vec<Record>, StoreError> {
        let mut records = self.store().page(filter, offset, limit).await?;
        records.iter_mut().for_each(|r| self.engine.overlay(r));
        Ok(records)
    }

    /// The records in the sources that pass `keep`. Python counts the same
    /// set with a scan or with a Qdrant count on these sources. Archived records count.
    fn count_where(&self, keep: impl Fn(&str) -> bool) -> u64 {
        self.sources
            .iter()
            .filter(|(s, _)| keep(s))
            .map(|(_, n)| n)
            .sum()
    }
}

/// `source` is one of `sources`. An empty list matches no record.
fn source_in(sources: Vec<String>) -> Filter {
    if sources.is_empty() {
        // The Python value for "no source": a keyword that no record has.
        return Filter::must([Condition::matches("source", "__no_match__".to_string())]);
    }
    Filter::must([Condition::matches("source", sources)])
}

/// The `source` of a record as Python reads it: `m.get("source", "")`.
fn source_of(r: &Record) -> &str {
    r.source.as_deref().unwrap_or("")
}

/// The record as Python `get_memory` returns it, with `confidence`.
// shortcut: the half-life is the default (90 days) because the server has no
// extraction profiles yet; resolve it from the profile of the source when the
// profile routes are ported.
fn with_confidence(r: &Record, now: &PyDateTime) -> Value {
    records::with_confidence(r, None, now)
}

fn not_found(id: i64) -> Response {
    ApiError::new(StatusCode::NOT_FOUND, format!("Memory ID {id} not found")).into_response()
}

/// The JSON 500 of the Python handlers that catch each error.
fn caught_500(e: StoreError) -> Response {
    tracing::error!(error = %e, "read failed");
    ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "Internal server error").into_response()
}

/// The text 500 of an error that no Python handler catches.
fn uncaught_500(e: StoreError) -> Response {
    tracing::error!(error = %e, "read failed");
    internal_error()
}

// ---- Query parameters, with the pydantic 2 error items -----------------------------

/// pydantic 2 `bool` from a string: no spaces, any case.
fn py_bool(raw: &str) -> Option<bool> {
    match raw.to_ascii_lowercase().as_str() {
        "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
        "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
        _ => None,
    }
}

/// `name: Optional[bool] = Query(None)` (a `bool` with a default reads `None` as that default).
fn query_bool(q: &[(String, String)], name: &str, errors: &mut Vec<Value>) -> Option<bool> {
    let raw = last(q, name)?;
    let v = py_bool(raw);
    if v.is_none() {
        errors.push(item(
            "bool_parsing",
            json!(["query", name]),
            "Input should be a valid boolean, unable to interpret input",
            json!(raw),
        ));
    }
    v
}

/// `name: Optional[str] = Query(None, max_length=max)`.
fn query_str(
    q: &[(String, String)],
    name: &str,
    max: usize,
    errors: &mut Vec<Value>,
) -> Option<String> {
    let raw = last(q, name)?;
    if raw.chars().count() > max {
        let mut e = item(
            "string_too_long",
            json!(["query", name]),
            &format!("String should have at most {max} characters"),
            json!(raw),
        );
        e["ctx"] = json!({"max_length": max});
        errors.push(e);
        return None;
    }
    Some(raw.to_string())
}

fn check(errors: Vec<Value>) -> Result<(), Response> {
    if errors.is_empty() {
        Ok(())
    } else {
        Err(ApiError::validation(errors).into_response())
    }
}

/// `ids: List[int] = Field(..., min_length=1, max_length=1000)` of `MemoryGetBatchRequest`.
fn batch_ids(body: &Map<String, Value>) -> Result<Vec<i64>, Vec<Value>> {
    let loc = |i: Option<usize>| match i {
        Some(i) => json!(["body", "ids", i]),
        None => json!(["body", "ids"]),
    };
    let Some(v) = body.get("ids") else {
        return Err(vec![missing(loc(None), Value::Object(body.clone()))]);
    };
    let Some(list) = v.as_array() else {
        return Err(vec![item(
            "list_type",
            loc(None),
            "Input should be a valid list",
            v.clone(),
        )]);
    };
    let mut ids = Vec::with_capacity(list.len());
    let mut errors = Vec::new();
    for (i, x) in list.iter().enumerate() {
        let parsed = match x {
            Value::Bool(b) => Ok(i64::from(*b)),
            // shortcut: an integer above i64::MAX saturates; no record can have it.
            Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
                (Some(v), _, _) => Ok(v),
                (None, Some(_), _) => Ok(i64::MAX),
                (_, _, Some(f)) if f.fract() == 0.0 => Ok(f as i64),
                _ => Err(item(
                    "int_from_float",
                    loc(Some(i)),
                    "Input should be a valid integer, got a number with a fractional part",
                    x.clone(),
                )),
            },
            Value::String(s) => py_int(s).ok_or_else(|| int_parsing(loc(Some(i)), x.clone())),
            _ => Err(item(
                "int_type",
                loc(Some(i)),
                "Input should be a valid integer",
                x.clone(),
            )),
        };
        match parsed {
            Ok(id) => ids.push(id),
            Err(e) => errors.push(e),
        }
    }
    if !errors.is_empty() {
        return Err(errors);
    }
    if list.is_empty() {
        return Err(vec![list_too_short(loc(None), v.clone(), 1, 0)]);
    }
    if list.len() > 1000 {
        let mut e = item(
            "too_long",
            loc(None),
            &format!(
                "List should have at most 1000 items after validation, not {}",
                list.len()
            ),
            v.clone(),
        );
        e["ctx"] = json!({"field_type": "List", "max_length": 1000, "actual_length": list.len()});
        return Err(vec![e]);
    }
    Ok(ids)
}

// ---- Folders, links and conflicts as Python builds them -----------------------------

/// `GET /folders`: the first part of each readable source, with counts.
fn folders(sources: &[(String, u64)], auth: &AuthContext) -> Value {
    let mut counts: BTreeMap<&str, u64> = BTreeMap::new();
    for (source, n) in sources.iter().map(|(s, n)| (s.as_str(), *n)) {
        if !auth.can_read(source) {
            continue;
        }
        let folder = match source.split_once('/') {
            Some((first, _)) => first,
            None if source.is_empty() => "(ungrouped)",
            None => source,
        };
        *counts.entry(folder).or_default() += n;
    }
    let total: u64 = counts.values().sum();
    let list: Vec<Value> = counts
        .into_iter()
        .map(|(name, count)| json!({"name": name, "count": count}))
        .collect();
    json!({"folders": list, "total": total})
}

/// The entries of the `links` field of a stored record.
fn links_of(r: &Record) -> Vec<Map<String, Value>> {
    match r.field("links") {
        Some(Value::Array(items)) => items
            .into_iter()
            .filter_map(|v| match v {
                Value::Object(m) => Some(m),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn type_matches(link: &Map<String, Value>, wanted: Option<&str>) -> bool {
    match wanted {
        None | Some("") => true,
        Some(t) => link.get("type").and_then(Value::as_str) == Some(t),
    }
}

fn link_entry(mut link: Map<String, Value>, from_id: u64, direction: &str) -> Value {
    link.insert("from_id".into(), from_id.into());
    link.insert("direction".into(), direction.into());
    Value::Object(link)
}

/// `true` for a value that Python reads as true in `if value`.
fn truthy(r: &Record, key: &str) -> bool {
    r.field(key).is_some_and(|v| records::truthy(&v))
}

// ---- Routes ------------------------------------------------------------------------

/// The reader of the engine, or the 503 reply of a server with no store.
fn reader(state: &AppState) -> Result<Reader<'_>, Response> {
    match state.engine.as_deref() {
        Some(engine) => Ok(Reader::new(engine)),
        None => Err(ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "The memory store is not configured: set QDRANT_URL",
        )
        .into_response()),
    }
}

pub async fn get_memory(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Path(raw): Path<String>,
) -> Result<Json<Value>, Response> {
    let id = path_int("memory_id", &raw).map_err(IntoResponse::into_response)?;
    let rd = reader(&state)?;
    let Some(r) = rd.get(id).await.map_err(caught_500)? else {
        return Err(not_found(id));
    };
    if !auth.can_read(source_of(&r)) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "Access denied to this memory's source",
        )
        .into_response());
    }
    Ok(Json(with_confidence(&r, &PyDateTime::now_utc())))
}

pub async fn get_batch(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(body): JsonBody,
) -> Result<Json<Value>, Response> {
    let ids = batch_ids(&body).map_err(|e| ApiError::validation(e).into_response())?;
    let rd = reader(&state)?;
    let wanted: Vec<u64> = ids.iter().filter_map(|&i| u64::try_from(i).ok()).collect();
    let found = rd.get_many(&wanted).await.map_err(caught_500)?;
    let by_id: BTreeMap<u64, &Record> = found.iter().map(|r| (r.id, r)).collect();
    let now = PyDateTime::now_utc();
    let mut memories = Vec::new();
    let mut missing_ids = Vec::new();
    for id in ids {
        match u64::try_from(id).ok().and_then(|i| by_id.get(&i)) {
            Some(r) if auth.can_read(source_of(r)) => memories.push(with_confidence(r, &now)),
            Some(_) => {}
            None => missing_ids.push(id),
        }
    }
    Ok(Json(json!({
        "memories": memories,
        "missing_ids": missing_ids,
        "count": memories.len(),
    })))
}

/// The scope of `/memories` and `/memories/count`: the `source` and
/// `source_boundary` query values.
struct Scope {
    source: Option<String>,
    boundary: bool,
}

impl Scope {
    fn read(q: &[(String, String)], errors: &mut Vec<Value>) -> Self {
        let source = query_str(q, "source", 500, errors);
        let boundary = query_bool(q, "source_boundary", errors).unwrap_or(false);
        Self { source, boundary }
    }

    /// Python `if source and not auth.can_read(source)`.
    fn check_access(&self, auth: &AuthContext) -> Result<(), Response> {
        match self.source.as_deref() {
            Some(s) if !s.is_empty() && !auth.can_read(s) => Err(ApiError::new(
                StatusCode::FORBIDDEN,
                format!("Key does not have read access to source: {s}"),
            )
            .into_response()),
            _ => Ok(()),
        }
    }

    fn prefix(&self) -> Option<&str> {
        self.source.as_deref().filter(|s| !s.is_empty())
    }

    fn matches(&self, source: &str) -> bool {
        self.prefix()
            .is_none_or(|p| source_matches_scope(source, p, self.boundary))
    }
}

pub async fn count(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let scope = Scope::read(&q, &mut errors);
    check(errors)?;
    scope.check_access(&auth)?;
    let rd = reader(&state)?;
    let count = if auth.prefixes.is_some() {
        rd.count_where(|s| scope.matches(s) && auth.can_read(s))
    } else if scope.prefix().is_some() {
        rd.count_where(|s| scope.matches(s))
    } else {
        rd.total()
    };
    Ok(Json(json!({"count": count})))
}

pub async fn list(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let offset = query_int(&q, "offset", 0, 0, None, &mut errors);
    let limit = query_int(&q, "limit", 20, 1, Some(5000), &mut errors);
    let scope = Scope::read(&q, &mut errors);
    let pinned = query_bool(&q, "pinned", &mut errors);
    check(errors)?;
    scope.check_access(&auth)?;
    let rd = reader(&state)?;
    // Python pages the records in the scope, then removes the records that the
    // key cannot read and the records with another `pinned` value from the page.
    let (filter, total) = match scope.prefix() {
        Some(_) => {
            let picked = rd.sources.iter().filter(|(s, _)| scope.matches(s));
            let names: Vec<String> = picked.clone().map(|(s, _)| s.clone()).collect();
            (Some(source_in(names)), picked.map(|(_, n)| n).sum())
        }
        None => (None, rd.total()),
    };
    let page = rd
        .page(filter, offset as u64, limit as u64)
        .await
        .map_err(uncaught_500)?;
    let now = PyDateTime::now_utc();
    let memories: Vec<Value> = page
        .iter()
        .filter(|r| auth.can_read(source_of(r)))
        .filter(|r| pinned.is_none_or(|p| pinned_equals(r, p)))
        .map(|r| with_confidence(r, &now))
        .collect();
    let total = if auth.prefixes.is_some() {
        rd.count_where(|s| scope.matches(s) && auth.can_read(s))
    } else {
        total
    };
    Ok(Json(json!({
        "memories": memories,
        "total": total,
        "offset": offset,
        "limit": limit,
    })))
}

/// Python `m.get("pinned") == pinned`: `1 == True` and `0.0 == False` are true.
fn pinned_equals(r: &Record, want: bool) -> bool {
    match r.field("pinned") {
        Some(Value::Bool(b)) => b == want,
        Some(Value::Number(n)) => n.as_f64() == Some(if want { 1.0 } else { 0.0 }),
        _ => false,
    }
}

pub async fn list_folders(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Result<Json<Value>, Response> {
    let rd = reader(&state)?;
    Ok(Json(folders(&rd.sources, &auth)))
}

pub async fn stats(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Result<Json<Value>, Response> {
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let rd = reader(&state)?;
    let total = rd.total();
    let (created_at, last_updated) = store_times(rd.engine, &state.data_dir);
    Ok(Json(json!({
        "total_memories": total,
        "dimension": rd.engine.dim,
        "model": rd.engine.embedder.config().model,
        "created_at": created_at,
        "last_updated": last_updated,
        // The Python value is the size of the embedded Qdrant files; a Qdrant
        // server keeps no files in DATA_DIR, so Python also gives 0 there.
        "index_size_bytes": 0,
        "backup_count": backup_count(&state.data_dir.join("backups")),
    })))
}

/// `created_at` and `last_updated` of `/stats`: from `state.db`, else from
/// the `config.json` of a Python install in DATA_DIR (the migration keeps
/// that file), else null.
fn store_times(engine: &Engine, data_dir: &FsPath) -> (Value, Value) {
    let python: Value = std::fs::read(data_dir.join("config.json"))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or(Value::Null);
    let get = |key: &str| match engine.state.meta(key) {
        Ok(Some(v)) => Value::String(v),
        _ => python.get(key).cloned().unwrap_or(Value::Null),
    };
    (get("created_at"), get("last_updated"))
}

/// Python `len(list(backup_dir.glob("*_*")))`.
fn backup_count(dir: &FsPath) -> usize {
    std::fs::read_dir(dir).map_or(0, |entries| {
        entries
            .filter_map(Result::ok)
            .filter(|e| {
                let name = e.file_name();
                let name = name.to_string_lossy();
                !name.starts_with('.') && name.contains('_')
            })
            .count()
    })
}

pub async fn links(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Path(raw): Path<String>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let id = path_int("memory_id", &raw);
    let mut errors = id
        .as_ref()
        .err()
        .map(|e| e.detail.clone())
        .map_or_else(Vec::new, |d| d.as_array().cloned().unwrap_or_default());
    let link_type = last(&q, "type").map(str::to_string);
    let incoming = query_bool(&q, "include_incoming", &mut errors).unwrap_or(false);
    check(errors)?;
    let id = id.expect("checked above");
    let rd = reader(&state)?;
    // Python lets a store error here go to the 500 of Starlette.
    let Some(mem) = rd.get(id).await.map_err(uncaught_500)? else {
        return Err(not_found(id));
    };
    if !auth.can_read(source_of(&mem)) {
        return Err(
            ApiError::new(StatusCode::FORBIDDEN, "Not authorized to read this memory")
                .into_response(),
        );
    }

    // Outgoing: the links of the record whose target exists, in stored order.
    let outgoing: Vec<Map<String, Value>> = links_of(&mem)
        .into_iter()
        .filter(|l| type_matches(l, link_type.as_deref()))
        .collect();
    let targets: Vec<u64> = outgoing
        .iter()
        .filter_map(|l| l.get("to_id").and_then(Value::as_u64))
        .collect();
    let found = rd.get_many(&targets).await.map_err(uncaught_500)?;
    let readable: BTreeMap<u64, bool> = found
        .iter()
        .map(|r| (r.id, auth.can_read(source_of(r))))
        .collect();
    let mut out = Vec::new();
    for link in outgoing {
        let to = link.get("to_id").and_then(Value::as_u64);
        // A deleted target is left out; a target that the key cannot read too.
        if to.and_then(|t| readable.get(&t)).copied() == Some(true) {
            out.push(link_entry(link, mem.id, "outgoing"));
        }
    }

    // Incoming: the links of the other records to this record, in ID order.
    if incoming {
        let to_me = Filter::must([Condition::matches("links[].to_id", id)]);
        let mut offset = None;
        loop {
            let (page, next) = rd
                .store()
                .scroll(Some(to_me.clone()), offset, CONFLICT_PAGE)
                .await
                .map_err(uncaught_500)?;
            for mut from in page {
                rd.engine.overlay(&mut from);
                if from.id == mem.id || !auth.can_read(source_of(&from)) {
                    continue;
                }
                for link in links_of(&from) {
                    let to_me = link.get("to_id").and_then(Value::as_i64) == Some(id);
                    if to_me && type_matches(&link, link_type.as_deref()) {
                        out.push(link_entry(link, from.id, "incoming"));
                    }
                }
            }
            match next {
                Some(n) => offset = Some(n),
                None => break,
            }
        }
    }
    Ok(Json(json!({"memory_id": id, "links": out})))
}

pub async fn conflicts(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let limit = query_int(&q, "limit", 50, i64::MIN, None, &mut errors).clamp(1, 500);
    let offset = query_int(&q, "offset", 0, i64::MIN, None, &mut errors).max(0) as u64;
    check(errors)?;
    let rd = reader(&state)?;
    // `conflicts_with` is present and not null. Archived, deferred and
    // unreadable records are left out here, as Python does in its scan.
    let has_conflict = Filter::must_not([Condition::is_empty("conflicts_with")]);
    let now = PyDateTime::now_utc();
    let mut total = 0u64;
    let mut page = Vec::new();
    let mut cursor = None;
    loop {
        let (batch, next) = rd
            .store()
            .scroll(Some(has_conflict.clone()), cursor, CONFLICT_PAGE)
            .await
            .map_err(uncaught_500)?;
        for mut r in batch {
            rd.engine.overlay(&mut r);
            if r.field("conflicts_with").is_none_or(|v| v.is_null())
                || truthy(&r, "archived")
                || truthy(&r, "deferred")
                || (auth.prefixes.is_some() && !auth.can_read(source_of(&r)))
            {
                continue;
            }
            if total >= offset && (page.len() as i64) < limit {
                page.push(r);
            }
            total += 1;
        }
        match next {
            Some(n) => cursor = Some(n),
            None => break,
        }
    }
    let mut entries = Vec::with_capacity(page.len());
    for r in page {
        let mut entry = r.to_json();
        if let Some(review) = r.field("conflict_review").filter(records::truthy) {
            entry["needs_review"] = review;
        }
        let other = match r.field("conflicts_with").and_then(|v| v.as_i64()) {
            Some(cw) => rd.get(cw).await.map_err(uncaught_500)?.map(|o| (cw, o)),
            None => None,
        };
        entry["conflicting_memory"] = match other {
            Some((_, o)) if auth.can_read(source_of(&o)) => with_confidence(&o, &now),
            Some((cw, _)) => json!({"id": cw, "redacted": true}),
            None => Value::Null,
        };
        entries.push(entry);
    }
    let count = entries.len() as u64;
    Ok(Json(json!({
        "conflicts": entries,
        "count": count,
        "total": total,
        "offset": offset,
        "has_more": offset + count < total,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scoped(prefixes: &[&str]) -> AuthContext {
        AuthContext {
            role: "read-only".into(),
            prefixes: Some(prefixes.iter().map(|p| p.to_string()).collect()),
            ..AuthContext::unrestricted()
        }
    }

    #[test]
    fn bool_parsing_follows_pydantic() {
        for (s, v) in [
            ("true", true),
            ("TRUE", true),
            ("1", true),
            ("t", true),
            ("yes", true),
            ("on", true),
            ("f", false),
            ("Off", false),
            ("0", false),
            ("no", false),
        ] {
            assert_eq!(py_bool(s), Some(v), "{s}");
        }
        for s in ["", " true", "on ", "2", "maybe"] {
            assert_eq!(py_bool(s), None, "{s:?}");
        }
        let mut errors = Vec::new();
        let q = vec![("pinned".to_string(), "2".to_string())];
        assert_eq!(query_bool(&q, "pinned", &mut errors), None);
        assert_eq!(
            errors,
            [json!({"type": "bool_parsing", "loc": ["query", "pinned"],
                    "msg": "Input should be a valid boolean, unable to interpret input", "input": "2"})]
        );
    }

    #[test]
    fn source_longer_than_500_characters_is_refused() {
        let mut errors = Vec::new();
        let long = "é".repeat(501);
        let q = vec![("source".to_string(), long.clone())];
        assert_eq!(query_str(&q, "source", 500, &mut errors), None);
        assert_eq!(errors[0]["type"], "string_too_long");
        assert_eq!(errors[0]["ctx"], json!({"max_length": 500}));
        let q = vec![("source".to_string(), "é".repeat(500))];
        assert!(query_str(&q, "source", 500, &mut Vec::new()).is_some());
    }

    // Each value: pydantic 2 `List[int]` with min_length=1, max_length=1000 in the Python server.
    #[test]
    fn batch_ids_follow_pydantic() {
        let ids = |v: Value| batch_ids(json!({"ids": v}).as_object().unwrap());
        assert_eq!(
            ids(json!([1, "2", true, 3.0, " 7 "])).unwrap(),
            [1, 2, 1, 3, 7]
        );
        assert_eq!(ids(json!([3.5])).unwrap_err()[0]["type"], "int_from_float");
        assert_eq!(ids(json!(["x"])).unwrap_err()[0]["type"], "int_parsing");
        assert_eq!(ids(json!([null])).unwrap_err()[0]["type"], "int_type");
        assert_eq!(
            ids(json!([[1]])).unwrap_err()[0]["loc"],
            json!(["body", "ids", 0])
        );
        assert_eq!(ids(json!("abc")).unwrap_err()[0]["type"], "list_type");
        assert_eq!(ids(json!([])).unwrap_err()[0]["type"], "too_short");
        let e = ids(Value::Array((0..1001).map(Value::from).collect())).unwrap_err();
        assert_eq!(
            e[0]["msg"],
            "List should have at most 1000 items after validation, not 1001"
        );
        assert_eq!(batch_ids(&Map::new()).unwrap_err()[0]["type"], "missing");
    }

    #[test]
    fn folders_group_by_first_part_and_follow_the_key() {
        let sources = vec![
            (String::new(), 2),
            ("claude-code/a".to_string(), 3),
            ("claude-code/b/c".to_string(), 2),
            ("codex".to_string(), 1),
            ("wip/x".to_string(), 4),
        ];
        assert_eq!(
            folders(&sources, &AuthContext::unrestricted()),
            json!({"folders": [{"name": "(ungrouped)", "count": 2}, {"name": "claude-code", "count": 5},
                               {"name": "codex", "count": 1}, {"name": "wip", "count": 4}], "total": 12})
        );
        assert_eq!(
            folders(&sources, &scoped(&["claude-code/"])),
            json!({"folders": [{"name": "claude-code", "count": 5}], "total": 5})
        );
    }

    #[test]
    fn pinned_compares_as_python() {
        let r = |v: Value| Record::from_json(json!({"id": 1, "pinned": v})).unwrap();
        assert!(pinned_equals(&r(json!(true)), true));
        assert!(pinned_equals(&r(json!(1)), true));
        assert!(pinned_equals(&r(json!(0.0)), false));
        assert!(!pinned_equals(&r(json!("yes")), true));
        assert!(!pinned_equals(
            &Record::from_json(json!({"id": 1})).unwrap(),
            false
        ));
    }

    #[test]
    fn backups_are_counted_as_the_python_glob() {
        let dir = std::env::temp_dir().join(format!("reads-backups-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(backup_count(&dir), 0);
        for name in [
            "manual_20261116",
            "pre_delete_1",
            "nounderscore",
            ".hidden_x",
        ] {
            std::fs::create_dir_all(dir.join(name)).unwrap();
        }
        assert_eq!(backup_count(&dir), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
