//! Auth: the `X-API-Key` header, the admin key from `API_KEY`, managed keys in
//! `keys.db`, three roles, prefix scope, and the limit of failed attempts per client.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::{
    extract::{Request, State},
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Response},
};
use serde_json::{Map, Value, json};

use crate::{AppState, error::ApiError, shell::Client};

const FAIL_WINDOW: Duration = Duration::from_secs(60);

/// `auth_context.source_matches_prefixes`. `None` is unrestricted. A prefix
/// matches its exact source and each source under it, never `prefix-other`.
pub fn source_matches_prefixes(source: &str, prefixes: Option<&[String]>) -> bool {
    let Some(prefixes) = prefixes else {
        return true;
    };
    if source.split('/').any(|part| part == "..") {
        return false;
    }
    prefixes.iter().any(|p| {
        // Python: pfx.rstrip("/").removesuffix("/*").rstrip("/")
        let p = p.trim_end_matches('/');
        let base = p.strip_suffix("/*").unwrap_or(p).trim_end_matches('/');
        source == base
            || source
                .strip_prefix(base)
                .is_some_and(|rest| rest.starts_with('/'))
    })
}

#[derive(Clone, Debug, PartialEq)]
pub struct AuthContext {
    /// "admin", "read-write" or "read-only".
    pub role: String,
    /// `None` is unrestricted (the admin key, or no auth).
    pub prefixes: Option<Vec<String>>,
    /// "env", "managed" or "none".
    pub key_type: &'static str,
    pub key_id: Option<String>,
    pub key_name: Option<String>,
    pub principal_id: Option<String>,
}

impl AuthContext {
    pub fn unrestricted() -> Self {
        Self::admin("none")
    }

    fn admin(key_type: &'static str) -> Self {
        Self {
            role: "admin".into(),
            prefixes: None,
            key_type,
            key_id: None,
            key_name: None,
            principal_id: None,
        }
    }

    pub fn can_read(&self, source: &str) -> bool {
        self.role == "admin" || source_matches_prefixes(source, self.prefixes.as_deref())
    }

    pub fn can_write(&self, source: &str) -> bool {
        match self.role.as_str() {
            "read-only" => false,
            "admin" => true,
            _ => source_matches_prefixes(source, self.prefixes.as_deref()),
        }
    }

    pub fn can_manage_keys(&self) -> bool {
        self.role == "admin"
    }

    pub fn require_admin(&self) -> Result<(), ApiError> {
        if self.can_manage_keys() {
            Ok(())
        } else {
            Err(ApiError::new(StatusCode::FORBIDDEN, "Admin key required"))
        }
    }

    /// The body of `GET /api/keys/me`.
    pub fn me(&self) -> Value {
        let mut m = Map::new();
        m.insert("type".into(), json!(self.key_type));
        m.insert("role".into(), json!(self.role));
        if let Some(p) = &self.prefixes {
            m.insert("prefixes".into(), json!(p));
        }
        if let Some(id) = &self.key_id {
            m.insert("id".into(), json!(id));
        }
        if let Some(name) = &self.key_name {
            m.insert("name".into(), json!(name));
        }
        if self.principal_id.is_some() || self.key_type == "managed" {
            // A managed key from before principals shows principal_id: null.
            m.insert("principal_id".into(), json!(self.principal_id));
        }
        Value::Object(m)
    }
}

/// Failed attempts for each client host in the last 60 s.
#[derive(Default)]
pub struct Failures(Mutex<HashMap<String, Vec<Instant>>>);

impl Failures {
    /// Python order: prune, then refuse at the limit.
    fn over_limit(&self, host: &str, limit: i64, now: Instant) -> bool {
        let mut map = self.0.lock().unwrap();
        if let Some(times) = map.get_mut(host) {
            times.retain(|t| now.duration_since(*t) < FAIL_WINDOW);
            if times.is_empty() {
                map.remove(host);
            }
        }
        if map.len() > 10_000 {
            map.retain(|_, times| {
                times.retain(|t| now.duration_since(*t) < FAIL_WINDOW);
                !times.is_empty()
            });
        }
        map.get(host).map_or(0, Vec::len) as i64 >= limit
    }

    fn record(&self, host: &str, now: Instant) {
        self.0
            .lock()
            .unwrap()
            .entry(host.to_string())
            .or_default()
            .push(now);
    }
}

fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn unauthorized() -> Response {
    ApiError::new(StatusCode::UNAUTHORIZED, "Invalid or missing API key").into_response()
}

/// Route layer for each route that needs a key. Sets the [`AuthContext`] extension.
pub async fn require_key(
    State(state): State<Arc<AppState>>,
    mut req: Request,
    next: Next,
) -> Response {
    let host = req
        .extensions()
        .get::<Client>()
        .map_or_else(|| "unknown".to_string(), |c| c.host.clone());
    let now = Instant::now();
    if state.failures.over_limit(&host, state.auth_rate_limit, now) {
        return ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "Too many failed authentication attempts",
        )
        .into_response();
    }
    let raw = req
        .headers()
        .get("x-api-key")
        .map(|v| v.as_bytes().to_vec())
        .unwrap_or_default();

    let auth = if raw.is_empty() {
        if !state.api_key.is_empty() {
            state.failures.record(&host, now);
            return unauthorized();
        }
        AuthContext::unrestricted()
    } else if !state.api_key.is_empty() && ct_eq(&raw, state.api_key.as_bytes()) {
        AuthContext::admin("env")
    } else {
        let st = state.clone();
        let raw = String::from_utf8_lossy(&raw).into_owned();
        match tokio::task::spawn_blocking(move || st.keys.lookup(&raw)).await {
            Ok(Ok(Some(k))) => AuthContext {
                role: k.role,
                prefixes: Some(k.prefixes),
                key_type: "managed",
                key_id: Some(k.id),
                key_name: Some(k.name),
                principal_id: k.principal_id,
            },
            Ok(Ok(None)) => {
                state.failures.record(&host, now);
                return unauthorized();
            }
            Ok(Err(e)) => {
                tracing::error!(error = %e, "keys.db lookup failed");
                return crate::shell::internal_error();
            }
            Err(e) => {
                tracing::error!(error = %e, "keys.db lookup task failed");
                return crate::shell::internal_error();
            }
        }
    };
    req.extensions_mut().insert(auth);
    next.run(req).await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn prefix_boundary_rule() {
        let pf = p(&["claude-code/"]);
        let m = |s| source_matches_prefixes(s, Some(&pf));
        assert!(m("claude-code"));
        assert!(m("claude-code/voltis"));
        assert!(!m("claude-code-other/x"));
        assert!(!m("claude-code/../codex"));
        assert!(!m("codex/x"));
        for form in ["codex/*", "codex", "codex//", "codex/*/"] {
            let pf = p(&[form]);
            assert!(source_matches_prefixes("codex/a", Some(&pf)), "{form}");
            assert!(!source_matches_prefixes("codexa", Some(&pf)), "{form}");
        }
        assert!(source_matches_prefixes("anything/..", None));
        assert!(!source_matches_prefixes("x", Some(&[])));
    }

    fn ctx(role: &str, prefixes: &[&str]) -> AuthContext {
        AuthContext {
            role: role.into(),
            prefixes: Some(p(prefixes)),
            key_type: "managed",
            key_id: Some("id-1".into()),
            key_name: Some("n".into()),
            principal_id: None,
        }
    }

    #[test]
    fn three_roles() {
        let ro = ctx("read-only", &["wip/"]);
        assert!(ro.can_read("wip/a") && !ro.can_read("codex/a"));
        assert!(!ro.can_write("wip/a"));
        let rw = ctx("read-write", &["wip/"]);
        assert!(rw.can_write("wip/a") && !rw.can_write("codex/a"));
        assert!(!rw.can_manage_keys());
        let admin = ctx("admin", &[]);
        assert!(admin.can_read("x") && admin.can_write("x") && admin.can_manage_keys());
        assert!(rw.require_admin().is_err() && admin.require_admin().is_ok());
    }

    #[test]
    fn me_body() {
        assert_eq!(
            AuthContext::admin("env").me(),
            json!({"type": "env", "role": "admin"})
        );
        assert_eq!(
            AuthContext::unrestricted().me(),
            json!({"type": "none", "role": "admin"})
        );
        assert_eq!(
            ctx("read-only", &["claude-code/"]).me(),
            json!({"type": "managed", "role": "read-only", "prefixes": ["claude-code/"],
                   "id": "id-1", "name": "n", "principal_id": null})
        );
    }

    #[test]
    fn failure_limit_and_window() {
        let f = Failures::default();
        let t0 = Instant::now();
        for _ in 0..3 {
            assert!(!f.over_limit("1.2.3.4", 3, t0));
            f.record("1.2.3.4", t0);
        }
        assert!(f.over_limit("1.2.3.4", 3, t0));
        assert!(!f.over_limit("5.6.7.8", 3, t0));
        assert!(!f.over_limit("1.2.3.4", 3, t0 + FAIL_WINDOW));
        assert!(f.0.lock().unwrap().is_empty());
    }

    #[test]
    fn constant_time_compare() {
        assert!(ct_eq(b"abc", b"abc"));
        assert!(!ct_eq(b"abc", b"abd"));
        assert!(!ct_eq(b"abc", b"ab"));
    }
}
