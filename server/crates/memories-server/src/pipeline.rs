//! The extraction pipeline of `llm_extract.run_extraction` (v5.19.0): hygiene,
//! stage 1 facts, stage 2 AUDN decisions, stage 3 writes, the auto links, the
//! dry run, the debug trace, the single-call mode and the regex fallback.
//!
//! Not ported: project promotion (off in production; task P5-6), shadow
//! providers and `EXTRACT_TRAINING_DATA_DIR`.

use std::sync::Arc;

use memories_engine::Engine;
use memories_engine::policy::{
    TrustedAuthorship, is_reserved_namespace_source, redact_secrets, validate_project_write,
};
use memories_engine::rank::scores::py_round;
use memories_engine::scope::{Scope, source_matches_prefixes};
use memories_engine::search::SearchArgs;
use memories_engine::writes::{NewMemory, Patch, WriteError, Writer, source_of};
use memories_extract::provider::Provider;
use memories_extract::text::{
    self, AUDN_SYSTEM, Fact, FallbackConfig, clean_transcript, py_str, py_type, truthy,
};
use memories_types::config;
use serde_json::{Map, Value, json};

use crate::AppState;
use crate::extract::{Pipeline, PipelineError, PipelineFuture};
use crate::keys::uuid4_hex;

/// The settings of the pipeline, with the Python names.
#[derive(Clone, Debug)]
pub struct PipelineConfig {
    /// `EXTRACT_MAX_FACT_CHARS`: the fact clip of the AUDN prompt.
    pub max_fact_chars: usize,
    pub similar_text_chars: usize,
    pub similar_per_fact: usize,
    pub max_links: usize,
    pub min_link_score: f64,
    pub novelty_gate: bool,
    pub novelty_threshold: f64,
    pub redact: bool,
    pub fallback: FallbackConfig,
    /// `EXTRACT_FOREIGN_TARGET_ADD` (Rust only, default on): an UPDATE or
    /// CONFLICT whose target has another source adds the fact as new. Python
    /// gives an error action, and the fact is lost.
    pub foreign_add: bool,
}

impl Default for PipelineConfig {
    fn default() -> Self {
        Self::from_lookup(|_| None)
    }
}

/// Python `_env_float`.
fn env_float(raw: Option<String>, default: f64, minimum: f64) -> f64 {
    let raw = raw.unwrap_or_default();
    let raw = raw.trim();
    if raw.is_empty() {
        return default.max(minimum);
    }
    raw.parse::<f64>().unwrap_or(default).max(minimum)
}

impl PipelineConfig {
    pub fn from_env() -> Self {
        Self::from_lookup(|n| std::env::var(n).ok())
    }

    pub fn from_lookup(var: impl Fn(&str) -> Option<String>) -> Self {
        let int = |name: &str, default: i64, minimum: i64| {
            let (v, warn) = config::parse_int(var(name).as_deref(), default, minimum);
            if let Some(w) = warn {
                tracing::warn!("{name}: {w}");
            }
            v as usize
        };
        let lower = |name: &str| var(name).unwrap_or_default().trim().to_lowercase();
        let gate = lower("EXTRACT_NOVELTY_GATE");
        let threshold = var("EXTRACT_NOVELTY_THRESHOLD").unwrap_or_default();
        let threshold = threshold.trim();
        Self {
            max_fact_chars: int("EXTRACT_MAX_FACT_CHARS", 500, 40),
            similar_text_chars: int("EXTRACT_SIMILAR_TEXT_CHARS", 280, 40),
            similar_per_fact: int("EXTRACT_SIMILAR_PER_FACT", 5, 1),
            max_links: int("EXTRACT_MAX_LINKS", 3, 0),
            min_link_score: env_float(var("EXTRACT_MIN_LINK_SCORE"), 0.005, 0.0),
            novelty_gate: gate.is_empty() || !matches!(gate.as_str(), "0" | "false" | "no" | "off"),
            novelty_threshold: if threshold.is_empty() {
                0.85
            } else {
                threshold.parse().unwrap_or(0.85)
            },
            redact: var("EXTRACT_REDACT_SECRETS")
                .is_none_or(|v| !matches!(v.trim().to_lowercase().as_str(), "0" | "false" | "no")),
            fallback: fallback_config(&var),
            foreign_add: config::parse_bool(var("EXTRACT_FOREIGN_TARGET_ADD").as_deref(), true),
        }
    }
}

/// The `EXTRACT_FALLBACK_*` settings of `app.py`.
pub fn fallback_config(var: &impl Fn(&str) -> Option<String>) -> FallbackConfig {
    let int = |name: &str, default: i64, minimum: i64| {
        config::parse_int(var(name).as_deref(), default, minimum).0 as usize
    };
    FallbackConfig {
        enabled: config::parse_bool(var("EXTRACT_FALLBACK_ADD").as_deref(), false),
        max_facts: int("EXTRACT_FALLBACK_MAX_FACTS", 1, 1),
        min_chars: int("EXTRACT_FALLBACK_MIN_FACT_CHARS", 24, 5),
        max_chars: int("EXTRACT_FALLBACK_MAX_FACT_CHARS", 280, 32),
        novelty_threshold: env_float(var("EXTRACT_FALLBACK_NOVELTY_THRESHOLD"), 0.88, 0.0).min(1.0),
    }
}

// ---- Errors --------------------------------------------------------------------

/// An error of `execute_actions` that stops the whole call.
#[derive(Debug)]
pub enum ExecError {
    /// `ProjectMemoryPolicyError`: the commit route answers 422.
    Policy(String),
    /// Another exception outside the per-action `try` (Python: HTTP 500).
    Internal(String),
}

impl From<ExecError> for PipelineError {
    fn from(e: ExecError) -> Self {
        match e {
            ExecError::Policy(m) => PipelineError::Fatal {
                result: Some(json!({"error": "project_policy", "detail": m})),
                error: m,
            },
            ExecError::Internal(m) => PipelineError::Fatal {
                error: m,
                result: None,
            },
        }
    }
}

/// A failed write: a policy error stops the call; another error is the text
/// of an `error` action.
fn split(e: WriteError) -> Result<String, ExecError> {
    match e {
        WriteError::Policy(p) => Err(ExecError::Policy(p.0)),
        e => Ok(e.to_string()),
    }
}

fn engine_retry(e: impl std::fmt::Display) -> PipelineError {
    PipelineError::Retry(e.to_string())
}

// ---- Shared helpers ------------------------------------------------------------

fn get<'a>(m: &'a Map<String, Value>, k: &str) -> &'a Value {
    m.get(k).unwrap_or(&Value::Null)
}

/// The text of a value as Python passes it on: a string, else `str(v)`.
fn as_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        v => py_str(v),
    }
}

fn src_of(v: &Value) -> String {
    match v.get("source") {
        None => String::new(),
        Some(s) => as_text(s),
    }
}

/// A memory ID from a decision. Python looks the value up in a dict, so an
/// integral float finds the record too.
fn record_id(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().filter(|f| f.fract() == 0.0).map(|f| f as i64)),
        _ => None,
    }
}

/// A `fact_index` as a Python dict key: `True` finds key 1, `1.0` finds key 1.
fn index_key(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(b) => Some(*b as i64),
        v => record_id(v),
    }
}

/// `facts[fact_index]` with the Python range check.
fn fact_index(v: Option<&Value>) -> Result<i64, ExecError> {
    match v {
        None => Ok(-1),
        Some(Value::Bool(b)) => Ok(*b as i64),
        Some(Value::Number(n)) if n.is_i64() || n.is_u64() => Ok(n.as_i64().unwrap_or(i64::MAX)),
        Some(Value::Number(_)) => Err(ExecError::Internal(
            "list indices must be integers or slices, not float".into(),
        )),
        Some(v) => Err(ExecError::Internal(format!(
            "'<=' not supported between instances of 'int' and '{}'",
            py_type(v)
        ))),
    }
}

fn fact_at(facts: &[Value], fi: i64) -> Option<&Value> {
    usize::try_from(fi).ok().and_then(|i| facts.get(i))
}

fn trusted_of(v: &Value) -> Option<TrustedAuthorship> {
    let author = v.get("author")?.as_str()?;
    TrustedAuthorship::principal(author, v["origin_client"].as_str().unwrap_or(""))
}

/// `is_novel` with `allowed_source_prefixes` and `exclude_reserved_sources`:
/// one hit, no reinforcement, then the policy filter.
async fn policy_novel(
    engine: &Engine,
    text: &str,
    threshold: f64,
    allowed: Option<&[String]>,
) -> Result<(bool, Option<Value>), WriteError> {
    let args = SearchArgs {
        query: text.to_owned(),
        k: 1,
        scope: Scope {
            allowed_prefixes: allowed.map(<[String]>::to_vec),
            exclude_reserved: true,
            ..Scope::default()
        },
        ..SearchArgs::default()
    };
    let top = engine.search(&args, false).await?.into_iter().find(|r| {
        let s = src_of(r);
        !is_reserved_namespace_source(&s) && source_matches_prefixes(&s, allowed)
    });
    Ok(match top {
        None => (true, None),
        Some(t) => (t["similarity"].as_f64().unwrap_or(0.0) < threshold, Some(t)),
    })
}

/// The `is_novel` call of the gate and of the fallback: the exact source for a
/// reserved source, else the policy scope.
async fn novel(
    w: &Writer,
    text: &str,
    threshold: f64,
    source: &str,
    allowed: Option<&[String]>,
) -> Result<(bool, Option<Value>), WriteError> {
    if is_reserved_namespace_source(source) {
        w.is_novel(text, threshold, Some(source)).await
    } else {
        policy_novel(&w.engine, text, threshold, allowed).await
    }
}

// ---- Stage 3: execute ----------------------------------------------------------

/// The inputs of `execute_actions` other than the actions and the facts.
pub struct Exec<'a> {
    pub writer: &'a Writer,
    pub source: &'a str,
    pub allowed: Option<&'a [String]>,
    pub trusted: Option<&'a TrustedAuthorship>,
    pub job_id: Option<&'a str>,
    pub document_at: Option<&'a str>,
    /// `novelty_gate and EXTRACT_NOVELTY_GATE`.
    pub gate: bool,
    pub threshold: f64,
    /// [`PipelineConfig::foreign_add`].
    pub foreign_add: bool,
}

impl Exec<'_> {
    /// `_novelty_gate_check`: (gated, similar). Any error is "not gated".
    async fn gate_check(&self, text: &str) -> (bool, Option<Value>) {
        let reserved = is_reserved_namespace_source(self.source);
        let (is_new, similar) =
            match novel(self.writer, text, self.threshold, self.source, self.allowed).await {
                Ok(r) => r,
                Err(e) => {
                    tracing::warn!("Novelty gate check failed (fail-open): {e}");
                    return (false, None);
                }
            };
        if is_new {
            return (false, None);
        }
        let sim_source = similar.as_ref().map(src_of);
        if !reserved
            && sim_source
                .as_deref()
                .is_some_and(is_reserved_namespace_source)
        {
            return (false, None);
        }
        if self.allowed.is_some()
            && !sim_source
                .as_deref()
                .is_some_and(|s| source_matches_prefixes(s, self.allowed))
        {
            return (false, None);
        }
        if reserved && !self.source.is_empty() && sim_source.as_deref() != Some(self.source) {
            return (false, None);
        }
        (true, similar)
    }

    fn meta(&self, fact: &Value) -> Map<String, Value> {
        let mut m = Map::new();
        if let Some(f) = fact.as_object() {
            m.insert(
                "category".into(),
                f.get("category").cloned().unwrap_or(json!("detail")),
            );
        }
        if let Some(j) = self.job_id {
            m.insert("extraction_job_id".into(), json!(j));
            m.insert("extract_source".into(), json!(self.source));
        }
        if let Some(d) = self.document_at.filter(|d| !d.is_empty()) {
            m.insert("document_at".into(), json!(d));
        }
        m
    }

    async fn add(
        &self,
        text: &str,
        meta: Map<String, Value>,
        dedup: bool,
    ) -> Result<Option<u64>, WriteError> {
        let item = NewMemory {
            text: text.to_owned(),
            source: self.source.to_owned(),
            metadata: Some(meta),
        };
        let ids = self
            .writer
            .add_memories(vec![item], dedup, self.trusted)
            .await?;
        Ok(ids.first().copied())
    }

    fn perm(&self, what: &str, old_id: &Value) -> WriteError {
        WriteError::Invalid(format!(
            "old_id not authorized for {what}: {}",
            py_str(old_id)
        ))
    }

    /// The record of `old_id` and its source, after the same-source check.
    /// `Ok(None)`: the record is pinned or archived (`protected`).
    async fn existing(&self, what: &str, old_id: &Value) -> Result<Option<String>, WriteError> {
        let rec = match record_id(old_id) {
            Some(id) => self.writer.get(id).await?,
            None => {
                return Err(WriteError::NotFound(format!(
                    "Memory ID {} not found",
                    py_str(old_id)
                )));
            }
        };
        let src = source_of(&rec);
        if src != self.source {
            return Err(self.perm(what, old_id));
        }
        if what != "conflict"
            && (rec.field("pinned") == Some(json!(true))
                || rec.field("archived") == Some(json!(true)))
        {
            return Ok(None);
        }
        if self.allowed.is_some() && !source_matches_prefixes(&src, self.allowed) {
            return Err(self.perm(what, old_id));
        }
        Ok(Some(src))
    }

    /// With [`Self::foreign_add`]: an UPDATE or CONFLICT whose `old_id` has
    /// another source, which [`Self::existing`] refuses.
    async fn foreign(&self, act: &str, old_id: &Value) -> bool {
        if !self.foreign_add || !matches!(act, "UPDATE" | "CONFLICT") {
            return false;
        }
        let Some(id) = record_id(old_id) else {
            return false;
        };
        self.writer
            .get(id)
            .await
            .is_ok_and(|rec| source_of(&rec) != self.source)
    }

    fn source_denied(&self, what: &str) -> Option<WriteError> {
        (!source_matches_prefixes(self.source, self.allowed)).then(|| {
            WriteError::Invalid(format!("source not authorized for {what}: {}", self.source))
        })
    }

    /// One action. `Err` is the text of an `error` action.
    async fn one(
        &self,
        act: &str,
        action: &Map<String, Value>,
        fact: &Value,
        fact_text: &str,
        out: &mut Counts,
    ) -> Result<(), WriteError> {
        let old_id = get(action, "old_id");
        // Keep the fact text: `new_text` can hold the words of the other source.
        let foreign = self.foreign(act, old_id).await;
        let act = if foreign { "FALLBACK_ADD" } else { act };
        match act {
            "ADD" | "FALLBACK_ADD" => {
                if let Some(e) = self.source_denied("add") {
                    return Err(e);
                }
                if self.gate {
                    let (gated, similar) = self.gate_check(fact_text).await;
                    if gated {
                        let s = similar.unwrap_or_else(|| json!({}));
                        out.actions.push(json!({
                            "action": "noop",
                            "text": fact_text,
                            "reason": "novelty_gate",
                            "existing_id": s.get("id").cloned().unwrap_or(Value::Null),
                            "similarity": s.get("similarity").cloned().unwrap_or(Value::Null),
                        }));
                        out.gated += 1;
                        return Ok(());
                    }
                }
                let id = self.add(fact_text, self.meta(fact), true).await?;
                let name = if act == "FALLBACK_ADD" {
                    "fallback_add"
                } else {
                    "add"
                };
                let mut a = json!({"action": name, "text": fact_text, "id": id});
                if foreign {
                    a["foreign_old_id"] = old_id.clone();
                }
                out.actions.push(a);
                out.stored += 1;
                // `fallback_count` stays the count of AUDN failures.
                if act == "FALLBACK_ADD" && !foreign {
                    out.fallback += 1;
                }
            }
            "UPDATE" => {
                if !old_id.is_null() && self.existing("update", old_id).await?.is_none() {
                    out.actions.push(
                        json!({"action": "skipped", "reason": "protected", "old_id": old_id}),
                    );
                    return Ok(());
                }
                let new_text = action
                    .get("new_text")
                    .map_or_else(|| fact_text.to_owned(), as_text);
                if let Some(e) = self.source_denied("update") {
                    return Err(e);
                }
                if let Some(id) = record_id(old_id) {
                    validate_project_write(&new_text, self.source, self.trusted)?;
                    let mut patch = Map::new();
                    patch.insert("is_latest".into(), json!(false));
                    let archive = Patch {
                        archived: Some(true),
                        metadata_patch: Some(patch),
                        ..Patch::default()
                    };
                    self.writer
                        .update_memory(id, archive, self.trusted, false)
                        .await?;
                }
                let mut meta = self.meta(fact);
                meta.insert("supersedes".into(), old_id.clone());
                meta.insert("is_latest".into(), json!(true));
                let new_id = self.add(&new_text, meta, false).await?;
                if let (Some(n), Some(o)) = (new_id.filter(|n| *n != 0), record_id(old_id))
                    && let Err(e) = self.writer.add_link(n as i64, o, "supersedes").await
                {
                    tracing::debug!("supersedes link {n} -> {o} skipped: {e}");
                }
                out.actions.push(json!({
                    "action": "update", "old_id": old_id, "text": new_text, "new_id": new_id,
                }));
                out.updated += 1;
            }
            "DELETE" => {
                if old_id.is_null() {
                    return Ok(());
                }
                if self.existing("delete", old_id).await?.is_none() {
                    out.actions.push(
                        json!({"action": "skipped", "reason": "protected", "old_id": old_id}),
                    );
                    return Ok(());
                }
                if let Some(id) = record_id(old_id) {
                    self.writer.delete_memory(id, false).await?;
                }
                out.actions
                    .push(json!({"action": "delete", "old_id": old_id}));
                out.deleted += 1;
            }
            "CONFLICT" => {
                if let Some(e) = self.source_denied("add") {
                    return Err(e);
                }
                let mut meta = self.meta(fact);
                if !old_id.is_null() {
                    self.existing("conflict", old_id).await?;
                    meta.insert("conflicts_with".into(), old_id.clone());
                }
                let id = self.add(fact_text, meta, false).await?;
                out.actions.push(json!({
                    "action": "conflict", "text": fact_text, "id": id, "conflicts_with": old_id,
                }));
                out.stored += 1;
                out.conflict += 1;
            }
            "NOOP" => out.actions.push(json!({
                "action": "noop",
                "text": fact_text,
                "existing_id": get(action, "existing_id"),
            })),
            _ => {}
        }
        Ok(())
    }

    /// `execute_actions`: one result action for each input action that it
    /// handles. A policy error stops the call before any write when the
    /// pre-check finds it.
    pub async fn run(
        &self,
        actions: &[Map<String, Value>],
        facts: &[Value],
    ) -> Result<Value, ExecError> {
        let upper = |a: &Map<String, Value>| match a.get("action") {
            None => Ok(String::new()),
            Some(Value::String(s)) => Ok(s.to_uppercase()),
            Some(v) => Err(ExecError::Internal(format!(
                "'{}' object has no attribute 'upper'",
                py_type(v)
            ))),
        };
        for action in actions {
            let act = upper(action)?;
            if !matches!(act.as_str(), "ADD" | "FALLBACK_ADD" | "UPDATE" | "CONFLICT") {
                continue;
            }
            let fi = fact_index(action.get("fact_index"))?;
            let fact_text = match fact_at(facts, fi) {
                Some(Value::Object(f)) => f.get("text").map(as_text).unwrap_or_default(),
                Some(f) => py_str(f),
                None => String::new(),
            };
            // A foreign target writes the fact text, never `new_text`.
            let candidate = match (act.as_str(), action.get("new_text")) {
                ("UPDATE", Some(t)) if !self.foreign(&act, get(action, "old_id")).await => {
                    as_text(t)
                }
                _ => fact_text,
            };
            validate_project_write(&candidate, self.source, self.trusted)
                .map_err(|p| ExecError::Policy(p.0))?;
        }
        let default_fact = json!({"text": "", "category": "detail"});
        let mut out = Counts::default();
        for action in actions {
            let act = upper(action)?;
            let fi = fact_index(action.get("fact_index"))?;
            let fact = fact_at(facts, fi).unwrap_or(&default_fact);
            let fact_text = match fact {
                Value::Object(f) => match f.get("text") {
                    Some(t) => as_text(t),
                    None => return Err(ExecError::Internal("'text'".into())),
                },
                f => py_str(f),
            };
            if let Err(e) = self.one(&act, action, fact, &fact_text, &mut out).await {
                let msg = split(e)?;
                tracing::error!("Failed to execute {act} for fact: {msg}");
                out.actions
                    .push(json!({"action": "error", "text": fact_text, "error": msg}));
            }
        }
        if out.gated > 0 {
            tracing::info!(
                "Novelty gate suppressed {} near-duplicate add(s)",
                out.gated
            );
        }
        Ok(json!({
            "actions": out.actions,
            "stored_count": out.stored,
            "updated_count": out.updated,
            "deleted_count": out.deleted,
            "conflict_count": out.conflict,
            "fallback_count": out.fallback,
            "gated_count": out.gated,
            "promotion_candidates": [],
        }))
    }
}

#[derive(Default)]
struct Counts {
    actions: Vec<Value>,
    stored: u64,
    updated: u64,
    deleted: u64,
    conflict: u64,
    fallback: u64,
    gated: u64,
}

// ---- Regex fallback (app.py) ---------------------------------------------------

/// `_run_fallback_extraction`: add-only facts from the regex extractor.
pub async fn run_fallback(
    w: &Writer,
    cfg: &FallbackConfig,
    messages: &str,
    source: &str,
    context: &str,
    allowed: Option<&[String]>,
    trusted: Option<&TrustedAuthorship>,
) -> Result<Value, WriteError> {
    let facts = text::fallback_facts(&clean_transcript(messages), cfg);
    let refused = |error: &str| {
        json!({
            "actions": [], "extracted_count": facts.len(), "stored_count": 0,
            "updated_count": 0, "deleted_count": 0, "mode": "fallback_add", "error": error,
        })
    };
    if allowed.is_some() && source.is_empty() {
        return Ok(refused("source_required"));
    }
    let source = if source.is_empty() {
        "extract/fallback"
    } else {
        source
    };
    if allowed.is_some() && !source_matches_prefixes(source, allowed) {
        return Ok(refused("source_not_authorized"));
    }
    let mut actions = Vec::new();
    let mut stored = 0;
    for fact in &facts {
        let (is_new, similar) = novel(w, fact, cfg.novelty_threshold, source, allowed).await?;
        if is_new {
            let mut meta = Map::new();
            meta.insert("extraction_mode".into(), json!("fallback_add"));
            meta.insert("context".into(), json!(context));
            let item = NewMemory {
                text: fact.clone(),
                source: source.to_owned(),
                metadata: Some(meta),
            };
            if let Some(id) = w.add_memories(vec![item], false, trusted).await?.first() {
                stored += 1;
                actions
                    .push(json!({"action": "add", "text": fact, "id": id, "mode": "fallback_add"}));
            }
        } else {
            let s = similar.unwrap_or(Value::Null);
            actions.push(json!({
                "action": "noop", "text": fact, "mode": "fallback_add",
                "matched_id": s.get("id").cloned().unwrap_or(Value::Null),
                "similarity": s.get("similarity").cloned().unwrap_or(Value::Null),
            }));
        }
    }
    let noops = actions.iter().filter(|a| a["action"] == "noop").count();
    Ok(json!({
        "actions": actions,
        "extracted_count": facts.len(),
        "stored_count": stored,
        "updated_count": 0,
        "deleted_count": 0,
        "noop_count": noops,
        "mode": "fallback_add",
    }))
}

/// `_merge_runtime_fallback_result`.
fn merge_fallback(primary: &Value, mut fallback: Value) -> Value {
    fallback["fallback_triggered"] = json!(true);
    fallback["fallback_reason"] = primary["error"].clone();
    for (from, to) in [
        ("error_stage", "fallback_source_stage"),
        ("error_message", "primary_error"),
    ] {
        if primary.get(from).is_some_and(truthy) {
            fallback[to] = primary[from].clone();
        }
    }
    fallback
}

// ---- Maintenance ---------------------------------------------------------------

/// `_apply_maintenance`: the `related_to` links and the compaction candidates.
async fn maintenance(
    w: &Writer,
    cfg: &PipelineConfig,
    decisions: &[Map<String, Value>],
    result_actions: &[Value],
    similar: &[Vec<Value>],
    source: &str,
) -> (Vec<Value>, Vec<Value>) {
    let score = text::mem_score;
    // Python compares with `==`, so a deleted `5.0` matches the record 5.
    let deleted: Vec<i64> = result_actions
        .iter()
        .filter(|a| a["action"] == "delete")
        .filter_map(|a| record_id(&a["old_id"]))
        .collect();
    let gone = |m: &Value| record_id(&m["id"]).is_some_and(|id| deleted.contains(&id));
    let mut links = Vec::new();
    let mut max_links = cfg.max_links;
    if max_links > 0 && decisions.len() != result_actions.len() {
        tracing::error!(
            "decisions/actions length mismatch: {} vs {}; skipping auto-linking",
            decisions.len(),
            result_actions.len()
        );
        max_links = 0;
    }
    if max_links > 0 {
        for (d, ra) in decisions.iter().zip(result_actions) {
            if !matches!(
                ra["action"].as_str(),
                Some("add" | "fallback_add" | "conflict")
            ) {
                continue;
            }
            let Some(new_id) = ra["id"].as_i64() else {
                continue;
            };
            let fi = match d.get("fact_index") {
                None => -1,
                Some(v) => index_key(v).unwrap_or(-1),
            };
            let mems = usize::try_from(fi).ok().and_then(|i| similar.get(i));
            let mut scored: Vec<&Value> = mems
                .into_iter()
                .flatten()
                .filter(|m| {
                    score(m) >= cfg.min_link_score
                        && !m["id"].is_null()
                        && src_of(m) == source
                        && !gone(m)
                        && m["id"].as_i64() != Some(new_id)
                })
                .collect();
            scored.sort_by(|a, b| score(b).total_cmp(&score(a)));
            for t in scored.into_iter().take(max_links) {
                let Some(to) = t["id"].as_i64() else { continue };
                match w.add_link(new_id, to, "related_to").await {
                    Ok(_) => links.push(json!({
                        "from_id": new_id, "to_id": to, "rrf_score": py_round(score(t), 6),
                    })),
                    Err(e) => tracing::warn!("Auto-link {new_id} -> {to} skipped: {e}"),
                }
            }
        }
    }
    if !links.is_empty() {
        tracing::info!("Auto-linked {} edges during extraction", links.len());
    }
    let mut candidates = Vec::new();
    for (fi, mems) in similar.iter().enumerate() {
        if mems.len() < 3 {
            continue;
        }
        let mut scores: Vec<(&Value, f64, String)> = mems
            .iter()
            .filter(|m| !m["id"].is_null() && src_of(m) == source && !gone(m))
            .map(|m| (&m["id"], score(m), src_of(m)))
            .collect();
        if scores.len() < 3 {
            continue;
        }
        // Python `sort(reverse=True)` is stable: equal scores keep their order.
        scores.sort_by(|a, b| b.1.total_cmp(&a.1));
        let mut best: &[(&Value, f64, String)] = &[];
        for start in 0..scores.len() - 2 {
            let mut end = start + 1;
            while end < scores.len()
                && scores[start].1 > 0.0
                && scores[end].1 / scores[start].1 >= 0.8
            {
                end += 1;
            }
            if end - start >= 3 && end - start > best.len() {
                best = &scores[start..end];
            }
        }
        if best.len() >= 3 {
            let mut sources: Vec<String> = best.iter().map(|s| s.2.clone()).collect();
            sources.sort();
            sources.dedup();
            let mut families: Vec<&str> = sources
                .iter()
                .filter(|s| s.contains('/'))
                .map(|s| s.split('/').next().unwrap_or(""))
                .collect();
            families.sort_unstable();
            families.dedup();
            let avg = best.iter().map(|s| s.1).sum::<f64>() / best.len() as f64;
            candidates.push(json!({
                "fact_index": fi,
                "memory_ids": best.iter().map(|s| s.0.clone()).collect::<Vec<_>>(),
                "avg_rrf_score": py_round(avg, 6),
                "sources": sources,
                "cross_source": families.len() > 1,
            }));
        }
    }
    (links, candidates)
}

// ---- The pipeline --------------------------------------------------------------

/// The stage tokens of Python: `{"input": n, "output": n}`.
fn tokens(input: u64, output: u64) -> Value {
    json!({"input": input, "output": output})
}

pub struct ExtractPipeline {
    pub provider: Arc<Provider>,
    pub cfg: PipelineConfig,
}

impl Pipeline for ExtractPipeline {
    fn run(&self, state: Arc<AppState>, request: Value) -> PipelineFuture {
        let (provider, cfg) = (self.provider.clone(), self.cfg.clone());
        Box::pin(async move { run_job(&state, &provider, &cfg, &request).await })
    }
}

/// One queued job: `run_extraction`, then the runtime fallback of the worker.
async fn run_job(
    state: &AppState,
    provider: &Provider,
    cfg: &PipelineConfig,
    request: &Value,
) -> Result<Value, PipelineError> {
    let source = request["source"].as_str().unwrap_or("");
    let allowed: Option<Vec<String>> = request["allowed_prefixes"].as_array().map(|a| {
        a.iter()
            .filter_map(|p| p.as_str().map(str::to_owned))
            .collect()
    });
    let profile = match request.get("profile").and_then(Value::as_object) {
        Some(p) => p.clone(),
        None => {
            let effective = if source.is_empty() {
                "extract/unknown"
            } else {
                source
            };
            // A job queued by an older binary has `dry_run` only at the top level.
            let mut p = state.extract.profiles.lock().unwrap().resolve(effective);
            if truthy(&request["dry_run"]) {
                p.insert("dry_run".into(), json!(true));
            }
            p
        }
    };
    let job = Job {
        w: state.writer.get().cloned(),
        provider,
        cfg,
        source,
        context: request["context"].as_str().unwrap_or("stop"),
        allowed: allowed.as_deref(),
        trusted: trusted_of(&request["trusted_authorship"]),
        debug: truthy(&request["debug"]),
        document_at: request["document_at"].as_str(),
        profile: &profile,
    };
    let messages = request["messages"].as_str().unwrap_or("");
    let result = job.extraction(messages).await?;
    let dry_run = profile.get("dry_run").is_some_and(truthy);
    if cfg.fallback.enabled && result["error"] == "provider_runtime_failure" && !dry_run {
        let fb = run_fallback(
            job.w()?,
            &cfg.fallback,
            messages,
            source,
            job.context,
            job.allowed,
            job.trusted.as_ref(),
        )
        .await
        .map_err(write_failed)?;
        tracing::info!(
            "Extract runtime fallback completed: source={source} extracted={} stored={}",
            fb["extracted_count"],
            fb["stored_count"]
        );
        return Ok(merge_fallback(&result, fb));
    }
    Ok(result)
}

/// A write error that ends a try: a policy error fails the job at once.
pub fn write_failed(e: WriteError) -> PipelineError {
    match split(e) {
        Err(x) => x.into(),
        Ok(m) => PipelineError::Retry(m),
    }
}

struct Job<'a> {
    /// Not set until the store opens: a job that needs no write still ends.
    w: Option<Arc<Writer>>,
    provider: &'a Provider,
    cfg: &'a PipelineConfig,
    source: &'a str,
    context: &'a str,
    allowed: Option<&'a [String]>,
    trusted: Option<TrustedAuthorship>,
    debug: bool,
    document_at: Option<&'a str>,
    profile: &'a Map<String, Value>,
}

fn int_of(m: &Map<String, Value>, k: &str, default: usize) -> usize {
    m.get(k)
        .and_then(Value::as_u64)
        .map_or(default, |v| v as usize)
}

impl Job<'_> {
    fn w(&self) -> Result<&Writer, PipelineError> {
        self.w
            .as_deref()
            .ok_or_else(|| PipelineError::Retry("the memory store is not ready".into()))
    }

    fn exec<'a>(&'a self, w: &'a Writer, job_id: &'a str) -> Exec<'a> {
        Exec {
            writer: w,
            source: self.source,
            allowed: self.allowed,
            trusted: self.trusted.as_ref(),
            job_id: Some(job_id),
            document_at: self.document_at,
            gate: self.cfg.novelty_gate,
            threshold: self.cfg.novelty_threshold,
            foreign_add: self.cfg.foreign_add,
        }
    }

    /// `run_extraction`.
    async fn extraction(&self, raw: &str) -> Result<Value, PipelineError> {
        let mut messages = clean_transcript(raw);
        if self.cfg.redact {
            let (clean, kinds) = redact_secrets(&messages);
            if !kinds.is_empty() {
                tracing::info!(
                    "Redacted credential-shaped content before extraction: {}",
                    kinds.join(", ")
                );
            }
            messages = clean;
        }
        let zero = || json!({"extract": tokens(0, 0), "audn": tokens(0, 0)});
        if messages.is_empty() {
            return Ok(json!({
                "actions": [], "extracted_count": 0, "stored_count": 0, "updated_count": 0,
                "deleted_count": 0, "skipped_reason": "empty_after_hygiene", "tokens": zero(),
                "links_created": [], "compaction_candidates": [], "promotion_candidates": [],
            }));
        }
        let job_id: String = uuid4_hex().chars().take(12).collect();
        let p = self.profile;
        let max_facts = int_of(p, "max_facts", 30);
        let max_chars = int_of(p, "max_fact_chars", 500);
        let context = if p.get("mode").and_then(Value::as_str) == Some("aggressive") {
            "pre_compact"
        } else {
            self.context
        };
        let rules = p.get("rules");
        if p.get("single_call").is_some_and(truthy) {
            return self.single_call(&messages, rules, max_facts, &job_id).await;
        }

        // Stage 1: facts.
        let system = text::extraction_system_prompt(self.source, context, rules);
        let (facts, extract_tokens) = match self.provider.complete(&system, &messages).await {
            Err(e) => (Err(e.to_string()), tokens(0, 0)),
            Ok(c) => (
                text::parse_facts(&c.text, max_facts, max_chars),
                tokens(c.input_tokens, c.output_tokens),
            ),
        };
        let facts = match facts {
            Ok(f) => f,
            Err(e) => {
                tracing::error!("Fact extraction failed: {e}");
                return Ok(json!({
                    "actions": [], "extracted_count": 0, "stored_count": 0, "updated_count": 0,
                    "deleted_count": 0, "error": "provider_runtime_failure",
                    "error_stage": "extract_facts", "error_message": e,
                    "tokens": {"extract": extract_tokens, "audn": tokens(0, 0)},
                    "promotion_candidates": [],
                }));
            }
        };
        tracing::info!("Extracted {} facts (context={context})", facts.len());
        if facts.is_empty() {
            return Ok(json!({
                "actions": [], "extracted_count": 0, "stored_count": 0, "updated_count": 0,
                "deleted_count": 0, "tokens": {"extract": extract_tokens, "audn": tokens(0, 0)},
                "promotion_candidates": [],
            }));
        }

        // Stage 2: AUDN.
        let w = self.w()?;
        let similar = self.similar(w, &facts).await;
        let (decisions, audn_tokens) = self.audn(&facts, &similar, rules).await;
        let fact_values: Vec<Value> = facts.iter().map(Fact::to_json).collect();

        if p.get("dry_run").is_some_and(truthy) {
            let annotated: Vec<Value> = decisions
                .iter()
                .map(|d| {
                    let mut e = d.clone();
                    if let Ok(fi) = fact_index(d.get("fact_index"))
                        && let Some(f) = fact_at(&fact_values, fi)
                    {
                        e.insert("fact".into(), f.clone());
                    }
                    Value::Object(e)
                })
                .collect();
            return Ok(json!({
                "dry_run": true, "actions": annotated, "extracted_count": facts.len(),
                "tokens": {"extract": extract_tokens, "audn": audn_tokens},
                "links_created": [], "compaction_candidates": [], "promotion_candidates": [],
            }));
        }

        // Stage 3: writes.
        let mut result = self.exec(w, &job_id).run(&decisions, &fact_values).await?;
        result["extracted_count"] = json!(facts.len());
        result["tokens"] = json!({"extract": extract_tokens, "audn": audn_tokens});
        result["job_id"] = json!(job_id);
        let actions = result["actions"].as_array().cloned().unwrap_or_default();
        let (links, candidates) =
            maintenance(w, self.cfg, &decisions, &actions, &similar, self.source).await;
        result["links_created"] = json!(links);
        result["compaction_candidates"] = json!(candidates);
        if self.debug {
            result["debug_trace"] = self.debug_trace(&facts, &decisions, &actions, &similar);
        }
        tracing::info!(
            "Extraction complete: {} extracted, {} stored, {} updated, {} deleted",
            facts.len(),
            result["stored_count"],
            result["updated_count"],
            result["deleted_count"]
        );
        Ok(result)
    }

    /// `extract_and_decide_single_call`, then the writes. A provider error
    /// uses a try of the job; Python fails the job at once.
    async fn single_call(
        &self,
        messages: &str,
        rules: Option<&Value>,
        max_facts: usize,
        job_id: &str,
    ) -> Result<Value, PipelineError> {
        let (system, user) = text::single_call_prompts(messages, rules, max_facts);
        let c = self
            .provider
            .complete(&system, &user)
            .await
            .map_err(engine_retry)?;
        let actions =
            text::parse_single_call(&c.text, max_facts).map_err(|e| PipelineError::Fatal {
                error: e,
                result: None,
            })?;
        let facts: Vec<Value> = actions
            .iter()
            .map(|a| json!({"text": get(a, "text"), "category": get(a, "category")}))
            .collect();
        let mut result = self.exec(self.w()?, job_id).run(&actions, &facts).await?;
        result["tokens"] = json!({"single_call": {
            "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
        }});
        result["job_id"] = json!(job_id);
        result["links_created"] = json!([]);
        result["compaction_candidates"] = json!([]);
        Ok(result)
    }

    /// The similar memories of each fact for the AUDN prompt. A failed search
    /// gives no memories for that fact.
    async fn similar(&self, w: &Writer, facts: &[Fact]) -> Vec<Vec<Value>> {
        let reserved = is_reserved_namespace_source(self.source);
        let exact = (reserved && !self.source.is_empty()).then(|| self.source.to_owned());
        let mut out = Vec::with_capacity(facts.len());
        for f in facts {
            let args = SearchArgs {
                query: f.text.clone(),
                k: self.cfg.similar_per_fact,
                scope: Scope {
                    source_exact: exact.clone(),
                    ..Scope::default()
                },
                ..SearchArgs::default()
            };
            let results = match w.engine.hybrid_search(&args).await {
                Ok(r) => r
                    .into_iter()
                    .filter(|r| {
                        let s = src_of(r);
                        (exact.is_none() || exact.as_deref() == Some(s.as_str()))
                            && source_matches_prefixes(&s, self.allowed)
                            && (reserved || !is_reserved_namespace_source(&s))
                    })
                    .collect(),
                Err(e) => {
                    tracing::debug!("AUDN similar search failed: {e}");
                    Vec::new()
                }
            };
            out.push(results);
        }
        out
    }

    /// `run_audn` with an LLM. A failed call or reply gives a fallback add
    /// for each fact.
    async fn audn(
        &self,
        facts: &[Fact],
        similar: &[Vec<Value>],
        rules: Option<&Value>,
    ) -> (Vec<Map<String, Value>>, Value) {
        let c = &self.cfg;
        let prompt = text::audn_prompt(
            facts,
            similar,
            rules,
            c.max_fact_chars,
            c.similar_text_chars,
            c.similar_per_fact,
        );
        let reply = match self.provider.complete(AUDN_SYSTEM, &prompt).await {
            Err(e) => Err(e.to_string()),
            Ok(r) => {
                text::parse_decisions(&r.text).map(|d| (d, tokens(r.input_tokens, r.output_tokens)))
            }
        };
        match reply {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("AUDN cycle failed: {e}");
                let all = (0..facts.len())
                    .map(|i| {
                        let mut m = Map::new();
                        m.insert("action".into(), json!("FALLBACK_ADD"));
                        m.insert("fact_index".into(), json!(i));
                        m
                    })
                    .collect();
                (all, tokens(0, 0))
            }
        }
    }

    fn debug_trace(
        &self,
        facts: &[Fact],
        decisions: &[Map<String, Value>],
        actions: &[Value],
        similar: &[Vec<Value>],
    ) -> Value {
        let c = &self.cfg;
        let text_at = |fi: i64| {
            usize::try_from(fi)
                .ok()
                .and_then(|i| facts.get(i))
                .map(|f| f.text.clone())
                .unwrap_or_default()
        };
        let trace: Vec<Value> = decisions
            .iter()
            .map(|d| {
                let fi_value = d.get("fact_index").cloned().unwrap_or(json!(-1));
                let fi = index_key(&fi_value).unwrap_or(-1);
                let mut e = Map::new();
                e.insert("fact_index".into(), fi_value);
                e.insert(
                    "action".into(),
                    d.get("action").cloned().unwrap_or(json!("UNKNOWN")),
                );
                let sim = usize::try_from(fi)
                    .ok()
                    .and_then(|i| similar.get(i))
                    .map(|m| text::debug_similar(m, c.similar_text_chars, c.similar_per_fact))
                    .unwrap_or_default();
                e.insert("similar_memories".into(), json!(sim));
                let ft = text_at(fi);
                let d_old = get(d, "old_id");
                for ra in actions {
                    let same_text = ra["text"].as_str() == Some(ft.as_str());
                    match ra["action"].as_str().unwrap_or("") {
                        "add" | "fallback_add" if same_text => {
                            e.insert("new_id".into(), ra["id"].clone());
                        }
                        "update" if *d_old == ra["old_id"] => {
                            e.insert("old_id".into(), ra["old_id"].clone());
                            e.insert("new_id".into(), ra["new_id"].clone());
                        }
                        "delete" if *d_old == ra["old_id"] => {
                            e.insert("old_id".into(), ra["old_id"].clone());
                        }
                        "noop" if same_text => {
                            e.insert("existing_id".into(), ra["existing_id"].clone());
                        }
                        "conflict" if same_text => {
                            e.insert("new_id".into(), ra["id"].clone());
                            e.insert("conflicts_with".into(), ra["conflicts_with"].clone());
                        }
                        _ => {}
                    }
                }
                Value::Object(e)
            })
            .collect();
        fn of<'a>(
            actions: &'a [Value],
            names: &'static [&'static str],
        ) -> impl Iterator<Item = &'a Value> {
            actions
                .iter()
                .filter(move |a| names.contains(&a["action"].as_str().unwrap_or("")))
        }
        json!({
            "extracted_facts": facts.iter().map(Fact::to_json).collect::<Vec<_>>(),
            "audn_decisions": trace,
            "execution_summary": {
                "added": of(actions, &["add", "fallback_add"]).filter(|a| !a["id"].is_null()).map(|a| a["id"].clone()).collect::<Vec<_>>(),
                "updated": of(actions, &["update"]).map(|a| json!({"old": a["old_id"], "new": a["new_id"]})).collect::<Vec<_>>(),
                "deleted": of(actions, &["delete"]).filter(|a| !a["old_id"].is_null()).map(|a| a["old_id"].clone()).collect::<Vec<_>>(),
                "noops": of(actions, &["noop"]).count(),
                "conflicts": of(actions, &["conflict"]).count(),
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_defaults_and_python_parsing() {
        let c = PipelineConfig::default();
        assert_eq!(
            (
                c.max_fact_chars,
                c.similar_text_chars,
                c.similar_per_fact,
                c.max_links
            ),
            (500, 280, 5, 3)
        );
        assert_eq!((c.min_link_score, c.novelty_threshold), (0.005, 0.85));
        assert!(c.novelty_gate && c.redact && !c.fallback.enabled && c.foreign_add);
        let c = PipelineConfig::from_lookup(|n| {
            Some(match n {
                "EXTRACT_MAX_FACT_CHARS" => "10".into(),
                "EXTRACT_MAX_LINKS" => "-4".into(),
                "EXTRACT_MIN_LINK_SCORE" => "x".into(),
                "EXTRACT_NOVELTY_GATE" => " Off ".into(),
                "EXTRACT_NOVELTY_THRESHOLD" => "-2".into(),
                "EXTRACT_REDACT_SECRETS" => "NO".into(),
                "EXTRACT_FALLBACK_ADD" => "on".into(),
                "EXTRACT_FALLBACK_NOVELTY_THRESHOLD" => "7".into(),
                "EXTRACT_FOREIGN_TARGET_ADD" => "false".into(),
                _ => return None,
            })
        });
        assert_eq!((c.max_fact_chars, c.max_links), (40, 0));
        assert_eq!((c.min_link_score, c.novelty_threshold), (0.005, -2.0));
        assert!(!c.novelty_gate && !c.redact && c.fallback.enabled && !c.foreign_add);
        assert_eq!(c.fallback.novelty_threshold, 1.0);
        let c =
            PipelineConfig::from_lookup(|n| (n == "EXTRACT_REDACT_SECRETS").then(|| "off".into()));
        assert!(c.redact, "Python does not read 'off' as false here");
    }

    #[test]
    fn fact_index_follows_python() {
        assert_eq!(fact_index(None).unwrap(), -1);
        assert_eq!(fact_index(Some(&json!(true))).unwrap(), 1);
        assert_eq!(fact_index(Some(&json!(4))).unwrap(), 4);
        assert!(matches!(
            fact_index(Some(&json!("1"))),
            Err(ExecError::Internal(_))
        ));
        assert_eq!(record_id(&json!(3.0)), Some(3));
        assert_eq!(record_id(&json!("3")), None);
        assert_eq!(index_key(&json!(true)), Some(1));
        assert_eq!(index_key(&json!(2.0)), Some(2));
    }

    #[test]
    fn merge_keeps_the_primary_error() {
        let p = json!({"error": "provider_runtime_failure", "error_stage": "extract_facts", "error_message": "boom"});
        let m = merge_fallback(&p, json!({"mode": "fallback_add"}));
        assert_eq!(m["fallback_triggered"], true);
        assert_eq!(m["fallback_source_stage"], "extract_facts");
        assert_eq!(m["primary_error"], "boom");
    }
}
