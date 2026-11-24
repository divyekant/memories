//! The five `/sync` routes, with the replies of a Python server that has no
//! cloud sync configuration (task P5-7, production baseline P0-3).
//!
//! shortcut: no cloud sync upload or download. Production runs without cloud
//! sync (the image has no boto3). Port `cloud_sync.py` when a host needs it.

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use axum::{
    Extension, Json,
    extract::Query,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::{Map, Value, json};

use crate::{
    auth::AuthContext,
    error::ApiError,
    validate::{Fields, finish},
};

fn admin(auth: &AuthContext) -> Result<(), Response> {
    auth.require_admin().map_err(IntoResponse::into_response)
}

fn not_configured() -> Response {
    ApiError::new(StatusCode::BAD_REQUEST, "Cloud sync not configured").into_response()
}

/// The `confirm: bool = False` query check. Starlette reads the last value.
fn check_confirm(q: &[(String, String)]) -> Result<(), Response> {
    let mut m = Map::new();
    if let Some((_, v)) = q.iter().rev().find(|(k, _)| k == "confirm") {
        m.insert("confirm".into(), json!(v));
    }
    let mut errors = Vec::new();
    Fields::new(&m, vec![json!("query")], &mut errors).boolean("confirm", Some(false));
    finish(errors, Some(())).map_err(IntoResponse::into_response)
}

pub async fn status(Extension(auth): Extension<AuthContext>) -> Result<Json<Value>, Response> {
    admin(&auth)?;
    Ok(Json(
        json!({"enabled": false, "message": "Cloud sync not configured"}),
    ))
}

/// `POST /sync/upload` and `GET /sync/snapshots`.
pub async fn no_query(Extension(auth): Extension<AuthContext>) -> Response {
    admin(&auth).err().unwrap_or_else(not_configured)
}

/// `POST /sync/download` and `POST /sync/restore/{backup_name}`.
pub async fn with_confirm(
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Response {
    check_confirm(&q)
        .and_then(|()| admin(&auth))
        .err()
        .unwrap_or_else(not_configured)
}
