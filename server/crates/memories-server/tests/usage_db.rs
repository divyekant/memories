//! `usage.db` against the Python `UsageTracker`: `fixtures/usage/usage.db` and
//! `expected.json` come from `fixtures/usage/make_fixture.py` (Python v5.19.0).

#[allow(dead_code)]
mod common;

use std::path::Path;

use memories_server::usage::{Row, Usage};
use rusqlite::Connection;
use serde_json::{Value, json};

const FIXTURE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/usage");

fn load(name: &str) -> Value {
    serde_json::from_str(&std::fs::read_to_string(Path::new(FIXTURE).join(name)).unwrap()).unwrap()
}

fn s(a: &Value, k: &str) -> String {
    a[k].as_str().unwrap_or("").to_string()
}

fn i(a: &Value, k: &str) -> i64 {
    a[k].as_i64().unwrap_or(0)
}

/// The Python call of `rows.json` as a Rust row, with the Python defaults.
fn row(method: &str, a: &Value) -> (Row, &'static str) {
    match method {
        "log_api_event" => (
            Row::Api {
                operation: s(a, "operation"),
                source: s(a, "source"),
                count: a["count"].as_i64().unwrap_or(1),
                client: s(a, "client"),
                session_id: s(a, "session_id"),
                invocation: s(a, "invocation"),
            },
            "api_events",
        ),
        "log_extraction_tokens" => (
            Row::Tokens {
                provider: s(a, "provider"),
                model: s(a, "model"),
                stage: s(a, "stage"),
                input_tokens: i(a, "input_tokens"),
                output_tokens: i(a, "output_tokens"),
                source: s(a, "source"),
            },
            "extraction_tokens",
        ),
        "log_retrieval" => (
            Row::Retrieval {
                memory_id: i(a, "memory_id"),
                query: s(a, "query"),
                source: s(a, "source"),
                rank: i(a, "rank"),
                result_count: i(a, "result_count"),
            },
            "retrieval_log",
        ),
        "log_search_feedback" => (
            Row::Feedback {
                memory_id: i(a, "memory_id"),
                query: s(a, "query"),
                signal: s(a, "signal"),
                search_id: s(a, "search_id"),
            },
            "search_feedback",
        ),
        "log_extraction_outcome" => (
            Row::Outcome {
                source: s(a, "source"),
                extracted: i(a, "extracted"),
                stored: i(a, "stored"),
                updated: i(a, "updated"),
                deleted: i(a, "deleted"),
                noop: i(a, "noop"),
                conflict: i(a, "conflict"),
                fallback: i(a, "fallback"),
                links_created: i(a, "links_created"),
            },
            "extraction_outcomes",
        ),
        "log_graph_search" => (
            Row::Graph {
                query: s(a, "query"),
                graph_weight: a["graph_weight"].as_f64().unwrap_or(0.0),
                direct_count: i(a, "direct_count"),
                graph_count: i(a, "graph_count"),
                total_results: i(a, "total_results"),
            },
            "graph_search_events",
        ),
        "log_temporal_search" => (
            Row::Temporal {
                query: s(a, "query"),
                has_since: a["has_since"].as_bool().unwrap_or(false),
                has_until: a["has_until"].as_bool().unwrap_or(false),
                auto_detected: a["auto_detected"].as_bool().unwrap_or(false),
            },
            "temporal_search_events",
        ),
        m => panic!("unknown method {m}"),
    }
}

fn read(u: &Usage, method: &str, a: &Value) -> Value {
    let period = a["period"].as_str().unwrap_or("7d");
    match method {
        "get_usage" => u.usage(period, a["session_id"].as_str().unwrap_or("")),
        "get_search_quality" => {
            let ids: Option<Vec<i64>> = a["memory_ids"]
                .as_array()
                .map(|v| v.iter().map(|x| x.as_i64().unwrap()).collect());
            u.search_quality(period, ids.as_deref())
        }
        "get_extraction_quality" => u.extraction_quality(period),
        "get_quality_summary" => u.quality_summary(period),
        "get_graph_search_stats" => u.graph_stats(period),
        "get_temporal_search_stats" => u.temporal_stats(period),
        "get_failures" => u.failures(&s(a, "failure_type"), i(a, "limit")),
        "get_problem_queries" => u
            .problem_queries(
                a["min_feedback"].as_i64().unwrap_or(2),
                a["limit"].as_i64().unwrap_or(20),
            )
            .map(Value::Array),
        "get_stale_memories" => u
            .stale_memories(
                a["min_retrievals"].as_i64().unwrap_or(3),
                a["limit"].as_i64().unwrap_or(20),
            )
            .map(Value::Array),
        "get_feedback_scores" => u.feedback_scores().map(|v| {
            json!(
                v.iter()
                    .map(|(id, n)| json!([id, *n as i64]))
                    .collect::<Vec<_>>()
            )
        }),
        "get_feedback_history" => u
            .feedback_history(i(a, "memory_id"), i(a, "limit"))
            .map(Value::Array),
        m => panic!("unknown method {m}"),
    }
    .unwrap()
}

fn check_reads(u: &Usage) {
    let expected = load("expected.json");
    let reads = load("reads.json");
    let mut bad = Vec::new();
    for r in reads.as_array().unwrap() {
        let name = r["name"].as_str().unwrap();
        let got = read(u, r["method"].as_str().unwrap(), &r["args"]);
        if got != expected[name] {
            bad.push(format!(
                "{name}:\n  python {}\n  rust   {got}",
                expected[name]
            ));
        }
    }
    assert!(
        bad.is_empty(),
        "{} reads differ:\n{}",
        bad.len(),
        bad.join("\n")
    );
}

#[tokio::test]
async fn reads_a_usage_db_that_python_wrote() {
    let dir = common::temp_dir();
    std::fs::copy(Path::new(FIXTURE).join("usage.db"), dir.join("usage.db")).unwrap();
    let u = Usage::open(&dir.join("usage.db")).unwrap();
    check_reads(&u);
}

#[tokio::test]
async fn rust_writes_give_the_python_reads() {
    let dir = common::temp_dir();
    let u = Usage::open(&dir.join("usage.db")).unwrap();
    let ts = Connection::open(dir.join("usage.db")).unwrap();
    for r in load("rows.json").as_array().unwrap() {
        let (row, table) = row(r["method"].as_str().unwrap(), &r["args"]);
        u.log(row);
        u.flush().await;
        ts.execute(
            &format!("UPDATE {table} SET ts = ?1 WHERE id = (SELECT MAX(id) FROM {table})"),
            [r["ts"].as_str().unwrap()],
        )
        .unwrap();
    }
    check_reads(&u);
}

#[tokio::test]
async fn delete_feedback_reports_the_row() {
    let dir = common::temp_dir();
    std::fs::copy(Path::new(FIXTURE).join("usage.db"), dir.join("usage.db")).unwrap();
    let u = Usage::open(&dir.join("usage.db")).unwrap();
    assert!(u.delete_feedback(1).await);
    assert!(!u.delete_feedback(1).await);
    assert!(!u.delete_feedback(9999).await);
    let h = u.feedback_history(1, 50).unwrap();
    assert_eq!(h.len(), 1);
    assert_eq!(h[0]["id"], 2);
}

#[tokio::test]
async fn many_rows_go_in_batches_and_all_arrive() {
    let dir = common::temp_dir();
    let u = Usage::open(&dir.join("usage.db")).unwrap();
    for n in 0..5000 {
        u.log(Row::Api {
            operation: "search".into(),
            source: String::new(),
            count: 1,
            client: String::new(),
            session_id: format!("s{}", n % 2),
            invocation: String::new(),
        });
    }
    u.flush().await;
    let out = u.usage("all", "").unwrap();
    assert_eq!(out["operations"]["search"]["total"], 5000);
    assert_eq!(out["operations"]["search"]["by_session"]["s1"], 2500);
}

#[test]
fn migrates_a_usage_db_from_before_v5_7_2() {
    let dir = common::temp_dir();
    let path = dir.join("usage.db");
    let c = Connection::open(&path).unwrap();
    c.execute_batch(
        "CREATE TABLE api_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            operation TEXT NOT NULL, source TEXT DEFAULT '', count INTEGER DEFAULT 1);
         INSERT INTO api_events (operation, source, count) VALUES ('add', 'a', 2);
         CREATE TABLE retrieval_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            memory_id INTEGER NOT NULL, query TEXT DEFAULT '', source TEXT DEFAULT '');
         CREATE TABLE extraction_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            source TEXT DEFAULT '', extracted INTEGER DEFAULT 0, stored INTEGER DEFAULT 0,
            updated INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0, noop INTEGER DEFAULT 0,
            conflict INTEGER DEFAULT 0);
         CREATE TABLE temporal_search_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            query TEXT DEFAULT '', has_since INTEGER DEFAULT 0, has_until INTEGER DEFAULT 0);",
    )
    .unwrap();
    drop(c);
    let u = Usage::open(&path).unwrap();
    let cols = |t: &str| -> Vec<String> {
        let c = Connection::open(&path).unwrap();
        let mut st = c.prepare(&format!("PRAGMA table_info({t})")).unwrap();
        st.query_map([], |r| r.get::<_, String>(1))
            .unwrap()
            .map(Result::unwrap)
            .collect()
    };
    for (t, want) in [
        ("api_events", &["client", "session_id", "invocation"][..]),
        ("retrieval_log", &["rank", "result_count"]),
        ("extraction_outcomes", &["fallback", "links_created"]),
        ("temporal_search_events", &["auto_detected"]),
    ] {
        let have = cols(t);
        for w in want {
            assert!(have.iter().any(|h| h == w), "{t}.{w}");
        }
    }
    let out = u.usage("all", "").unwrap();
    assert_eq!(out["operations"]["add"]["by_client"]["(unknown)"], 2);
}
