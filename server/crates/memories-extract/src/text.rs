//! The text rules of the extraction pipeline of v5.19.0: transcript cleanup
//! (`transcript_hygiene.clean_transcript`), the fallback extractor of `app.py`,
//! the JSON parse rules, the prompts and the parse of the LLM replies
//! (`llm_extract.py`). The golden file `parity/golden/extract/text.ndjson`
//! checks each function against Python.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

// ---- Python helpers ------------------------------------------------------------

/// Python `str.isspace()`: Unicode White_Space plus U+001C to U+001F.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python `str.strip()`.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// Python `str.splitlines()`.
pub fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        if matches!(
            c,
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        ) {
            out.push(&s[start..i]);
            start = i + c.len_utf8();
            if c == '\r' && it.peek().is_some_and(|&(_, n)| n == '\n') {
                it.next();
                start += 1;
            }
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// Python `type(v).__name__` of a JSON value.
pub fn py_type(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Python `str(v)` of a JSON value. Lists and maps use the JSON text.
pub fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        Value::Number(n) if n.is_f64() => py_float_repr(n.as_f64().unwrap_or(0.0)),
        other => other.to_string(),
    }
}

/// Python `repr(float)`, as `json.dumps` writes it.
pub fn py_float_repr(x: f64) -> String {
    if x.is_nan() {
        return "NaN".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "Infinity" } else { "-Infinity" }.into();
    }
    let a = x.abs();
    if x == 0.0 || (1e-4..1e16).contains(&a) {
        let s = format!("{x}");
        return if s.contains('.') { s } else { s + ".0" };
    }
    let s = format!("{x:e}");
    let (mant, exp) = s.split_once('e').unwrap_or((&s, "0"));
    let (sign, digits) = match exp.strip_prefix('-') {
        Some(d) => ('-', d),
        None => ('+', exp),
    };
    format!("{mant}e{sign}{digits:0>2}")
}

/// A Python string literal as `json.dumps` writes it (`ensure_ascii=True`).
pub fn py_json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut buf = [0u16; 2];
                for u in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{u:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

/// A JSON value as Python `json.dumps(v, separators=(",", ":"))` writes it.
/// The keys keep the order of the map (sorted, for a `serde_json` map).
pub fn py_json_compact(v: &Value) -> String {
    match v {
        Value::Null => "null".into(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) if n.is_f64() => py_float_repr(n.as_f64().unwrap_or(0.0)),
        Value::Number(n) => n.to_string(),
        Value::String(s) => py_json_str(s),
        Value::Array(a) => format!(
            "[{}]",
            a.iter().map(py_json_compact).collect::<Vec<_>>().join(",")
        ),
        Value::Object(m) => format!(
            "{{{}}}",
            m.iter()
                .map(|(k, v)| format!("{}:{}", py_json_str(k), py_json_compact(v)))
                .collect::<Vec<_>>()
                .join(",")
        ),
    }
}

/// `_clip_text`: one space between words, and at most `max_chars` characters.
pub fn clip_text(text: &str, max_chars: usize) -> String {
    let compact = text
        .split(is_py_space)
        .filter(|w| !w.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if compact.chars().count() <= max_chars {
        return compact;
    }
    let head: String = compact.chars().take(max_chars.saturating_sub(3)).collect();
    format!("{}...", head.trim_end_matches(is_py_space))
}

/// Fills `{name}` fields of a template in one pass, as `str.format` does.
fn fill(template: &str, fields: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(i) = rest.find('{') {
        out.push_str(&rest[..i]);
        rest = &rest[i..];
        match fields.iter().find(|(name, _)| {
            rest[1..].starts_with(name) && rest[1 + name.len()..].starts_with('}')
        }) {
            Some((name, value)) => {
                out.push_str(value);
                rest = &rest[name.len() + 2..];
            }
            None => {
                out.push('{');
                rest = &rest[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

// ---- Transcript cleanup --------------------------------------------------------

const ROLE: &str = "(?:user|assistant|human|system)";

static ROLE_AT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"(?i)^[ \t]*{ROLE}[ \t]*:")).unwrap());
static HEADING_AT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[ \t]*#{1,6}[ \t]").unwrap());
static REMINDER_OPEN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)<system-reminder>").unwrap());
static REMINDER_CLOSE_AT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^</system-reminder>").unwrap());
static STRAY_TAG: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)</?system-reminder>").unwrap());
// The heads of the blocks of `transcript_hygiene.py`; each block then runs
// to the first boundary (see `boundary`).
static LINE_START: LazyLock<String> =
    LazyLock::new(|| format!(r"(?im)^[ \t]*(?:{ROLE}[ \t]*:[ \t]*)?"));
static PREAMBLE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r"{}IMPORTANT:[ \t]+The following memories from prior sessions\b",
        *LINE_START
    ))
    .unwrap()
});
static SECTION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r"{}#{{1,6}}[ \t]*(?:retrieved|relevant)[ \t]+memories\b",
        *LINE_START
    ))
    .unwrap()
});
static HOOK_CONTEXT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r"{}(?:[A-Za-z][\w-]*[ \t]+){{1,3}}hook[ \t]+additional[ \t]+context[ \t]*:",
        *LINE_START
    ))
    .unwrap()
});
static BLANK_RUNS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n{3,}").unwrap());
static ONLY_ROLE_PREFIXES: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"(?i)\A(?:[ \t]*{ROLE}[ \t]*:[ \t]*\n*)*\z")).unwrap());

fn line_start(t: &str, p: usize) -> bool {
    p == 0 || t.as_bytes()[p - 1] == b'\n'
}

/// `_BOUNDARY_LOOKAHEAD`: a role line, a markdown heading, or the end.
fn boundary(t: &str, p: usize) -> bool {
    p == t.len()
        || (line_start(t, p) && (ROLE_AT.is_match(&t[p..]) || HEADING_AT.is_match(&t[p..])))
}

fn char_boundaries(t: &str, from: usize) -> impl Iterator<Item = usize> + '_ {
    (from..=t.len()).filter(move |&p| t.is_char_boundary(p))
}

/// `_SYSTEM_REMINDER_RE.sub("", t)`: a block runs to its closing tag, or to
/// the next role line when the tag was cut off.
fn strip_reminders(t: &str) -> String {
    let mut out = String::with_capacity(t.len());
    let (mut pos, mut search) = (0, 0);
    while let Some(m) = REMINDER_OPEN.find_at(t, search) {
        let mut end = t.len();
        for p in char_boundaries(t, m.end()) {
            if let Some(c) = REMINDER_CLOSE_AT.find(&t[p..]) {
                end = p + c.end();
                break;
            }
            if p == t.len() || (line_start(t, p) && ROLE_AT.is_match(&t[p..])) {
                end = p;
                break;
            }
        }
        out.push_str(&t[pos..m.start()]);
        pos = end;
        search = end;
    }
    out.push_str(&t[pos..]);
    out
}

/// Removes each block that starts with `head` and runs to the next boundary.
fn strip_blocks(t: &str, head: &Regex) -> String {
    let mut out = String::with_capacity(t.len());
    let (mut pos, mut search) = (0, 0);
    while let Some(m) = head.find_at(t, search) {
        let end = char_boundaries(t, m.end())
            .find(|&p| boundary(t, p))
            .unwrap_or(t.len());
        out.push_str(&t[pos..m.start()]);
        pos = end;
        search = end;
    }
    out.push_str(&t[pos..]);
    out
}

/// `clean_transcript`: removes the context that hooks put in a transcript.
/// Returns "" when only injected content or white space stays.
pub fn clean_transcript(text: &str) -> String {
    if py_strip(text).is_empty() {
        return String::new();
    }
    let t = strip_reminders(text);
    let t = STRAY_TAG.replace_all(&t, "");
    let t = strip_blocks(&t, &PREAMBLE);
    let t = strip_blocks(&t, &SECTION);
    let t = strip_blocks(&t, &HOOK_CONTEXT);
    let t = BLANK_RUNS.replace_all(&t, "\n\n");
    let t = py_strip(&t);
    if t.is_empty() || ONLY_ROLE_PREFIXES.is_match(t) {
        return String::new();
    }
    t.to_owned()
}

// ---- Fallback extractor (app.py) -----------------------------------------------

/// The settings of the fallback extractor, with the Python names.
#[derive(Clone, Debug)]
pub struct FallbackConfig {
    /// `EXTRACT_FALLBACK_ADD`.
    pub enabled: bool,
    /// `EXTRACT_FALLBACK_MAX_FACTS`.
    pub max_facts: usize,
    /// `EXTRACT_FALLBACK_MIN_FACT_CHARS`.
    pub min_chars: usize,
    /// `EXTRACT_FALLBACK_MAX_FACT_CHARS`.
    pub max_chars: usize,
    /// `EXTRACT_FALLBACK_NOVELTY_THRESHOLD`.
    pub novelty_threshold: f64,
}

impl Default for FallbackConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            max_facts: 1,
            min_chars: 24,
            max_chars: 280,
            novelty_threshold: 0.88,
        }
    }
}

static DECISION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        &r"(?i)\b(decide(?:d|s|ing)?|decision|prefer|standard|policy|we\s+should|we\s+will|let'?s|going\s+with|use\s+[a-z0-9_.-]+|remember\s+)\b"
            .replace("\\s", "[\\s\\x1c-\\x1f]"),
    )
    .unwrap()
});
static ROLE_PREFIX: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^(?:user|assistant) *: *").unwrap());

/// `_normalize_candidate_text`.
fn normalize_candidate(line: &str) -> String {
    let compact = py_strip(line)
        .split(is_py_space)
        .filter(|w| !w.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let compact = ROLE_PREFIX.replace(&compact, "");
    py_strip(&compact).to_owned()
}

/// `_fallback_extract_facts`: a few lines that look like decisions.
pub fn fallback_facts(messages: &str, cfg: &FallbackConfig) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for raw in py_splitlines(messages) {
        let line = normalize_candidate(raw);
        let n = line.chars().count();
        if line.is_empty()
            || line.ends_with('?')
            || n < cfg.min_chars
            || n > cfg.max_chars
            || line.split(' ').filter(|w| !w.is_empty()).count() < 4
            || !DECISION.is_match(&line)
        {
            continue;
        }
        let lower = line.to_lowercase();
        if ["ok ", "okay ", "sure ", "thanks", "thank you"]
            .iter()
            .any(|p| lower.starts_with(p))
        {
            continue;
        }
        if !out.contains(&line) {
            out.push(line);
        }
        if out.len() >= cfg.max_facts {
            break;
        }
    }
    out
}

// ---- LLM replies ---------------------------------------------------------------

fn json_list(text: &str) -> Option<Vec<Value>> {
    match serde_json::from_str(text) {
        Ok(Value::Array(a)) => Some(a),
        _ => None,
    }
}

/// `_parse_json_array`: the whole reply, a code block, or the text from the
/// first `[` to the last `]`. Else an empty list.
pub fn parse_json_array(text: &str) -> Vec<Value> {
    let text = py_strip(text);
    if let Some(a) = json_list(text) {
        return a;
    }
    if text.contains("```") {
        for block in text.split("```") {
            let block = py_strip(block);
            let block = match block.strip_prefix("json") {
                Some(b) => py_strip(b),
                None => block,
            };
            if let Some(a) = json_list(block) {
                return a;
            }
        }
    }
    if let (Some(s), Some(e)) = (text.find('['), text.rfind(']'))
        && e > s
        && let Some(a) = json_list(&text[s..=e])
    {
        return a;
    }
    Vec::new()
}

pub const PROMPT_FACTS: &str = include_str!("prompts/facts.txt");
pub const PROMPT_FACTS_AGGRESSIVE: &str = include_str!("prompts/facts_aggressive.txt");
pub const PROMPT_AUDN: &str = include_str!("prompts/audn.txt");
pub const PROMPT_SINGLE_CALL: &str = include_str!("prompts/single_call.txt");
pub const AUDN_SYSTEM: &str = "You are a memory manager. Output only valid JSON.";

/// `_build_rules_section`.
pub fn rules_section(rules: Option<&Value>) -> String {
    let Some(rules) = rules.and_then(Value::as_object).filter(|r| !r.is_empty()) else {
        return String::new();
    };
    let list = |k: &str| -> Vec<String> {
        match rules.get(k) {
            Some(Value::Array(a)) => a.iter().map(py_str).collect(),
            _ => Vec::new(),
        }
    };
    let mut parts = vec!["## Project-Specific Rules".to_string()];
    let always = list("always_remember");
    if !always.is_empty() {
        parts.push("ALWAYS remember these types of information:".into());
        parts.extend(always.iter().map(|i| format!("  - {i}")));
    }
    let never = list("never_remember");
    if !never.is_empty() {
        parts.push("NEVER remember these types of information:".into());
        parts.extend(never.iter().map(|i| format!("  - {i}")));
    }
    if let Some(c) = rules
        .get("custom_instructions")
        .filter(|c| crate::text::truthy(c))
    {
        parts.push(format!("Additional instructions: {}", py_str(c)));
    }
    parts.join("\n")
}

/// Python `bool(v)`.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `_build_extraction_system_prompt` (no promotion fields: the promotion mode
/// is off in production, see the decision record).
pub fn extraction_system_prompt(source: &str, context: &str, rules: Option<&Value>) -> String {
    let template = if context == "pre_compact" {
        PROMPT_FACTS_AGGRESSIVE
    } else {
        PROMPT_FACTS
    };
    let project = match source.rsplit_once('/') {
        Some((_, last)) => last,
        None if source.is_empty() => "this",
        None => source,
    };
    let mut prompt = fill(template, &[("project", project)]);
    let rules = rules_section(rules);
    if !rules.is_empty() {
        prompt.push_str("\n\n");
        prompt.push_str(&rules);
    }
    prompt
}

/// One fact of stage 1.
#[derive(Clone, Debug, PartialEq)]
pub struct Fact {
    pub category: String,
    pub text: String,
}

impl Fact {
    pub fn to_json(&self) -> Value {
        json!({"category": self.category, "text": self.text})
    }
}

/// The facts of an extraction reply (the loop of `extract_facts`). An error
/// is the Python exception text; Python then gives no facts.
pub fn parse_facts(reply: &str, max_facts: usize, max_chars: usize) -> Result<Vec<Fact>, String> {
    let mut facts = Vec::new();
    for item in parse_json_array(reply) {
        match &item {
            Value::Object(m) if m.contains_key("text") => {
                let cat = match m.get("category") {
                    None => "detail".to_string(),
                    Some(Value::String(s)) => s.to_lowercase(),
                    Some(v) => {
                        return Err(format!("'{}' object has no attribute 'lower'", py_type(v)));
                    }
                };
                let cat = if matches!(cat.as_str(), "decision" | "learning" | "detail") {
                    cat
                } else {
                    "detail".into()
                };
                let text = clip_text(&py_str(&m["text"]), max_chars);
                if !text.is_empty() {
                    facts.push(Fact {
                        category: cat,
                        text,
                    });
                }
            }
            Value::String(s) if !py_strip(s).is_empty() => {
                let text = clip_text(s, max_chars);
                if !text.is_empty() {
                    facts.push(Fact {
                        category: "detail".into(),
                        text,
                    });
                }
            }
            _ => {}
        }
    }
    facts.truncate(max_facts);
    Ok(facts)
}

/// `float(m.get("rrf_score", m.get("similarity", 0.0)))`.
pub fn mem_score(m: &Value) -> f64 {
    let pick = if m.get("rrf_score").is_some() {
        &m["rrf_score"]
    } else {
        &m["similarity"]
    };
    pick.as_f64().unwrap_or(0.0)
}

fn py_round(x: f64, digits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.digits$}").parse().unwrap_or(x)
}

/// The AUDN user prompt of `run_audn`. `facts_clip` is `EXTRACT_MAX_FACT_CHARS`
/// (the value of the environment, not of the profile), `text_chars` is
/// `EXTRACT_SIMILAR_TEXT_CHARS` and `per_fact` is `EXTRACT_SIMILAR_PER_FACT`.
pub fn audn_prompt(
    facts: &[Fact],
    similar: &[Vec<Value>],
    rules: Option<&Value>,
    facts_clip: usize,
    text_chars: usize,
    per_fact: usize,
) -> String {
    let facts_json = format!(
        "[{}]",
        facts
            .iter()
            .enumerate()
            .map(|(i, f)| format!(
                "{{\"index\":{i},\"text\":{},\"category\":{}}}",
                py_json_str(&clip_text(&f.text, facts_clip)),
                py_json_str(&f.category)
            ))
            .collect::<Vec<_>>()
            .join(",")
    );
    let similar_json = format!(
        "{{{}}}",
        similar
            .iter()
            .enumerate()
            .map(|(i, mems)| {
                let items: Vec<String> = mems
                    .iter()
                    .take(per_fact)
                    .map(|m| {
                        format!(
                            "{{\"id\":{},\"text\":{},\"relevance\":{}}}",
                            py_json_compact(m.get("id").unwrap_or(&Value::Null)),
                            py_json_str(&clip_text(
                                &py_str(m.get("text").unwrap_or(&json!(""))),
                                text_chars
                            )),
                            py_float_repr(py_round(mem_score(m), 3))
                        )
                    })
                    .collect();
                format!("\"{i}\":[{}]", items.join(","))
            })
            .collect::<Vec<_>>()
            .join(",")
    );
    let mut prompt = fill(
        PROMPT_AUDN,
        &[("facts_json", &facts_json), ("similar_json", &similar_json)],
    );
    let rules = rules_section(rules);
    if !rules.is_empty() {
        prompt.push_str("\n\n");
        prompt.push_str(&rules);
    }
    prompt
}

/// The `debug_similar` entries of `run_audn` for one fact.
pub fn debug_similar(mems: &[Value], text_chars: usize, per_fact: usize) -> Vec<Value> {
    mems.iter()
        .take(per_fact)
        .map(|m| {
            let s = if m.get("similarity").is_some() {
                &m["similarity"]
            } else {
                &m["rrf_score"]
            };
            json!({
                "id": m.get("id").cloned().unwrap_or(Value::Null),
                "text": clip_text(&py_str(m.get("text").unwrap_or(&json!(""))), text_chars),
                "similarity": py_round(s.as_f64().unwrap_or(0.0), 4),
            })
        })
        .collect()
}

/// The decisions of an AUDN reply: each map with `action`, the action in
/// upper case. An error (an action that is not a string) is the Python
/// exception text; Python then gives a fallback add for each fact.
pub fn parse_decisions(reply: &str) -> Result<Vec<Map<String, Value>>, String> {
    let mut out = Vec::new();
    for d in parse_json_array(reply) {
        let Value::Object(mut m) = d else { continue };
        let Some(a) = m.get("action") else { continue };
        let Some(s) = a.as_str() else {
            return Err(format!("'{}' object has no attribute 'upper'", py_type(a)));
        };
        let up = s.to_uppercase();
        m.insert("action".into(), json!(up));
        out.push(m);
    }
    Ok(out)
}

/// The system and user prompts of `extract_and_decide_single_call`.
pub fn single_call_prompts(
    messages: &str,
    rules: Option<&Value>,
    max_facts: usize,
) -> (String, String) {
    let system = fill(
        PROMPT_SINGLE_CALL,
        &[("rules_section", &rules_section(rules))],
    );
    let head: String = messages.chars().take(max_facts * 500).collect();
    let user = format!("Extract and classify facts from this conversation:\n\n{head}");
    (system, user)
}

/// The actions of a single-call reply: at most `max_facts`, each with the
/// defaults of Python, and only ADD or NOOP. An error is the Python exception
/// text (an item that is not a map); the job then fails.
pub fn parse_single_call(reply: &str, max_facts: usize) -> Result<Vec<Map<String, Value>>, String> {
    let mut out = Vec::new();
    for (i, a) in parse_json_array(reply)
        .into_iter()
        .take(max_facts)
        .enumerate()
    {
        let Value::Object(mut m) = a else {
            return Err(format!(
                "'{}' object has no attribute 'setdefault'",
                py_type(&a)
            ));
        };
        m.entry("fact_index").or_insert(json!(i));
        m.entry("action").or_insert(json!("ADD"));
        m.entry("category").or_insert(json!("detail"));
        m.entry("text").or_insert(json!(""));
        if !matches!(m["action"].as_str(), Some("ADD" | "NOOP")) {
            m.insert("action".into(), json!("ADD"));
        }
        out.push(m);
    }
    Ok(out)
}
