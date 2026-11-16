//! The core write routes of `app.py` v5.19.0: add, add-batch, upsert,
//! upsert-batch, PATCH and DELETE `/memory/{memory_id}`, and delete-batch.
//! The body checks run before the key checks, as in FastAPI.

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use std::sync::Arc;

use axum::{
    Extension, Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use memories_engine::policy::{TrustedAuthorship, is_substantive_replacement};
use memories_engine::rank::scores::py_round;
use memories_engine::writes::{
    NewMemory, Patch, UpsertEntry, WriteError, Writer, source_of, text_of,
};
use serde_json::{Map, Value, json};

use crate::{
    AppState,
    audit::Entry,
    auth::AuthContext,
    error::{ApiError, BodyResult, JsonBody, int_parsing, py_int},
    shell::Client,
    validate::{Fields, finish, int, model},
};

fn err(status: StatusCode, detail: impl Into<Value>) -> Response {
    ApiError::new(status, detail).into_response()
}

fn server_error() -> Response {
    err(StatusCode::INTERNAL_SERVER_ERROR, "Internal server error")
}

fn writer(state: &AppState) -> Result<Arc<Writer>, Response> {
    state.writer.clone().ok_or_else(|| {
        err(
            StatusCode::SERVICE_UNAVAILABLE,
            "The memory store is not configured: set QDRANT_URL",
        )
    })
}

/// `_require_write`.
fn require_write(auth: &AuthContext, source: &str) -> Result<(), Response> {
    if auth.can_write(source) {
        Ok(())
    } else {
        Err(err(
            StatusCode::FORBIDDEN,
            format!("Key does not have write access to source: {source}"),
        ))
    }
}

/// `_trusted_authorship`: only a managed key with a valid principal.
fn trusted(auth: &AuthContext, headers: &HeaderMap) -> Option<TrustedAuthorship> {
    if auth.key_type != "managed" {
        return None;
    }
    let principal = auth.principal_id.as_deref().filter(|p| !p.is_empty())?;
    let client = headers
        .get("x-memories-client")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    TrustedAuthorship::principal(principal, client)
}

/// The reply of a failed write. `value_errors` is a route that answers a
/// Python `ValueError` with 404 (409 for a pinned record); the other routes
/// answer 500.
fn failed(e: WriteError, value_errors: bool) -> Response {
    match e {
        WriteError::Policy(p) => err(StatusCode::UNPROCESSABLE_ENTITY, p.0),
        WriteError::NotFound(m) if value_errors => err(StatusCode::NOT_FOUND, m),
        WriteError::Pinned(m) if value_errors => err(StatusCode::CONFLICT, m),
        e => {
            tracing::error!(error = %e, "write failed");
            server_error()
        }
    }
}

async fn audit(
    state: &Arc<AppState>,
    auth: &AuthContext,
    client: &Option<Extension<Client>>,
    action: &str,
    resource: String,
    source: &str,
) {
    let mut e = Entry::from_request(auth, client.as_ref().map(|c| &c.0), action);
    e.resource_id = resource;
    e.source_prefix = source.into();
    state.audit(e).await;
}

/// A Python ID for `str(x or "")`: 0 and none give "".
fn id_or_empty(v: &Value) -> Option<String> {
    match v {
        Value::Number(n) if n.as_i64() != Some(0) => Some(n.to_string()),
        _ => None,
    }
}

// ---- Body models ---------------------------------------------------------------

const ON_DUPLICATE: &str = "^(add|skip|supersede)$";

struct AddBody {
    text: String,
    source: String,
    metadata: Option<Map<String, Value>>,
    deduplicate: bool,
    on_duplicate: Option<String>,
}

/// `AddMemoryRequest`.
fn add_body(m: &Map<String, Value>, loc: Vec<Value>, errors: &mut Vec<Value>) -> Option<AddBody> {
    let mut f = Fields::new(m, loc, errors);
    let text = f.string("text", true, (1, 50_000), None);
    let source = f.string("source", true, (1, 500), None);
    let metadata = f.dict("metadata");
    let deduplicate = f.boolean("deduplicate", Some(false));
    let on_duplicate = f.string(
        "on_duplicate",
        false,
        (0, usize::MAX),
        Some((ON_DUPLICATE, |s| matches!(s, "add" | "skip" | "supersede"))),
    );
    Some(AddBody {
        text: text?,
        source: source?,
        metadata,
        deduplicate: deduplicate.unwrap_or(false),
        on_duplicate,
    })
}

/// `UpsertMemoryRequest`.
fn upsert_body(
    m: &Map<String, Value>,
    loc: Vec<Value>,
    errors: &mut Vec<Value>,
) -> Option<UpsertEntry> {
    let mut f = Fields::new(m, loc, errors);
    let text = f.string("text", true, (1, 50_000), None);
    let source = f.string("source", true, (1, 500), None);
    let key = f.string("key", true, (1, 500), None);
    let metadata = f.dict("metadata");
    Some(UpsertEntry {
        text: text?,
        source: source?,
        key: key?,
        metadata,
    })
}

fn body_loc() -> Vec<Value> {
    vec![json!("body")]
}

/// A model body: the items of the model, or the 422 reply.
fn parse<T>(
    map: Map<String, Value>,
    check: impl FnOnce(&Map<String, Value>, Vec<Value>, &mut Vec<Value>) -> Option<T>,
) -> Result<T, Response> {
    let mut errors = Vec::new();
    let v = check(&map, body_loc(), &mut errors);
    finish(errors, v).map_err(IntoResponse::into_response)
}

/// The check of one model: its value, or `None` with error items.
type Check<T> = fn(&Map<String, Value>, Vec<Value>, &mut Vec<Value>) -> Option<T>;

/// A list of models at `body.<name>` with its length limits.
fn list_body<T>(
    map: &Map<String, Value>,
    name: &str,
    len: (usize, usize),
    check: Check<T>,
    errors: &mut Vec<Value>,
) -> Option<Vec<T>> {
    let mut f = Fields::new(map, body_loc(), errors);
    f.list(name, len, |v, loc, errors| {
        let m = model(v, loc.clone(), errors)?;
        check(m, loc, errors)
    })
}

// ---- Routes --------------------------------------------------------------------

pub async fn add(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let b = parse(map, add_body)?;
    require_write(&auth, &b.source)?;
    let trusted = trusted(&auth, &headers);
    let w = writer(&state)?;
    let item = NewMemory {
        text: b.text.clone(),
        source: b.source.clone(),
        metadata: b.metadata.filter(|m| !m.is_empty()),
    };
    if let Some(mode) = &b.on_duplicate {
        let mut r = w
            .add_with_doctrine(item, mode, trusted.as_ref())
            .await
            .map_err(|e| failed(e, false))?;
        let action = match r["action"].as_str() {
            Some("added") => "memory.created",
            Some("superseded") => "memory.superseded",
            _ => "memory.add_skipped",
        };
        let resource = id_or_empty(&r["id"])
            .or_else(|| id_or_empty(&r["blocked_by"]))
            .unwrap_or_default();
        audit(&state, &auth, &client, action, resource, &b.source).await;
        r["success"] = json!(true);
        return Ok(Json(r));
    }
    let ids = w
        .add_memories(vec![item], b.deduplicate, trusted.as_ref())
        .await
        .map_err(|e| failed(e, false))?;
    let id = ids.first().copied();
    let mut out = json!({
        "success": true,
        "id": id,
        "message": if id.is_some() { "Memory added successfully" } else { "Duplicate skipped" },
    });
    if id.is_none() && b.deduplicate {
        let exact = trusted.as_ref().map(|_| b.source.as_str());
        let (_, top) = w
            .is_novel(&b.text, 0.88, exact)
            .await
            .map_err(|e| failed(e, false))?;
        if let Some(top) = top {
            let top_source = top.get("source").and_then(Value::as_str).unwrap_or("");
            if trusted.is_none() || top_source == b.source {
                let sim = top["similarity"].as_f64().unwrap_or(0.0);
                out["blocked_by"] = top["id"].clone();
                out["blocked_similarity"] = json!(py_round(sim, 4));
                out["hint"] = json!(format!(
                    "memory {} already covers this; pass on_duplicate=supersede to replace it",
                    top["id"]
                ));
            }
        }
    }
    let resource = id.map(|i| i.to_string()).filter(|s| s != "0");
    audit(
        &state,
        &auth,
        &client,
        "memory.created",
        resource.unwrap_or_default(),
        &b.source,
    )
    .await;
    Ok(Json(out))
}

pub async fn add_batch(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let (items, deduplicate) = parse(map, |m, loc, errors| {
        let items = list_body(m, "memories", (1, 500), add_body, errors);
        let dedupe = Fields::new(m, loc, errors).boolean("deduplicate", Some(false));
        Some((items?, dedupe.unwrap_or(false)))
    })?;
    for b in &items {
        require_write(&auth, &b.source)?;
    }
    let trusted = trusted(&auth, &headers);
    let w = writer(&state)?;
    // Python v5.19.0 fails with 500 on a batch that mixes items with and
    // without metadata. This server keeps the metadata of each item.
    let items = items
        .into_iter()
        .map(|b| NewMemory {
            text: b.text,
            source: b.source,
            metadata: b.metadata.filter(|m| !m.is_empty()),
        })
        .collect();
    let ids = w
        .add_memories(items, deduplicate, trusted.as_ref())
        .await
        .map_err(|e| failed(e, false))?;
    Ok(Json(json!({
        "success": true, "ids": ids, "count": ids.len(),
        "message": format!("Added {} memories", ids.len()),
    })))
}

pub async fn upsert(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let entry = parse(map, upsert_body)?;
    require_write(&auth, &entry.source)?;
    let trusted = trusted(&auth, &headers);
    let w = writer(&state)?;
    let mut r = w
        .upsert_memory(entry, trusted.as_ref())
        .await
        .map_err(|e| failed(e, false))?;
    r["success"] = json!(true);
    Ok(Json(r))
}

pub async fn upsert_batch(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: HeaderMap,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let entries = parse(map, |m, _, errors| {
        list_body(m, "memories", (1, 1000), upsert_body, errors)
    })?;
    let trusted = trusted(&auth, &headers);
    for e in &entries {
        require_write(&auth, &e.source)?;
    }
    let w = writer(&state)?;
    let mut r = w
        .upsert_memories(entries, trusted.as_ref())
        .await
        .map_err(|e| failed(e, false))?;
    r["success"] = json!(true);
    Ok(Json(r))
}

/// The `memory_id` path parameter: an error item goes into `errors`.
fn path_id(raw: &str, errors: &mut Vec<Value>) -> Option<i64> {
    let id = py_int(raw);
    if id.is_none() {
        errors.push(int_parsing(json!(["path", "memory_id"]), json!(raw)));
    }
    id
}

pub async fn patch(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    headers: HeaderMap,
    Path(raw): Path<String>,
    body: BodyResult,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let id = path_id(&raw, &mut errors);
    let map = body
        .take(&mut errors)
        .map_err(IntoResponse::into_response)?;
    let patch = map.as_ref().map(|m| {
        let mut f = Fields::new(m, body_loc(), &mut errors);
        Patch {
            text: f.string("text", false, (1, 50_000), None),
            source: f.string("source", false, (1, 500), None),
            metadata_patch: f.dict("metadata_patch"),
            pinned: f.boolean("pinned", None),
            archived: f.boolean("archived", None),
        }
    });
    let (id, patch) = finish(errors, id.zip(patch)).map_err(IntoResponse::into_response)?;
    let trusted = trusted(&auth, &headers);
    let has_patch = patch.metadata_patch.as_ref().is_some_and(|m| !m.is_empty());
    if patch.text.is_none()
        && patch.source.is_none()
        && !has_patch
        && patch.pinned.is_none()
        && patch.archived.is_none()
    {
        return Err(err(
            StatusCode::BAD_REQUEST,
            "At least one field must be provided",
        ));
    }
    let w = writer(&state)?;
    let existing = if auth.prefixes.is_some() || trusted.is_some() {
        Some(w.get(id).await.map_err(|e| failed(e, true))?)
    } else {
        None
    };
    if auth.prefixes.is_some() {
        let e = existing.as_ref().map(source_of).unwrap_or_default();
        // Python raises the 403 inside its `except Exception`, so the reply is 500.
        let denied =
            !auth.can_write(&e) || patch.source.as_deref().is_some_and(|s| !auth.can_write(s));
        if denied {
            return Err(server_error());
        }
    }
    let apply = trusted.is_some()
        && existing.as_ref().is_some_and(|r| {
            is_substantive_replacement(
                text_of(r).as_deref(),
                patch.text.as_deref(),
                &source_of(r),
                patch.source.as_deref(),
            )
        });
    let content = patch.text.is_some() || patch.source.is_some() || has_patch;
    let (pinned, archived) = (patch.pinned, patch.archived);
    let r = w
        .update_memory(id, patch, trusted.as_ref(), apply)
        .await
        .map_err(|e| failed(e, true))?;
    let resource = id.to_string();
    if content {
        audit(
            &state,
            &auth,
            &client,
            "memory.updated",
            resource.clone(),
            "",
        )
        .await;
    }
    if let Some(p) = pinned {
        let action = if p {
            "memory.pinned"
        } else {
            "memory.unpinned"
        };
        audit(&state, &auth, &client, action, resource.clone(), "").await;
    }
    if let Some(a) = archived {
        let action = if a {
            "memory.archived"
        } else {
            "memory.unarchived"
        };
        audit(&state, &auth, &client, action, resource, "").await;
    }
    Ok(Json(r))
}

pub async fn delete(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
    Path(raw): Path<String>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let id = path_id(&raw, &mut errors);
    let mut query = Map::new();
    if let Some((_, v)) = q.iter().rev().find(|(k, _)| k == "force") {
        query.insert("force".into(), json!(v));
    }
    let force =
        Fields::new(&query, vec![json!("query")], &mut errors).boolean("force", Some(false));
    let id = finish(errors, id).map_err(IntoResponse::into_response)?;
    let force = force.unwrap_or(false);
    if auth.role == "read-only" {
        return Err(err(
            StatusCode::FORBIDDEN,
            "Read-only keys cannot delete memories",
        ));
    }
    let w = writer(&state)?;
    let existing = w.get(id).await.map_err(|e| failed(e, true))?;
    let source = source_of(&existing);
    if auth.prefixes.is_some() {
        require_write(&auth, &source)?;
    }
    let mut r = w
        .delete_memory(id, force)
        .await
        .map_err(|e| failed(e, true))?;
    audit(
        &state,
        &auth,
        &client,
        "memory.deleted",
        id.to_string(),
        &source,
    )
    .await;
    r["success"] = json!(true);
    Ok(Json(r))
}

pub async fn delete_batch(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let ids = parse(map, |m, loc, errors| {
        Fields::new(m, loc, errors).list("ids", (1, 1000), int)
    })?;
    let w = writer(&state)?;
    if auth.prefixes.is_some() {
        for &id in &ids {
            if let Ok(r) = w.get(id).await {
                require_write(&auth, &source_of(&r))?;
            }
        }
    }
    let mut r = w
        .delete_memories(&ids)
        .await
        .map_err(|e| failed(e, false))?;
    r["success"] = json!(true);
    Ok(Json(r))
}
