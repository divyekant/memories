//! The `/promotions` routes and the `promotion` part of `GET /metrics` in the
//! off mode of project promotion, with no engine. Each expected body is the
//! reply of the Python server v5.19.0.

mod common;

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode, header},
};
use memories_server::{AppState, app, audit::Entry};
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";
const SLUG: &str = "^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$";

async fn send(
    st: &Arc<AppState>,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> (StatusCode, Value) {
    let mut b = Request::builder()
        .method(method)
        .uri(path)
        .header("X-API-Key", ADMIN);
    if body.is_some() {
        b = b.header(header::CONTENT_TYPE, "application/json");
    }
    let body = body.map_or_else(Body::empty, |s| Body::from(s.to_string()));
    let res = app(st.clone())
        .oneshot(b.body(body).unwrap())
        .await
        .unwrap();
    let status = res.status();
    let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn state(audit: bool) -> Arc<AppState> {
    let mut cfg = common::config(ADMIN, 50);
    cfg.audit_log = audit;
    common::state(&cfg)
}

fn pattern(field: &str, input: &str) -> Value {
    json!({"type": "string_pattern_mismatch", "loc": ["query", field],
           "msg": format!("String should match pattern '{SLUG}'"), "input": input, "ctx": {"pattern": SLUG}})
}

fn date(field: &str, input: &str, error: &str) -> Value {
    json!({"type": "datetime_from_date_parsing", "loc": ["query", field],
           "msg": format!("Input should be a valid datetime or date, {error}"), "input": input,
           "ctx": {"error": error}})
}

#[tokio::test]
async fn list_is_empty_and_checks_the_query() {
    let st = state(false);
    assert_eq!(
        send(&st, "GET", "/promotions", None).await,
        (
            StatusCode::OK,
            json!({"promotions": [], "count": 0, "limit": 50, "offset": 0})
        )
    );
    assert_eq!(
        send(
            &st,
            "GET",
            "/promotions?state=candidate&limit=7&offset=3&project_id=a-1&owner=dk",
            None
        )
        .await,
        (
            StatusCode::OK,
            json!({"promotions": [], "count": 0, "limit": 7, "offset": 3})
        )
    );
    assert_eq!(
        send(
            &st,
            "GET",
            "/promotions?since=1700000000&until=2026-01-01T10:00:00%2B05:30",
            None
        )
        .await
        .0,
        StatusCode::OK
    );
    // pydantic truncates the digits after the microseconds.
    for v in [
        "2026-01-01T10:00:00.123456789Z",
        "2026-01-01%2010:00:00.1234567",
        "1700000000.1234567",
    ] {
        assert_eq!(
            send(&st, "GET", &format!("/promotions?since={v}"), None)
                .await
                .0,
            StatusCode::OK,
            "{v}"
        );
    }
    assert_eq!(
        send(&st, "GET", "/promotions?state=nope", None).await,
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            json!({"detail": "'nope' is not a valid PromotionStatus"})
        )
    );
    let (s, body) = send(
        &st,
        "GET",
        "/promotions?project_id=Bad_&owner=-x&since=abc&until=2026-13-01&limit=0&offset=-1&state=zz",
        None,
    )
    .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body,
        json!({"detail": [
            pattern("project_id", "Bad_"),
            pattern("owner", "-x"),
            date("since", "abc", "input is too short"),
            date("until", "2026-13-01", "month value is outside expected range of 1-12"),
            {"type": "greater_than_equal", "loc": ["query", "limit"], "msg": "Input should be greater than or equal to 1", "input": "0", "ctx": {"ge": 1}},
            {"type": "greater_than_equal", "loc": ["query", "offset"], "msg": "Input should be greater than or equal to 0", "input": "-1", "ctx": {"ge": 0}},
        ]})
    );
    // Starlette gives the last value of a repeated parameter.
    assert_eq!(
        send(&st, "GET", "/promotions?project_id=a&project_id=B_", None)
            .await
            .1,
        json!({"detail": [pattern("project_id", "B_")]})
    );
    for (input, error) in [
        (
            "2026-01-01T25:00",
            "unexpected extra characters at the end of the input",
        ),
        ("2026-02-30", "day value is outside expected range"),
        ("%202026-01-01", "invalid character in year"),
        ("1.5e9", "input is too short"),
    ] {
        let shown = input.replace("%20", " ");
        assert_eq!(
            send(&st, "GET", &format!("/promotions?since={input}"), None)
                .await
                .1,
            json!({"detail": [date("since", &shown, error)]})
        );
    }
}

#[tokio::test]
async fn get_finds_no_candidate() {
    let st = state(false);
    let gone = (
        StatusCode::NOT_FOUND,
        json!({"detail": "Promotion candidate not found"}),
    );
    assert_eq!(send(&st, "GET", "/promotions/1", None).await, gone);
    assert_eq!(send(&st, "GET", "/promotions/-5", None).await, gone);
    assert_eq!(
        send(&st, "GET", "/promotions/99999999999999999999999", None).await,
        gone
    );
    assert_eq!(
        send(&st, "GET", "/promotions/abc", None).await,
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            json!({"detail": [{"type": "int_parsing", "loc": ["path", "candidate_id"],
            "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "abc"}]})
        )
    );
}

#[tokio::test]
async fn approve_and_reject_check_the_body_then_find_no_candidate() {
    let st = state(true);
    let gone = (
        StatusCode::NOT_FOUND,
        json!({"detail": "Promotion candidate not found"}),
    );
    let blank = (
        StatusCode::CONFLICT,
        json!({"detail": "reason is required"}),
    );
    let int_path = json!({"type": "int_parsing", "loc": ["path", "candidate_id"],
        "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "x"});
    for action in ["approve", "reject"] {
        let path = format!("/promotions/1/{action}");
        assert_eq!(
            send(&st, "POST", &path, Some(r#"{"reason":"parity"}"#)).await,
            gone
        );
        assert_eq!(
            send(&st, "POST", &path, Some(r#"{"reason":"   "}"#)).await,
            blank
        );
        assert_eq!(
            send(&st, "POST", &path, Some("{\"reason\":\"\\u001c\"}")).await,
            blank
        );
        assert_eq!(
            send(&st, "POST", &path, Some(r#"{"reason":null}"#)).await.1,
            json!({"detail": [{"type": "string_type", "loc": ["body", "reason"], "msg": "Input should be a valid string", "input": null}]})
        );
        assert_eq!(
            send(&st, "POST", &path, Some("[1]")).await.1,
            json!({"detail": [{"type": "model_attributes_type", "loc": ["body"],
                "msg": "Input should be a valid dictionary or object to extract fields from", "input": [1]}]})
        );
        assert_eq!(
            send(&st, "POST", &format!("/promotions/x/{action}"), None)
                .await
                .1,
            json!({"detail": [int_path.clone(), {"type": "missing", "loc": ["body"], "msg": "Field required", "input": null}]})
        );
        assert_eq!(
            send(
                &st,
                "POST",
                &format!("/promotions/x/{action}"),
                Some(r#"{"zz":1}"#)
            )
            .await
            .1,
            json!({"detail": [int_path.clone(),
                {"type": "missing", "loc": ["body", "reason"], "msg": "Field required", "input": {"zz": 1}},
                {"type": "extra_forbidden", "loc": ["body", "zz"], "msg": "Extra inputs are not permitted", "input": 1}]})
        );
    }
    assert_eq!(
        send(
            &st,
            "POST",
            "/promotions/1/approve",
            Some(r#"{"reason":"ok","shared_text":null}"#)
        )
        .await,
        gone
    );
    assert_eq!(
        send(
            &st,
            "POST",
            "/promotions/1/approve",
            Some(r#"{"reason":"","shared_text":"","zz":1,"aa":2}"#)
        )
        .await
        .1,
        json!({"detail": [
            {"type": "string_too_short", "loc": ["body", "reason"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}},
            {"type": "string_too_short", "loc": ["body", "shared_text"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}},
            // Python gives the extra fields in body order (zz, aa). serde_json
            // keeps no key order, so this server gives them in name order.
            {"type": "extra_forbidden", "loc": ["body", "aa"], "msg": "Extra inputs are not permitted", "input": 2},
            {"type": "extra_forbidden", "loc": ["body", "zz"], "msg": "Extra inputs are not permitted", "input": 1},
        ]})
    );
    assert_eq!(
        send(
            &st,
            "POST",
            "/promotions/1/reject",
            Some(r#"{"reason":"ok","shared_text":"x"}"#)
        )
        .await
        .1,
        json!({"detail": [{"type": "extra_forbidden", "loc": ["body", "shared_text"], "msg": "Extra inputs are not permitted", "input": "x"}]})
    );
    // Python audits only a decision that succeeds.
    assert_eq!(send(&st, "GET", "/audit", None).await.1["total"], 0);
}

#[tokio::test]
async fn metrics_has_the_promotion_part() {
    let st = state(true);
    let want = json!({
        "status_counts": {}, "outcomes_by_route": {}, "outcomes_by_principal": {},
        "outcomes_by_policy": {}, "project_count_by_kind": {}, "exact_reuse_count": 0,
        "exact_reuse_rate": 0.0, "semantic_near_duplicate_count": 0,
        "semantic_near_duplicate_rate": 0.0, "promotion_error_count": 0,
        "unreviewable_count": 0, "oldest_unreviewable_age_hours": 0.0,
        "terminal_retention_expirations": 0,
        "alerts": {"unreviewable_rate_alert": false, "unreviewable_backlog_alert": false},
    });
    assert_eq!(
        send(&st, "GET", "/metrics", None).await.1["promotion"],
        want
    );
    st.audit(Entry {
        action: "promotion.failed".into(),
        ..Entry::default()
    })
    .await;
    st.audit(Entry {
        action: "promotion.rejected".into(),
        ..Entry::default()
    })
    .await;
    assert_eq!(
        send(&st, "GET", "/metrics", None).await.1["promotion"]["promotion_error_count"],
        1
    );
}
