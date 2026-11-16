//! `state.db`: the SQLite file of the Rust server. It holds the ID counter,
//! named values (`meta`), for example the state of the migration, and the
//! journal of the writes of two steps (`pending_ops`).
//!
//! The counter only goes up. An ID that the counter gave out is not given out
//! again: not after a restart, not after a delete of the highest ID, and not
//! after a crash between the reserve and the write (that ID stays unused).
//! The Python server took `max(id) + 1` at each load, so a delete of the
//! highest ID gave that ID again.

use std::ops::Range;
use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;

use rusqlite::{Connection, OptionalExtension, TransactionBehavior};

const COUNTER: &str = "memory_id";
const DOC_LEN_SUM: &str = "doc_len_sum";
const DOC_COUNT: &str = "doc_count";

#[derive(Debug, thiserror::Error)]
pub enum StateError {
    #[error("state.db: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("state.db task failed: {0}")]
    Task(String),
    #[error("the ID counter cannot give {count} more IDs after {next}")]
    Exhausted { next: u64, count: u64 },
}

pub struct StateDb {
    conn: Mutex<Connection>,
}

impl StateDb {
    pub fn open(path: &Path) -> Result<Self, StateError> {
        let conn = Connection::open(path)?;
        conn.busy_timeout(Duration::from_secs(10))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "FULL")?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS counters (
                 name TEXT PRIMARY KEY,
                 next INTEGER NOT NULL CHECK (next >= 0)
             );
             CREATE TABLE IF NOT EXISTS meta (
                 name TEXT PRIMARY KEY,
                 value TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS pending_ops (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 op TEXT NOT NULL
             )",
        )?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    fn update<T>(
        &self,
        f: impl FnOnce(u64) -> Result<(u64, T), StateError>,
    ) -> Result<T, StateError> {
        let mut conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        // IMMEDIATE takes the write lock before the read, so two processes on
        // one file cannot read the same value.
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let next: i64 = tx
            .query_row(
                "SELECT next FROM counters WHERE name = ?1",
                [COUNTER],
                |r| r.get(0),
            )
            .optional()?
            .unwrap_or(0);
        let (new_next, out) = f(next as u64)?;
        tx.execute(
            "INSERT INTO counters (name, next) VALUES (?1, ?2)
             ON CONFLICT (name) DO UPDATE SET next = excluded.next",
            rusqlite::params![COUNTER, new_next as i64],
        )?;
        tx.commit()?;
        Ok(out)
    }

    /// Reserve `count` new IDs. The reserve is on disk before the call returns.
    pub fn reserve_ids(&self, count: u64) -> Result<Range<u64>, StateError> {
        self.update(|next| {
            let end = next
                .checked_add(count)
                .filter(|&e| i64::try_from(e).is_ok())
                .ok_or(StateError::Exhausted { next, count })?;
            Ok((end, next..end))
        })
    }

    /// Move the counter up to at least `min_next`. It never goes down. Call it
    /// at start with the highest stored ID plus 1.
    pub fn raise_id_floor(&self, min_next: u64) -> Result<u64, StateError> {
        if i64::try_from(min_next).is_err() {
            return Err(StateError::Exhausted {
                next: min_next,
                count: 0,
            });
        }
        self.update(|next| {
            let new = next.max(min_next);
            Ok((new, new))
        })
    }

    /// The next ID that `reserve_ids` gives.
    pub fn next_id(&self) -> Result<u64, StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        let next: Option<i64> = conn
            .query_row(
                "SELECT next FROM counters WHERE name = ?1",
                [COUNTER],
                |r| r.get(0),
            )
            .optional()?;
        Ok(next.unwrap_or(0) as u64)
    }

    pub fn get_meta(&self, name: &str) -> Result<Option<String>, StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        Ok(conn
            .query_row("SELECT value FROM meta WHERE name = ?1", [name], |r| {
                r.get(0)
            })
            .optional()?)
    }

    /// The sum of the keyword document lengths and the record count (decision
    /// P0-2). (0, 0) before the first write.
    pub fn doc_lengths(&self) -> Result<(u64, u64), StateError> {
        let get = |n| -> Result<u64, StateError> {
            Ok(self.get_meta(n)?.and_then(|v| v.parse().ok()).unwrap_or(0))
        };
        Ok((get(DOC_LEN_SUM)?, get(DOC_COUNT)?))
    }

    /// Add the deltas in one transaction and return the new values. An add
    /// gives (+len, +1), a delete (-len, -1), a text change (new - old, 0).
    /// A value does not go below 0.
    pub fn add_doc_lengths(
        &self,
        sum_delta: i64,
        count_delta: i64,
    ) -> Result<(u64, u64), StateError> {
        let mut conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let get = |n: &str| -> Result<i64, StateError> {
            let v: Option<String> = tx
                .query_row("SELECT value FROM meta WHERE name = ?1", [n], |r| r.get(0))
                .optional()?;
            Ok(v.and_then(|v| v.parse().ok()).unwrap_or(0))
        };
        let new = (
            get(DOC_LEN_SUM)?.saturating_add(sum_delta).max(0) as u64,
            get(DOC_COUNT)?.saturating_add(count_delta).max(0) as u64,
        );
        for (name, v) in [(DOC_LEN_SUM, new.0), (DOC_COUNT, new.1)] {
            tx.execute(
                "INSERT INTO meta (name, value) VALUES (?1, ?2)
                 ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                rusqlite::params![name, v.to_string()],
            )?;
        }
        tx.commit()?;
        Ok(new)
    }

    pub fn set_doc_lengths(&self, sum: u64, count: u64) -> Result<(), StateError> {
        self.set_meta(&[
            (DOC_LEN_SUM, &sum.to_string()),
            (DOC_COUNT, &count.to_string()),
        ])
    }

    /// Journal a write of two steps before its first step. The entry is on disk
    /// before the call returns.
    pub fn begin_op(&self, op: &str) -> Result<i64, StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        conn.execute("INSERT INTO pending_ops (op) VALUES (?1)", [op])?;
        Ok(conn.last_insert_rowid())
    }

    /// Remove a journal entry after the last step of its write.
    pub fn end_op(&self, id: i64) -> Result<(), StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        conn.execute("DELETE FROM pending_ops WHERE id = ?1", [id])?;
        Ok(())
    }

    /// The journal entries that have no end, oldest first.
    pub fn pending_ops(&self) -> Result<Vec<(i64, String)>, StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        let mut stmt = conn.prepare("SELECT id, op FROM pending_ops ORDER BY id")?;
        let rows = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.collect::<Result<_, _>>()?)
    }

    /// Write all `values` in one transaction.
    pub fn set_meta(&self, values: &[(&str, &str)]) -> Result<(), StateError> {
        let mut conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        for (name, value) in values {
            tx.execute(
                "INSERT INTO meta (name, value) VALUES (?1, ?2)
                 ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                [name, value],
            )?;
        }
        tx.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;
    use std::path::PathBuf;
    use std::sync::Arc;

    struct TempDir(PathBuf);
    impl TempDir {
        fn new(name: &str) -> Self {
            let dir =
                std::env::temp_dir().join(format!("memories-state-{name}-{}", std::process::id()));
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

    #[test]
    fn ids_are_not_given_again_after_a_restart() {
        let dir = TempDir::new("restart");
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.reserve_ids(3).unwrap(), 0..3);
        assert_eq!(db.reserve_ids(1).unwrap(), 3..4);
        drop(db);
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.next_id().unwrap(), 4);
        assert_eq!(db.reserve_ids(2).unwrap(), 4..6);
    }

    #[test]
    fn pending_ops_stay_until_their_end() {
        let dir = TempDir::new("ops");
        let db = StateDb::open(&dir.db()).unwrap();
        let a = db.begin_op("a").unwrap();
        let b = db.begin_op("b").unwrap();
        db.end_op(a).unwrap();
        drop(db);
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.pending_ops().unwrap(), vec![(b, "b".to_owned())]);
        db.end_op(b).unwrap();
        assert!(db.pending_ops().unwrap().is_empty());
        assert!(
            db.begin_op("c").unwrap() > b,
            "an entry ID is not given again"
        );
    }

    #[test]
    fn meta_values_stay_after_a_restart() {
        let dir = TempDir::new("meta");
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.get_meta("a").unwrap(), None);
        db.set_meta(&[("a", "1"), ("b", "2")]).unwrap();
        db.set_meta(&[("a", "3")]).unwrap();
        drop(db);
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.get_meta("a").unwrap().as_deref(), Some("3"));
        assert_eq!(db.get_meta("b").unwrap().as_deref(), Some("2"));
        assert_eq!(db.doc_lengths().unwrap(), (0, 0));
        db.set_doc_lengths(30, 4).unwrap();
        assert_eq!(db.doc_lengths().unwrap(), (30, 4));
        assert_eq!(db.add_doc_lengths(5, 1).unwrap(), (35, 5));
        assert_eq!(
            db.add_doc_lengths(-50, -9).unwrap(),
            (0, 0),
            "no value below 0"
        );
    }

    #[test]
    fn the_floor_only_goes_up() {
        let dir = TempDir::new("floor");
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.raise_id_floor(100).unwrap(), 100);
        // The highest stored ID was deleted: the floor from the store is lower now.
        assert_eq!(db.raise_id_floor(42).unwrap(), 100);
        assert_eq!(db.reserve_ids(1).unwrap(), 100..101);
        assert_eq!(db.raise_id_floor(0).unwrap(), 101);
    }

    #[test]
    fn the_counter_stops_at_the_qdrant_id_limit() {
        let dir = TempDir::new("limit");
        let db = StateDb::open(&dir.db()).unwrap();
        let max = i64::MAX as u64;
        db.raise_id_floor(max - 1).unwrap();
        assert_eq!(db.reserve_ids(1).unwrap(), max - 1..max);
        assert!(matches!(
            db.reserve_ids(1),
            Err(StateError::Exhausted { .. })
        ));
        assert!(db.raise_id_floor(max + 1).is_err());
        assert_eq!(db.next_id().unwrap(), max);
    }

    #[test]
    fn parallel_reserves_give_unique_ids() {
        let dir = TempDir::new("parallel");
        // Two handles on one file: the case of two processes.
        let a = Arc::new(StateDb::open(&dir.db()).unwrap());
        let b = Arc::new(StateDb::open(&dir.db()).unwrap());
        let handles: Vec<_> = (0..8)
            .map(|i| {
                let db = if i % 2 == 0 { a.clone() } else { b.clone() };
                std::thread::spawn(move || {
                    (0..50)
                        .flat_map(|_| db.reserve_ids(2).unwrap())
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        let ids: Vec<u64> = handles
            .into_iter()
            .flat_map(|h| h.join().unwrap())
            .collect();
        let unique: HashSet<_> = ids.iter().copied().collect();
        assert_eq!(ids.len(), 800);
        assert_eq!(unique.len(), 800);
        assert_eq!(a.next_id().unwrap(), 800);
    }
}
