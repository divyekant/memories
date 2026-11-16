//! The bulk write and relation write routes of `app.py` v5.19.0 (task P3-2).
//! Where Python raises an error that no handler catches, the reply is the
//! plain-text 500 of Starlette (`internal_error`). Where Python raises an
//! `HTTPException` inside `except Exception`, the reply is the JSON 500.

#![allow(clippy::result_large_err)]

use std::sync::Arc;

use axum::{
    Extension, Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use memories_engine::policy::validate_namespace_preserving_replacement;
use memories_engine::records::truthy;
use memories_engine::writes::{NewMemory, Patch, WriteError, source_of};
use serde_json::{Map, Value, json};

use crate::{
    AppState,
    auth::AuthContext,
    error::{BodyResult, JsonBody, int_parsing, missing, py_int},
    model::{Bounds, Model},
    shell::{Client, internal_error},
    validate::{Fields, finish, int},
    writes::{
        audit, body_loc, err, failed, parse, path_id, require_write, server_error, trusted, writer,
    },
};

type Reply = Result<Json<Value>, Response>;

fn success(mut r: Value) -> Json<Value> {
    r["success"] = json!(true);
    Json(r)
}

/// A required `int` field of a model.
fn req_int(f: &Map<String, Value>, name: &str, errors: &mut Vec<Value>) -> Option<i64> {
    let mut loc = body_loc();
    loc.push(json!(name));
    match f.get(name) {
        None => {
            errors.push(missing(Value::Array(loc), Value::Object(f.clone())));
            None
        }
        Some(v) => int(v, loc, errors),
    }
}

/// The last value of a query parameter, as Starlette reads it.
fn query_value(q: &[(String, String)], name: &str) -> Option<String> {
    q.iter()
        .rev()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.clone())
}

// ---- deletes -------------------------------------------------------------------

/// The body of `DeleteBySourceRequest` and `DeleteByPrefixRequest`.
fn delete_body(
    map: Map<String, Value>,
    field: &'static str,
) -> Result<(String, bool, bool), Response> {
    parse(map, |m, loc, errors| {
        let mut f = Fields::new(m, loc, errors);
        let s = f.string(field, true, (1, 500), None);
        let dry_run = f.boolean("dry_run", Some(false));
        let skip = f.boolean("skip_snapshot", Some(false));
        Some((s?, dry_run.unwrap_or(false), skip.unwrap_or(false)))
    })
}

pub async fn delete_by_source(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Reply {
    let (pattern, dry_run, skip_snapshot) = delete_body(map, "source_pattern")?;
    require_write(&auth, &pattern)?;
    let w = writer(&state)?;
    let fail = |e: WriteError| failed(e, false);
    if auth.prefixes.is_none() {
        let r = w
            .delete_by_source(&pattern, skip_snapshot, dry_run)
            .await
            .map_err(fail)?;
        return Ok(success(r));
    }
    // A scoped key: only the matching records that the key can write.
    let ids: Vec<i64> = w
        .engine
        .catalog()
        .iter()
        .filter(|(_, e)| e.source.contains(&pattern) && auth.can_write(&e.source))
        .map(|(id, _)| id as i64)
        .collect();
    if ids.is_empty() {
        return Ok(success(json!({"deleted_count": 0})));
    }
    if dry_run {
        return Ok(success(json!({"count": ids.len(), "would_delete": ids})));
    }
    if !skip_snapshot && ids.len() > 10 {
        w.snapshot(&format!("delete_by_source_scoped:{pattern}"))
            .await
            .map_err(fail)?;
    }
    let r = w.delete_memories_with(&ids, true).await.map_err(fail)?;
    Ok(success(json!({
        "deleted_count": r.get("deleted_count").cloned().unwrap_or(json!(0)),
        "deleted_ids": r.get("deleted_ids").cloned().unwrap_or(json!([])),
        "missing_ids": r.get("missing_ids").cloned().unwrap_or(json!([])),
    })))
}

pub async fn delete_by_prefix(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Reply {
    let (prefix, dry_run, skip_snapshot) = delete_body(map, "source_prefix")?;
    require_write(&auth, &prefix)?;
    let w = writer(&state)?;
    let r = w
        .delete_by_prefix(&prefix, skip_snapshot, dry_run)
        .await
        .map_err(|e| failed(e, false))?;
    Ok(success(r))
}

/// `DELETE /memories?source=<prefix>`.
pub async fn delete_memories(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Reply {
    let mut errors = Vec::new();
    let source = match query_value(&q, "source") {
        None => {
            errors.push(missing(json!(["query", "source"]), Value::Null));
            None
        }
        Some(v) => {
            let m = Map::from_iter([("source".to_owned(), json!(v))]);
            Fields::new(&m, vec![json!("query")], &mut errors).string(
                "source",
                true,
                (1, 500),
                None,
            )
        }
    };
    let source = finish(errors, source).map_err(IntoResponse::into_response)?;
    require_write(&auth, &source)?;
    let w = writer(&state)?;
    let r = w
        .delete_by_prefix(&source, false, false)
        .await
        .map_err(|e| failed(e, false))?;
    Ok(Json(json!({"count": r["deleted_count"]})))
}

// ---- supersede and merge -------------------------------------------------------

/// `POST /memory/supersede` (`SupersedeRequest`).
pub async fn supersede(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Reply {
    let (old_id, new_text, source) = parse(map, |m, loc, errors| {
        let old_id = req_int(m, "old_id", errors);
        let mut f = Fields::new(m, loc, errors);
        let text = f.string("new_text", true, (0, usize::MAX), None);
        let source = f.string("source", false, (0, usize::MAX), None);
        Some((old_id?, text?, source.unwrap_or_default()))
    })?;
    let trusted = trusted(&auth, &headers);
    require_write(&auth, &source)?;
    let w = writer(&state)?;
    if auth.prefixes.is_some() {
        // Python reads the record outside its `try`: a missing record is a 500.
        let existing = w.get(old_id).await.map_err(|_| internal_error())?;
        require_write(&auth, &source_of(&existing))?;
    }
    let r = w
        .supersede(old_id, new_text, &source, None, trusted.as_ref())
        .await
        .map_err(|e| failed(e, true))?;
    Ok(success(r))
}

/// `POST /memory/{memory_id}/supersede` (`SupersedeByIdRequest`).
pub async fn supersede_by_id(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    headers: HeaderMap,
    Path(raw): Path<String>,
    body: BodyResult,
) -> Reply {
    let mut errors = Vec::new();
    let id = path_id(&raw, &mut errors);
    let map = body
        .take(&mut errors)
        .map_err(IntoResponse::into_response)?;
    let fields = map.as_ref().and_then(|m| {
        let mut f = Fields::new(m, body_loc(), &mut errors);
        let text = f.string("text", true, (1, 50_000), None);
        let source = f.string("source", false, (0, 500), None);
        let metadata = f.dict("metadata");
        Some((text?, source.unwrap_or_default(), metadata))
    });
    let (id, (text, source, metadata)) =
        finish(errors, id.zip(fields)).map_err(IntoResponse::into_response)?;
    let trusted = trusted(&auth, &headers);
    if auth.role == "read-only" {
        return Err(err(
            StatusCode::FORBIDDEN,
            "Read-only keys cannot supersede memories",
        ));
    }
    let w = writer(&state)?;
    let existing = w
        .get(id)
        .await
        .map_err(|_| err(StatusCode::NOT_FOUND, format!("Memory ID {id} not found")))?;
    let old_source = source_of(&existing);
    require_write(&auth, &old_source)?;
    if !source.is_empty() {
        require_write(&auth, &source)?;
    }
    if existing.field("pinned").is_some_and(|v| truthy(&v)) {
        return Err(err(
            StatusCode::CONFLICT,
            format!("Memory ID {id} is pinned; unpin it before superseding"),
        ));
    }
    let r = w
        .supersede(id, text, &source, metadata, trusted.as_ref())
        .await
        .map_err(|e| failed(e, true))?;
    let resource = match &r["new_id"] {
        Value::Number(n) if n.as_i64() != Some(0) => n.to_string(),
        _ => String::new(),
    };
    audit(
        &state,
        &auth,
        &client,
        "memory.superseded",
        resource,
        &old_source,
    )
    .await;
    Ok(success(r))
}

/// `POST /memory/merge` (`MergeRequest`).
pub async fn merge(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Reply {
    let (ids, text, source) = parse(map, |m, loc, errors| {
        let mut f = Fields::new(m, loc, errors);
        let ids = f.list("ids", (2, usize::MAX), int);
        let text = f.string("merged_text", true, (1, 50_000), None);
        let source = f.string("source", true, (1, 500), None);
        Some((ids?, text?, source?))
    })?;
    let trusted = trusted(&auth, &headers);
    let w = writer(&state)?;
    for &mid in &ids {
        // Python reads each record outside its `try`.
        let existing = w.get(mid).await.map_err(|_| internal_error())?;
        require_write(&auth, &source_of(&existing))?;
    }
    require_write(&auth, &source)?;
    let r = w
        .merge_memories(&ids, text, &source, trusted.as_ref())
        .await
        .map_err(|e| match e {
            WriteError::Policy(p) => err(StatusCode::UNPROCESSABLE_ENTITY, p.0),
            WriteError::NotFound(m) | WriteError::Invalid(m) => err(StatusCode::BAD_REQUEST, m),
            e => {
                tracing::error!(error = %e, "merge failed");
                internal_error()
            }
        })?;
    let resource = r["id"].to_string();
    audit(&state, &auth, &client, "memory.merged", resource, &source).await;
    Ok(Json(r))
}

// ---- links ---------------------------------------------------------------------

/// `POST /memory/{memory_id}/link` (`AddLinkRequest`).
pub async fn add_link(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    Path(raw): Path<String>,
    body: BodyResult,
) -> Reply {
    let mut errors = Vec::new();
    let id = path_id(&raw, &mut errors);
    let map = body
        .take(&mut errors)
        .map_err(IntoResponse::into_response)?;
    let fields = map.as_ref().and_then(|m| {
        let to_id = req_int(m, "to_id", &mut errors);
        let kind =
            Fields::new(m, body_loc(), &mut errors).string("type", true, (0, usize::MAX), None);
        Some((to_id?, kind?))
    });
    let (id, (to_id, kind)) =
        finish(errors, id.zip(fields)).map_err(IntoResponse::into_response)?;
    let w = writer(&state)?;
    // Python catches each error here: a ValueError is a 400, and each other
    // error (the 403 of `_require_write` too) is the JSON 500.
    let fail = |e: WriteError| match e {
        WriteError::NotFound(m) | WriteError::Invalid(m) => err(StatusCode::BAD_REQUEST, m),
        e => {
            tracing::error!(error = %e, "add link failed");
            server_error()
        }
    };
    let from = w.get(id).await.map_err(fail)?;
    require_write(&auth, &source_of(&from)).map_err(|_| server_error())?;
    let to = w.get(to_id).await.map_err(fail)?;
    require_write(&auth, &source_of(&to)).map_err(|_| server_error())?;
    let r = w.add_link(id, to_id, &kind).await.map_err(fail)?;
    audit(
        &state,
        &auth,
        &client,
        "memory.linked",
        id.to_string(),
        &to_id.to_string(),
    )
    .await;
    Ok(Json(r))
}

/// `DELETE /memory/{memory_id}/link/{target_id}?type=related_to`.
pub async fn remove_link(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Path((raw_id, raw_target)): Path<(String, String)>,
    Query(q): Query<Vec<(String, String)>>,
) -> Reply {
    let mut errors = Vec::new();
    let id = path_id(&raw_id, &mut errors);
    let target = py_int(&raw_target);
    if target.is_none() {
        errors.push(int_parsing(json!(["path", "target_id"]), json!(raw_target)));
    }
    let (id, target) = finish(errors, id.zip(target)).map_err(IntoResponse::into_response)?;
    let kind = query_value(&q, "type").unwrap_or_else(|| "related_to".into());
    let w = writer(&state)?;
    // Python has no `try` here: each engine error is the plain 500.
    let from = w.get(id).await.map_err(|_| internal_error())?;
    require_write(&auth, &source_of(&from))?;
    let r = w
        .remove_link(id, target, &kind)
        .await
        .map_err(|_| internal_error())?;
    Ok(Json(r))
}

// ---- archive and rename ----------------------------------------------------------

/// `POST /memory/archive-batch`: a missing record is skipped; a record that
/// the key cannot write stops the batch with 403.
pub async fn archive_batch(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Reply {
    let ids = parse(map, |m, loc, errors| {
        Fields::new(m, loc, errors).list("ids", (1, 1000), int)
    })?;
    let trusted = trusted(&auth, &headers);
    let w = writer(&state)?;
    let mut archived = 0;
    for mid in ids {
        let existing = match w.get(mid).await {
            Ok(r) => r,
            Err(WriteError::NotFound(_)) => continue,
            Err(_) => return Err(internal_error()),
        };
        require_write(&auth, &source_of(&existing))?;
        let patch = Patch {
            archived: Some(true),
            ..Patch::default()
        };
        match w.update_memory(mid, patch, trusted.as_ref(), false).await {
            Ok(_) => {}
            Err(WriteError::Policy(p)) => return Err(err(StatusCode::UNPROCESSABLE_ENTITY, p.0)),
            Err(WriteError::NotFound(_)) => continue,
            Err(_) => return Err(internal_error()),
        }
        audit(
            &state,
            &auth,
            &client,
            "memory.archived",
            mid.to_string(),
            "",
        )
        .await;
        archived += 1;
    }
    Ok(Json(json!({"archived_count": archived})))
}

/// `POST /folders/rename`: move each record of the folder to the new prefix.
pub async fn rename_folder(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Reply {
    let (old, new) = parse(map, |m, loc, errors| {
        let mut f = Fields::new(m, loc, errors);
        let old = f.string("old_name", true, (1, 500), None);
        let new = f.string("new_name", true, (1, 500), None);
        Some((old?, new?))
    })?;
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let trusted = trusted(&auth, &headers);
    let w = writer(&state)?;
    let child = format!("{old}/");
    let targets: Vec<(i64, String, String)> = w
        .engine
        .catalog()
        .iter()
        .filter(|(_, e)| e.source == old || e.source.starts_with(&child))
        .map(|(id, e)| {
            (
                id as i64,
                e.source.clone(),
                format!("{new}{}", &e.source[old.len()..]),
            )
        })
        .collect();
    if targets.is_empty() {
        return Err(err(
            StatusCode::NOT_FOUND,
            format!("No memories found with folder prefix '{old}'"),
        ));
    }
    // Check every move before the first write: the rename has no rollback.
    for (_, current, target) in &targets {
        validate_namespace_preserving_replacement(&[current], target, "folder rename")
            .map_err(|p| err(StatusCode::UNPROCESSABLE_ENTITY, p.0))?;
    }
    let (mut updated, mut errors) = (0, 0);
    for (id, _, target) in targets {
        let patch = Patch {
            source: Some(target),
            ..Patch::default()
        };
        match w.update_memory(id, patch, trusted.as_ref(), false).await {
            Ok(_) => updated += 1,
            Err(WriteError::Policy(p)) => return Err(err(StatusCode::UNPROCESSABLE_ENTITY, p.0)),
            Err(WriteError::NotFound(m)) => {
                tracing::warn!(id, "folder rename skip: {m}");
                errors += 1;
            }
            Err(_) => return Err(internal_error()),
        }
    }
    Ok(Json(
        json!({"success": true, "updated": updated, "errors": errors,
                   "old_name": old, "new_name": new}),
    ))
}

// ---- maintenance ---------------------------------------------------------------

const THRESHOLD: Bounds = Bounds {
    ge: Some(0.5),
    gt: None,
    le: Some(1.0),
};

/// `POST /memory/conflicts/resolve` (`ResolveConflictsRequest`).
pub async fn resolve_conflicts(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    JsonBody(map): JsonBody,
) -> Reply {
    let mut m = Model::new(&map, body_loc());
    let dry_run = m.boolean("dry_run", true);
    let max = m.int("max", 0, 0, 10_000);
    let (dry_run, max) = finish(std::mem::take(&mut m.errors), dry_run.zip(max))
        .map_err(IntoResponse::into_response)?;
    if auth.role == "read-only" {
        return Err(err(
            StatusCode::FORBIDDEN,
            "Read-only keys cannot resolve conflicts",
        ));
    }
    if auth.prefixes.is_some() {
        return Err(err(
            StatusCode::FORBIDDEN,
            "Conflict resolution spans sources; an unscoped key is required",
        ));
    }
    let w = writer(&state)?;
    let r = w
        .resolve_conflicts(dry_run, max as usize)
        .await
        .map_err(|e| failed(e, false))?;
    if !dry_run {
        let resource = r["resolved_count"].to_string();
        audit(
            &state,
            &auth,
            &client,
            "memory.conflicts_resolved",
            resource,
            "",
        )
        .await;
    }
    Ok(success(r))
}

/// `POST /memory/deduplicate` (`DeduplicateRequest`).
pub async fn deduplicate(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Reply {
    let mut m = Model::new(&map, body_loc());
    let threshold = m.float("threshold", Some(0.90), false, THRESHOLD);
    let dry_run = m.boolean("dry_run", true);
    let (threshold, dry_run) = finish(std::mem::take(&mut m.errors), threshold.zip(dry_run))
        .map_err(IntoResponse::into_response)?;
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let w = writer(&state)?;
    let r = w
        .deduplicate(threshold, dry_run)
        .await
        .map_err(|e| failed(e, false))?;
    if !dry_run {
        // Python v5.19.0 audits with `len(result["removed"])` of an int: the
        // TypeError gives the JSON 500 after the delete, and no audit row.
        tracing::error!("deduplicate: kept Python reply, 500 after the delete");
        return Err(server_error());
    }
    Ok(Json(r))
}

/// `POST /memory/missed` (`MissedMemoryRequest`).
pub async fn missed(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Reply {
    let (text, source, context) = parse(map, |m, loc, errors| {
        let mut f = Fields::new(m, loc, errors);
        let text = f.string("text", true, (1, 50_000), None);
        let source = f.string("source", true, (1, 500), None);
        let context = f.string("context", false, (0, 10_000), None);
        Some((text?, source?, context))
    })?;
    require_write(&auth, &source)?;
    let trusted = trusted(&auth, &headers);
    let w = writer(&state)?;
    let mut metadata = Map::from_iter([("origin".to_owned(), json!("missed_capture"))]);
    if let Some(c) = context.filter(|c| !c.is_empty()) {
        metadata.insert("capture_context".into(), json!(c));
    }
    let item = NewMemory {
        text,
        source: source.clone(),
        metadata: Some(metadata),
    };
    let ids = w
        .add_memories(vec![item], false, trusted.as_ref())
        .await
        .map_err(|e| match e {
            WriteError::Policy(p) => err(StatusCode::UNPROCESSABLE_ENTITY, p.0),
            e => {
                tracing::error!(error = %e, "missed capture failed");
                internal_error()
            }
        })?;
    let id = ids.first().copied().ok_or_else(internal_error)?;
    let count = {
        let mut counts = state.missed.lock().unwrap_or_else(|e| e.into_inner());
        let n = counts.entry(source.clone()).or_insert(0);
        *n += 1;
        *n
    };
    audit(
        &state,
        &auth,
        &client,
        "memory.missed",
        id.to_string(),
        &source,
    )
    .await;
    Ok(Json(
        json!({"id": id, "source": source, "missed_count": count}),
    ))
}
