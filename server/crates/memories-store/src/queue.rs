//! The extract job queue: a table in `state.db`.
//!
//! The Python server kept the queue and the job results in RAM, so a restart
//! lost them. Here each job is a row. A job that was `running` at a stop goes
//! back to `queued` at the next start ([`JobQueue::recover`]).
//!
//! `job` is the JSON body of `GET /memory/extract/{job_id}`. `request` is the
//! input of the pipeline. Both are JSON text.

use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde_json::{Value, json};

use crate::StateError;

/// The result of [`JobQueue::enqueue`]. `depth` is the count of queued jobs
/// after the call, as the Python `extract_queue.qsize()` after `put_nowait`.
#[derive(Debug, PartialEq, Eq)]
pub enum Enqueue {
    Queued { depth: u64 },
    Full { depth: u64 },
}

/// A job that a worker took from the queue.
#[derive(Debug)]
pub struct Claimed {
    pub job_id: String,
    pub request: Value,
    /// The number of tries, this one included.
    pub attempts: u32,
}

#[derive(Debug, PartialEq)]
pub struct Stats {
    /// Jobs with status `queued`.
    pub depth: u64,
    /// All rows: queued, running and finished jobs (Python `len(extract_jobs)`).
    pub tracked: u64,
    /// The age in seconds of the oldest queued job.
    pub oldest_age_sec: Option<f64>,
}

pub struct JobQueue {
    conn: Mutex<Connection>,
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

fn parse(text: &str) -> Value {
    serde_json::from_str(text).unwrap_or(Value::Null)
}

impl JobQueue {
    pub fn open(path: &Path) -> Result<Self, StateError> {
        let conn = Connection::open(path)?;
        conn.busy_timeout(Duration::from_secs(10))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        // FULL: a job that got 202 is on disk before the reply.
        conn.pragma_update(None, "synchronous", "FULL")?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS extract_jobs (
                 seq INTEGER PRIMARY KEY AUTOINCREMENT,
                 job_id TEXT NOT NULL UNIQUE,
                 status TEXT NOT NULL,
                 attempts INTEGER NOT NULL DEFAULT 0,
                 request TEXT NOT NULL,
                 job TEXT NOT NULL,
                 enqueued_at REAL NOT NULL,
                 not_before REAL NOT NULL DEFAULT 0,
                 finished_at REAL
             );
             CREATE INDEX IF NOT EXISTS idx_extract_jobs_status ON extract_jobs(status, seq);",
        )?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    fn with_tx<T>(
        &self,
        f: impl FnOnce(&rusqlite::Transaction) -> Result<T, StateError>,
    ) -> Result<T, StateError> {
        let mut conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let out = f(&tx)?;
        tx.commit()?;
        Ok(out)
    }

    fn depth(tx: &Connection) -> rusqlite::Result<u64> {
        tx.query_row(
            "SELECT COUNT(*) FROM extract_jobs WHERE status = 'queued'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|n| n as u64)
    }

    fn set_job(tx: &Connection, job_id: &str, job: &Value) -> rusqlite::Result<()> {
        tx.execute(
            "UPDATE extract_jobs SET job = ?1 WHERE job_id = ?2",
            params![job.to_string(), job_id],
        )
        .map(drop)
    }

    fn job(tx: &Connection, job_id: &str) -> rusqlite::Result<Option<Value>> {
        tx.query_row(
            "SELECT job FROM extract_jobs WHERE job_id = ?1",
            [job_id],
            |r| r.get::<_, String>(0),
        )
        .optional()
        .map(|t| t.as_deref().map(parse))
    }

    /// Put each job that was `running` at the last stop back in the queue, in
    /// its old place. A job that already had `max_tries` tries fails. Returns
    /// (put back, failed). Call it once at start, before the workers start.
    pub fn recover(&self, max_tries: u32, completed_at: &str) -> Result<(u64, u64), StateError> {
        self.with_tx(|tx| {
            let running: Vec<(String, u32)> = tx
                .prepare("SELECT job_id, attempts FROM extract_jobs WHERE status = 'running'")?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?;
            let (mut back, mut failed) = (0, 0);
            for (id, attempts) in running {
                let mut job = Self::job(tx, &id)?.unwrap_or(Value::Null);
                if attempts >= max_tries {
                    job["status"] = json!("failed");
                    job["completed_at"] = json!(completed_at);
                    job["error"] = json!("the server stopped during the last try");
                    tx.execute(
                        "UPDATE extract_jobs SET status = 'failed', finished_at = ?1 WHERE job_id = ?2",
                        params![now(), id],
                    )?;
                    failed += 1;
                } else {
                    job["status"] = json!("queued");
                    tx.execute(
                        "UPDATE extract_jobs SET status = 'queued' WHERE job_id = ?1",
                        [&id],
                    )?;
                    back += 1;
                }
                Self::set_job(tx, &id, &job)?;
            }
            Ok((back, failed))
        })
    }

    /// Add a job if fewer than `max_depth` jobs are queued.
    pub fn enqueue(
        &self,
        job_id: &str,
        job: &Value,
        request: &Value,
        max_depth: u64,
    ) -> Result<Enqueue, StateError> {
        self.with_tx(|tx| {
            let depth = Self::depth(tx)?;
            if depth >= max_depth {
                return Ok(Enqueue::Full { depth });
            }
            tx.execute(
                "INSERT INTO extract_jobs (job_id, status, request, job, enqueued_at)
                 VALUES (?1, 'queued', ?2, ?3, ?4)",
                params![job_id, request.to_string(), job.to_string(), now()],
            )?;
            Ok(Enqueue::Queued { depth: depth + 1 })
        })
    }

    /// Take the oldest queued job and mark it `running`. The job body gets
    /// `started_at` and the queue depth after the take, as in Python.
    pub fn claim(&self, started_at: &str) -> Result<Option<Claimed>, StateError> {
        self.with_tx(|tx| {
            let row: Option<(String, String, u32)> = tx
                .query_row(
                    "SELECT job_id, request, attempts FROM extract_jobs
                     WHERE status = 'queued' AND not_before <= ?1 ORDER BY seq LIMIT 1",
                    [now()],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let Some((job_id, request, attempts)) = row else {
                return Ok(None);
            };
            tx.execute(
                "UPDATE extract_jobs SET status = 'running', attempts = attempts + 1 WHERE job_id = ?1",
                [&job_id],
            )?;
            let mut job = Self::job(tx, &job_id)?.unwrap_or(Value::Null);
            job["status"] = json!("running");
            job["started_at"] = json!(started_at);
            job["queue_depth"] = json!(Self::depth(tx)?);
            Self::set_job(tx, &job_id, &job)?;
            Ok(Some(Claimed {
                job_id,
                request: parse(&request),
                attempts: attempts + 1,
            }))
        })
    }

    fn finish(
        tx: &Connection,
        job_id: &str,
        status: &str,
        edit: impl FnOnce(&mut Value),
    ) -> Result<(), StateError> {
        let Some(mut job) = Self::job(tx, job_id)? else {
            return Ok(());
        };
        job["status"] = json!(status);
        edit(&mut job);
        tx.execute(
            "UPDATE extract_jobs SET status = ?1, finished_at = ?2 WHERE job_id = ?3",
            params![status, now(), job_id],
        )?;
        Self::set_job(tx, job_id, &job)?;
        Ok(())
    }

    pub fn complete(
        &self,
        job_id: &str,
        completed_at: &str,
        result: Value,
    ) -> Result<(), StateError> {
        self.with_tx(|tx| {
            Self::finish(tx, job_id, "completed", |job| {
                job["completed_at"] = json!(completed_at);
                job["result"] = result;
            })
        })
    }

    /// A try failed. Before `max_tries` tries, the job goes back to the queue
    /// in its old place, and no worker takes it for `delay`; then it fails
    /// with `error`. Returns true if it failed.
    pub fn fail(
        &self,
        job_id: &str,
        completed_at: &str,
        error: &str,
        max_tries: u32,
        delay: Duration,
    ) -> Result<bool, StateError> {
        self.with_tx(|tx| {
            let attempts: Option<u32> = tx
                .query_row(
                    "SELECT attempts FROM extract_jobs WHERE job_id = ?1",
                    [job_id],
                    |r| r.get(0),
                )
                .optional()?;
            let Some(attempts) = attempts else {
                return Ok(true);
            };
            if attempts < max_tries {
                let mut job = Self::job(tx, job_id)?.unwrap_or(Value::Null);
                job["status"] = json!("queued");
                tx.execute(
                    "UPDATE extract_jobs SET status = 'queued', not_before = ?1 WHERE job_id = ?2",
                    params![now() + delay.as_secs_f64(), job_id],
                )?;
                Self::set_job(tx, job_id, &job)?;
                return Ok(false);
            }
            Self::finish(tx, job_id, "failed", |job| {
                job["completed_at"] = json!(completed_at);
                job["error"] = json!(error);
            })?;
            Ok(true)
        })
    }

    /// The job fails with no more tries (a project policy error). `result`
    /// goes in the job body if set.
    pub fn fail_now(
        &self,
        job_id: &str,
        completed_at: &str,
        error: &str,
        result: Option<Value>,
    ) -> Result<(), StateError> {
        self.with_tx(|tx| {
            Self::finish(tx, job_id, "failed", |job| {
                job["completed_at"] = json!(completed_at);
                job["error"] = json!(error);
                if let Some(r) = result {
                    job["result"] = r;
                }
            })
        })
    }

    /// Store a job that finished outside the queue (the synchronous fallback).
    /// The row status is `job["status"]`.
    pub fn insert_finished(&self, job_id: &str, job: &Value) -> Result<(), StateError> {
        let status = job["status"].as_str().unwrap_or("failed");
        self.with_tx(|tx| {
            tx.execute(
                "INSERT INTO extract_jobs (job_id, status, request, job, enqueued_at, finished_at)
                 VALUES (?1, ?2, '{}', ?3, ?4, ?4)",
                params![job_id, status, job.to_string(), now()],
            )?;
            Ok(())
        })
    }

    pub fn get(&self, job_id: &str) -> Result<Option<Value>, StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        Ok(Self::job(&conn, job_id)?)
    }

    pub fn stats(&self) -> Result<Stats, StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        let (depth, tracked, oldest): (i64, i64, Option<f64>) = conn.query_row(
            "SELECT COALESCE(SUM(status = 'queued'), 0), COUNT(*),
                    MIN(CASE WHEN status = 'queued' THEN enqueued_at END)
             FROM extract_jobs",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        Ok(Stats {
            depth: depth as u64,
            tracked: tracked as u64,
            oldest_age_sec: oldest.map(|t| (now() - t).max(0.0)),
        })
    }

    /// Python `_trim_finished_extract_jobs`: delete finished jobs older than
    /// `retention_sec`; then, above `max_jobs` finished jobs, the oldest ones.
    /// Queued and running jobs stay. Returns the count of deleted rows.
    pub fn trim(&self, retention_sec: u64, max_jobs: u64) -> Result<u64, StateError> {
        self.with_tx(|tx| {
            let mut n = tx.execute(
                "DELETE FROM extract_jobs WHERE finished_at IS NOT NULL AND finished_at < ?1",
                [now() - retention_sec as f64],
            )? as u64;
            // Python counts all jobs, but its queue held only 40. With 10,000
            // queued jobs, that rule would delete each result at once.
            let finished: i64 = tx.query_row(
                "SELECT COUNT(*) FROM extract_jobs WHERE finished_at IS NOT NULL",
                [],
                |r| r.get(0),
            )?;
            let over = (finished as u64).saturating_sub(max_jobs);
            if over > 0 {
                n += tx.execute(
                    "DELETE FROM extract_jobs WHERE seq IN (
                         SELECT seq FROM extract_jobs WHERE finished_at IS NOT NULL
                         ORDER BY finished_at LIMIT ?1)",
                    [over as i64],
                )? as u64;
            }
            Ok(n)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    struct TempDir(PathBuf);
    impl TempDir {
        fn new(name: &str) -> Self {
            let dir =
                std::env::temp_dir().join(format!("memories-queue-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&dir);
            std::fs::create_dir_all(&dir).unwrap();
            Self(dir)
        }
        fn db(&self) -> PathBuf {
            self.0.join("state.db")
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn add(q: &JobQueue, id: &str, max: u64) -> Enqueue {
        q.enqueue(
            id,
            &json!({"job_id": id, "status": "queued"}),
            &json!({"messages": id}),
            max,
        )
        .unwrap()
    }

    #[test]
    fn jobs_come_out_in_order_with_the_python_job_fields() {
        let dir = TempDir::new("order");
        let q = JobQueue::open(&dir.db()).unwrap();
        assert_eq!(add(&q, "a", 10), Enqueue::Queued { depth: 1 });
        assert_eq!(add(&q, "b", 10), Enqueue::Queued { depth: 2 });
        let c = q.claim("t1").unwrap().unwrap();
        assert_eq!((c.job_id.as_str(), c.attempts), ("a", 1));
        assert_eq!(c.request, json!({"messages": "a"}));
        let job = q.get("a").unwrap().unwrap();
        assert_eq!(job["status"], "running");
        assert_eq!(job["started_at"], "t1");
        assert_eq!(job["queue_depth"], 1);
        q.complete("a", "t2", json!({"stored_count": 0})).unwrap();
        let job = q.get("a").unwrap().unwrap();
        assert_eq!(job["status"], "completed");
        assert_eq!(job["completed_at"], "t2");
        assert_eq!(job["result"], json!({"stored_count": 0}));
        assert_eq!(q.claim("t3").unwrap().unwrap().job_id, "b");
        assert!(q.claim("t4").unwrap().is_none());
        assert!(q.get("nojob").unwrap().is_none());
    }

    #[test]
    fn a_full_queue_refuses_and_running_jobs_do_not_count() {
        let dir = TempDir::new("full");
        let q = JobQueue::open(&dir.db()).unwrap();
        assert_eq!(add(&q, "a", 2), Enqueue::Queued { depth: 1 });
        assert_eq!(add(&q, "b", 2), Enqueue::Queued { depth: 2 });
        assert_eq!(add(&q, "c", 2), Enqueue::Full { depth: 2 });
        assert!(q.get("c").unwrap().is_none());
        q.claim("t").unwrap();
        assert_eq!(add(&q, "c", 2), Enqueue::Queued { depth: 2 });
    }

    #[test]
    fn a_job_gets_three_tries_then_fails() {
        let dir = TempDir::new("tries");
        let q = JobQueue::open(&dir.db()).unwrap();
        add(&q, "a", 10);
        add(&q, "b", 10);
        for attempt in 1..=3 {
            let c = q.claim("t").unwrap().unwrap();
            assert_eq!((c.job_id.as_str(), c.attempts), ("a", attempt));
            let failed = q.fail("a", "t9", "boom", 3, Duration::ZERO).unwrap();
            assert_eq!(failed, attempt == 3);
        }
        let job = q.get("a").unwrap().unwrap();
        assert_eq!(job["status"], "failed");
        assert_eq!(job["error"], "boom");
        assert_eq!(job["completed_at"], "t9");
        assert_eq!(q.claim("t").unwrap().unwrap().job_id, "b");
    }

    #[test]
    fn fail_now_and_insert_finished_skip_the_queue() {
        let dir = TempDir::new("now");
        let q = JobQueue::open(&dir.db()).unwrap();
        add(&q, "a", 10);
        q.claim("t").unwrap();
        q.fail_now(
            "a",
            "t2",
            "policy",
            Some(json!({"error": "project_policy"})),
        )
        .unwrap();
        let job = q.get("a").unwrap().unwrap();
        assert_eq!(job["status"], "failed");
        assert_eq!(job["result"]["error"], "project_policy");
        q.insert_finished("b", &json!({"job_id": "b", "status": "completed"}))
            .unwrap();
        assert_eq!(q.get("b").unwrap().unwrap()["status"], "completed");
        let s = q.stats().unwrap();
        assert_eq!((s.depth, s.tracked), (0, 2));
        assert!(q.claim("t").unwrap().is_none());
    }

    #[test]
    fn a_failed_try_waits_for_its_delay() {
        let dir = TempDir::new("delay");
        let q = JobQueue::open(&dir.db()).unwrap();
        add(&q, "a", 10);
        add(&q, "b", 10);
        q.claim("t").unwrap();
        q.fail("a", "t", "busy", 3, Duration::from_secs(60))
            .unwrap();
        assert_eq!(q.stats().unwrap().depth, 2);
        assert_eq!(q.claim("t").unwrap().unwrap().job_id, "b");
        assert!(q.claim("t").unwrap().is_none());
    }

    #[test]
    fn a_restart_puts_running_jobs_back_and_keeps_queued_jobs() {
        let dir = TempDir::new("restart");
        let q = JobQueue::open(&dir.db()).unwrap();
        for id in ["a", "b", "c"] {
            add(&q, id, 10);
        }
        q.claim("t").unwrap();
        drop(q);
        let q = JobQueue::open(&dir.db()).unwrap();
        assert_eq!(q.recover(3, "t").unwrap(), (1, 0));
        assert_eq!(q.get("a").unwrap().unwrap()["status"], "queued");
        assert_eq!(q.stats().unwrap().depth, 3);
        let ids: Vec<String> =
            std::iter::from_fn(|| q.claim("t").unwrap().map(|c| c.job_id)).collect();
        assert_eq!(ids, ["a", "b", "c"]);
    }

    #[test]
    fn a_job_that_stops_the_server_three_times_fails() {
        let dir = TempDir::new("poison");
        let q = JobQueue::open(&dir.db()).unwrap();
        add(&q, "a", 10);
        for _ in 0..3 {
            q.claim("t").unwrap();
            q.recover(3, "t9").unwrap();
        }
        let job = q.get("a").unwrap().unwrap();
        assert_eq!(job["status"], "failed");
        assert_eq!(job["completed_at"], "t9");
        assert!(q.claim("t").unwrap().is_none());
    }

    #[test]
    fn stats_count_queued_and_tracked_jobs() {
        let dir = TempDir::new("stats");
        let q = JobQueue::open(&dir.db()).unwrap();
        let s = q.stats().unwrap();
        assert_eq!((s.depth, s.tracked, s.oldest_age_sec), (0, 0, None));
        add(&q, "a", 10);
        add(&q, "b", 10);
        q.claim("t").unwrap();
        let s = q.stats().unwrap();
        assert_eq!((s.depth, s.tracked), (1, 2));
        assert!(s.oldest_age_sec.unwrap() >= 0.0);
    }

    #[test]
    fn trim_deletes_only_finished_jobs() {
        let dir = TempDir::new("trim");
        let q = JobQueue::open(&dir.db()).unwrap();
        for id in ["a", "b", "c", "d"] {
            add(&q, id, 10);
        }
        for id in ["a", "b"] {
            q.claim("t").unwrap();
            q.complete(id, "t", json!({})).unwrap();
        }
        // Many queued jobs do not count against the cap.
        assert_eq!(q.trim(300, 2).unwrap(), 0);
        // Retention not reached: the cap deletes the oldest finished job.
        assert_eq!(q.trim(300, 1).unwrap(), 1);
        assert!(q.get("a").unwrap().is_none());
        assert!(q.get("b").unwrap().is_some());
        assert_eq!(q.trim(300, 0).unwrap(), 1);
        assert_eq!(q.stats().unwrap().tracked, 2);
        // Retention 0: each finished job goes.
        q.claim("t").unwrap();
        q.complete("c", "t", json!({})).unwrap();
        std::thread::sleep(Duration::from_millis(5));
        assert_eq!(q.trim(0, 100).unwrap(), 1);
        assert_eq!(q.stats().unwrap().tracked, 1);
    }
}
