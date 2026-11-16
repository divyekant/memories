//! Record and replay layer for tests.
//!
//! `ReplayServer` is a local HTTP server. It gives the recorded replies in
//! order and captures each request in the form of the replay files:
//! `{method, path, headers, body}`. The captured headers hide each secret.
//! Point a provider base URL (for example `ANTHROPIC_BASE_URL`) at it.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use axum::body::Bytes;
use axum::http::{HeaderMap, Method, StatusCode, Uri};
use axum::response::IntoResponse;
use serde_json::{Value, json};

/// Transport headers that clients set on their own. They are not part of the contract.
const IGNORED_HEADERS: [&str; 6] = [
    "host",
    "content-length",
    "user-agent",
    "accept",
    "accept-encoding",
    "connection",
];
const SECRET_HEADERS: [&str; 2] = ["authorization", "x-api-key"];
pub const REDACTED: &str = "<redacted>";

/// Replace each known secret with `<NAME>`, so a test sees which secret was sent.
/// Hide a whole auth header that holds an unknown value.
pub fn redact(name: &str, value: &str, secrets: &[(String, String)]) -> String {
    let out = secrets
        .iter()
        .filter(|(_, s)| !s.is_empty())
        .fold(value.to_string(), |v, (n, s)| {
            v.replace(s.as_str(), &format!("<{n}>"))
        });
    if SECRET_HEADERS.contains(&name) && out == value {
        REDACTED.to_string()
    } else {
        out
    }
}

/// The contract headers of a request: lower-case names, sorted, secrets hidden.
pub fn contract_headers(headers: &HeaderMap, secrets: &[(String, String)]) -> Value {
    let map: BTreeMap<String, Value> = headers
        .iter()
        .filter(|(k, _)| {
            let k = k.as_str();
            !IGNORED_HEADERS.contains(&k) && !k.starts_with("x-stainless-")
        })
        .map(|(k, v)| {
            let v = String::from_utf8_lossy(v.as_bytes());
            (
                k.as_str().to_string(),
                json!(redact(k.as_str(), &v, secrets)),
            )
        })
        .collect();
    json!(map)
}

#[derive(Default)]
struct State {
    replies: Vec<(u16, Value)>,
    next: usize,
    captured: Vec<Value>,
}

pub struct ReplayServer {
    base_url: String,
    state: Arc<Mutex<State>>,
    task: tokio::task::JoinHandle<()>,
}

impl ReplayServer {
    /// Start on a free loopback port. `replies` are `(status, JSON body)` in order.
    /// `secrets` are `(name, value)` pairs to hide. After the last reply, each
    /// request gets HTTP 418, which a provider does not retry.
    pub async fn start(replies: Vec<(u16, Value)>, secrets: Vec<(String, String)>) -> Self {
        let state = Arc::new(Mutex::new(State {
            replies,
            ..State::default()
        }));
        let shared = state.clone();
        let secrets = Arc::new(secrets);
        let app = axum::Router::new().fallback(
            move |method: Method, uri: Uri, headers: HeaderMap, body: Bytes| {
                let shared = shared.clone();
                let secrets = secrets.clone();
                async move {
                    let body = if body.is_empty() {
                        Value::Null
                    } else {
                        serde_json::from_slice(&body)
                            .unwrap_or_else(|_| json!(String::from_utf8_lossy(&body)))
                    };
                    let mut s = shared.lock().unwrap();
                    s.captured.push(json!({
                        "method": method.as_str(),
                        "path": uri.path_and_query().map_or(uri.path(), |p| p.as_str()),
                        "headers": contract_headers(&headers, &secrets),
                        "body": body,
                    }));
                    let (status, reply) = s
                        .replies
                        .get(s.next)
                        .cloned()
                        .unwrap_or((418, json!({"error": "no recorded reply"})));
                    s.next += 1;
                    (StatusCode::from_u16(status).unwrap(), axum::Json(reply)).into_response()
                }
            },
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        Self {
            base_url,
            state,
            task,
        }
    }

    pub fn base_url(&self) -> String {
        self.base_url.clone()
    }

    /// The captured requests, oldest first.
    pub fn requests(&self) -> Vec<Value> {
        self.state.lock().unwrap().captured.clone()
    }
}

impl Drop for ReplayServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redact_hides_known_and_unknown_secrets() {
        let s = vec![("KEY".to_string(), "sk-secret".to_string())];
        assert_eq!(
            redact("authorization", "Bearer sk-secret", &s),
            "Bearer <KEY>"
        );
        assert_eq!(redact("authorization", "Bearer other", &s), REDACTED);
        assert_eq!(redact("x-api-key", "unknown", &[]), REDACTED);
        assert_eq!(redact("anthropic-beta", "a,b", &s), "a,b");
        assert_eq!(redact("x-custom", "has sk-secret", &s), "has <KEY>");
    }
}
