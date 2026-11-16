//! `GET /events/stream`, `GET /events/recent`, the three `/webhooks` routes,
//! the webhook calls, and the web UI files (`GET /ui`, `GET /ui/static/*`).

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use std::path::{Component, Path as FsPath, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::{
    Extension, Json,
    body::Body,
    extract::{Path, Query, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use memories_engine::events::{EVENT_TYPES, Event};
use memories_engine::rank::PyDateTime;
use memories_store::Webhook;
use serde_json::{Value, json};
use tokio::sync::{Semaphore, broadcast::error::RecvError};

use crate::{
    AppState,
    admin::{Body as Fields, last, query_int},
    auth::AuthContext,
    error::{ApiError, JsonBody},
    shell::internal_error,
};

type Q = Query<Vec<(String, String)>>;

const KEEPALIVE: Duration = Duration::from_secs(30);
/// Python sends each webhook call once. This server tries a call up to
/// 3 times when the call fails or the receiver answers 5xx.
const WEBHOOK_TRIES: u32 = 3;
const WEBHOOK_TIMEOUT: Duration = Duration::from_secs(5);
const WEBHOOK_RETRY_DELAY: Duration = Duration::from_secs(1);
/// Webhook calls in progress at one time. More events wait, and the bus
/// drops the oldest events when 256 wait (a warning in the log).
const WEBHOOK_CALLS: usize = 32;

/// `_event_visible_to`: a scoped key sees the events of its sources, and
/// the events with no source.
fn visible(auth: &AuthContext, data: &Value) -> bool {
    if auth.prefixes.is_none() {
        return true;
    }
    match data.get("source") {
        Some(Value::String(s)) if !s.is_empty() => auth.can_read(s),
        _ => true,
    }
}

pub async fn recent(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Result<Json<Value>, ApiError> {
    let mut errors = Vec::new();
    let limit = query_int(&q, "limit", 50, i64::MIN, None, &mut errors);
    if !errors.is_empty() {
        return Err(ApiError::validation(errors));
    }
    let all = state.events.recent(usize::MAX);
    // Python `events[-limit:]`: 0 keeps all, a negative limit drops the first events.
    let skip = match limit {
        0 => 0,
        n if n > 0 => all.len().saturating_sub(n as usize),
        n => usize::try_from(n.unsigned_abs()).unwrap_or(usize::MAX),
    };
    let events: Vec<Value> = all
        .iter()
        .skip(skip)
        .filter(|e| visible(&auth, &e.data))
        .map(Event::to_json)
        .collect();
    Ok(Json(json!({"count": events.len(), "events": events})))
}

pub async fn stream(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Q,
) -> Response {
    // Python `if event_type and ...`: an empty value is no filter.
    let event_type = last(&q, "event_type")
        .filter(|t| !t.is_empty())
        .map(str::to_owned);
    let rx = state.events.subscribe();
    let stop = state.shutdown.subscribe();
    let body = futures_util::stream::unfold((rx, stop, true), move |(mut rx, mut stop, first)| {
        let (auth, event_type) = (auth.clone(), event_type.clone());
        async move {
            if first {
                return Some((
                    Ok::<_, std::convert::Infallible>("retry: 5000\n\n".to_string()),
                    (rx, stop, false),
                ));
            }
            loop {
                let next = tokio::select! {
                    // A stream that stays open would hold the graceful shutdown.
                    _ = stop.wait_for(|s| *s) => return None,
                    r = tokio::time::timeout(KEEPALIVE, rx.recv()) => r,
                };
                match next {
                    Err(_) => return Some((Ok(": keepalive\n\n".to_string()), (rx, stop, false))),
                    Ok(Ok(e)) => {
                        if event_type.as_deref().is_some_and(|t| t != e.event_type)
                            || !visible(&auth, &e.data)
                        {
                            continue;
                        }
                        return Some((Ok(e.to_sse()), (rx, stop, false)));
                    }
                    // A slow reader loses the oldest events, as a full Python queue.
                    Ok(Err(RecvError::Lagged(_))) => continue,
                    Ok(Err(RecvError::Closed)) => return None,
                }
            }
        }
    });
    (
        [(header::CONTENT_TYPE, "text/event-stream; charset=utf-8")],
        Body::from_stream(body),
    )
        .into_response()
}

/// Runs a blocking `state.db` call off the async threads. A failure is HTTP 500.
async fn blocking<T: Send + 'static>(
    f: impl FnOnce() -> Result<T, memories_store::StateError> + Send + 'static,
) -> Result<T, Response> {
    match tokio::task::spawn_blocking(f).await {
        Ok(Ok(v)) => Ok(v),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "state.db: webhook operation failed");
            Err(internal_error())
        }
        Err(e) => {
            tracing::error!(error = %e, "state.db: webhook task failed");
            Err(internal_error())
        }
    }
}

pub async fn create_webhook(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    JsonBody(map): JsonBody,
) -> Result<Json<Value>, Response> {
    let mut f = Fields::new(&map);
    let url = f.string_checked("url", true, false, None);
    let events = f.string_list("events", true);
    f.finish().map_err(IntoResponse::into_response)?;
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let url = url.expect("checked");
    // Python `events or list(EVENT_TYPES)`.
    let events = events
        .filter(|e| !e.is_empty())
        .unwrap_or_else(|| EVENT_TYPES.iter().map(|t| t.to_string()).collect());
    let st = state.clone();
    let created_at = PyDateTime::now_utc().isoformat_utc();
    let wh = blocking(move || st.webhooks.create(&url, &events, &created_at)).await?;
    tracing::info!("Webhook registered: {} -> {}", wh.id, wh.url);
    Ok(Json(wh.to_json()))
}

pub async fn list_webhooks(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Result<Json<Value>, Response> {
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let st = state.clone();
    let list = blocking(move || st.webhooks.list()).await?;
    let list: Vec<Value> = list.iter().map(Webhook::to_json).collect();
    Ok(Json(json!({"webhooks": list})))
}

pub async fn delete_webhook(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Path(id): Path<String>,
) -> Result<Json<Value>, Response> {
    auth.require_admin().map_err(IntoResponse::into_response)?;
    let st = state.clone();
    let deleted = blocking(move || st.webhooks.delete(&id)).await?;
    Ok(Json(json!({"deleted": deleted})))
}

/// Sends each event to the webhooks of its type, until the bus closes.
pub fn spawn_webhooks(state: Arc<AppState>) -> tokio::task::JoinHandle<()> {
    let mut rx = state.events.subscribe();
    tokio::spawn(async move {
        // As httpx: no redirect.
        let client = match reqwest::Client::builder()
            .timeout(WEBHOOK_TIMEOUT)
            .redirect(reqwest::redirect::Policy::none())
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                tracing::error!(error = %e, "cannot make the webhook HTTP client; webhooks are off");
                return;
            }
        };
        let calls = Arc::new(Semaphore::new(WEBHOOK_CALLS));
        loop {
            let event = match rx.recv().await {
                Ok(e) => e,
                Err(RecvError::Lagged(n)) => {
                    tracing::warn!("webhooks: {n} events were dropped, the calls are too slow");
                    continue;
                }
                Err(RecvError::Closed) => return,
            };
            let st = state.clone();
            let Ok(hooks) = blocking(move || st.webhooks.list()).await else {
                continue;
            };
            let payload = event.to_json();
            for wh in hooks
                .into_iter()
                .filter(|w| w.events.iter().any(|t| t == event.event_type))
            {
                let Ok(permit) = calls.clone().acquire_owned().await else {
                    return;
                };
                let (client, payload) = (client.clone(), payload.clone());
                tokio::spawn(async move {
                    deliver(&client, &wh, &payload).await;
                    drop(permit);
                });
            }
        }
    })
}

async fn deliver(client: &reqwest::Client, wh: &Webhook, payload: &Value) {
    for attempt in 1..=WEBHOOK_TRIES {
        let error = match client.post(&wh.url).json(payload).send().await {
            Ok(r) if !r.status().is_server_error() => return,
            Ok(r) => format!("HTTP {}", r.status().as_u16()),
            // A bad URL fails each try the same way.
            Err(e) if e.is_builder() => {
                tracing::warn!("Webhook {} failed: {e}", wh.id);
                return;
            }
            Err(e) => e.to_string(),
        };
        tracing::warn!(
            "Webhook {} failed (try {attempt} of {WEBHOOK_TRIES}): {error}",
            wh.id
        );
        if attempt < WEBHOOK_TRIES {
            tokio::time::sleep(WEBHOOK_RETRY_DELAY * attempt).await;
        }
    }
}

// ---- Web UI files ------------------------------------------------------------

/// The Python `mimetypes` type of a file, with the Starlette charset for text.
fn content_type(path: &FsPath) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "svg" => "image/svg+xml",
        "json" | "map" => "application/json",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "ico" => "image/vnd.microsoft.icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        _ => "text/plain; charset=utf-8",
    }
}

async fn file(path: &FsPath) -> Option<Response> {
    let meta = tokio::fs::metadata(path).await.ok()?;
    if !meta.is_file() {
        return None;
    }
    let bytes = tokio::fs::read(path).await.ok()?;
    Some(([(header::CONTENT_TYPE, content_type(path))], bytes).into_response())
}

pub async fn ui_page(State(state): State<Arc<AppState>>) -> Response {
    match file(&state.ui_dir.join("index.html")).await {
        Some(r) => r,
        None => ApiError::new(StatusCode::NOT_FOUND, "UI not found").into_response(),
    }
}

/// The file at `rel` inside `dir`, or `None` if `rel` leaves `dir`.
fn inside(dir: &FsPath, rel: &str) -> Option<PathBuf> {
    let rel = FsPath::new(rel);
    if rel.components().any(|c| !matches!(c, Component::Normal(_))) {
        return None;
    }
    // As Starlette: a symbolic link must not lead out of the directory.
    let root = dir.canonicalize().ok()?;
    let full = root.join(rel).canonicalize().ok()?;
    full.starts_with(&root).then_some(full)
}

pub async fn ui_static(State(state): State<Arc<AppState>>, Path(rel): Path<String>) -> Response {
    let found = match inside(&state.ui_dir, &rel) {
        Some(p) => file(&p).await,
        None => None,
    };
    found.unwrap_or_else(|| ApiError::new(StatusCode::NOT_FOUND, "Not Found").into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_path_must_stay_in_the_directory() {
        let dir = std::env::temp_dir().join(format!("memories-ui-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("a")).unwrap();
        std::fs::write(dir.join("a/b.js"), "").unwrap();
        assert!(inside(&dir, "a/b.js").is_some());
        for bad in ["../a/b.js", "a/../a/b.js", "/etc/passwd", "a/../../x"] {
            assert!(inside(&dir, bad).is_none(), "{bad}");
        }
    }

    #[test]
    fn scoped_keys_see_their_sources_and_events_with_no_source() {
        let mut ro = AuthContext::unrestricted();
        ro.role = "read-only".into();
        ro.prefixes = Some(vec!["claude-code/".into()]);
        assert!(visible(&ro, &json!({"source": "claude-code/x"})));
        assert!(!visible(&ro, &json!({"source": "codex/x"})));
        assert!(visible(&ro, &json!({"source": ""})));
        assert!(visible(&ro, &json!({"job_id": "j"})));
        assert!(visible(
            &AuthContext::unrestricted(),
            &json!({"source": "codex/x"})
        ));
    }
}
