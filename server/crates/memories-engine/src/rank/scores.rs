//! Score rules of `memory_engine.py` (v5.19.0): tokens, rounding, recency,
//! confidence, the temporal filter, the signal weights and `relative_score`.

use super::pydt::PyDateTime;

/// Python `round(x, digits)`: the nearest decimal, ties to even on the
/// exact binary value. The Rust formatter rounds the same way.
pub fn py_round(x: f64, digits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.digits$}").parse().unwrap_or(x)
}

/// Python `str.isspace()`: Unicode White_Space plus U+001C to U+001F.
fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python `text.lower().split()`: the BM25 tokens of a stored text.
pub fn doc_tokens(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(is_py_space)
        .filter(|t| !t.is_empty())
        .map(str::to_owned)
        .collect()
}

const STOPWORDS: &[&str] = &[
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "did", "do", "does", "for",
    "from", "had", "has", "have", "how", "i", "in", "is", "it", "of", "on", "or", "our", "should",
    "that", "the", "this", "to", "was", "we", "were", "what", "when", "where", "which", "who",
    "why", "will", "with", "would", "you",
];

/// `MemoryEngine._bm25_query_tokens`: the keyword terms of a query, without
/// stopwords and a trailing `?`. A query of only stopwords keeps its words.
pub fn query_tokens(query: &str) -> Vec<String> {
    let words = doc_tokens(query);
    let kept: Vec<String> = words
        .iter()
        .map(|t| t.trim_end_matches('?'))
        .filter(|t| !t.is_empty() && !STOPWORDS.contains(t))
        .map(str::to_owned)
        .collect();
    if kept.is_empty() { words } else { kept }
}

/// Python `a or b or c` for optional strings: the first non-empty value.
pub fn first_set<'a>(values: &[Option<&'a str>]) -> Option<&'a str> {
    values.iter().flatten().copied().find(|s| !s.is_empty())
}

fn decay(anchor: Option<&str>, half_life: f64, default_half_life: f64, now: &PyDateTime) -> f64 {
    let Some(ts) = anchor.filter(|s| !s.is_empty()) else {
        return 0.0;
    };
    let Some(ts) = PyDateTime::fromisoformat(ts) else {
        return 0.0;
    };
    let age_days = now.seconds_since(&ts.or_utc()) / 86400.0;
    if age_days < 0.0 {
        return 1.0;
    }
    let half_life = if half_life <= 0.0 {
        default_half_life
    } else {
        half_life
    };
    0.5_f64.powf(age_days / half_life)
}

/// `MemoryEngine._recency_score`: 1.0 now, 0.5 after one half-life.
pub fn recency_score(created_at: Option<&str>, half_life_days: f64, now: &PyDateTime) -> f64 {
    decay(created_at, half_life_days, 30.0, now)
}

/// `MemoryEngine.compute_confidence`: 1.0 when fresh, 0.5 after one half-life.
pub fn compute_confidence(anchor: Option<&str>, half_life_days: f64, now: &PyDateTime) -> f64 {
    decay(anchor, half_life_days, 90.0, now)
}

/// `MemoryEngine._passes_temporal_filter`. `doc_date` is
/// `document_at or created_at or timestamp`. A value that does not parse
/// passes, as do a naive and an aware value that Python cannot compare.
pub fn passes_temporal_filter(
    doc_date: Option<&str>,
    since: Option<&str>,
    until: Option<&str>,
) -> bool {
    let since = since.filter(|s| !s.is_empty());
    let until = until.filter(|s| !s.is_empty());
    if since.is_none() && until.is_none() {
        return true;
    }
    let Some(doc) = doc_date.filter(|s| !s.is_empty()) else {
        return true;
    };
    let parse = |s: &str| PyDateTime::fromisoformat(&s.replace('Z', "+00:00"));
    let Some(ts) = parse(doc) else {
        return true;
    };
    if let Some(since) = since {
        let Some(bound) = parse(since) else {
            return true;
        };
        match ts.lt(&bound) {
            Some(true) => return false,
            Some(false) => {}
            None => return true,
        }
    }
    if let Some(until) = until {
        let Some(bound) = parse(until) else {
            return true;
        };
        match bound.lt(&ts) {
            Some(true) => return false,
            Some(false) => {}
            None => return true,
        }
    }
    true
}

/// Python `max(0.0, min(1.0, x))`.
fn clamp01(x: f64) -> f64 {
    let low = if x < 1.0 { x } else { 1.0 };
    if low > 0.0 { low } else { 0.0 }
}

/// The weights of a hybrid search, as the request gives them.
#[derive(Clone, Copy, Debug)]
pub struct WeightInput {
    pub vector: f64,
    pub recency: f64,
    pub recency_half_life_days: f64,
    pub feedback: f64,
    pub confidence: f64,
    pub graph: f64,
}

/// The weights after the clamps and the 5-signal scaling of `hybrid_search`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Weights {
    pub vector: f64,
    pub keyword: f64,
    pub recency: f64,
    pub recency_half_life_days: f64,
    pub feedback: f64,
    pub confidence: f64,
    pub graph: f64,
    /// The clamped recency weight: the recency signal runs when it is above 0.
    pub recency_raw: f64,
}

impl Weights {
    pub fn new(w: WeightInput) -> Self {
        let recency = clamp01(w.recency);
        let half_life = if w.recency_half_life_days <= 0.0 {
            30.0
        } else {
            w.recency_half_life_days
        };
        let mut feedback = clamp01(w.feedback);
        let mut confidence = clamp01(w.confidence);
        let graph = clamp01(w.graph);
        let mut auxiliary = feedback + confidence;
        if auxiliary > 1.0 {
            feedback /= auxiliary;
            confidence /= auxiliary;
            auxiliary = 1.0;
        }
        let core = 1.0 - auxiliary;
        Self {
            vector: w.vector * core * (1.0 - recency),
            keyword: (1.0 - w.vector) * core * (1.0 - recency),
            recency: recency * core,
            recency_half_life_days: half_life,
            feedback,
            confidence,
            graph,
            recency_raw: recency,
        }
    }
}

/// `annotate_relative_scores`: each raw score (`similarity`, else
/// `rrf_score`) over the top raw score of the set, to 4 decimals.
pub fn relative_scores(raw: &[f64]) -> Vec<f64> {
    let max = raw
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, |m, x| if x > m { x } else { m });
    raw.iter()
        .map(|&x| {
            if max <= 0.0 || x <= 0.0 {
                0.0
            } else {
                py_round(x / max, 4)
            }
        })
        .collect()
}
