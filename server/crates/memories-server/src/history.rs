//! Metrics history in `metrics.db`: the server writes one process row and one
//! row for each route with requests at each interval, so that the Performance
//! page of the web UI can show trends that continue after a restart.
//! `GET /metrics/history` reads the rows. `METRICS_HISTORY_INTERVAL_SEC`
//! (default 60, 0 turns it off) and `METRICS_HISTORY_DAYS` (default 14) set it.

use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, params};
use serde_json::{Map, Value, json};

use crate::AppState;
use crate::shell::{Window, percentile, round2};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS process_samples (
    ts INTEGER PRIMARY KEY,
    rss_kb INTEGER NOT NULL,
    active INTEGER NOT NULL,
    extract_depth INTEGER NOT NULL,
    memories INTEGER NOT NULL,
    requests INTEGER NOT NULL,
    errors INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS route_samples (
    ts INTEGER NOT NULL,
    route TEXT NOT NULL,
    count INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    p50 REAL NOT NULL,
    p95 REAL NOT NULL,
    max REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_route_samples_ts ON route_samples(ts);
CREATE TABLE IF NOT EXISTS starts (
    ts INTEGER PRIMARY KEY,
    version TEXT NOT NULL
);
";

/// The most points of each series in one `GET /metrics/history` reply.
const MAX_POINTS: i64 = 360;

pub struct History {
    conn: Mutex<Connection>,
    pub interval_sec: u64,
    pub retain_days: u64,
}

/// The process values of one sample.
pub struct Process {
    pub rss_kb: i64,
    pub active: i64,
    pub extract_depth: i64,
    pub memories: i64,
}

pub fn now_sec() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64)
}

impl History {
    pub fn open(path: &Path, interval_sec: u64, retain_days: u64) -> rusqlite::Result<Self> {
        let conn = Connection::open(path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self {
            conn: Mutex::new(conn),
            interval_sec,
            retain_days,
        })
    }

    fn conn(&self) -> std::sync::MutexGuard<'_, Connection> {
        self.conn.lock().unwrap_or_else(|p| p.into_inner())
    }

    pub fn record_start(&self, ts: i64, version: &str) -> rusqlite::Result<()> {
        self.conn().execute(
            "INSERT OR REPLACE INTO starts (ts, version) VALUES (?1, ?2)",
            params![ts, version],
        )?;
        Ok(())
    }

    /// Writes one sample, and deletes the rows older than the retention.
    pub fn write(&self, ts: i64, p: &Process, routes: &[Window]) -> rusqlite::Result<()> {
        let mut conn = self.conn();
        let tx = conn.transaction()?;
        let requests: u64 = routes.iter().map(|w| w.count).sum();
        let errors: u64 = routes.iter().map(|w| w.errors).sum();
        tx.execute(
            "INSERT OR REPLACE INTO process_samples VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                ts,
                p.rss_kb,
                p.active,
                p.extract_depth,
                p.memories,
                requests as i64,
                errors as i64
            ],
        )?;
        {
            let mut ins =
                tx.prepare_cached("INSERT INTO route_samples VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)")?;
            for w in routes {
                ins.execute(params![
                    ts,
                    w.route,
                    w.count as i64,
                    w.errors as i64,
                    round2(percentile(&w.latencies, 0.5)),
                    round2(percentile(&w.latencies, 0.95)),
                    round2(w.latencies.last().copied().unwrap_or(0.0)),
                ])?;
            }
        }
        let cutoff = ts - (self.retain_days * 86_400) as i64;
        for table in ["process_samples", "route_samples", "starts"] {
            tx.execute(&format!("DELETE FROM {table} WHERE ts < ?1"), [cutoff])?;
        }
        tx.commit()
    }

    /// The samples of `[from, to)`, merged into buckets so that each series
    /// has at most [`MAX_POINTS`] points. In a bucket of more than one sample,
    /// `p50` is the mean of the sample p50 values (weighted by requests), and
    /// `p95` and `max` are the highest sample values.
    pub fn query(&self, from: i64, to: i64) -> rusqlite::Result<Value> {
        let step = self.interval_sec.max(1) as i64;
        let span = (to - from).max(step);
        let raw = (span / MAX_POINTS).max(step);
        let bucket = (raw + step - 1) / step * step;
        let conn = self.conn();
        let mut st = conn.prepare(
            "SELECT (ts / ?1) * ?1 AS t, CAST(AVG(rss_kb) AS INTEGER), MAX(rss_kb), MAX(active),
                    MAX(extract_depth), MAX(memories), SUM(requests), SUM(errors), COUNT(*)
             FROM process_samples WHERE ts >= ?2 AND ts < ?3 GROUP BY t ORDER BY t",
        )?;
        let process: Vec<Value> = st
            .query_map(params![bucket, from, to], |r| {
                Ok(json!({
                    "ts": r.get::<_, i64>(0)?,
                    "rss_kb": r.get::<_, i64>(1)?,
                    "rss_max_kb": r.get::<_, i64>(2)?,
                    "active_max": r.get::<_, i64>(3)?,
                    "extract_depth_max": r.get::<_, i64>(4)?,
                    "memories": r.get::<_, i64>(5)?,
                    "requests": r.get::<_, i64>(6)?,
                    "errors": r.get::<_, i64>(7)?,
                    "samples": r.get::<_, i64>(8)?,
                }))
            })?
            .collect::<Result<_, _>>()?;
        let mut st = conn.prepare(
            "SELECT route, (ts / ?1) * ?1 AS t, SUM(count), SUM(errors),
                    SUM(p50 * count) / SUM(count), MAX(p95), MAX(max)
             FROM route_samples WHERE ts >= ?2 AND ts < ?3 GROUP BY route, t ORDER BY route, t",
        )?;
        let mut routes: Map<String, Value> = Map::new();
        let rows = st.query_map(params![bucket, from, to], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, i64>(3)?,
                r.get::<_, f64>(4)?,
                r.get::<_, f64>(5)?,
                r.get::<_, f64>(6)?,
            ))
        })?;
        for row in rows {
            let (route, t, count, errors, p50, p95, max) = row?;
            let e = routes
                .entry(route)
                .or_insert_with(|| json!({"count": 0, "errors": 0, "points": []}));
            e["count"] = json!(e["count"].as_i64().unwrap_or(0) + count);
            e["errors"] = json!(e["errors"].as_i64().unwrap_or(0) + errors);
            e["points"].as_array_mut().unwrap().push(json!({
                "ts": t, "count": count, "errors": errors,
                "p50": round2(p50), "p95": p95, "max": max,
            }));
        }
        let mut st =
            conn.prepare("SELECT ts, version FROM starts WHERE ts >= ?1 AND ts < ?2 ORDER BY ts")?;
        let starts: Vec<Value> = st
            .query_map(params![from, to], |r| {
                Ok(json!({"ts": r.get::<_, i64>(0)?, "version": r.get::<_, String>(1)?}))
            })?
            .collect::<Result<_, _>>()?;
        Ok(json!({
            "interval_sec": step,
            "bucket_sec": bucket,
            "from": from,
            "to": to,
            "process": process,
            "routes": routes,
            "starts": starts,
        }))
    }
}

/// Writes a sample at each interval. A failed write is logged, not fatal.
pub async fn run(state: Arc<AppState>) {
    let Some(h) = &state.history else {
        return;
    };
    if let Err(e) = h.record_start(now_sec(), crate::VERSION) {
        tracing::warn!(error = %e, "metrics history: cannot record the start");
    }
    let every = Duration::from_secs(h.interval_sec);
    let mut tick = tokio::time::interval_at(tokio::time::Instant::now() + every, every);
    loop {
        tick.tick().await;
        let routes = state.metrics.take_window();
        let extract_depth = match crate::extract::queue_fields(&state).await {
            Ok(v) => v["queue_depth"].as_i64().unwrap_or(0),
            Err(_) => 0,
        };
        let p = Process {
            rss_kb: crate::usage_routes::process_memory_kb()["rss_kb"]
                .as_i64()
                .unwrap_or(0),
            active: state
                .metrics
                .active_requests
                .load(std::sync::atomic::Ordering::Relaxed)
                .max(0),
            extract_depth,
            memories: state.engine.get().map_or(0, |e| e.count()) as i64,
        };
        let st = state.clone();
        let res = tokio::task::spawn_blocking(move || {
            st.history
                .as_ref()
                .map_or(Ok(()), |h| h.write(now_sec(), &p, &routes))
        })
        .await;
        match res {
            Ok(Ok(())) => {}
            Ok(Err(e)) => tracing::warn!(error = %e, "metrics history: write failed"),
            Err(e) => tracing::warn!(error = %e, "metrics history: write task failed"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn win(route: &str, lat: &[f64], errors: u64) -> Window {
        Window {
            route: route.into(),
            count: lat.len() as u64,
            errors,
            latencies: lat.to_vec(),
        }
    }

    fn proc_(rss: i64) -> Process {
        Process {
            rss_kb: rss,
            active: 1,
            extract_depth: 0,
            memories: 10,
        }
    }

    #[test]
    fn write_query_and_retention() {
        let dir = std::env::temp_dir().join(format!("mem-history-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let h = History::open(&dir.join("metrics.db"), 60, 1).unwrap();
        h.record_start(0, "6.0.0").unwrap();
        h.write(
            60,
            &proc_(100),
            &[win("POST /search", &[10.0, 20.0, 30.0, 40.0], 1)],
        )
        .unwrap();
        h.write(120, &proc_(300), &[win("POST /search", &[50.0], 0)])
            .unwrap();
        let v = h.query(0, 180).unwrap();
        assert_eq!(v["bucket_sec"], 60);
        assert_eq!(v["process"].as_array().unwrap().len(), 2);
        assert_eq!(v["process"][0]["requests"], 4);
        assert_eq!(v["process"][0]["errors"], 1);
        let r = &v["routes"]["POST /search"];
        assert_eq!(
            (r["count"].as_i64(), r["errors"].as_i64()),
            (Some(5), Some(1))
        );
        assert_eq!(r["points"][0]["p50"], 20.0);
        assert_eq!(r["points"][0]["p95"], 40.0);
        assert_eq!(v["starts"][0]["version"], "6.0.0");
        // One bucket for a long span: weighted p50, highest p95 and max.
        let v = h.query(0, 60 * MAX_POINTS * 10).unwrap();
        assert_eq!(v["bucket_sec"], 600);
        assert_eq!(v["process"][0]["rss_kb"], 200);
        assert_eq!(v["process"][0]["rss_max_kb"], 300);
        let p = &v["routes"]["POST /search"]["points"][0];
        assert_eq!(
            (p["count"].as_i64(), p["p50"].as_f64()),
            (Some(5), Some(26.0))
        );
        assert_eq!(p["max"], 50.0);
        // A write a day later deletes the older rows.
        h.write(86_400 + 120, &proc_(1), &[]).unwrap();
        let v = h.query(0, 200_000).unwrap();
        assert_eq!(v["process"].as_array().unwrap().len(), 2);
        assert_eq!(v["routes"]["POST /search"]["count"], 1);
        assert!(v["starts"].as_array().unwrap().is_empty());
    }
}
