//! The project namespace policy of `project_memory.py` (v5.19.0): source
//! shapes, the reserved fields, trusted authorship and the project write check.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

const PROJECT_KINDS: [&str; 4] = ["decisions", "knowledge", "state", "operations"];
const ORIGIN_CLIENTS: [&str; 5] = ["codex", "claude-code", "hook", "manual", "other"];

/// The fields that only the server writes. Caller metadata never sets them.
pub const RESERVED_METADATA_FIELDS: [&str; 5] = [
    "author",
    "contributors",
    "origin_client",
    "source_memory_ids",
    "promotion",
];

/// `ProjectMemoryPolicyError`: the routes answer HTTP 422 with this text.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct PolicyError(pub String);

/// `is_valid_slug`: `^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`.
pub fn is_valid_slug(s: &str) -> bool {
    let ok = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit();
    let b = s.as_bytes();
    !b.is_empty() && ok(b[0]) && ok(b[b.len() - 1]) && b.iter().all(|&c| ok(c) || c == b'-')
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemorySource<'a> {
    /// "project" or "person".
    pub namespace: &'static str,
    pub project_id: &'a str,
    pub kind: &'a str,
    pub principal_id: Option<&'a str>,
}

/// `parse_memory_source`: a strict `project/<id>/<kind>` or
/// `person/<principal>/<id>/<kind>` source, else `None`.
pub fn parse_memory_source(source: &str) -> Option<MemorySource<'_>> {
    let parts: Vec<&str> = source.split('/').collect();
    match parts.as_slice() {
        ["person", principal, project, kind]
            if is_valid_slug(principal)
                && is_valid_slug(project)
                && PROJECT_KINDS.contains(kind) =>
        {
            Some(MemorySource {
                namespace: "person",
                project_id: project,
                kind,
                principal_id: Some(principal),
            })
        }
        ["project", project, kind] if is_valid_slug(project) && PROJECT_KINDS.contains(kind) => {
            Some(MemorySource {
                namespace: "project",
                project_id: project,
                kind,
                principal_id: None,
            })
        }
        _ => None,
    }
}

pub fn is_project_source(source: &str) -> bool {
    parse_memory_source(source).is_some_and(|p| p.namespace == "project")
}

/// Includes malformed `project/` and `person/` sources.
pub fn is_reserved_namespace_source(source: &str) -> bool {
    source.starts_with("project/") || source.starts_with("person/")
}

fn policy_namespace(source: &str) -> Vec<&str> {
    match parse_memory_source(source) {
        Some(p) if p.namespace == "person" => {
            vec!["person", p.principal_id.unwrap_or(""), p.project_id]
        }
        Some(p) => vec!["project", p.project_id],
        None if is_reserved_namespace_source(source) => vec!["reserved", source],
        None => vec!["legacy"],
    }
}

/// `is_namespace_crossing_source_move`. `None` is no source change.
pub fn is_namespace_crossing_source_move(current: &str, new: Option<&str>) -> bool {
    new.is_some_and(|n| n != current && policy_namespace(current) != policy_namespace(n))
}

/// `is_substantive_authored_content_replacement`: a text change, or a source
/// move across an authorship policy boundary.
pub fn is_substantive_replacement(
    current_text: Option<&str>,
    new_text: Option<&str>,
    current_source: &str,
    new_source: Option<&str>,
) -> bool {
    new_text.is_some_and(|t| Some(t) != current_text)
        || is_namespace_crossing_source_move(current_source, new_source)
}

/// `validate_namespace_preserving_replacement`.
pub fn validate_namespace_preserving_replacement(
    existing: &[&str],
    target: &str,
    operation: &str,
) -> Result<(), PolicyError> {
    for &e in existing {
        if e != target && (is_reserved_namespace_source(e) || is_reserved_namespace_source(target))
        {
            return Err(PolicyError(format!(
                "{operation} source cannot cross project or person namespace boundaries"
            )));
        }
    }
    Ok(())
}

/// `normalize_origin_client`: the allowlist value, else "other".
pub fn normalize_origin_client(value: &Value) -> String {
    let v = value
        .as_str()
        .map(|s| s.trim_matches(is_py_space).to_lowercase());
    match v {
        Some(v) if ORIGIN_CLIENTS.contains(&v.as_str()) => v,
        _ => "other".into(),
    }
}

/// `TrustedAuthorship.principal` of a managed key. The API routes give no
/// contributors and no source memory IDs, so `as_metadata` has two fields.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrustedAuthorship {
    pub author: String,
    pub origin_client: String,
    /// `source_memory_ids` of `TrustedAuthorship.system` (consolidation).
    pub source_memory_ids: Vec<i64>,
}

impl TrustedAuthorship {
    /// `None` for an author that is not a slug, as `_trusted_authorship` does.
    pub fn principal(principal_id: &str, origin_client: &str) -> Option<Self> {
        is_valid_slug(principal_id).then(|| Self {
            author: principal_id.into(),
            origin_client: normalize_origin_client(&json!(origin_client)),
            source_memory_ids: Vec::new(),
        })
    }

    /// `TrustedAuthorship.system(source_memory_ids=ids)`: IDs in first-seen order.
    pub fn system(ids: Vec<i64>) -> Self {
        let mut seen = std::collections::HashSet::new();
        Self {
            author: "system".into(),
            origin_client: "other".into(),
            source_memory_ids: ids.into_iter().filter(|i| seen.insert(*i)).collect(),
        }
    }

    pub fn as_metadata(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("author".into(), json!(self.author));
        m.insert("origin_client".into(), json!(self.origin_client));
        if !self.source_memory_ids.is_empty() {
            m.insert("contributors".into(), json!([]));
            m.insert("source_memory_ids".into(), json!(self.source_memory_ids));
        }
        m
    }
}

pub const MSG_PROJECT_SHAPE: &str = "project sources must be project/<project>/<kind>; person sources must be person/<principal>/<project>/<kind>, with a supported kind";
pub const MSG_NEEDS_AUTHOR: &str =
    "project-namespace memories require trusted principal or system authorship";
pub const MSG_CROSSING: &str =
    "namespace-crossing replacements require trusted principal or system authorship";
const MSG_CREDENTIAL: &str = "project-namespace memories cannot contain credential-shaped values";

/// `validate_project_write`.
pub fn validate_project_write(
    text: &str,
    source: &str,
    trusted: Option<&TrustedAuthorship>,
) -> Result<(), PolicyError> {
    if is_reserved_namespace_source(source) && parse_memory_source(source).is_none() {
        return Err(PolicyError(MSG_PROJECT_SHAPE.into()));
    }
    if !is_project_source(source) {
        return Ok(());
    }
    if trusted.is_none() {
        return Err(PolicyError(MSG_NEEDS_AUTHOR.into()));
    }
    if has_secret(text) {
        return Err(PolicyError(MSG_CREDENTIAL.into()));
    }
    Ok(())
}

// ---- Credential detection (`transcript_hygiene.redact_secrets`) ---------------

/// Python `str.isspace()`.
fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

// Python `\s` also matches U+001C to U+001F; `S` puts them in each class.
// The Rust regex crate has no look-behind: a pattern puts the look-behind
// text before capture group 1, and the value is group 1. `secret` is the
// group that the placeholder check reads.
struct Pattern {
    re: Regex,
    secret: usize,
}

fn pattern(re: &str, secret: usize) -> Pattern {
    let re = re.replace("\\s", "[\\s\\x1c-\\x1f]");
    Pattern {
        re: Regex::new(&re).expect("secret pattern"),
        secret,
    }
}

static PATTERNS: LazyLock<Vec<Pattern>> = LazyLock::new(|| {
    vec![
        pattern(r"(\bAKIA[0-9A-Z]{16}\b)", 1),
        pattern(r"(\bgh[pousr]_[A-Za-z0-9]{20,}\b)", 1),
        pattern(r"(\bsk-[A-Za-z0-9_-]{16,}\b)", 1),
        pattern(r"(\bxox[baprs]-[A-Za-z0-9-]{10,}\b)", 1),
        pattern(r"(\b\d{8,10}:[A-Za-z0-9_-]{35}\b)", 1),
        pattern(
            r"(\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b)",
            1,
        ),
        // (?<=://)...(?=@)
        pattern(r"://([^\s/:@]+:[^\s/@]+)@", 1),
        // (?<=bearer )...
        pattern(r"(?i)bearer ([A-Za-z0-9._~+/=-]{16,}\b)", 1),
        // (?<![A-Za-z0-9])(key part)(value)
        pattern(
            r#"(?i)(?:^|[^A-Za-z0-9])(((?:[A-Z0-9_]*?(?:api[_-]?key|x-api-key|access[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret|password|passwd|token|secret))\s*[=:]\s*["']?)([^\s"',;]{8,}))"#,
            3,
        ),
    ]
});

static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(?:<[^>]*>|\[[^\]]*\]|\$\{[^}]*\}|x{3,}|y{3,}|changeme|placeholder|your[_-].*|example.*|redacted.*)$").unwrap()
});
static ENV_NAME: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Z][A-Z0-9]*_[A-Z0-9_]+$").unwrap());
static DOC_CONTEXT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(example|e\.g\.|format|placeholder|demo|localhost|127\.0\.0\.1|0\.0\.0\.0|\.local\b|supabase-demo|<your|your[_-]|template|local dev|dev cred|test user|test cred|local cred)").unwrap()
});
static LOCAL_HOST_AFTER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^@(?:127\.0\.0\.1|localhost|0\.0\.0\.0|[^@\s\x1c-\x1f]*\.local)\b").unwrap()
});

fn is_placeholder(value: &str) -> bool {
    let v = value
        .trim_matches(is_py_space)
        .trim_matches(|c| c == '"' || c == '\'');
    v.is_empty()
        || PLACEHOLDER.is_match(v)
        || ENV_NAME.is_match(v)
        || v.contains(['<', '>', '['])
        || v.contains("${")
        || v.to_uppercase().ends_with("EXAMPLE")
}

/// `_suppressed`, with Python character windows of 30 before and 60 after.
fn suppressed(text: &str, start: usize, end: usize, value: &str) -> bool {
    let before = &text[..start];
    let from = before.char_indices().rev().nth(29).map_or(0, |(i, _)| i);
    let after = &text[end..];
    let to = after.char_indices().nth(60).map_or(after.len(), |(i, _)| i);
    is_placeholder(value)
        || DOC_CONTEXT.is_match(&before[from..])
        || LOCAL_HOST_AFTER.is_match(&after[..to])
}

/// The type names of [`PATTERNS`], in the same order.
const PATTERN_NAMES: [&str; 9] = [
    "aws_access_key",
    "github_token",
    "openai_style_key",
    "slack_token",
    "telegram_bot_token",
    "jwt",
    "url_credentials",
    "bearer_token",
    "key_value_secret",
];

/// `transcript_hygiene.redact_secrets`: each credential becomes
/// `[REDACTED:<type>]`, one pattern after the other on the changed text.
/// Returns the text and the sorted names of the types that it found.
pub fn redact_secrets(text: &str) -> (String, Vec<&'static str>) {
    let mut text = text.to_owned();
    let mut found = std::collections::BTreeSet::new();
    for (name, p) in PATTERN_NAMES.iter().zip(PATTERNS.iter()) {
        let mut out = String::new();
        let mut last = 0;
        for c in p.re.captures_iter(&text) {
            let m = c.get(1).expect("group 1");
            if suppressed(&text, m.start(), m.end(), c.get(p.secret).unwrap().as_str()) {
                continue;
            }
            out.push_str(&text[last..m.start()]);
            if *name == "key_value_secret" {
                out.push_str(c.get(2).unwrap().as_str());
            }
            out.push_str(&format!("[REDACTED:{name}]"));
            last = m.end();
            found.insert(*name);
        }
        if last > 0 {
            out.push_str(&text[last..]);
            text = out;
        }
    }
    (text, found.into_iter().collect())
}

/// True when `redact_secrets(text)` finds a credential. A match of an earlier
/// pattern changes the text only after a find, so the original text is enough.
pub fn has_secret(text: &str) -> bool {
    PATTERNS.iter().any(|p| {
        p.re.captures_iter(text).any(|c| {
            let m = c.get(1).expect("group 1");
            !suppressed(text, m.start(), m.end(), c.get(p.secret).unwrap().as_str())
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // Expected values: project_memory.py and transcript_hygiene.py of v5.19.0.
    #[test]
    fn sources_parse_as_python() {
        #[allow(clippy::type_complexity)]
        let cases: [(&str, Option<(&str, &str, &str, Option<&str>)>); 13] = [
            (
                "project/a/decisions",
                Some(("project", "a", "decisions", None)),
            ),
            ("project/a/bad", None),
            ("project/A/state", None),
            ("project//state", None),
            (
                "person/dk/a/knowledge",
                Some(("person", "a", "knowledge", Some("dk"))),
            ),
            ("person/dk/a", None),
            ("personx/y", None),
            ("person/dk/a/knowledge/x", None),
            ("codex/x", None),
            ("project/", None),
            ("person", None),
            (
                "project/a-b/operations",
                Some(("project", "a-b", "operations", None)),
            ),
            ("project/-a/state", None),
        ];
        for (s, want) in cases {
            let got =
                parse_memory_source(s).map(|p| (p.namespace, p.project_id, p.kind, p.principal_id));
            assert_eq!(got, want, "{s}");
        }
    }

    #[test]
    fn secrets_match_redact_secrets() {
        let e = "é".repeat(40);
        let e10 = "é".repeat(10);
        let cases: Vec<(String, bool)> = vec![
            ("key AKIAABCDEFGHIJKLMNOP here".into(), true),
            ("ghp_abcdefghijklmnopqrstuvwxyz".into(), true),
            ("sk-abcdefghijklmnop1234".into(), true),
            ("example: sk-abcdefghijklmnop1234".into(), false),
            ("postgres://user:pass@db.host/x".into(), true),
            ("postgres://user:pass@localhost/x".into(), false),
            ("Bearer abcdefghijklmnopqrstuvwx".into(), true),
            ("API_KEY=supersecretvalue".into(), true),
            ("API_KEY=STRIPE_SECRET_KEY".into(), false),
            ("password: changeme".into(), false),
            ("password policy is strict".into(), false),
            ("MY_TOKEN=abcd1234efgh".into(), true),
            ("xMY_TOKEN=abcd1234efgh".into(), true),
            ("token=<your-token-here>".into(), false),
            ("eyJabcdefghijk.abcdefghijkl.abcdefg".into(), true),
            ("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi".into(), true),
            ("xoxb-1234567890-abc".into(), true),
            ("AKIAIOSFODNN7EXAMPLE".into(), false),
            ("the secret = 'hunter2hunter2'".into(), true),
            (format!("{e}api_key=realrealreal12"), true),
            (format!("demo {e10}api_key=realrealreal12"), false),
            ("sk-ABCDEFGHIJKLMNOPQRS example".into(), true),
            ("plain text".into(), false),
        ];
        for (t, want) in cases {
            assert_eq!(has_secret(&t), want, "{t}");
        }
    }

    #[test]
    fn namespace_moves() {
        let cases = [
            ("codex/x", Some("codex/y"), false),
            ("codex/x", Some("project/a/state"), true),
            ("project/a/state", Some("project/a/decisions"), false),
            ("person/dk/a/knowledge", Some("person/zz/a/knowledge"), true),
            ("project/bad", Some("project/bad2"), true),
            ("codex/x", None, false),
            ("codex/x", Some("codex/x"), false),
        ];
        for (a, b, want) in cases {
            assert_eq!(is_namespace_crossing_source_move(a, b), want, "{a} {b:?}");
        }
    }

    #[test]
    fn origin_clients_and_authorship() {
        for (v, want) in [
            (json!("Codex"), "codex"),
            (json!(" claude-code "), "claude-code"),
            (json!("x"), "other"),
            (json!(5), "other"),
            (Value::Null, "other"),
            (json!("HOOK"), "hook"),
        ] {
            assert_eq!(normalize_origin_client(&v), want);
        }
        let t = TrustedAuthorship::principal("dk", "Codex").unwrap();
        assert_eq!(
            Value::Object(t.as_metadata()),
            json!({"author": "dk", "origin_client": "codex"})
        );
        assert!(TrustedAuthorship::principal("Not A Slug", "").is_none());
    }

    #[test]
    fn project_writes() {
        let t = TrustedAuthorship::principal("dk", "codex");
        assert_eq!(
            validate_project_write("x", "project/bad", None)
                .unwrap_err()
                .0,
            MSG_PROJECT_SHAPE
        );
        assert_eq!(
            validate_project_write("x", "project/a/state", None)
                .unwrap_err()
                .0,
            MSG_NEEDS_AUTHOR
        );
        assert_eq!(
            validate_project_write("sk-abcdefghijklmnop1234", "project/a/state", t.as_ref())
                .unwrap_err()
                .0,
            MSG_CREDENTIAL
        );
        assert!(validate_project_write("x", "project/a/state", t.as_ref()).is_ok());
        // A person source needs no authorship here; a legacy source passes.
        assert!(validate_project_write("x", "person/dk/a/knowledge", None).is_ok());
        assert!(validate_project_write("sk-abcdefghijklmnop1234", "codex/x", None).is_ok());
    }
}
