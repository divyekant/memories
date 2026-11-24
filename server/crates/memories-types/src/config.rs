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
    ("ANTHROPIC_API_KEY", Supported),
    ("ANTHROPIC_AUTH", Supported),
    ("ANTHROPIC_OAUTH_TOKEN", Supported),
    ("ANTHROPIC_WORKSPACE_ID", Supported),
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
    ("DOCTRINE_IDENTICAL_THRESHOLD", Supported),
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
    ("EMBED_DIMENSION", Supported),
    ("EMBED_DOC_PREFIX", Supported),
    ("EMBED_MODEL", Supported),
    ("EMBED_PROVIDER", Supported),
    ("EMBED_QUERY_PREFIX", Supported),
    ("EXTRACT_FALLBACK_ADD", Supported),
    ("EXTRACT_FALLBACK_MAX_FACTS", Supported),
    ("EXTRACT_FALLBACK_MAX_FACT_CHARS", Supported),
    ("EXTRACT_FALLBACK_MIN_FACT_CHARS", Supported),
    ("EXTRACT_FALLBACK_NOVELTY_THRESHOLD", Supported),
    ("EXTRACT_JOBS_MAX", Supported),
    ("EXTRACT_JOB_RETENTION_SEC", Supported),
    ("EXTRACT_MAX_FACTS", NotUsed),
    ("EXTRACT_MAX_FACT_CHARS", Supported),
    ("EXTRACT_MAX_INFLIGHT", Supported),
    ("EXTRACT_MAX_LINKS", Supported),
    ("EXTRACT_MIN_LINK_SCORE", Supported),
    ("EXTRACT_MODEL", Supported),
    ("EXTRACT_NOVELTY_GATE", Supported),
    ("EXTRACT_NOVELTY_THRESHOLD", Supported),
    ("EXTRACT_PROVIDER", Supported),
    ("EXTRACT_QUEUE_MAX", Supported),
    ("EXTRACT_REDACT_SECRETS", Supported),
    ("EXTRACT_SIMILAR_PER_FACT", Supported),
    ("EXTRACT_SIMILAR_TEXT_CHARS", Supported),
    ("EXTRACT_TRAINING_DATA_DIR", NotUsed),
    ("FORWARDED_ALLOW_IPS", Supported),
    ("MAINTENANCE_CONFLICT_DRAIN", Supported),
    ("MAINTENANCE_CONFLICT_MAX", Supported),
    ("MAINTENANCE_ENABLED", Supported),
    ("MAX_BACKUPS", Supported),
    ("MAX_EXTRACT_MESSAGE_CHARS", Supported),
    ("MEMORIES_PERSIST_DELAY_SEC", NotUsed),
    ("MEMORY_TRIM_COOLDOWN_SEC", NotUsed),
    ("MEMORY_TRIM_ENABLED", NotUsed),
    ("MEMORY_TRIM_PERIODIC_SEC", NotUsed),
    ("METRICS_LATENCY_SAMPLES", Supported),
    ("METRICS_TREND_SAMPLES", Supported),
    ("MODEL_CACHE_DIR", Supported),
    ("MODEL_NAME", Supported),
    ("OLLAMA_URL", Supported),
    ("OMLX_API_KEY", Supported),
    ("OMLX_URL", Supported),
    ("OPENAI_API_KEY", Supported),
    ("PARITY_CAPTURE_DIR", NotUsed),
    ("PARITY_CAPTURE_MAX_MB", NotUsed),
    ("PORT", Supported),
    ("PRELOADED_MODEL_CACHE_DIR", Supported),
    ("PROJECT_PROMOTION_AUDIT_FLOOR", NotUsed),
    ("PROJECT_PROMOTION_AUDIT_PERIOD_DAYS", NotUsed),
    ("PROJECT_PROMOTION_CLASSIFIER_VERSION", NotUsed),
    ("PROJECT_PROMOTION_MODE", Supported),
    ("PROJECT_PROMOTION_NEAR_DUPLICATE_THRESHOLD", NotUsed),
    ("PROJECT_PROMOTION_RECONCILE_BATCH", NotUsed),
    ("PROJECT_PROMOTION_RECONCILE_BUDGET_SECONDS", NotUsed),
    ("PROJECT_PROMOTION_REJECTED_RETENTION_DAYS", Supported),
    ("PROJECT_PROMOTION_RELEVANCE_THRESHOLD", NotUsed),
    ("PROJECT_PROMOTION_REVIEWER_VERSION", NotUsed),
    ("PROJECT_PROMOTION_REVIEW_MODEL", NotUsed),
    ("PROJECT_PROMOTION_REVIEW_PROVIDER", NotUsed),
    ("PROJECT_PROMOTION_UNREVIEWABLE_BACKLOG_AGE_HOURS", NotUsed),
    ("PROJECT_PROMOTION_UNREVIEWABLE_RATE_COUNT", NotUsed),
    ("PROJECT_PROMOTION_UNREVIEWABLE_RATE_WINDOW_HOURS", NotUsed),
    ("QDRANT_API_KEY", Supported),
    ("QDRANT_COLLECTION", Supported),
    ("QDRANT_READ_CONSISTENCY", Supported),
    ("QDRANT_REPLICATION_FACTOR", Supported),
    ("QDRANT_URL", Supported),
    ("QDRANT_WAIT", Supported),
    ("QDRANT_WRITE_CONSISTENCY_FACTOR", Supported),
    ("QDRANT_WRITE_ORDERING", Supported),
    ("SEARCH_GRAPH_DECAY", NotUsed),
    ("SEARCH_GRAPH_MAX_NEIGHBORS", Supported),
    ("SEARCH_GRAPH_RESERVED_SLOTS", Supported),
    ("SEARCH_GRAPH_SEED_K", NotUsed),
    ("SEARCH_PPR_ALPHA", Supported),
    ("SEARCH_PPR_MAX_ITERS", Supported),
    ("SEARCH_PPR_MIN_RELATIVE", Supported),
    ("SHADOW_LOG_DIR", NotUsed),
    ("SHADOW_OLLAMA_URL", NotUsed),
    ("SHADOW_OMLX_API_KEY", NotUsed),
    ("SHADOW_OMLX_URL", NotUsed),
    ("SHADOW_PROVIDERS", NotUsed),
    ("STORAGE_BACKEND", NotUsed),
    ("USAGE_TRACKING", Supported),
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
            matches!(
                n,
                "CLOUD_SYNC_BUCKET" | "API_KEY" | "MAINTENANCE_ENABLED" | "UNKNOWN"
            )
            .then(String::new)
        });
        assert_eq!(got, vec!["CLOUD_SYNC_BUCKET"]);
    }
}
