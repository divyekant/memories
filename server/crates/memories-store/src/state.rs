//! `state.db`: the SQLite file of the Rust server. It holds the ID counter and
//! the BM25 document-length sums of decision P0-2 (table `meta`).
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
             );
             CREATE TABLE IF NOT EXISTS meta (
                 name TEXT PRIMARY KEY,
                 value TEXT NOT NULL
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

    /// The sum of the token counts of all stored texts, and the record count.
    /// The keyword vector of a write uses `sum / count` as `avgdl`.
    pub fn doc_lengths(&self) -> Result<(u64, u64), StateError> {
        let conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        read_doc_lengths(&conn)
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
        let (sum, count) = read_doc_lengths(&tx)?;
        let apply = |v: u64, d: i64| (v as i64).saturating_add(d).max(0) as u64;
        let new = (apply(sum, sum_delta), apply(count, count_delta));
        write_doc_lengths(&tx, new)?;
        tx.commit()?;
        Ok(new)
    }

    /// Set both values, for example after a migration.
    pub fn set_doc_lengths(&self, sum: u64, count: u64) -> Result<(), StateError> {
        let mut conn = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        write_doc_lengths(&tx, (sum, count))?;
        tx.commit()?;
        Ok(())
    }
}

const DOC_LEN_SUM: &str = "doc_len_sum";
const DOC_COUNT: &str = "doc_count";

fn read_doc_lengths(conn: &Connection) -> Result<(u64, u64), StateError> {
    let get = |name: &str| -> Result<u64, StateError> {
        let v: Option<String> = conn
            .query_row("SELECT value FROM meta WHERE name = ?1", [name], |r| {
                r.get(0)
            })
            .optional()?;
        Ok(v.and_then(|v| v.parse().ok()).unwrap_or(0))
    };
    Ok((get(DOC_LEN_SUM)?, get(DOC_COUNT)?))
}

fn write_doc_lengths(conn: &Connection, (sum, count): (u64, u64)) -> Result<(), StateError> {
    for (name, v) in [(DOC_LEN_SUM, sum), (DOC_COUNT, count)] {
        conn.execute(
            "INSERT INTO meta (name, value) VALUES (?1, ?2)
             ON CONFLICT (name) DO UPDATE SET value = excluded.value",
            rusqlite::params![name, v.to_string()],
        )?;
    }
    Ok(())
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
    fn doc_lengths_add_subtract_and_persist() {
        let dir = TempDir::new("doclen");
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.doc_lengths().unwrap(), (0, 0));
        assert_eq!(db.add_doc_lengths(12, 2).unwrap(), (12, 2));
        assert_eq!(db.add_doc_lengths(-5, -1).unwrap(), (7, 1));
        // A replace: the new length minus the old length, no count change.
        assert_eq!(db.add_doc_lengths(3, 0).unwrap(), (10, 1));
        drop(db);
        let db = StateDb::open(&dir.db()).unwrap();
        assert_eq!(db.doc_lengths().unwrap(), (10, 1));
        db.set_doc_lengths(900, 30).unwrap();
        assert_eq!(db.doc_lengths().unwrap(), (900, 30));
        // A sum never goes below 0, also after a lost update.
        assert_eq!(db.add_doc_lengths(-1000, -40).unwrap(), (0, 0));
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
