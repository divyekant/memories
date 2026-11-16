//! Usage analytics in `usage.db`: the same seven tables, schema and reads as
//! the Python `UsageTracker`. `USAGE_TRACKING=true` turns it on. When it is
//! off, the routes act as the Python `NullTracker`.
//!
//! One writer thread writes the rows in batches. A read first waits until
//! the writer has written each row sent before it ([`Usage::flush`]), so a
//! read sees each earlier write, as in Python.

use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;

use memories_engine::rank::scores::py_round;
use rusqlite::{Connection, ToSql, params, params_from_iter};
use serde_json::{Map, Value, json};
use tokio::sync::{mpsc, oneshot};

use crate::keys::sql_to_json;

/// Rows that wait for the writer. A row past this limit is dropped with a
/// warning (Python writes each row in the request and drops none). The limit
/// holds more than the rows of the largest `/search/batch` request.
const QUEUE_MAX: usize = 50_000;
/// The most rows in one write transaction.
const BATCH_MAX: usize = 512;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS api_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    operation TEXT NOT NULL,
    source TEXT DEFAULT '',
    client TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    invocation TEXT DEFAULT '',
    count INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS extraction_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    source TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_api_events_ts ON api_events(ts);
CREATE INDEX IF NOT EXISTS idx_api_events_op ON api_events(operation);
CREATE INDEX IF NOT EXISTS idx_extraction_tokens_ts ON extraction_tokens(ts);
CREATE TABLE IF NOT EXISTS retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    memory_id INTEGER NOT NULL,
    query TEXT DEFAULT '',
    source TEXT DEFAULT '',
    rank INTEGER DEFAULT 0,
    result_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_retrieval_memory ON retrieval_log(memory_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_ts ON retrieval_log(ts);
CREATE TABLE IF NOT EXISTS search_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    memory_id INTEGER NOT NULL,
    query TEXT DEFAULT '',
    signal TEXT NOT NULL,
    search_id TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_feedback_ts ON search_feedback(ts);
CREATE INDEX IF NOT EXISTS idx_feedback_memory ON search_feedback(memory_id);
CREATE TABLE IF NOT EXISTS extraction_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    source TEXT DEFAULT '',
    extracted INTEGER DEFAULT 0,
    stored INTEGER DEFAULT 0,
    updated INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    noop INTEGER DEFAULT 0,
    conflict INTEGER DEFAULT 0,
    fallback INTEGER DEFAULT 0,
    links_created INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_extraction_outcomes_ts ON extraction_outcomes(ts);
CREATE TABLE IF NOT EXISTS graph_search_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    query TEXT DEFAULT '',
    graph_weight REAL DEFAULT 0.0,
    direct_count INTEGER DEFAULT 0,
    graph_count INTEGER DEFAULT 0,
    total_results INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_graph_search_ts ON graph_search_events(ts);
CREATE TABLE IF NOT EXISTS temporal_search_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    query TEXT DEFAULT '',
    has_since INTEGER DEFAULT 0,
    has_until INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_temporal_search_ts ON temporal_search_events(ts);
";

/// The columns that the Python migrations add to a database of an older version.
const ADDED_COLUMNS: &[(&str, &str, &str)] = &[
    ("api_events", "client", "TEXT DEFAULT ''"),
    ("api_events", "session_id", "TEXT DEFAULT ''"),
    ("api_events", "invocation", "TEXT DEFAULT ''"),
    ("retrieval_log", "rank", "INTEGER DEFAULT 0"),
    ("retrieval_log", "result_count", "INTEGER DEFAULT 0"),
    ("extraction_outcomes", "fallback", "INTEGER DEFAULT 0"),
    ("extraction_outcomes", "links_created", "INTEGER DEFAULT 0"),
    (
        "temporal_search_events",
        "auto_detected",
        "INTEGER DEFAULT 0",
    ),
];

/// Per-model prices in USD for each 1M tokens (`MODEL_PRICING`).
const MODEL_PRICING: &[(&str, f64, f64)] = &[
    ("claude-haiku-5-5", 0.10, 0.50),
    ("claude-haiku-4-5-20251001", 1.00, 5.00),
    ("claude-sonnet-4-5-20250514", 3.00, 15.00),
    ("gpt-4.1-nano", 0.10, 0.40),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gemma3:4b", 0.0, 0.0),
];

/// `PERIOD_SQL`. An unknown period reads as `7d`.
fn period_sql(period: &str) -> &'static str {
    match period {
        "today" => "AND ts >= strftime('%Y-%m-%dT00:00:00Z', 'now')",
        "30d" => "AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days')",
        "all" => "",
        _ => "AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')",
    }
}

/// Python `text[:n]`: the first n code points.
fn cut(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

/// One row of a table, as the Python `log_*` methods take it.
#[derive(Clone, Debug)]
pub enum Row {
    Api {
        operation: String,
        source: String,
        count: i64,
        client: String,
        session_id: String,
        invocation: String,
    },
    Tokens {
        provider: String,
        model: String,
        stage: String,
        input_tokens: i64,
        output_tokens: i64,
        source: String,
    },
    Retrieval {
        memory_id: i64,
        query: String,
        source: String,
        rank: i64,
        result_count: i64,
    },
    /// A signal other than `useful` or `not_useful` is dropped.
    Feedback {
        memory_id: i64,
        query: String,
        signal: String,
        search_id: String,
    },
    Outcome {
        source: String,
        extracted: i64,
        stored: i64,
        updated: i64,
        deleted: i64,
        noop: i64,
        conflict: i64,
        fallback: i64,
        links_created: i64,
    },
    Graph {
        query: String,
        graph_weight: f64,
        direct_count: i64,
        graph_count: i64,
        total_results: i64,
    },
    Temporal {
        query: String,
        has_since: bool,
        has_until: bool,
        auto_detected: bool,
    },
}

impl Row {
    fn insert(&self, c: &Connection) -> rusqlite::Result<usize> {
        match self {
            Row::Api {
                operation,
                source,
                count,
                client,
                session_id,
                invocation,
            } => c.execute(
                "INSERT INTO api_events (operation, source, client, session_id, invocation, count) \
                 VALUES (?, ?, ?, ?, ?, ?)",
                params![
                    operation,
                    cut(source, 500),
                    cut(client, 100),
                    cut(session_id, 200),
                    cut(invocation, 100),
                    count
                ],
            ),
            Row::Tokens {
                provider,
                model,
                stage,
                input_tokens,
                output_tokens,
                source,
            } => c.execute(
                "INSERT INTO extraction_tokens (provider, model, stage, input_tokens, output_tokens, source) \
                 VALUES (?, ?, ?, ?, ?, ?)",
                params![provider, model, stage, input_tokens, output_tokens, source],
            ),
            Row::Retrieval {
                memory_id,
                query,
                source,
                rank,
                result_count,
            } => c.execute(
                "INSERT INTO retrieval_log (memory_id, query, source, rank, result_count) VALUES (?, ?, ?, ?, ?)",
                params![memory_id, cut(query, 500), source, rank, result_count],
            ),
            Row::Feedback {
                memory_id,
                query,
                signal,
                search_id,
            } => c.execute(
                "INSERT INTO search_feedback (memory_id, query, signal, search_id) VALUES (?, ?, ?, ?)",
                params![memory_id, cut(query, 500), signal, search_id],
            ),
            Row::Outcome {
                source,
                extracted,
                stored,
                updated,
                deleted,
                noop,
                conflict,
                fallback,
                links_created,
            } => c.execute(
                "INSERT INTO extraction_outcomes (source, extracted, stored, updated, deleted, noop, conflict, fallback, links_created) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![source, extracted, stored, updated, deleted, noop, conflict, fallback, links_created],
            ),
            Row::Graph {
                query,
                graph_weight,
                direct_count,
                graph_count,
                total_results,
            } => c.execute(
                "INSERT INTO graph_search_events (query, graph_weight, direct_count, graph_count, total_results) \
                 VALUES (?, ?, ?, ?, ?)",
                params![cut(query, 500), graph_weight, direct_count, graph_count, total_results],
            ),
            Row::Temporal {
                query,
                has_since,
                has_until,
                auto_detected,
            } => c.execute(
                "INSERT INTO temporal_search_events (query, has_since, has_until, auto_detected) \
                 VALUES (?, ?, ?, ?)",
                params![
                    cut(query, 500),
                    i64::from(*has_since),
                    i64::from(*has_until),
                    i64::from(*auto_detected)
                ],
            ),
        }
    }
}

enum Cmd {
    Row(Row),
    Flush(oneshot::Sender<()>),
    DeleteFeedback(i64, oneshot::Sender<bool>),
}

pub struct Usage {
    tx: mpsc::Sender<Cmd>,
    /// The connection of the reads. One read at a time.
    read: Mutex<Connection>,
}

fn connect(path: &Path) -> rusqlite::Result<Connection> {
    let c = Connection::open(path)?;
    c.busy_timeout(Duration::from_secs(5))?;
    c.pragma_update(None, "journal_mode", "WAL")?;
    c.pragma_update(None, "synchronous", "NORMAL")?;
    Ok(c)
}

impl Usage {
    /// Opens or makes the database, runs the Python migrations, and starts
    /// the writer thread. The thread stops when the `Usage` is dropped.
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        let w = connect(path)?;
        w.execute_batch(SCHEMA)?;
        for (table, col, decl) in ADDED_COLUMNS {
            let has = w
                .prepare(&format!(
                    "SELECT 1 FROM pragma_table_info('{table}') WHERE name = ?"
                ))?
                .exists([col])?;
            // Python starts when a migration fails; so does this server.
            if !has
                && let Err(e) =
                    w.execute(&format!("ALTER TABLE {table} ADD COLUMN {col} {decl}"), [])
            {
                tracing::warn!("usage.db: cannot add {table}.{col}: {e}");
            }
        }
        w.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_events_session ON api_events(session_id)",
            [],
        )?;
        let read = connect(path)?;
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        std::thread::Builder::new()
            .name("usage-writer".into())
            .spawn(move || writer(w, rx))
            .expect("cannot start the usage writer thread");
        tracing::info!("Usage tracker initialized: {}", path.display());
        Ok(Self {
            tx,
            read: Mutex::new(read),
        })
    }

    /// Queues a row. It does not wait for the write.
    pub fn log(&self, row: Row) {
        if let Row::Feedback { signal, .. } = &row
            && signal != "useful"
            && signal != "not_useful"
        {
            return;
        }
        if self.tx.try_send(Cmd::Row(row)).is_err() {
            tracing::warn!("usage.db: the write queue is full; a usage row is dropped");
        }
    }

    /// Waits until each row queued before this call is written.
    pub async fn flush(&self) {
        let (tx, rx) = oneshot::channel();
        if self.tx.send(Cmd::Flush(tx)).await.is_ok() {
            let _ = rx.await;
        }
    }

    /// Deletes a feedback row. False if no row has this ID.
    pub async fn delete_feedback(&self, id: i64) -> bool {
        let (tx, rx) = oneshot::channel();
        if self.tx.send(Cmd::DeleteFeedback(id, tx)).await.is_err() {
            return false;
        }
        rx.await.unwrap_or(false)
    }

    fn conn(&self) -> std::sync::MutexGuard<'_, Connection> {
        self.read.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// `get_usage`.
    pub fn usage(&self, period: &str, session_id: &str) -> rusqlite::Result<Value> {
        let c = self.conn();
        let pf = period_sql(period);
        let sf = if session_id.is_empty() {
            ""
        } else {
            " AND session_id = ?"
        };
        let mut st = c.prepare(&format!(
            "SELECT operation, source, client, session_id, invocation, SUM(count) as total \
             FROM api_events WHERE 1=1 {pf}{sf} \
             GROUP BY operation, source, client, session_id, invocation"
        ))?;
        let p: Vec<&dyn ToSql> = if session_id.is_empty() {
            vec![]
        } else {
            vec![&session_id]
        };
        let mut ops = Map::new();
        let mut rows = st.query(params_from_iter(p))?;
        while let Some(r) = rows.next()? {
            let op: String = r.get(0)?;
            let total: i64 = r.get(5)?;
            let entry = ops.entry(op).or_insert_with(|| {
                json!({"total": 0, "by_source": {}, "by_client": {}, "by_session": {}, "by_invocation": {}})
            });
            entry["total"] = json!(entry["total"].as_i64().unwrap_or(0) + total);
            for (i, key) in [
                (1, "by_source"),
                (2, "by_client"),
                (3, "by_session"),
                (4, "by_invocation"),
            ] {
                let mut name: String = r.get::<_, Option<String>>(i)?.unwrap_or_default();
                if name.is_empty() {
                    name = "(unknown)".into();
                }
                let n = entry[key][&name].as_i64().unwrap_or(0) + total;
                entry[key][name] = json!(n);
            }
        }
        drop(rows);
        let mut st = c.prepare(&format!(
            "SELECT provider, model, stage, SUM(input_tokens) as inp, SUM(output_tokens) as out, COUNT(*) as calls \
             FROM extraction_tokens WHERE 1=1 {pf} GROUP BY provider, model, stage"
        ))?;
        let (mut total_in, mut total_out, mut total_calls) = (0i64, 0i64, 0i64);
        let mut by_model = Map::new();
        let mut rows = st.query([])?;
        while let Some(r) = rows.next()? {
            let model: String = r.get(1)?;
            let (inp, out, calls): (i64, i64, i64) = (r.get(3)?, r.get(4)?, r.get(5)?);
            total_in += inp;
            total_out += out;
            total_calls += calls;
            let m = by_model
                .entry(model)
                .or_insert_with(|| json!({"calls": 0, "input_tokens": 0, "output_tokens": 0}));
            for (k, v) in [
                ("calls", calls),
                ("input_tokens", inp),
                ("output_tokens", out),
            ] {
                m[k] = json!(m[k].as_i64().unwrap_or(0) + v);
            }
        }
        let mut cost = 0.0;
        for (model, d) in &by_model {
            let (pin, pout) = match MODEL_PRICING.iter().find(|(m, ..)| m == model) {
                Some((_, i, o)) => (*i, *o),
                None => {
                    tracing::warn!(
                        "Unknown model {model:?} for pricing; using fallback $1/$4 per 1M tokens"
                    );
                    (1.0, 4.0)
                }
            };
            cost += d["input_tokens"].as_i64().unwrap_or(0) as f64 / 1_000_000.0 * pin;
            cost += d["output_tokens"].as_i64().unwrap_or(0) as f64 / 1_000_000.0 * pout;
        }
        Ok(json!({
            "enabled": true,
            "period": period,
            "session_id": if session_id.is_empty() { Value::Null } else { json!(session_id) },
            "operations": ops,
            "extraction": {
                "total_calls": total_calls,
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "by_model": by_model,
                "estimated_cost_usd": py_round(cost, 4),
            },
        }))
    }

    /// Feedback counts by signal: `(useful, not_useful)`.
    fn feedback_counts(c: &Connection, filter: &str) -> rusqlite::Result<(i64, i64)> {
        let mut st = c.prepare(&format!(
            "SELECT signal, COUNT(*) as cnt FROM search_feedback WHERE 1=1 {filter} GROUP BY signal"
        ))?;
        let mut rows = st.query([])?;
        let (mut useful, mut not_useful) = (0, 0);
        while let Some(r) = rows.next()? {
            match r.get::<_, String>(0)?.as_str() {
                "useful" => useful = r.get(1)?,
                "not_useful" => not_useful = r.get(1)?,
                _ => {}
            }
        }
        Ok((useful, not_useful))
    }

    fn one(c: &Connection, sql: &str) -> rusqlite::Result<Vec<Value>> {
        c.query_row(sql, [], |r| {
            (0..r.as_ref().column_count())
                .map(|i| r.get_ref(i).map(sql_to_json))
                .collect()
        })
    }

    /// `get_search_quality`. `memory_ids` is the scope of a scoped caller.
    pub fn search_quality(
        &self,
        period: &str,
        memory_ids: Option<&[i64]>,
    ) -> rusqlite::Result<Value> {
        let pf = period_sql(period);
        let c = self.conn();
        let mem = match memory_ids {
            None => "",
            Some([]) => {
                return Ok(json!({
                    "period": period,
                    "total_searches": 0,
                    "rank_distribution": {"top_3": 0, "rank_4_plus": 0},
                    "feedback": {"useful": 0, "not_useful": 0, "useful_ratio": 0.0},
                    "unique_memories_retrieved": 0,
                }));
            }
            Some(ids) => {
                // A temporary table, not one parameter per ID: SQLite limits
                // the parameter count, and a scope can hold more records.
                c.execute_batch(
                    "CREATE TEMP TABLE IF NOT EXISTS scope_ids (id INTEGER PRIMARY KEY); DELETE FROM temp.scope_ids;",
                )?;
                let mut ins = c.prepare("INSERT OR IGNORE INTO temp.scope_ids (id) VALUES (?)")?;
                for id in ids {
                    ins.execute([id])?;
                }
                " AND memory_id IN (SELECT id FROM temp.scope_ids)"
            }
        };
        let total = if memory_ids.is_some() {
            // No search ID in retrieval_log: Python counts the rows.
            Self::one(
                &c,
                &format!("SELECT COUNT(*) FROM retrieval_log WHERE 1=1{mem} {pf}"),
            )?
        } else {
            Self::one(
                &c,
                &format!(
                    "SELECT COALESCE(SUM(count), 0) FROM api_events WHERE operation IN ('search', 'search_batch') {pf}"
                ),
            )?
        };
        let mut st = c.prepare(&format!(
            "SELECT rank, COUNT(*) as cnt FROM retrieval_log WHERE rank > 0 {pf}{mem} GROUP BY rank"
        ))?;
        let (mut top3, mut rest) = (0i64, 0i64);
        let mut rows = st.query([])?;
        while let Some(r) = rows.next()? {
            let (rank, cnt): (i64, i64) = (r.get(0)?, r.get(1)?);
            if rank <= 3 {
                top3 += cnt;
            } else {
                rest += cnt;
            }
        }
        drop(rows);
        let (useful, not_useful) = Self::feedback_counts(&c, &format!("{pf}{mem}"))?;
        let unique = Self::one(
            &c,
            &format!("SELECT COUNT(DISTINCT memory_id) FROM retrieval_log WHERE 1=1 {pf}{mem}"),
        )?;
        Ok(json!({
            "period": period,
            "total_searches": total[0],
            "rank_distribution": {"top_3": top3, "rank_4_plus": rest},
            "feedback": {"useful": useful, "not_useful": not_useful, "useful_ratio": ratio(useful, useful + not_useful)},
            "unique_memories_retrieved": unique[0],
        }))
    }

    /// `get_extraction_quality`.
    pub fn extraction_quality(&self, period: &str) -> rusqlite::Result<Value> {
        let pf = period_sql(period);
        let c = self.conn();
        let t = Self::one(
            &c,
            &format!(
                "SELECT COUNT(*), COALESCE(SUM(extracted), 0), COALESCE(SUM(stored), 0), \
                 COALESCE(SUM(updated), 0), COALESCE(SUM(deleted), 0), COALESCE(SUM(noop), 0), \
                 COALESCE(SUM(conflict), 0), COALESCE(SUM(fallback), 0), COALESCE(SUM(links_created), 0) \
                 FROM extraction_outcomes WHERE 1=1 {pf}"
            ),
        )?;
        let names = [
            "extracted",
            "stored",
            "updated",
            "deleted",
            "noop",
            "conflict",
            "fallback",
            "links_created",
        ];
        let mut st = c.prepare(&format!(
            "SELECT source, SUM(extracted), SUM(stored), SUM(updated), SUM(deleted), SUM(noop), \
             SUM(conflict), SUM(fallback), SUM(links_created), COUNT(*) \
             FROM extraction_outcomes WHERE 1=1 {pf} GROUP BY source"
        ))?;
        let mut by_source = Map::new();
        let mut rows = st.query([])?;
        while let Some(r) = rows.next()? {
            let mut src: String = r.get::<_, Option<String>>(0)?.unwrap_or_default();
            if src.is_empty() {
                src = "(unknown)".into();
            }
            let mut m = Map::new();
            m.insert("extraction_count".into(), sql_to_json(r.get_ref(9)?));
            for (i, n) in names.iter().enumerate() {
                m.insert((*n).into(), sql_to_json(r.get_ref(i + 1)?));
            }
            by_source.insert(src, Value::Object(m));
        }
        let mut totals = Map::new();
        for (i, n) in names.iter().enumerate() {
            totals.insert((*n).into(), t[i + 1].clone());
        }
        totals.insert("noop_ratio".into(), json!(ratio(num(&t[5]), num(&t[1]))));
        Ok(json!({
            "period": period,
            "extraction_count": t[0],
            "totals": totals,
            "by_source": by_source,
        }))
    }

    /// `get_quality_summary`.
    pub fn quality_summary(&self, period: &str) -> rusqlite::Result<Value> {
        let pf = period_sql(period);
        let c = self.conn();
        let searches = Self::one(
            &c,
            &format!(
                "SELECT COALESCE(SUM(count), 0) FROM api_events WHERE operation IN ('search', 'search_batch') {pf}"
            ),
        )?;
        let (useful, not_useful) = Self::feedback_counts(&c, pf)?;
        let e = Self::one(
            &c,
            &format!(
                "SELECT COUNT(*), COALESCE(SUM(extracted), 0), COALESCE(SUM(stored), 0), \
                 COALESCE(SUM(updated), 0), COALESCE(SUM(deleted), 0), COALESCE(SUM(noop), 0), \
                 COALESCE(SUM(conflict), 0), COALESCE(SUM(fallback), 0) \
                 FROM extraction_outcomes WHERE 1=1 {pf}"
            ),
        )?;
        let g = Self::one(
            &c,
            &format!(
                "SELECT COUNT(*), COALESCE(SUM(graph_count), 0) FROM graph_search_events WHERE 1=1 {pf}"
            ),
        )?;
        let t = Self::one(
            &c,
            &format!("SELECT COUNT(*) FROM temporal_search_events WHERE 1=1 {pf}"),
        )?;
        let extracted = num(&e[1]);
        let rate = |i: usize| ratio(num(&e[i]), extracted);
        Ok(json!({
            "retrieval_precision": {
                "positive_feedback_rate": ratio(useful, useful + not_useful),
                "total_searches": searches[0],
                "searches_with_feedback": useful + not_useful,
            },
            "extraction_accuracy": {
                "total_extractions": e[0],
                "add_rate": rate(2),
                "update_rate": rate(3),
                "noop_rate": rate(5),
                "delete_rate": rate(4),
                "conflict_rate": rate(6),
                "fallback_rate": rate(7),
            },
            "graph_search": {"total_graph_searches": g[0], "total_graph_results": g[1]},
            "temporal_search": {"total_temporal_searches": t[0]},
            "period": period,
        }))
    }

    /// `get_graph_search_stats`.
    pub fn graph_stats(&self, period: &str) -> rusqlite::Result<Value> {
        let c = self.conn();
        let g = Self::one(
            &c,
            &format!(
                "SELECT COUNT(*), COALESCE(SUM(graph_count), 0), COALESCE(SUM(direct_count), 0) \
                 FROM graph_search_events WHERE 1=1 {}",
                period_sql(period)
            ),
        )?;
        let total = num(&g[0]);
        let avg = if total > 0 {
            py_round(num(&g[1]) as f64 / total as f64, 2)
        } else {
            0.0
        };
        Ok(json!({
            "total_graph_searches": g[0],
            "total_graph_results": g[1],
            "total_direct_results": g[2],
            "avg_graph_results": avg,
            "period": period,
        }))
    }

    /// `get_temporal_search_stats`.
    pub fn temporal_stats(&self, period: &str) -> rusqlite::Result<Value> {
        let c = self.conn();
        let t = Self::one(
            &c,
            &format!(
                "SELECT COUNT(*), \
                 COALESCE(SUM(CASE WHEN has_since=1 AND has_until=1 THEN 1 ELSE 0 END), 0), \
                 COALESCE(SUM(CASE WHEN has_since=1 AND has_until=0 THEN 1 ELSE 0 END), 0), \
                 COALESCE(SUM(CASE WHEN has_since=0 AND has_until=1 THEN 1 ELSE 0 END), 0), \
                 COALESCE(SUM(CASE WHEN auto_detected=1 THEN 1 ELSE 0 END), 0) \
                 FROM temporal_search_events WHERE 1=1 {}",
                period_sql(period)
            ),
        )?;
        Ok(json!({
            "total_temporal_searches": t[0],
            "since_only": t[2],
            "until_only": t[3],
            "range_queries": t[1],
            "auto_detected": t[4],
            "explicit": num(&t[0]) - num(&t[4]),
            "period": period,
        }))
    }

    /// `get_failures`: `retrieval` (negative feedback) or `extraction` (high noop).
    ///
    /// Python leaves the order of equal sort keys to the SQLite query plan. The
    /// reads of this file add the ID or key order that SQLite 3.45 (Python v5.19.0) gives.
    pub fn failures(&self, kind: &str, limit: i64) -> rusqlite::Result<Value> {
        let c = self.conn();
        let mut out = Vec::new();
        if kind == "retrieval" {
            let mut st = c.prepare(
                "SELECT sf.ts, sf.memory_id, sf.query, sf.signal, sf.search_id \
                 FROM search_feedback sf WHERE sf.signal = 'not_useful' \
                 ORDER BY sf.ts DESC, sf.id DESC LIMIT ?",
            )?;
            let mut rows = st.query([limit])?;
            while let Some(r) = rows.next()? {
                out.push(json!({
                    "type": "retrieval",
                    "query": sql_to_json(r.get_ref(2)?),
                    "timestamp": sql_to_json(r.get_ref(0)?),
                    "feedback": "negative",
                    "memory_id": sql_to_json(r.get_ref(1)?),
                    "search_id": sql_to_json(r.get_ref(4)?),
                }));
            }
        } else if kind == "extraction" {
            let mut st = c.prepare(
                "SELECT ts, source, extracted, stored, updated, deleted, noop, conflict \
                 FROM extraction_outcomes WHERE extracted > 0 AND noop > 0 \
                 ORDER BY CAST(noop AS REAL) / extracted DESC, ts DESC, id ASC LIMIT ?",
            )?;
            let mut rows = st.query([limit])?;
            while let Some(r) = rows.next()? {
                let v = |i| r.get_ref(i).map(sql_to_json);
                let (extracted, noop) = (v(2)?, v(6)?);
                out.push(json!({
                    "type": "extraction",
                    "source": v(1)?,
                    "timestamp": v(0)?,
                    "extracted": extracted,
                    "stored": v(3)?,
                    "noop": noop,
                    "noop_ratio": ratio(num(&noop), num(&extracted)),
                    "conflict": v(7)?,
                }));
            }
        }
        Ok(json!({"failures": out}))
    }

    /// `get_problem_queries` with `min_negative_ratio` 0.5 and no ID scope.
    pub fn problem_queries(&self, min_feedback: i64, limit: i64) -> rusqlite::Result<Vec<Value>> {
        let c = self.conn();
        let mut st = c.prepare(
            "SELECT query, COUNT(*) as total, \
             SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as not_useful \
             FROM search_feedback WHERE query != '' \
             GROUP BY query \
             HAVING COUNT(*) >= ? AND CAST(not_useful AS FLOAT) / COUNT(*) >= ? \
             ORDER BY not_useful DESC, query ASC LIMIT ?",
        )?;
        let rows = st.query_map(params![min_feedback, 0.5, limit], |r| {
            let (total, bad): (i64, i64) = (r.get(1)?, r.get(2)?);
            let ratio = if total > 0 {
                json!(py_round(bad as f64 / total as f64, 2))
            } else {
                json!(0)
            };
            Ok(json!({"query": sql_to_json(r.get_ref(0)?), "total": total, "not_useful": bad, "ratio": ratio}))
        })?;
        rows.collect()
    }

    /// `get_stale_memories` with no ID scope.
    pub fn stale_memories(&self, min_retrievals: i64, limit: i64) -> rusqlite::Result<Vec<Value>> {
        let c = self.conn();
        let mut st = c.prepare(
            "SELECT r.memory_id, r.retrievals, \
             COALESCE(f.useful, 0) as useful, COALESCE(f.not_useful, 0) as not_useful \
             FROM (SELECT memory_id, COUNT(*) as retrievals FROM retrieval_log \
                   GROUP BY memory_id) r \
             LEFT JOIN (SELECT memory_id, \
               SUM(CASE WHEN signal='useful' THEN 1 ELSE 0 END) as useful, \
               SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as not_useful \
               FROM search_feedback GROUP BY memory_id) f ON r.memory_id = f.memory_id \
             WHERE r.retrievals >= ? AND COALESCE(f.useful, 0) = 0 \
             AND (COALESCE(f.useful, 0) + COALESCE(f.not_useful, 0)) > 0 \
             ORDER BY r.retrievals DESC, r.memory_id ASC LIMIT ?",
        )?;
        let rows = st.query_map(params![min_retrievals, limit], |r| {
            let v = |i| r.get_ref(i).map(sql_to_json);
            Ok(json!({"memory_id": v(0)?, "retrievals": v(1)?, "useful": v(2)?, "not_useful": v(3)?}))
        })?;
        rows.collect()
    }

    /// The memory IDs of each `retrieval_log` row, after the queued rows are
    /// written (for `get_unretrieved_memory_ids`).
    pub async fn retrieved_ids(&self) -> rusqlite::Result<std::collections::HashSet<i64>> {
        self.flush().await;
        let c = self.conn();
        let mut st = c.prepare_cached("SELECT DISTINCT memory_id FROM retrieval_log")?;
        let rows = st.query_map([], |r| r.get::<_, i64>(0))?;
        rows.collect()
    }

    /// `get_feedback_scores()`: the net score (useful minus not_useful) of
    /// each memory with feedback, in the order of the SQL rows.
    pub fn feedback_scores(&self) -> rusqlite::Result<Vec<(i64, f64)>> {
        let c = self.conn();
        let mut st = c.prepare_cached(
            "SELECT memory_id, \
             SUM(CASE WHEN signal='useful' THEN 1 ELSE 0 END) - \
             SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as net \
             FROM search_feedback GROUP BY memory_id",
        )?;
        let rows = st.query_map([], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)? as f64))
        })?;
        rows.collect()
    }

    /// `get_feedback_history`.
    pub fn feedback_history(&self, memory_id: i64, limit: i64) -> rusqlite::Result<Vec<Value>> {
        let c = self.conn();
        let mut st = c.prepare(
            "SELECT id, ts, memory_id, query, signal, search_id \
             FROM search_feedback WHERE memory_id = ? ORDER BY ts DESC, id ASC LIMIT ?",
        )?;
        let names = ["id", "ts", "memory_id", "query", "signal", "search_id"];
        let rows = st.query_map(params![memory_id, limit], |r| {
            let mut m = Map::new();
            for (i, n) in names.iter().enumerate() {
                m.insert((*n).into(), sql_to_json(r.get_ref(i)?));
            }
            Ok(Value::Object(m))
        })?;
        rows.collect()
    }
}

fn num(v: &Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_f64().map(|f| f as i64))
        .unwrap_or(0)
}

/// Python `round(a / b, 4) if b > 0 else 0.0`.
fn ratio(a: i64, b: i64) -> f64 {
    if b > 0 {
        py_round(a as f64 / b as f64, 4)
    } else {
        0.0
    }
}

/// The writer: each batch is one transaction. A flush answers after the
/// commit of the rows before it.
fn writer(mut c: Connection, mut rx: mpsc::Receiver<Cmd>) {
    let mut batch = Vec::with_capacity(BATCH_MAX);
    while let Some(first) = rx.blocking_recv() {
        batch.push(first);
        while batch.len() < BATCH_MAX {
            match rx.try_recv() {
                Ok(cmd) => batch.push(cmd),
                Err(_) => break,
            }
        }
        let mut flushes = Vec::new();
        let mut deletes = Vec::new();
        match c.transaction() {
            Ok(tx) => {
                for cmd in batch.drain(..) {
                    match cmd {
                        Cmd::Row(row) => {
                            if let Err(e) = row.insert(&tx) {
                                tracing::debug!(error = %e, "usage.db: cannot write a row");
                            }
                        }
                        Cmd::Flush(done) => flushes.push(done),
                        Cmd::DeleteFeedback(id, done) => {
                            let n = tx
                                .execute("DELETE FROM search_feedback WHERE id = ?", [id])
                                .unwrap_or_else(|e| {
                                    tracing::warn!(error = %e, "usage.db: cannot delete feedback");
                                    0
                                });
                            deletes.push((done, n > 0));
                        }
                    }
                }
                if let Err(e) = tx.commit() {
                    tracing::warn!(error = %e, "usage.db: cannot commit the usage rows");
                    deletes.iter_mut().for_each(|(_, ok)| *ok = false);
                }
            }
            Err(e) => {
                tracing::warn!(error = %e, "usage.db: cannot start a write");
                for cmd in batch.drain(..) {
                    match cmd {
                        Cmd::Row(_) => {}
                        Cmd::Flush(done) => flushes.push(done),
                        Cmd::DeleteFeedback(_, done) => deletes.push((done, false)),
                    }
                }
            }
        }
        for done in flushes {
            let _ = done.send(());
        }
        for (done, ok) in deletes {
            let _ = done.send(ok);
        }
    }
}
