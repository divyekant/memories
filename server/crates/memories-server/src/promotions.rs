//! The `/promotions` routes and the `promotion` part of `GET /metrics`, in the
//! `off` mode of project promotion only (task P5-6, production baseline P0-3).
//!
//! In the `off` mode the Python server makes no candidate. Production has no
//! record with a `promotion` field, so the list is empty and each candidate
//! ID is a 404. The replies keep the Python checks and their order.
//!
//! shortcut: a record with a `promotion` field (made by a Python server in
//! `shadow` or `auto` mode) is not read as a candidate. Port the promotion
//! service when a host needs a mode other than `off`.

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use std::collections::BTreeMap;
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, Query},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use memories_engine::{catalog::Catalog, policy::parse_memory_source};
use memories_extract::text::py_strip;
use serde_json::{Value, json};
use strum::EnumMessage;

use crate::{
    AppState,
    admin::{last, query_int},
    audit::Filter,
    error::{ApiError, BodyResult, item, path_int},
    keys::is_valid_slug,
    validate::{Fields, finish},
};

const SLUG_PATTERN: &str = "^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$";
const STATUSES: &[&str] = &[
    "private",
    "candidate",
    "shadow_approved",
    "deferred",
    "failed",
    "unreviewable",
    "rejected",
    "promoted",
];

/// `PROJECT_PROMOTION_MODE` as `load_promotion_config` reads it. An error
/// stops the start: this server has only the `off` mode.
pub fn check_mode(raw: Option<&str>) -> Result<(), String> {
    match raw.map(|v| v.trim().to_lowercase()).as_deref() {
        Some("shadow" | "auto") => Err(format!(
            "PROJECT_PROMOTION_MODE={} is not supported: this server has only the off mode of project promotion",
            raw.unwrap_or_default().trim()
        )),
        _ => Ok(()),
    }
}

fn not_found() -> Response {
    ApiError::new(StatusCode::NOT_FOUND, "Promotion candidate not found").into_response()
}

fn slug_query(q: &[(String, String)], name: &str, errors: &mut Vec<Value>) {
    if let Some(v) = last(q, name).filter(|v| !is_valid_slug(v)) {
        let mut e = item(
            "string_pattern_mismatch",
            json!(["query", name]),
            &format!("String should match pattern '{SLUG_PATTERN}'"),
            json!(v),
        );
        e["ctx"] = json!({"pattern": SLUG_PATTERN});
        errors.push(e);
    }
}

/// pydantic-core lax `datetime` from a string: a datetime (a number is a Unix
/// time), else a date. A failure gives the error of the date parse.
fn datetime_query(q: &[(String, String)], name: &str, errors: &mut Vec<Value>) {
    let Some(v) = last(q, name) else { return };
    // pydantic truncates digits after the microseconds. The speedate of
    // pydantic-core 2.23 reads no exponent ("1.5e9"); speedate 0.18 does.
    let cfg = speedate::DateTimeConfig {
        time_config: speedate::TimeConfig {
            microseconds_precision_overflow_behavior:
                speedate::MicrosecondsPrecisionOverflowBehavior::Truncate,
            unix_timestamp_offset: Some(0),
        },
        ..Default::default()
    };
    if !v.contains(['e', 'E'])
        && speedate::DateTime::parse_bytes_with_config(v.as_bytes(), &cfg).is_ok()
    {
        return;
    }
    if let Err(e) = speedate::Date::parse_bytes(v.as_bytes()) {
        let doc = e.get_documentation().unwrap_or_default();
        let mut e = item(
            "datetime_from_date_parsing",
            json!(["query", name]),
            &format!("Input should be a valid datetime or date, {doc}"),
            json!(v),
        );
        e["ctx"] = json!({"error": doc});
        errors.push(e);
    }
}

/// `GET /promotions`.
pub async fn list(Query(q): Query<Vec<(String, String)>>) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    slug_query(&q, "project_id", &mut errors);
    slug_query(&q, "owner", &mut errors);
    datetime_query(&q, "since", &mut errors);
    datetime_query(&q, "until", &mut errors);
    let limit = query_int(&q, "limit", 50, 1, Some(500), &mut errors);
    let offset = query_int(&q, "offset", 0, 0, None, &mut errors);
    if !errors.is_empty() {
        return Err(ApiError::validation(errors).into_response());
    }
    if let Some(s) = last(&q, "state").filter(|s| !STATUSES.contains(s)) {
        // Python: the ValueError of `PromotionStatus(state)`.
        let detail = format!("{} is not a valid PromotionStatus", py_repr(s));
        return Err(ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, detail).into_response());
    }
    Ok(Json(
        json!({"promotions": [], "count": 0, "limit": limit, "offset": offset}),
    ))
}

/// Python `repr(str)`: single quotes, unless the text has a single quote and
/// no double quote.
// shortcut: other characters that Python does not print (for example U+0085)
// are not escaped; add them if a client sends such a state value.
fn py_repr(s: &str) -> String {
    let q = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(q);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == q => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(q);
    out
}

/// `GET /promotions/{candidate_id}`.
pub async fn get(Path(raw): Path<String>) -> Response {
    match path_int("candidate_id", &raw) {
        Ok(_) => not_found(),
        Err(e) => e.into_response(),
    }
}

/// `POST /promotions/{candidate_id}/approve` (`PromotionApproveRequest`).
pub async fn approve(Path(raw): Path<String>, body: BodyResult) -> Response {
    decide(&raw, body, true)
}

/// `POST /promotions/{candidate_id}/reject` (`PromotionRejectRequest`).
pub async fn reject(Path(raw): Path<String>, body: BodyResult) -> Response {
    decide(&raw, body, false)
}

fn decide(raw: &str, body: BodyResult, approve: bool) -> Response {
    let mut errors = Vec::new();
    let id = path_int("candidate_id", raw)
        .map_err(|e| {
            if let Value::Array(items) = e.detail {
                errors.extend(items);
            }
        })
        .ok();
    let map = match body.take(&mut errors) {
        Ok(m) => m,
        Err(e) => return e.into_response(),
    };
    let reason = map.as_ref().and_then(|m| {
        let mut f = Fields::new(m, vec![json!("body")], &mut errors);
        let reason = f.string("reason", true, (1, 2000), None);
        if approve {
            f.string("shared_text", false, (1, 12000), None);
        }
        let known: &[&str] = if approve {
            &["reason", "shared_text"]
        } else {
            &["reason"]
        };
        for (k, v) in m.iter().filter(|(k, _)| !known.contains(&k.as_str())) {
            errors.push(item(
                "extra_forbidden",
                json!(["body", k]),
                "Extra inputs are not permitted",
                v.clone(),
            ));
        }
        reason
    });
    let reason = match finish(errors, id.and(reason)) {
        Ok(r) => r,
        Err(e) => return e.into_response(),
    };
    // Python `_manual_reason` runs before the candidate lookup.
    if py_strip(&reason).is_empty() {
        return ApiError::new(StatusCode::CONFLICT, "reason is required").into_response();
    }
    not_found()
}

/// The `promotion` part of `GET /metrics` (`PromotionService.metrics_snapshot`)
/// with no candidate record. `errors` is the count of `promotion.failed` audit rows.
pub fn metrics(catalog: Option<&Catalog>, errors: i64) -> Value {
    let mut kinds: BTreeMap<String, u64> = BTreeMap::new();
    let mut reuse = 0u64;
    for (_, e) in catalog.into_iter().flat_map(Catalog::iter) {
        if e.archived || !e.source_is_str {
            continue;
        }
        if let Some(p) = parse_memory_source(&e.source).filter(|p| p.namespace == "project") {
            *kinds.entry(p.kind.to_string()).or_default() += 1;
            reuse += u64::from(e.multi_source);
        }
    }
    json!({
        "status_counts": {},
        "outcomes_by_route": {},
        "outcomes_by_principal": {},
        "outcomes_by_policy": {},
        "project_count_by_kind": kinds,
        "exact_reuse_count": reuse,
        // No record is promoted, so Python divides by nothing and gives 0.0.
        "exact_reuse_rate": 0.0,
        "semantic_near_duplicate_count": 0,
        "semantic_near_duplicate_rate": 0.0,
        "promotion_error_count": errors,
        "unreviewable_count": 0,
        "oldest_unreviewable_age_hours": 0.0,
        "terminal_retention_expirations": 0,
        "alerts": {"unreviewable_rate_alert": false, "unreviewable_backlog_alert": false},
    })
}

/// The `promotion` part of `GET /metrics` for this server.
pub async fn metrics_part(state: &Arc<AppState>) -> Result<Value, Response> {
    let errors = match &state.audit {
        None => 0,
        Some(_) => {
            let st = state.clone();
            let f = Filter {
                action: Some("promotion.failed".into()),
                ..Filter::default()
            };
            tokio::task::spawn_blocking(move || st.audit.as_ref().map_or(Ok(0), |a| a.count(&f)))
                .await
                .ok()
                .and_then(Result::ok)
                .ok_or_else(crate::shell::internal_error)?
        }
    };
    let engine = state.engine.get();
    let catalog = engine.map(|e| e.catalog());
    Ok(metrics(catalog.as_deref(), errors))
}

#[cfg(test)]
mod tests {
    use super::*;
    use memories_types::Record;

    fn rec(id: u64, v: Value) -> Record {
        let mut m = v.as_object().unwrap().clone();
        m.insert("id".into(), json!(id));
        Record::from_json(Value::Object(m)).unwrap()
    }

    #[test]
    fn metrics_counts_live_project_records() {
        let mut c = Catalog::default();
        c.put(&rec(
            1,
            json!({"source": "project/alpha/knowledge", "source_memory_ids": [3, 4]}),
        ));
        c.put(&rec(
            2,
            json!({"source": "project/alpha/decisions", "source_memory_ids": [3]}),
        ));
        c.put(&rec(3, json!({"source": "project/beta/knowledge", "archived": true, "source_memory_ids": [1, 2]})));
        c.put(&rec(
            4,
            json!({"source": "person/dk/alpha/knowledge", "source_memory_ids": [1, 2]}),
        ));
        c.put(&rec(5, json!({"source": "project/Bad/knowledge"})));
        c.put(&rec(
            6,
            json!({"source": "project/beta/knowledge", "source_memory_ids": "1,2"}),
        ));
        let m = metrics(Some(&c), 3);
        assert_eq!(
            m["project_count_by_kind"],
            json!({"knowledge": 2, "decisions": 1})
        );
        assert_eq!(m["exact_reuse_count"], 1);
        assert_eq!(m["exact_reuse_rate"], 0.0);
        assert_eq!(m["promotion_error_count"], 3);
    }

    #[test]
    fn mode_check() {
        assert!(check_mode(None).is_ok());
        assert!(check_mode(Some("")).is_ok());
        assert!(check_mode(Some("off")).is_ok());
        // Python falls back to off when the value is not valid.
        assert!(check_mode(Some("bogus")).is_ok());
        assert!(check_mode(Some(" Shadow ")).is_err());
        assert!(check_mode(Some("AUTO")).is_err());
    }

    #[test]
    fn repr_is_python() {
        assert_eq!(py_repr("nope"), "'nope'");
        assert_eq!(py_repr("it's"), "\"it's\"");
        assert_eq!(py_repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(py_repr("a\nb\\"), "'a\\nb\\\\'");
    }
}
