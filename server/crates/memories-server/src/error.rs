//! Error replies with the FastAPI shape: `{"detail": ...}` and the 422 list.

use axum::{
    Json,
    body::Bytes,
    extract::{FromRequest, Request},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use serde_json::{Map, Value, json};

#[derive(Debug)]
pub struct ApiError {
    pub status: StatusCode,
    pub detail: Value,
}

impl ApiError {
    pub fn new(status: StatusCode, detail: impl Into<Value>) -> Self {
        Self {
            status,
            detail: detail.into(),
        }
    }

    /// HTTP 422 with a list of error items (see the item functions below).
    pub fn validation(items: Vec<Value>) -> Self {
        Self::new(StatusCode::UNPROCESSABLE_ENTITY, Value::Array(items))
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({ "detail": self.detail }))).into_response()
    }
}

// Error items with the pydantic 2 type names and messages.

pub fn item(kind: &str, loc: Value, msg: &str, input: Value) -> Value {
    json!({"type": kind, "loc": loc, "msg": msg, "input": input})
}

pub fn missing(loc: Value, input: Value) -> Value {
    item("missing", loc, "Field required", input)
}

pub fn int_parsing(loc: Value, input: Value) -> Value {
    item(
        "int_parsing",
        loc,
        "Input should be a valid integer, unable to parse string as an integer",
        input,
    )
}

fn plural(n: usize, word: &str) -> String {
    if n == 1 {
        format!("{n} {word}")
    } else {
        format!("{n} {word}s")
    }
}

pub fn list_too_short(loc: Value, input: Value, min: usize, actual: usize) -> Value {
    let msg = format!(
        "List should have at least {} after validation, not {actual}",
        plural(min, "item")
    );
    let mut v = item("too_short", loc, &msg, input);
    v["ctx"] = json!({"field_type": "List", "min_length": min, "actual_length": actual});
    v
}

pub fn string_too_short(loc: Value, input: Value, min: usize) -> Value {
    let msg = format!("String should have at least {}", plural(min, "character"));
    let mut v = item("string_too_short", loc, &msg, input);
    v["ctx"] = json!({"min_length": min});
    v
}

/// A path parameter that must be an integer, as FastAPI checks `memory_id: int`.
pub fn path_int(name: &str, raw: &str) -> Result<i64, ApiError> {
    raw.parse()
        .map_err(|_| ApiError::validation(vec![int_parsing(json!(["path", name]), json!(raw))]))
}

/// The JSON object body of a request, read as FastAPI reads a model body.
/// Unknown fields stay in the map; the route reads only the fields it knows.
pub struct JsonBody(pub Map<String, Value>);

impl<S: Send + Sync> FromRequest<S> for JsonBody {
    type Rejection = Response;

    async fn from_request(req: Request, state: &S) -> Result<Self, Self::Rejection> {
        let is_json = req
            .headers()
            .get(header::CONTENT_TYPE)
            .map(|v| content_type_is_json(v.to_str().unwrap_or("")));
        let bytes = Bytes::from_request(req, state)
            .await
            .map_err(IntoResponse::into_response)?;
        object_body(&bytes, is_json.unwrap_or(true))
            .map(JsonBody)
            .map_err(IntoResponse::into_response)
    }
}

/// FastAPI parses the body as JSON when there is no content type, or when the
/// type is `application/json` or `application/*+json`.
fn content_type_is_json(ct: &str) -> bool {
    let mime = ct
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    match mime.split_once('/') {
        Some(("application", sub)) => sub == "json" || sub.ends_with("+json"),
        _ => false,
    }
}

fn object_body(bytes: &[u8], is_json: bool) -> Result<Map<String, Value>, ApiError> {
    if bytes.is_empty() {
        return Err(ApiError::validation(vec![missing(
            json!(["body"]),
            Value::Null,
        )]));
    }
    let value = if is_json {
        serde_json::from_slice::<Value>(bytes).map_err(|e| json_invalid(bytes, &e))?
    } else {
        Value::String(String::from_utf8_lossy(bytes).into_owned())
    };
    match value {
        Value::Object(map) => Ok(map),
        other => Err(ApiError::validation(vec![item(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            other,
        )])),
    }
}

/// `loc` holds the character position of the error, as Python's JSONDecodeError.pos.
/// `ctx.error` is the serde_json message; the Python message text is different.
fn json_invalid(bytes: &[u8], e: &serde_json::Error) -> ApiError {
    // serde_json gives a line and a byte column; Python gives a character index.
    let mut lines = bytes.split_inclusive(|&b| b == b'\n');
    let chars = |b: &[u8]| String::from_utf8_lossy(b).chars().count();
    let before: usize = lines
        .by_ref()
        .take(e.line().saturating_sub(1))
        .map(chars)
        .sum();
    let line = lines.next().unwrap_or_default();
    let col = e.column().saturating_sub(1).min(line.len());
    let pos = before + chars(&line[..col]);
    let mut v = item(
        "json_invalid",
        json!(["body", pos]),
        "JSON decode error",
        json!({}),
    );
    v["ctx"] = json!({"error": e.to_string()});
    ApiError::validation(vec![v])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn detail(e: ApiError) -> Value {
        assert_eq!(e.status, StatusCode::UNPROCESSABLE_ENTITY);
        e.detail
    }

    #[test]
    fn empty_body_is_missing() {
        assert_eq!(
            detail(object_body(b"", true).unwrap_err()),
            json!([{"type": "missing", "loc": ["body"], "msg": "Field required", "input": null}])
        );
    }

    #[test]
    fn non_object_and_non_json_bodies() {
        let e = detail(object_body(b"[1]", true).unwrap_err());
        assert_eq!(e[0]["type"], "model_attributes_type");
        assert_eq!(e[0]["input"], json!([1]));
        let e = detail(object_body(br#"{"query":"x"}"#, false).unwrap_err());
        assert_eq!(e[0]["input"], json!(r#"{"query":"x"}"#));
    }

    #[test]
    fn bad_json_has_python_position() {
        // Python: {"type":"json_invalid","loc":["body",1],"msg":"JSON decode error","input":{},...}
        let e = detail(object_body(b"{bad", true).unwrap_err());
        assert_eq!(e[0]["loc"], json!(["body", 1]));
        assert_eq!(e[0]["msg"], "JSON decode error");
        assert_eq!(e[0]["input"], json!({}));
        let e = detail(object_body(b"{\n \"a\": x}", true).unwrap_err());
        assert_eq!(e[0]["loc"], json!(["body", 8]));
        // Python: json.loads('{"q":"é", bad}') fails at character 10.
        let e = detail(object_body("{\"q\":\"é\", bad}".as_bytes(), true).unwrap_err());
        assert_eq!(e[0]["loc"], json!(["body", 10]));
    }

    #[test]
    fn unknown_fields_stay_in_the_map() {
        let m = object_body(br#"{"query":"q","not_a_field":1}"#, true).unwrap();
        assert_eq!(m["query"], "q");
    }

    #[test]
    fn content_types() {
        assert!(content_type_is_json("application/json; charset=utf-8"));
        assert!(content_type_is_json("application/vnd.api+json"));
        assert!(!content_type_is_json("text/plain"));
        assert!(!content_type_is_json("application/x-ndjson"));
    }

    // The bodies of the golden cases search-422, search-batch-422 and search-batch-422-bad-query.
    #[test]
    fn items_match_golden_bodies() {
        assert_eq!(
            missing(json!(["body", "query"]), json!({"k": 3})),
            json!({"input": {"k": 3}, "loc": ["body", "query"], "msg": "Field required", "type": "missing"})
        );
        assert_eq!(
            list_too_short(json!(["body", "queries"]), json!([]), 1, 0),
            json!({"ctx": {"actual_length": 0, "field_type": "List", "min_length": 1}, "input": [],
                   "loc": ["body", "queries"], "msg": "List should have at least 1 item after validation, not 0",
                   "type": "too_short"})
        );
        assert_eq!(
            string_too_short(json!(["body", "queries", 0, "query"]), json!(""), 1),
            json!({"ctx": {"min_length": 1}, "input": "", "loc": ["body", "queries", 0, "query"],
                   "msg": "String should have at least 1 character", "type": "string_too_short"})
        );
        assert_eq!(
            detail(path_int("memory_id", "abc").unwrap_err()),
            json!([{"type": "int_parsing", "loc": ["path", "memory_id"],
                    "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "abc"}])
        );
    }
}
