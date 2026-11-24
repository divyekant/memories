//! `evidence_packet.build_evidence_packet` (v5.19.0): the agent-facing
//! packet of `/search/evidence`.

use std::cmp::Ordering;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::rank::PyDateTime;
use crate::records::{py_float, py_str, truthy};

fn get<'a>(m: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    m.get(key)
}

/// `str(document_at or updated_at or created_at or timestamp or "")`.
fn memory_date(m: &Map<String, Value>) -> String {
    ["document_at", "updated_at", "created_at", "timestamp"]
        .iter()
        .filter_map(|k| get(m, k))
        .find(|v| truthy(v))
        .map(py_str)
        .unwrap_or_default()
}

/// `_parse_date`: microseconds since 0001-01-01 UTC; a naive value is UTC.
fn parse_date(value: &str) -> Option<i64> {
    if value.is_empty() {
        return None;
    }
    PyDateTime::fromisoformat(&value.replace('Z', "+00:00")).map(|d| d.or_utc().utc_us())
}

/// `_score`: `float(similarity, else rrf_score, else 0.0)`; 0.0 if that fails.
pub fn score(m: &Map<String, Value>) -> f64 {
    let v = get(m, "similarity").or_else(|| get(m, "rrf_score"));
    match v {
        None => 0.0,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::Bool(b)) => f64::from(u8::from(*b)),
        Some(Value::String(s)) => py_float(s).unwrap_or(0.0),
        Some(_) => 0.0,
    }
}

fn compact(m: &Map<String, Value>, relation: &str) -> Value {
    json!({
        "id": get(m, "id").cloned().unwrap_or(Value::Null),
        "source": get(m, "source").cloned().unwrap_or_else(|| json!("")),
        "date": memory_date(m),
        "text": get(m, "text").cloned().unwrap_or_else(|| json!("")),
        "relation": relation,
        "score": score(m),
        "is_latest": get(m, "is_latest").is_some_and(truthy),
        "archived": get(m, "archived").is_some_and(truthy),
    })
}

static RECENCY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(latest|current|now|recent|changed|newest|today|yesterday)\b")
        .expect("regex")
});

/// Python `" ".join(text.split())`.
fn squash(text: &str) -> String {
    crate::rank::scores::py_split(text).join(" ")
}

fn follow_up_queries(query: &str) -> Vec<String> {
    let clean = squash(query);
    if clean.is_empty() {
        return Vec::new();
    }
    let lowered = clean.to_lowercase();
    let mut candidates = vec![clean.clone()];
    if !lowered.starts_with("latest ") {
        candidates.push(format!("latest {clean}"));
    }
    if !lowered.starts_with("current ") {
        candidates.push(format!("current {clean}"));
    }
    if !lowered.starts_with("what changed") {
        candidates.push(format!("what changed about {clean}"));
    }
    let mut seen = std::collections::HashSet::new();
    candidates
        .into_iter()
        .filter(|c| seen.insert(c.to_lowercase()))
        .collect()
}

/// The sort key of `_rank_key` (the date is `datetime.min` when it does not parse).
#[derive(Clone, Copy)]
struct Key {
    dated: bool,
    date: i64,
    score: f64,
    latest: bool,
}

fn key(m: &Map<String, Value>) -> Key {
    let date = parse_date(&memory_date(m));
    Key {
        dated: date.is_some(),
        date: date.unwrap_or(i64::MIN),
        score: score(m),
        latest: get(m, "is_latest").is_some_and(truthy),
    }
}

fn cmp_key(a: &Key, b: &Key, prefer_recency: bool) -> Ordering {
    let score = || a.score.partial_cmp(&b.score).unwrap_or(Ordering::Equal);
    let rest = (a.dated, a.date).cmp(&(b.dated, b.date));
    let first = if prefer_recency {
        rest.then_with(score)
    } else {
        score().then(rest)
    };
    first.then(a.latest.cmp(&b.latest))
}

pub fn build_evidence_packet(query: &str, results: &[Value]) -> Value {
    let empty = Map::new();
    let maps: Vec<&Map<String, Value>> = results
        .iter()
        .map(|r| r.as_object().unwrap_or(&empty))
        .collect();
    if maps.is_empty() {
        return json!({
            "current_answer": null,
            "supporting_memories": [],
            "older_evidence": [],
            "older_conflicting_memories": [],
            "source_date_trail": [],
            "confidence": {"level": "missing", "reasons": ["No memories were retrieved for this query."]},
            "follow_up_queries": follow_up_queries(query),
        });
    }
    let prefer = RECENCY.is_match(query);
    let mut ranked: Vec<(Key, &Map<String, Value>)> = maps.iter().map(|m| (key(m), *m)).collect();
    // Python sorted(reverse=True) is stable: equal keys keep their order.
    ranked.sort_by(|a, b| cmp_key(&b.0, &a.0, prefer));
    let current = ranked[0].1;
    let current_date = parse_date(&memory_date(current));

    let mut supporting = Vec::new();
    let mut older = Vec::new();
    for (_, m) in &ranked[1..] {
        let date = parse_date(&memory_date(m));
        match (current_date, date) {
            (Some(c), Some(d)) if d < c => older.push(compact(m, "older")),
            _ if get(m, "archived").is_some_and(truthy) => older.push(compact(m, "archived")),
            (None, Some(_)) => older.push(compact(m, "dated_unranked")),
            _ => supporting.push(compact(m, "supporting")),
        }
    }

    let dated = !memory_date(current).is_empty();
    let mut reasons = vec![if dated {
        "Current candidate has a source date."
    } else {
        "Current candidate has no source date."
    }];
    if !older.is_empty() {
        reasons.push(
            "Packet includes older evidence or separately dated evidence that may be superseded.",
        );
    }
    if get(current, "is_latest").is_some_and(truthy) {
        reasons.push("Current candidate is explicitly marked is_latest.");
    }
    let level = if !dated {
        "low"
    } else if !older.is_empty() {
        "medium"
    } else {
        "high"
    };
    let mut trail = vec![compact(current, "current")];
    trail.extend(supporting.iter().cloned());
    trail.extend(older.iter().cloned());
    let first = |v: &[Value], n: usize| v.iter().take(n).cloned().collect::<Vec<_>>();
    json!({
        "current_answer": compact(current, "current"),
        "supporting_memories": first(&supporting, 5),
        "older_evidence": first(&older, 5),
        "older_conflicting_memories": first(&older, 5),
        "source_date_trail": first(&trail, 10),
        "confidence": {"level": level, "reasons": reasons},
        "follow_up_queries": follow_up_queries(query),
    })
}
