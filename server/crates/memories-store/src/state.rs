//! `state.db`: the SQLite file of the Rust server. It holds the ID counter.
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

#[derive(Debug, thiserror::Error)]
pub enum StateError {
    #[error("state.db: {0}")]
    Sqlite(#[from] rusqlite::Error),
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
