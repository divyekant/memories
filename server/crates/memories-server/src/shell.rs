//! The HTTP shell: client address, access log, route metrics, CORS, the 404,
//! 405 and 500 replies, the trailing-slash redirect, and the route table.
//! Each rule copies FastAPI 0.x and Starlette as the Python server v5.19.0 runs them.

use std::collections::{HashMap, HashSet, VecDeque};
use std::net::SocketAddr;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use axum::{
    extract::{ConnectInfo, Request, State},
    http::{HeaderMap, HeaderValue, Method, StatusCode, header},
    middleware::Next,
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};

use crate::{AppState, error::ApiError};

/// The client as uvicorn sees it, after the proxy headers of a trusted proxy.
#[derive(Clone, Debug)]
pub struct Client {
    pub host: String,
    pub port: u16,
    pub scheme: String,
}

/// uvicorn `--forwarded-allow-ips` (`FORWARDED_ALLOW_IPS`, default `127.0.0.1`).
pub struct TrustedProxies {
    all: bool,
    hosts: HashSet<String>,
}

impl TrustedProxies {
    pub fn parse(list: &str) -> Self {
        let hosts: HashSet<String> = list.split(',').map(|h| h.trim().to_string()).collect();
        Self {
            all: hosts.contains("*"),
            hosts,
        }
    }

    pub fn client(&self, peer: Option<SocketAddr>, headers: &HeaderMap) -> Client {
        let mut c = Client {
            host: peer.map_or_else(|| "unknown".into(), |p| p.ip().to_string()),
            port: peer.map_or(0, |p| p.port()),
            scheme: "http".into(),
        };
        if peer.is_none() || !(self.all || self.hosts.contains(&c.host)) {
            return c;
        }
        let text = |name| {
            headers
                .get(name)
                .map(|v: &HeaderValue| String::from_utf8_lossy(v.as_bytes()).into_owned())
        };
        if let Some(proto) = text("x-forwarded-proto") {
            c.scheme = proto.trim().to_string();
        }
        if let Some(xff) = text("x-forwarded-for") {
            let hosts: Vec<&str> = xff.split(',').map(str::trim).collect();
            let host = if self.all {
                hosts.first().copied()
            } else {
                hosts
                    .iter()
                    .rev()
                    .copied()
                    .find(|h| !self.hosts.contains(*h))
            };
            c.host = host.unwrap_or("unknown").to_string();
            c.port = 0;
        }
        c
    }
}

/// Outermost layer: the client address and the access log line (uvicorn format).
pub async fn client_and_access_log(
    State(state): State<Arc<AppState>>,
    mut req: Request,
    next: Next,
) -> Response {
    let peer = req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| c.0);
    let client = state.trusted.client(peer, req.headers());
    let line = format!(
        "{}:{} - \"{} {} {:?}\"",
        client.host,
        client.port,
        req.method(),
        req.uri().path_and_query().map_or("/", |p| p.as_str()),
        req.version()
    );
    req.extensions_mut().insert(client);
    let res = next.run(req).await;
    let status = res.status();
    tracing::info!(target: "access", "{line} {} {}", status.as_u16(), status.canonical_reason().unwrap_or(""));
    res
}

// -- route metrics -------------------------------------------------------------

#[derive(Default)]
struct Bucket {
    count: u64,
    error_count: u64,
    total_latency_ms: f64,
    max_latency_ms: f64,
    last_status_code: u16,
    samples: VecDeque<f64>,
}

/// Request counts and latency for each `METHOD /path` (`_record_request_metric`).
pub struct Metrics {
    samples_max: usize,
    routes: Mutex<HashMap<String, Bucket>>,
    pub active_requests: AtomicI64,
}

/// `_normalize_metrics_path`: a segment of digits, or of 8 or more hex digits, is `{id}`.
pub fn normalize_metrics_path(path: &str) -> String {
    path.split('/')
        .enumerate()
        .map(|(i, seg)| {
            let digits = !seg.is_empty() && seg.bytes().all(|b| b.is_ascii_digit());
            let hex = seg.len() >= 8 && seg.bytes().all(|b| b.is_ascii_hexdigit());
            if i > 0 && (digits || hex) {
                "{id}"
            } else {
                seg
            }
        })
        .collect::<Vec<_>>()
        .join("/")
}

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

impl Metrics {
    pub fn new(samples_max: usize) -> Self {
        Self {
            samples_max,
            routes: Mutex::default(),
            active_requests: AtomicI64::new(0),
        }
    }

    pub fn record(&self, route_key: &str, latency_ms: f64, status: u16) {
        let mut routes = self.routes.lock().unwrap();
        let b = routes.entry(route_key.to_string()).or_default();
        b.count += 1;
        if status >= 400 {
            b.error_count += 1;
        }
        b.total_latency_ms += latency_ms;
        b.max_latency_ms = b.max_latency_ms.max(latency_ms);
        b.last_status_code = status;
        if b.samples.len() == self.samples_max {
            b.samples.pop_front();
        }
        b.samples.push_back(latency_ms);
    }

    /// The `requests` and `routes` parts of the `GET /metrics` body.
    pub fn snapshot(&self) -> Value {
        let routes = self.routes.lock().unwrap();
        let (mut total, mut errors) = (0u64, 0u64);
        let mut out = serde_json::Map::new();
        for (key, b) in routes.iter() {
            total += b.count;
            errors += b.error_count;
            let mut s: Vec<f64> = b.samples.iter().copied().collect();
            s.sort_by(f64::total_cmp);
            let p95 = if s.is_empty() {
                0.0
            } else {
                s[((0.95 * s.len() as f64).ceil() as usize).max(1) - 1]
            };
            let pct = |e: u64, c: u64| {
                if c == 0 {
                    0.0
                } else {
                    round2(e as f64 / c as f64 * 100.0)
                }
            };
            out.insert(
                key.clone(),
                json!({
                    "count": b.count,
                    "error_count": b.error_count,
                    "error_rate_pct": pct(b.error_count, b.count),
                    "avg_latency_ms": if b.count == 0 { 0.0 } else { round2(b.total_latency_ms / b.count as f64) },
                    "p95_latency_ms": round2(p95),
                    "max_latency_ms": round2(b.max_latency_ms),
                    "last_status_code": b.last_status_code,
                }),
            );
        }
        let rate = if total == 0 {
            0.0
        } else {
            round2(errors as f64 / total as f64 * 100.0)
        };
        json!({
            "requests": {
                "total_count": total,
                "error_count": errors,
                "error_rate_pct": rate,
                "active_http_requests": self.active_requests.load(Ordering::Relaxed).max(0),
            },
            "routes": Value::Object(out),
        })
    }
}

/// The metrics layer is outside CORS, as in the Python server, so it counts preflights and 404s.
pub async fn record_metrics(
    State(state): State<Arc<AppState>>,
    req: Request,
    next: Next,
) -> Response {
    let key = format!(
        "{} {}",
        req.method(),
        normalize_metrics_path(req.uri().path())
    );
    let start = Instant::now();
    state
        .metrics
        .active_requests
        .fetch_add(1, Ordering::Relaxed);
    let res = next.run(req).await;
    state
        .metrics
        .active_requests
        .fetch_sub(1, Ordering::Relaxed);
    state.metrics.record(
        &key,
        start.elapsed().as_secs_f64() * 1000.0,
        res.status().as_u16(),
    );
    res
}

// -- CORS (Starlette CORSMiddleware with the Python settings) ------------------

const CORS_ORIGINS: [&str; 4] = [
    "http://localhost:8900",
    "http://127.0.0.1:8900",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
];
const CORS_METHODS: [&str; 7] = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"];

pub async fn cors(req: Request, next: Next) -> Response {
    let Some(origin) = req.headers().get(header::ORIGIN).cloned() else {
        return next.run(req).await;
    };
    let allowed = CORS_ORIGINS
        .iter()
        .any(|o| origin.as_bytes() == o.as_bytes());
    if req.method() == Method::OPTIONS
        && let Some(method) = req.headers().get(header::ACCESS_CONTROL_REQUEST_METHOD)
    {
        {
            let mut failures = Vec::new();
            let mut res_headers = HeaderMap::new();
            res_headers.insert(header::VARY, HeaderValue::from_static("Origin"));
            res_headers.insert(
                header::ACCESS_CONTROL_ALLOW_METHODS,
                HeaderValue::from_static("DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"),
            );
            res_headers.insert(
                header::ACCESS_CONTROL_MAX_AGE,
                HeaderValue::from_static("600"),
            );
            if allowed {
                res_headers.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin);
            } else {
                failures.push("origin");
            }
            if !CORS_METHODS
                .iter()
                .any(|m| method.as_bytes() == m.as_bytes())
            {
                failures.push("method");
            }
            if let Some(h) = req.headers().get(header::ACCESS_CONTROL_REQUEST_HEADERS) {
                res_headers.insert(header::ACCESS_CONTROL_ALLOW_HEADERS, h.clone());
            }
            let (status, body) = if failures.is_empty() {
                (StatusCode::OK, "OK".to_string())
            } else {
                (
                    StatusCode::BAD_REQUEST,
                    format!("Disallowed CORS {}", failures.join(", ")),
                )
            };
            return (status, res_headers, text(body)).into_response();
        }
    }
    let mut res = next.run(req).await;
    if allowed {
        let h = res.headers_mut();
        h.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin);
        h.append(header::VARY, HeaderValue::from_static("Origin"));
    }
    res
}

fn text(body: String) -> impl IntoResponse {
    ([(header::CONTENT_TYPE, "text/plain; charset=utf-8")], body)
}

/// Starlette's reply to an error that no handler catches.
pub fn internal_error() -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        text("Internal Server Error".into()),
    )
        .into_response()
}

pub fn panic_reply(_: Box<dyn std::any::Any + Send + 'static>) -> Response {
    internal_error()
}

// -- 404, trailing-slash redirect, 405 ------------------------------------------

/// No route has this path. If the path with or without the trailing slash has
/// a route, Starlette redirects there with 307.
pub async fn not_found(req: Request) -> Response {
    let path = req.uri().path();
    if path != "/" {
        let other = match path.strip_suffix('/') {
            Some(p) => p.to_string(),
            None => format!("{path}/"),
        };
        if ROUTES
            .iter()
            .any(|(_, pattern)| path_matches(pattern, &other))
        {
            let scheme = req
                .extensions()
                .get::<Client>()
                .map_or("http", |c| c.scheme.as_str());
            let host = req
                .headers()
                .get(header::HOST)
                .and_then(|h| h.to_str().ok())
                .unwrap_or("localhost");
            let query = req
                .uri()
                .query()
                .map(|q| format!("?{q}"))
                .unwrap_or_default();
            let location = format!("{scheme}://{host}{other}{query}");
            return (
                StatusCode::TEMPORARY_REDIRECT,
                [(header::LOCATION, location)],
            )
                .into_response();
        }
    }
    ApiError::new(StatusCode::NOT_FOUND, "Not Found").into_response()
}

pub async fn method_not_allowed() -> Response {
    ApiError::new(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed").into_response()
}

/// A Python route that this server does not have yet.
pub async fn not_ported() -> Response {
    ApiError::new(
        StatusCode::NOT_IMPLEMENTED,
        "Not implemented in the Rust server yet",
    )
    .into_response()
}

/// Matches a path to a route pattern: `{x}` is one segment that is not empty,
/// `{*x}` is the rest of the path.
pub fn path_matches(pattern: &str, path: &str) -> bool {
    let mut pat = pattern.split('/');
    let mut segs = path.split('/');
    loop {
        match (pat.next(), segs.next()) {
            (None, None) => return true,
            (Some(p), Some(_)) if p.starts_with("{*") => return true,
            (Some(p), Some(s)) if p.starts_with('{') => {
                if s.is_empty() {
                    return false;
                }
            }
            (Some(p), Some(s)) if p == s => {}
            _ => return false,
        }
    }
}

/// Each route of the Python server v5.19.0, in axum path syntax. A route is
/// either served by this server or answers 501 (`not_ported`). A task that
/// ports a route registers its handler and adds the route to `served` in `lib.rs`.
pub const ROUTES: &[(&str, &str)] = &[
    ("GET", "/openapi.json"),
    ("GET", "/ui/static/{*path}"),
    ("GET", "/events/stream"),
    ("GET", "/events/recent"),
    ("POST", "/webhooks"),
    ("GET", "/webhooks"),
    ("DELETE", "/webhooks/{webhook_id}"),
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("GET", "/api/keys/me"),
    ("POST", "/api/keys"),
    ("GET", "/api/keys"),
    ("PATCH", "/api/keys/{key_id}"),
    ("DELETE", "/api/keys/{key_id}"),
    ("GET", "/ui"),
    ("GET", "/stats"),
    ("GET", "/metrics"),
    ("GET", "/usage"),
    ("POST", "/search/feedback"),
    ("GET", "/search/feedback/history"),
    ("DELETE", "/search/feedback/{feedback_id}"),
    ("GET", "/metrics/search-quality"),
    ("GET", "/metrics/extraction-quality"),
    ("GET", "/metrics/quality-summary"),
    ("GET", "/metrics/failures"),
    ("GET", "/metrics/problem-queries"),
    ("GET", "/metrics/stale-memories"),
    ("GET", "/metrics/graph-search"),
    ("GET", "/metrics/temporal-search"),
    ("GET", "/audit"),
    ("GET", "/promotions"),
    ("GET", "/promotions/{candidate_id}"),
    ("POST", "/promotions/{candidate_id}/approve"),
    ("POST", "/promotions/{candidate_id}/reject"),
    ("POST", "/audit/purge"),
    ("POST", "/maintenance/embedder/reload"),
    ("POST", "/maintenance/reembed"),
    ("POST", "/maintenance/compact"),
    ("POST", "/maintenance/consolidate"),
    ("POST", "/maintenance/prune"),
    ("POST", "/maintenance/enforce-policies"),
    ("POST", "/search"),
    ("POST", "/search/explain"),
    ("POST", "/search/evidence"),
    ("POST", "/search/batch"),
    ("POST", "/memory/add"),
    ("POST", "/memory/add-batch"),
    ("POST", "/memory/{memory_id}/supersede"),
    ("GET", "/memory/conflicts"),
    ("POST", "/memory/conflicts/resolve"),
    ("DELETE", "/memory/{memory_id}"),
    ("GET", "/memory/{memory_id}"),
    ("POST", "/memory/get-batch"),
    ("POST", "/memory/delete-by-source"),
    ("DELETE", "/memories"),
    ("POST", "/memory/delete-batch"),
    ("POST", "/memory/delete-by-prefix"),
    ("GET", "/snapshots"),
    ("POST", "/snapshots"),
    ("POST", "/snapshots/{name}/restore"),
    ("PATCH", "/memory/{memory_id}"),
    ("POST", "/memory/archive-batch"),
    ("POST", "/memory/upsert"),
    ("POST", "/memory/upsert-batch"),
    ("POST", "/memory/is-novel"),
    ("GET", "/memories/count"),
    ("GET", "/memories"),
    ("GET", "/folders"),
    ("POST", "/folders/rename"),
    ("POST", "/index/build"),
    ("POST", "/memory/deduplicate"),
    ("GET", "/backups"),
    ("POST", "/backup"),
    ("POST", "/restore"),
    ("GET", "/sync/status"),
    ("POST", "/sync/upload"),
    ("POST", "/sync/download"),
    ("GET", "/sync/snapshots"),
    ("POST", "/sync/restore/{backup_name}"),
    ("GET", "/export"),
    ("POST", "/import"),
    ("POST", "/memory/extract"),
    ("GET", "/memory/extract/{job_id}"),
    ("POST", "/memory/extract/commit"),
    ("POST", "/memory/supersede"),
    ("POST", "/memory/merge"),
    ("POST", "/memory/{memory_id}/link"),
    ("GET", "/memory/{memory_id}/links"),
    ("DELETE", "/memory/{memory_id}/link/{target_id}"),
    ("GET", "/extract/status"),
    ("GET", "/extraction/profiles"),
    ("GET", "/extraction/profiles/{*prefix}"),
    ("PUT", "/extraction/profiles/{*prefix}"),
    ("DELETE", "/extraction/profiles/{*prefix}"),
    ("POST", "/memory/missed"),
];

/// Routes with no key check, as in `verify_api_key` and FastAPI's own routes.
pub fn is_public(path: &str) -> bool {
    matches!(path, "/health" | "/health/ready" | "/ui" | "/openapi.json")
        || path.starts_with("/ui/")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn metrics_path() {
        assert_eq!(
            normalize_metrics_path("/memory/123/links"),
            "/memory/{id}/links"
        );
        assert_eq!(
            normalize_metrics_path("/memory/extract/0123456789abcdefABCDEF0123456789"),
            "/memory/extract/{id}"
        );
        assert_eq!(normalize_metrics_path("/api/keys/me"), "/api/keys/me");
        assert_eq!(
            normalize_metrics_path("/promotions/abc1234"),
            "/promotions/abc1234"
        );
        assert_eq!(normalize_metrics_path("/"), "/");
    }

    #[test]
    fn metrics_buckets() {
        let m = Metrics::new(2);
        m.record("GET /x", 10.0, 200);
        m.record("GET /x", 30.0, 404);
        m.record("GET /x", 20.0, 200);
        let s = m.snapshot();
        let r = &s["routes"]["GET /x"];
        assert_eq!(r["count"], 3);
        assert_eq!(r["error_count"], 1);
        assert_eq!(r["error_rate_pct"], 33.33);
        assert_eq!(r["avg_latency_ms"], 20.0);
        assert_eq!(r["p95_latency_ms"], 30.0); // samples keep the last 2: 30, 20
        assert_eq!(r["max_latency_ms"], 30.0);
        assert_eq!(r["last_status_code"], 200);
        assert_eq!(s["requests"]["total_count"], 3);
    }

    #[test]
    fn patterns() {
        assert!(path_matches("/memory/{memory_id}", "/memory/12"));
        assert!(!path_matches("/memory/{memory_id}", "/memory/"));
        assert!(!path_matches("/memory/{memory_id}", "/memory/12/links"));
        assert!(path_matches("/ui/static/{*path}", "/ui/static/a/b.js"));
        assert!(!path_matches("/ui/static/{*path}", "/ui/static"));
        assert!(path_matches("/health", "/health"));
        assert!(!path_matches("/health", "/health/"));
    }

    #[test]
    fn proxy_headers() {
        let mut h = HeaderMap::new();
        h.insert(
            "x-forwarded-for",
            HeaderValue::from_static("1.1.1.1, 127.0.0.1"),
        );
        h.insert("x-forwarded-proto", HeaderValue::from_static("https"));
        let local: SocketAddr = "127.0.0.1:5000".parse().unwrap();
        let remote: SocketAddr = "10.0.0.2:5000".parse().unwrap();
        let t = TrustedProxies::parse("127.0.0.1");
        let c = t.client(Some(local), &h);
        assert_eq!(
            (c.host.as_str(), c.port, c.scheme.as_str()),
            ("1.1.1.1", 0, "https")
        );
        let c = t.client(Some(remote), &h);
        assert_eq!(
            (c.host.as_str(), c.port, c.scheme.as_str()),
            ("10.0.0.2", 5000, "http")
        );
        let c = TrustedProxies::parse("*").client(Some(remote), &h);
        assert_eq!(c.host, "1.1.1.1");
        assert_eq!(t.client(None, &h).host, "unknown");
    }
}
