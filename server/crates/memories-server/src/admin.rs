//! Admin routes: the API keys (`/api/keys`) and the audit trail (`/audit`).
//! FastAPI checks the body and the query before the handler runs, so these
//! handlers give a 422 before the 403 of a key that is not admin.

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use std::sync::Arc;

use axum::{
    Extension, Json,
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::{Map, Value, json};

use crate::{
    AppState,
    audit::Filter,
    auth::AuthContext,
    error::{ApiError, JsonBody, int_parsing, item, missing, string_too_short},
    keys::{KeyError, KeyUpdate, is_valid_slug},
    shell::internal_error,
};

const ROLE_PATTERN: &str = "^(read-only|read-write|admin)$";
/// A pydantic `pattern` and the function that does the same match.
type Pattern = (&'static str, fn(&str) -> bool);

const SLUG_PATTERN: &str = "^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$";

fn is_role(s: &str) -> bool {
    matches!(s, "read-only" | "read-write" | "admin")
}

/// Runs blocking database work off the async threads. A failure is HTTP 500.
async fn blocking<T: Send + 'static, E: std::fmt::Display + Send + 'static>(
    f: impl FnOnce() -> Result<T, E> + Send + 'static,
) -> Result<T, Response> {
    match tokio::task::spawn_blocking(f).await {
        Ok(Ok(v)) => Ok(v),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "database operation failed");
            Err(internal_error())
        }
        Err(e) => {
            tracing::error!(error = %e, "database task failed");
            Err(internal_error())
        }
    }
}

// ---- Body checks, with the pydantic 2 error items ----------------------------

struct Body<'a> {
    map: &'a Map<String, Value>,
    errors: Vec<Value>,
}

impl<'a> Body<'a> {
    fn new(map: &'a Map<String, Value>) -> Self {
        Self {
            map,
            errors: Vec::new(),
        }
    }

    /// A `str` field with `min_length=1`, `max_length=200` and an optional pattern.
    /// `required` is a field with no default; else `null` and a missing field give `None`.
    fn string(&mut self, name: &str, required: bool, pattern: Option<Pattern>) -> Option<String> {
        self.string_checked(name, required, true, pattern)
    }

    /// `bounded` adds `min_length=1, max_length=200`; `role` has only a pattern.
    fn string_checked(
        &mut self,
        name: &str,
        required: bool,
        bounded: bool,
        pattern: Option<Pattern>,
    ) -> Option<String> {
        let loc = json!(["body", name]);
        let v = match self.map.get(name) {
            None if required => {
                self.errors
                    .push(missing(loc, Value::Object(self.map.clone())));
                return None;
            }
            None | Some(Value::Null) if !required => return None,
            Some(v) => v,
            None => unreachable!(),
        };
        let Some(s) = v.as_str() else {
            self.errors.push(item(
                "string_type",
                loc,
                "Input should be a valid string",
                v.clone(),
            ));
            return None;
        };
        let len = s.chars().count();
        if bounded && len < 1 {
            self.errors.push(string_too_short(loc, v.clone(), 1));
        } else if bounded && len > 200 {
            let mut e = item(
                "string_too_long",
                loc,
                "String should have at most 200 characters",
                v.clone(),
            );
            e["ctx"] = json!({"max_length": 200});
            self.errors.push(e);
        } else if let Some((p, _)) = pattern.filter(|(_, ok)| !ok(s)) {
            let mut e = item(
                "string_pattern_mismatch",
                loc,
                &format!("String should match pattern '{p}'"),
                v.clone(),
            );
            e["ctx"] = json!({"pattern": p});
            self.errors.push(e);
        } else {
            return Some(s.to_string());
        }
        None
    }

    /// A `List[str]` field. `None` is a missing field, or `null` when `nullable`.
    fn string_list(&mut self, name: &str, nullable: bool) -> Option<Vec<String>> {
        let v = match self.map.get(name) {
            None => return None,
            Some(Value::Null) if nullable => return None,
            Some(v) => v,
        };
        let Some(items) = v.as_array() else {
            self.errors.push(item(
                "list_type",
                json!(["body", name]),
                "Input should be a valid list",
                v.clone(),
            ));
            return None;
        };
        let before = self.errors.len();
        let out: Vec<String> = items
            .iter()
            .enumerate()
            .filter_map(|(i, x)| {
                let s = x.as_str().map(str::to_string);
                if s.is_none() {
                    self.errors.push(item(
                        "string_type",
                        json!(["body", name, i]),
                        "Input should be a valid string",
                        x.clone(),
                    ));
                }
                s
            })
            .collect();
        (self.errors.len() == before).then_some(out)
    }

    fn finish(self) -> Result<(), ApiError> {
        if self.errors.is_empty() {
            Ok(())
        } else {
            Err(ApiError::validation(self.errors))
        }
    }
}

// ---- Query checks ------------------------------------------------------------

/// The last value of each name, as Starlette gives a scalar query parameter.
fn last<'a>(q: &'a [(String, String)], name: &str) -> Option<&'a str> {
    q.iter()
        .rev()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

/// pydantic 2 `int` from a string: spaces around, an optional sign, digits
/// with single `_` between them, and an optional `.` with only zeros after it.
/// A value out of the `i64` range saturates, so the bound check still fails.
fn py_int(raw: &str) -> Option<i64> {
    let s = raw.trim();
    let (neg, s) = match s.as_bytes().first() {
        Some(b'-') => (true, &s[1..]),
        Some(b'+') => (false, &s[1..]),
        _ => (false, s),
    };
    let (int, frac) = s.split_once('.').unwrap_or((s, ""));
    if s.contains('.') && (frac.is_empty() || !frac.bytes().all(|b| b == b'0')) {
        return None;
    }
    let b = int.as_bytes();
    let digit_ok = |i: usize| {
        b[i].is_ascii_digit()
            || (b[i] == b'_'
                && i > 0
                && i + 1 < b.len()
                && b[i - 1].is_ascii_digit()
                && b[i + 1].is_ascii_digit())
    };
    if b.is_empty() || !(0..b.len()).all(digit_ok) {
        return None;
    }
    let mut v: i64 = 0;
    for d in b.iter().filter(|c| c.is_ascii_digit()) {
        v = v.saturating_mul(10).saturating_add(i64::from(d - b'0'));
    }
    Some(if neg { -v } else { v })
}

/// `name: int = Query(default, ge=min, le=max)`.
fn query_int(
    q: &[(String, String)],
    name: &str,
    default: i64,
    min: i64,
    max: Option<i64>,
    errors: &mut Vec<Value>,
) -> i64 {
    let Some(raw) = last(q, name) else {
        return default;
    };
    let loc = json!(["query", name]);
    match py_int(raw) {
        None => errors.push(int_parsing(loc, json!(raw))),
        Some(v) if v < min => {
            let mut e = item(
                "greater_than_equal",
                loc,
                &format!("Input should be greater than or equal to {min}"),
                json!(raw),
            );
            e["ctx"] = json!({"ge": min});
            errors.push(e);
        }
        Some(v) if max.is_some_and(|m| v > m) => {
            let m = max.unwrap();
            let mut e = item(
                "less_than_equal",
                loc,
                &format!("Input should be less than or equal to {m}"),
                json!(raw),
            );
            e["ctx"] = json!({"le": m});
            errors.push(e);
        }
        Some(v) => return v,
    }
    default
}

// ---- /api/keys -----------------------------------------------------------------

fn key_error(e: KeyError, not_found_status: StatusCode) -> Response {
    match e {
        KeyError::NotFound(m) | KeyError::Revoked(m) | KeyError::AlreadyRevoked(m) => {
            ApiError::new(not_found_status, m).into_response()
        }
        KeyError::Db(e) => {
            tracing::error!(error = %e, "keys.db write failed");
            internal_error()
        }
    }
}

pub async fn create_key(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let mut b = Body::new(&map);
    let name = b.string("name", true, None);
    let principal_id = b.string("principal_id", false, Some((SLUG_PATTERN, is_valid_slug)));
    let role = b.string_checked("role", true, false, Some((ROLE_PATTERN, is_role)));
    let prefixes = b.string_list("prefixes", false);
    b.finish().map_err(IntoResponse::into_response)?;
    let (name, role) = (name.unwrap(), role.unwrap());
    let prefixes = prefixes.unwrap_or_default();

    auth.require_admin().map_err(IntoResponse::into_response)?;
    if role != "admin" && prefixes.is_empty() {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "Non-admin keys must have at least one prefix",
        )
        .into_response());
    }
    let st = state.clone();
    let k = blocking(move || st.keys.create(&name, &role, prefixes, principal_id)).await?;
    Ok(Json(json!({
        "id": k.id,
        "key": k.key,
        "key_prefix": k.key_prefix,
        "name": k.name,
        "principal_id": k.principal_id,
        "role": k.role,
        "prefixes": k.prefixes,
        "created_at": k.created_at,
    })))
}

pub async fn list_keys(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Result<Json<Value>, Response> {
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let st = state.clone();
    let keys = blocking(move || st.keys.list()).await?;
    Ok(Json(json!({"count": keys.len(), "keys": keys})))
}

pub async fn update_key(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Path(key_id): Path<String>,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let mut b = Body::new(&map);
    let u = KeyUpdate {
        name: b.string("name", false, None),
        principal_id: b.string("principal_id", false, Some((SLUG_PATTERN, is_valid_slug))),
        role: b.string_checked("role", false, false, Some((ROLE_PATTERN, is_role))),
        prefixes: b.string_list("prefixes", true),
    };
    b.finish().map_err(IntoResponse::into_response)?;
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let st = state.clone();
    let id = key_id.clone();
    match tokio::task::spawn_blocking(move || st.keys.update(&id, &u)).await {
        Ok(Ok(())) => Ok(Json(json!({"success": true, "id": key_id}))),
        // Python: `422 if "principal_id" in str(e) else 404`. The message holds the
        // key id, so an id with that text gives 422.
        Ok(Err(e)) => {
            let status = if key_id.contains("principal_id") {
                StatusCode::UNPROCESSABLE_ENTITY
            } else {
                StatusCode::NOT_FOUND
            };
            Err(key_error(e, status))
        }
        Err(e) => {
            tracing::error!(error = %e, "keys.db task failed");
            Err(internal_error())
        }
    }
}

pub async fn revoke_key(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Path(key_id): Path<String>,
) -> Result<Json<Value>, Response> {
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let st = state.clone();
    let id = key_id.clone();
    match tokio::task::spawn_blocking(move || st.keys.revoke(&id)).await {
        Ok(Ok(())) => Ok(Json(json!({"id": key_id, "revoked": true}))),
        Ok(Err(e)) => Err(key_error(e, StatusCode::NOT_FOUND)),
        Err(e) => {
            tracing::error!(error = %e, "keys.db task failed");
            Err(internal_error())
        }
    }
}

// ---- /audit --------------------------------------------------------------------

pub async fn get_audit(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let limit = query_int(&q, "limit", 50, 1, Some(500), &mut errors);
    let offset = query_int(&q, "offset", 0, 0, None, &mut errors);
    if !errors.is_empty() {
        return Err(ApiError::validation(errors).into_response());
    }
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let f = Filter {
        action: last(&q, "action").map(str::to_string),
        key_id: last(&q, "key_id").map(str::to_string),
        resource_id: last(&q, "resource_id").map(str::to_string),
    };
    if state.audit.is_none() {
        return Ok(Json(json!({"entries": [], "count": 0, "total": 0})));
    }
    let st = state.clone();
    let (entries, total) = blocking(move || {
        let a = st.audit.as_ref().unwrap();
        Ok::<_, rusqlite::Error>((a.query(&f, limit, offset)?, a.count(&f)?))
    })
    .await?;
    Ok(Json(
        json!({"entries": entries, "count": entries.len(), "total": total}),
    ))
}

pub async fn purge_audit(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let days = query_int(&q, "retention_days", 90, 1, Some(365), &mut errors);
    if !errors.is_empty() {
        return Err(ApiError::validation(errors).into_response());
    }
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let st = state.clone();
    let purged = blocking(move || {
        Ok::<_, std::convert::Infallible>(st.audit.as_ref().map_or(0, |a| a.purge(days)))
    })
    .await?;
    Ok(Json(json!({"purged": purged, "retention_days": days})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pydantic_int_from_string() {
        // Each value from the TypeAdapter(int) probe of pydantic 2 in the Python server.
        for (s, v) in [
            ("5", 5),
            (" 5 ", 5),
            ("+5", 5),
            ("1_0", 10),
            ("05", 5),
            ("5.0", 5),
            ("5.000", 5),
            ("1_0.0", 10),
            (" -5 ", -5),
            ("+0", 0),
            ("-0", 0),
            ("\u{a0}5", 5),
            ("\t5\u{b}", 5),
            ("5\n", 5),
        ] {
            assert_eq!(py_int(s), Some(v), "{s:?}");
        }
        for s in [
            "", "abc", "5.5", "5.", ".0", "_1", "1__0", "5_", "+-5", "--5", "5.0_0", "5e0",
            "5.0e0", "1 0", "0005.", "5.10", "0x10", "\u{661}", "\u{2460}",
        ] {
            assert_eq!(py_int(s), None, "{s:?}");
        }
        assert_eq!(py_int("99999999999999999999999"), Some(i64::MAX));
        assert_eq!(py_int("-99999999999999999999999"), Some(-i64::MAX));
    }
}
