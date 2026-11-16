//! Query intent rules of `query_intent.py` (v5.19.0): time words in a query
//! give a date range, a recency boost, or no graph step.

use std::sync::LazyLock;

use regex::{Captures, Regex};

use super::pydt::{MAX_ORDINAL, checked_ord, days_in_month, ord_to_ymd, weekday};

/// An error where Python raises (`ValueError`, `OverflowError`, `KeyError`),
/// which `/search` returns as HTTP 500.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct IntentError;

type R<T> = Result<T, IntentError>;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct TemporalIntent {
    pub since: Option<String>,
    pub until: Option<String>,
    pub recency_boost: bool,
    pub suppress_graph: bool,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct SearchAdjustments {
    pub since: Option<String>,
    pub until: Option<String>,
    /// `None`: no change. `Some(0.0)`: no graph step.
    pub graph_weight: Option<f64>,
    pub recency_weight: Option<f64>,
    pub auto_detected: bool,
}

const WORD_TO_NUM: &[(&str, u64)] = &[
    ("a", 1),
    ("an", 1),
    ("one", 1),
    ("two", 2),
    ("three", 3),
    ("four", 4),
    ("five", 5),
    ("six", 6),
    ("seven", 7),
    ("eight", 8),
    ("nine", 9),
    ("ten", 10),
    ("eleven", 11),
    ("twelve", 12),
    ("few", 3),
    ("couple", 2),
    ("several", 4),
];

const MONTHS: &[(&str, i64)] = &[
    ("january", 1),
    ("february", 2),
    ("march", 3),
    ("april", 4),
    ("may", 5),
    ("june", 6),
    ("july", 7),
    ("august", 8),
    ("september", 9),
    ("october", 10),
    ("november", 11),
    ("december", 12),
    ("jan", 1),
    ("feb", 2),
    ("mar", 3),
    ("apr", 4),
    ("jun", 6),
    ("jul", 7),
    ("aug", 8),
    ("sep", 9),
    ("oct", 10),
    ("nov", 11),
    ("dec", 12),
];

const WEEKDAYS: &[(&str, i64)] = &[
    ("monday", 0),
    ("tuesday", 1),
    ("wednesday", 2),
    ("thursday", 3),
    ("friday", 4),
    ("saturday", 5),
    ("sunday", 6),
];

/// A Python `re` pattern with IGNORECASE. Python `\s` also matches U+001C
/// to U+001F, and its `\b` takes only letters, numbers and `_` as word
/// characters (Rust also takes combining marks). So the edge `\b` of a
/// pattern is checked here with the Python rule. The patterns use no
/// `\b` other than at an edge.
struct PyRegex {
    re: Regex,
    lead: bool,
    trail: bool,
}

static WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[\p{L}\p{N}_]$").expect("word"));

fn is_word(c: Option<char>) -> bool {
    c.is_some_and(|c| WORD.is_match(c.encode_utf8(&mut [0; 4])))
}

/// Python `\b` at byte offset `i`.
fn boundary(text: &str, i: usize) -> bool {
    is_word(text[..i].chars().next_back()) != is_word(text[i..].chars().next())
}

impl PyRegex {
    fn new(pattern: &str) -> Self {
        let lead = pattern.starts_with(r"\b");
        let trail = pattern.ends_with(r"\b");
        let core = &pattern[if lead { 2 } else { 0 }..pattern.len() - if trail { 2 } else { 0 }];
        // Python also matches U+0130 and U+0131 to `i`; Rust case folding does not.
        let core = core.replace('i', "[i\u{130}\u{131}]");
        let core = core.replace(r"\s", r"[\s\x1c-\x1f]");
        let re = Regex::new(&format!("(?i){core}")).expect("intent pattern");
        Self { re, lead, trail }
    }

    /// `re.search`: the leftmost match whose edges pass the Python `\b`.
    fn captures<'h>(&self, text: &'h str) -> Option<Captures<'h>> {
        let mut start = 0;
        while start <= text.len() {
            let c = self.re.captures_at(text, start)?;
            let m = c.get(0)?;
            let ok = (!self.lead || boundary(text, m.start()))
                && (!self.trail || boundary(text, m.end()));
            if ok {
                return Some(c);
            }
            start = m.start() + text[m.start()..].chars().next().map_or(1, char::len_utf8);
        }
        None
    }

    fn is_match(&self, text: &str) -> bool {
        self.captures(text).is_some()
    }
}

struct Patterns {
    month_range: PyRegex,
    in_month: PyRegex,
    yesterday: PyRegex,
    last_period: PyRegex,
    last_weekday: PyRegex,
    n_ago: PyRegex,
    past_n: PyRegex,
    this_period: PyRegex,
    recency: PyRegex,
    digit: Regex,
}

fn alternation(words: &[(&str, impl Copy)]) -> String {
    words.iter().map(|w| w.0).collect::<Vec<_>>().join("|")
}

static PATTERNS: LazyLock<Patterns> = LazyLock::new(|| {
    let month = alternation(MONTHS);
    let weekday = alternation(WEEKDAYS);
    let num = format!(r"(?:\d+|{})", alternation(WORD_TO_NUM));
    Patterns {
        month_range: PyRegex::new(&format!(
            r"(?:from\s+|between\s+)({month})\s+(?:to|and)\s+({month})"
        )),
        in_month: PyRegex::new(&format!(r"(?:in|from|during|month\s+of)\s+({month})\b")),
        yesterday: PyRegex::new(r"\byesterday\b"),
        last_period: PyRegex::new(r"\blast\s+(week|month|year)\b"),
        last_weekday: PyRegex::new(&format!(r"\blast\s+({weekday})\b")),
        n_ago: PyRegex::new(&format!(r"\b({num})\s+(day|week|month|year)s?\s+ago\b")),
        past_n: PyRegex::new(&format!(
            r"\b(?:past|last)\s+({num})\s+(day|week|month|year)s?\b"
        )),
        this_period: PyRegex::new(r"\bthis\s+(week|month|year)\b"),
        recency: PyRegex::new(r"\b(?:recently|latest|most\s+recent|newest)\b"),
        digit: Regex::new(r"^\d$").expect("digit pattern"),
    }
});

/// `dt.strftime("%Y-%m-%dT00:00:00Z")`. glibc does not pad the year.
fn iso_start(ordinal: i64) -> String {
    let (y, m, d) = ord_to_ymd(ordinal);
    format!("{y}-{m:02}-{d:02}T00:00:00Z")
}

fn iso_end(ordinal: i64) -> String {
    let (y, m, d) = ord_to_ymd(ordinal);
    format!("{y}-{m:02}-{d:02}T23:59:59Z")
}

/// `dt + timedelta(days=delta)`, with Python's range check.
fn shift(ordinal: i64, delta: i64) -> R<i64> {
    let out = ordinal.checked_add(delta).ok_or(IntentError)?;
    if (1..=MAX_ORDINAL).contains(&out) {
        Ok(out)
    } else {
        Err(IntentError)
    }
}

fn date(year: i64, month: i64, day: i64) -> R<i64> {
    checked_ord(year, month, day).ok_or(IntentError)
}

fn end_of_month(year: i64, month: i64) -> R<i64> {
    if !(1..=12).contains(&month) {
        return Err(IntentError);
    }
    date(year, month, days_in_month(year, month))
}

fn start_of_week(ordinal: i64) -> R<i64> {
    shift(ordinal, -weekday(ordinal))
}

/// The value of a Unicode decimal digit. Each Unicode block of decimal
/// digits holds whole runs of 0 to 9.
fn digit_value(c: char) -> u64 {
    if let Some(d) = c.to_digit(10) {
        return u64::from(d);
    }
    let is_digit = |cp: u32| {
        char::from_u32(cp).is_some_and(|ch| PATTERNS.digit.is_match(ch.encode_utf8(&mut [0; 4])))
    };
    let cp = u32::from(c);
    let mut start = cp;
    while start > 0 && is_digit(start - 1) {
        start -= 1;
    }
    u64::from((cp - start) % 10)
}

/// `_parse_number`: `int(s)`, else the number word, else 1.
fn parse_number(s: &str) -> R<u64> {
    if s.chars()
        .all(|c| PATTERNS.digit.is_match(c.encode_utf8(&mut [0; 4])))
        && !s.is_empty()
    {
        // An overflow here is an OverflowError of timedelta in Python.
        return s.chars().try_fold(0_u64, |acc, c| {
            acc.checked_mul(10)
                .and_then(|v| v.checked_add(digit_value(c)))
                .ok_or(IntentError)
        });
    }
    let lower = s.to_lowercase();
    Ok(WORD_TO_NUM.iter().find(|w| w.0 == lower).map_or(1, |w| w.1))
}

fn lookup(table: &[(&str, i64)], text: &str) -> R<i64> {
    let lower = text.to_lowercase();
    table
        .iter()
        .find(|w| w.0 == lower)
        .map(|w| w.1)
        .ok_or(IntentError)
}

fn days(n: u64, per: u64) -> R<i64> {
    n.checked_mul(per)
        .and_then(|d| i64::try_from(d).ok())
        .ok_or(IntentError)
}

fn group<'h>(c: &Captures<'h>, i: usize) -> &'h str {
    c.get(i).map_or("", |m| m.as_str())
}

fn range(since: i64, until: i64) -> TemporalIntent {
    TemporalIntent {
        since: Some(iso_start(since)),
        until: Some(iso_end(until)),
        recency_boost: false,
        suppress_graph: true,
    }
}

fn from(since: i64) -> TemporalIntent {
    TemporalIntent {
        since: Some(iso_start(since)),
        until: None,
        recency_boost: false,
        suppress_graph: true,
    }
}

/// `detect_temporal_intent`. `now` is (year, month, day) of the reference
/// date; the time of day and the time zone have no effect.
pub fn detect_temporal_intent(query: &str, now: (i64, i64, i64)) -> R<Option<TemporalIntent>> {
    let p = &*PATTERNS;
    let (year, month, _) = now;
    let today = date(now.0, now.1, now.2)?;

    if let Some(c) = p.month_range.captures(query) {
        let m1 = lookup(MONTHS, group(&c, 1))?;
        let m2 = lookup(MONTHS, group(&c, 2))?;
        let end_year = if m2 <= month { year } else { year - 1 };
        let start_year = if m1 > m2 { end_year - 1 } else { end_year };
        let since = date(start_year, m1, 1)?;
        let until = end_of_month(end_year, m2)?;
        return Ok(Some(range(since, until)));
    }
    if let Some(c) = p.in_month.captures(query) {
        let m = lookup(MONTHS, group(&c, 1))?;
        let y = if m <= month { year } else { year - 1 };
        let since = date(y, m, 1)?;
        return Ok(Some(range(since, end_of_month(y, m)?)));
    }
    if p.yesterday.is_match(query) {
        let d = shift(today, -1)?;
        return Ok(Some(range(d, d)));
    }
    if let Some(c) = p.last_period.captures(query) {
        let intent = match group(&c, 1).to_lowercase().as_str() {
            "week" => {
                let monday = shift(start_of_week(today)?, -7)?;
                Some(range(monday, shift(start_of_week(monday)?, 6)?))
            }
            "month" => {
                let (y, m) = if month == 1 {
                    (year - 1, 12)
                } else {
                    (year, month - 1)
                };
                Some(range(date(y, m, 1)?, end_of_month(y, m)?))
            }
            "year" => Some(range(date(year - 1, 1, 1)?, date(year - 1, 12, 31)?)),
            _ => None,
        };
        if intent.is_some() {
            return Ok(intent);
        }
    }
    if let Some(c) = p.last_weekday.captures(query) {
        let target = lookup(WEEKDAYS, group(&c, 1))?;
        let mut back = (weekday(today) - target).rem_euclid(7);
        if back == 0 {
            back = 7;
        }
        let d = shift(today, -back)?;
        return Ok(Some(range(d, d)));
    }
    if let Some(c) = p.n_ago.captures(query) {
        let n = parse_number(group(&c, 1))?;
        let intent = match group(&c, 2).to_lowercase().as_str() {
            "day" => {
                let d = shift(today, -days(n, 1)?)?;
                Some(range(d, d))
            }
            "week" => {
                let monday = start_of_week(shift(today, -days(n, 7)?)?)?;
                Some(range(monday, shift(start_of_week(monday)?, 6)?))
            }
            "month" => {
                let d = shift(today, -days(n, 30)?)?;
                Some(range(d, d))
            }
            "year" => {
                let d = shift(today, -days(n, 365)?)?;
                Some(range(d, d))
            }
            _ => None,
        };
        if intent.is_some() {
            return Ok(intent);
        }
    }
    if let Some(c) = p.past_n.captures(query) {
        let n = parse_number(group(&c, 1))?;
        let per = match group(&c, 2).to_lowercase().as_str() {
            "day" => 1,
            "week" => 7,
            "month" => 30,
            "year" => 365,
            _ => return Ok(None),
        };
        return Ok(Some(from(shift(today, -days(n, per)?)?)));
    }
    if let Some(c) = p.this_period.captures(query) {
        let since = match group(&c, 1).to_lowercase().as_str() {
            "week" => Some(start_of_week(today)?),
            "month" => Some(date(year, month, 1)?),
            "year" => Some(date(year, 1, 1)?),
            _ => None,
        };
        if let Some(since) = since {
            return Ok(Some(from(since)));
        }
    }
    if p.recency.is_match(query) {
        return Ok(Some(TemporalIntent {
            recency_boost: true,
            ..TemporalIntent::default()
        }));
    }
    Ok(None)
}

/// `classify_query`: the intent, with the values that the caller gave kept.
pub fn classify_query(
    query: &str,
    now: (i64, i64, i64),
    caller_since: Option<&str>,
    caller_until: Option<&str>,
    caller_graph_weight: Option<f64>,
) -> R<SearchAdjustments> {
    let Some(t) = detect_temporal_intent(query, now)? else {
        return Ok(SearchAdjustments::default());
    };
    let pick = |detected: Option<String>, caller: Option<&str>| match (detected, caller) {
        (Some(d), None) if !d.is_empty() => Some(d),
        (_, Some(c)) => Some(c.to_owned()),
        _ => None,
    };
    let graph_weight = match caller_graph_weight {
        None if t.suppress_graph => Some(0.0),
        other => other,
    };
    Ok(SearchAdjustments {
        since: pick(t.since, caller_since),
        until: pick(t.until, caller_until),
        graph_weight,
        recency_weight: t.recency_boost.then_some(0.2),
        auto_detected: true,
    })
}
