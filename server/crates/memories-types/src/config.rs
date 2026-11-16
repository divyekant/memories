//! Environment variables: the same names and defaults as the Python server.
//!
//! Each variable that the Python server reads is in [`VARS`] with one state:
//! supported, or accepted but not used. A value that the Python server cannot
//! start with is refused by the code that reads it (see [`int_strict`]).

/// The state of one environment variable in this server version.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Use {
    Supported,
    /// The server starts, and logs one line if the variable is set.
    NotUsed,
}

use Use::{NotUsed, Supported};

/// Each environment variable of the Python server v5.19.0 (all modules that
/// the server imports), and `FORWARDED_ALLOW_IPS` of uvicorn. A task that
/// ports a feature changes the state of its variables to `Supported`.
pub const VARS: &[(&str, Use)] = &[
    ("ANTHROPIC_API_KEY", NotUsed),
    ("ANTHROPIC_AUTH", NotUsed),
    ("ANTHROPIC_OAUTH_TOKEN", NotUsed),
    ("ANTHROPIC_WORKSPACE_ID", NotUsed),
    ("API_KEY", Supported),
    ("AUDIT_LOG", Supported),
    ("AUTH_RATE_LIMIT", Supported),
    ("CHATGPT_CLIENT_ID", NotUsed),
    ("CHATGPT_REFRESH_TOKEN", NotUsed),
    ("CLOUD_SYNC_ACCESS_KEY", NotUsed),
    ("CLOUD_SYNC_BUCKET", NotUsed),
    ("CLOUD_SYNC_ENABLED", NotUsed),
    ("CLOUD_SYNC_ENDPOINT", NotUsed),
    ("CLOUD_SYNC_PREFIX", NotUsed),
    ("CLOUD_SYNC_REGION", NotUsed),
    ("CLOUD_SYNC_SECRET_KEY", NotUsed),
    ("DATA_DIR", Supported),
    ("DOCTRINE_IDENTICAL_THRESHOLD", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_CHECK_SEC", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_ENABLED", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_HIGH_STREAK", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD", NotUsed),
    ("EMBEDDER_AUTO_RELOAD_WINDOW_SEC", NotUsed),
    ("EMBED_ALLOW_SPACE_REBIND", NotUsed),
    ("EMBED_API_KEY", NotUsed),
    ("EMBED_BASE_URL", NotUsed),
    ("EMBED_COLLECTION", NotUsed),
    ("EMBED_DIMENSION", NotUsed),
    ("EMBED_DOC_PREFIX", NotUsed),
    ("EMBED_MODEL", NotUsed),
    ("EMBED_PROVIDER", NotUsed),
    ("EMBED_QUERY_PREFIX", NotUsed),
    ("EXTRACT_FALLBACK_ADD", NotUsed),
    ("EXTRACT_FALLBACK_MAX_FACTS", NotUsed),
    ("EXTRACT_FALLBACK_MAX_FACT_CHARS", NotUsed),
    ("EXTRACT_FALLBACK_MIN_FACT_CHARS", NotUsed),
    ("EXTRACT_FALLBACK_NOVELTY_THRESHOLD", NotUsed),
    ("EXTRACT_JOBS_MAX", NotUsed),
    ("EXTRACT_JOB_RETENTION_SEC", NotUsed),
    ("EXTRACT_MAX_FACTS", NotUsed),
    ("EXTRACT_MAX_FACT_CHARS", NotUsed),
    ("EXTRACT_MAX_INFLIGHT", NotUsed),
    ("EXTRACT_MAX_LINKS", NotUsed),
    ("EXTRACT_MIN_LINK_SCORE", NotUsed),
    ("EXTRACT_MODEL", NotUsed),
    ("EXTRACT_NOVELTY_GATE", NotUsed),
    ("EXTRACT_NOVELTY_THRESHOLD", NotUsed),
    ("EXTRACT_PROVIDER", NotUsed),
    ("EXTRACT_QUEUE_MAX", NotUsed),
    ("EXTRACT_REDACT_SECRETS", NotUsed),
    ("EXTRACT_SIMILAR_PER_FACT", NotUsed),
    ("EXTRACT_SIMILAR_TEXT_CHARS", NotUsed),
    ("EXTRACT_TRAINING_DATA_DIR", NotUsed),
    ("FORWARDED_ALLOW_IPS", Supported),
    ("MAINTENANCE_CONFLICT_DRAIN", NotUsed),
    ("MAINTENANCE_CONFLICT_MAX", NotUsed),
    ("MAINTENANCE_ENABLED", NotUsed),
    ("MAX_BACKUPS", NotUsed),
    ("MAX_EXTRACT_MESSAGE_CHARS", NotUsed),
    ("MEMORIES_PERSIST_DELAY_SEC", NotUsed),
    ("MEMORY_TRIM_COOLDOWN_SEC", NotUsed),
    ("MEMORY_TRIM_ENABLED", NotUsed),
    ("MEMORY_TRIM_PERIODIC_SEC", NotUsed),
    ("METRICS_LATENCY_SAMPLES", Supported),
    ("METRICS_TREND_SAMPLES", NotUsed),
    ("MODEL_CACHE_DIR", NotUsed),
    ("MODEL_NAME", NotUsed),
    ("OLLAMA_URL", NotUsed),
    ("OMLX_API_KEY", NotUsed),
    ("OMLX_URL", NotUsed),
    ("OPENAI_API_KEY", NotUsed),
    ("PARITY_CAPTURE_DIR", NotUsed),
    ("PARITY_CAPTURE_MAX_MB", NotUsed),
    ("PORT", Supported),
    ("PRELOADED_MODEL_CACHE_DIR", NotUsed),
    ("PROJECT_PROMOTION_AUDIT_FLOOR", NotUsed),
    ("PROJECT_PROMOTION_AUDIT_PERIOD_DAYS", NotUsed),
    ("PROJECT_PROMOTION_CLASSIFIER_VERSION", NotUsed),
    ("PROJECT_PROMOTION_MODE", NotUsed),
    ("PROJECT_PROMOTION_NEAR_DUPLICATE_THRESHOLD", NotUsed),
    ("PROJECT_PROMOTION_RECONCILE_BATCH", NotUsed),
    ("PROJECT_PROMOTION_RECONCILE_BUDGET_SECONDS", NotUsed),
    ("PROJECT_PROMOTION_REJECTED_RETENTION_DAYS", NotUsed),
    ("PROJECT_PROMOTION_RELEVANCE_THRESHOLD", NotUsed),
    ("PROJECT_PROMOTION_REVIEWER_VERSION", NotUsed),
    ("PROJECT_PROMOTION_REVIEW_MODEL", NotUsed),
    ("PROJECT_PROMOTION_REVIEW_PROVIDER", NotUsed),
    ("PROJECT_PROMOTION_UNREVIEWABLE_BACKLOG_AGE_HOURS", NotUsed),
    ("PROJECT_PROMOTION_UNREVIEWABLE_RATE_COUNT", NotUsed),
    ("PROJECT_PROMOTION_UNREVIEWABLE_RATE_WINDOW_HOURS", NotUsed),
    ("QDRANT_API_KEY", NotUsed),
    ("QDRANT_COLLECTION", NotUsed),
    ("QDRANT_READ_CONSISTENCY", NotUsed),
    ("QDRANT_REPLICATION_FACTOR", NotUsed),
    ("QDRANT_URL", NotUsed),
    ("QDRANT_WAIT", NotUsed),
    ("QDRANT_WRITE_CONSISTENCY_FACTOR", NotUsed),
    ("QDRANT_WRITE_ORDERING", NotUsed),
    ("SEARCH_GRAPH_DECAY", NotUsed),
    ("SEARCH_GRAPH_MAX_NEIGHBORS", NotUsed),
    ("SEARCH_GRAPH_RESERVED_SLOTS", NotUsed),
    ("SEARCH_GRAPH_SEED_K", NotUsed),
    ("SEARCH_PPR_ALPHA", NotUsed),
    ("SEARCH_PPR_MAX_ITERS", NotUsed),
    ("SEARCH_PPR_MIN_RELATIVE", NotUsed),
    ("SHADOW_LOG_DIR", NotUsed),
    ("SHADOW_OLLAMA_URL", NotUsed),
    ("SHADOW_OMLX_API_KEY", NotUsed),
    ("SHADOW_OMLX_URL", NotUsed),
    ("SHADOW_PROVIDERS", NotUsed),
    ("STORAGE_BACKEND", NotUsed),
    ("USAGE_TRACKING", NotUsed),
    ("WORKSPACE_DIR", NotUsed),
];

/// The names in [`VARS`] with state `NotUsed` that are set in the environment.
pub fn set_but_not_used(get: impl Fn(&str) -> Option<String>) -> Vec<&'static str> {
    VARS.iter()
        .filter(|(name, u)| *u == NotUsed && get(name).is_some())
        .map(|(name, _)| *name)
        .collect()
}

/// The value as the Python server reads it with `os.getenv(name, default)`.
pub fn string(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

/// Python `_env_bool`: not set gives the default; else true only for
/// 1, true, yes, y, on (an empty value is false).
pub fn parse_bool(raw: Option<&str>, default: bool) -> bool {
    match raw {
        None => default,
        Some(v) => matches!(
            v.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "y" | "on"
        ),
    }
}

/// Python `_env_int`: empty or invalid gives the default; the result is at least `minimum`.
/// The error is the warning text for an invalid value.
pub fn parse_int(raw: Option<&str>, default: i64, minimum: i64) -> (i64, Option<String>) {
    let raw = raw.unwrap_or("").trim();
    if raw.is_empty() {
        return (default.max(minimum), None);
    }
    match raw.parse::<i64>() {
        Ok(v) => (v.max(minimum), None),
        Err(_) => (
            default.max(minimum),
            Some(format!("invalid value {raw:?}; using default {default}")),
        ),
    }
}

/// Python `int(os.getenv(name, default))`: the Python server does not start with
/// an invalid value, so this server refuses it too.
pub fn int_strict(name: &str, raw: Option<&str>, default: i64) -> Result<i64, String> {
    match raw {
        None => Ok(default),
        Some(v) => v.trim().parse::<i64>().map_err(|_| {
            format!("{name}={v:?} is not an integer. Set {name} to an integer or remove it.")
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_unique_and_sorted() {
        for w in VARS.windows(2) {
            assert!(w[0].0 < w[1].0, "{} then {}", w[0].0, w[1].0);
        }
    }

    #[test]
    fn bool_follows_python() {
        assert!(parse_bool(None, true));
        assert!(!parse_bool(Some(""), true));
        for v in ["1", "true", " YES ", "y", "On"] {
            assert!(parse_bool(Some(v), false), "{v}");
        }
        assert!(!parse_bool(Some("false"), true));
        assert!(!parse_bool(Some("2"), true));
    }

    #[test]
    fn int_follows_python() {
        assert_eq!(parse_int(None, 200, 20), (200, None));
        assert_eq!(parse_int(Some(" "), 5, 20), (20, None));
        assert_eq!(parse_int(Some("7"), 200, 20), (20, None));
        assert_eq!(parse_int(Some(" 300 "), 200, 20), (300, None));
        let (v, warn) = parse_int(Some("abc"), 200, 20);
        assert_eq!(v, 200);
        assert!(warn.unwrap().contains("abc"));
    }

    #[test]
    fn int_strict_refuses_invalid() {
        assert_eq!(int_strict("X", None, 50), Ok(50));
        assert_eq!(int_strict("X", Some(" 7 "), 50), Ok(7));
        assert!(int_strict("X", Some(""), 50).unwrap_err().contains("X"));
        assert!(int_strict("X", Some("ten"), 50).is_err());
    }

    #[test]
    fn only_set_unused_vars_are_reported() {
        let got = set_but_not_used(|n| {
            matches!(n, "QDRANT_URL" | "API_KEY" | "UNKNOWN").then(String::new)
        });
        assert_eq!(got, vec!["QDRANT_URL"]);
    }
}
