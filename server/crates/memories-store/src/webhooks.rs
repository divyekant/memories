//! The webhooks: a table in `state.db`.
//!
//! The Python server kept the webhooks in RAM, so a restart lost them, and
//! the ID counter started at 1 again. Here each webhook is a row, and an ID
//! is not given out again.

use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;

use rusqlite::{Connection, params};
use serde_json::{Value, json};

use crate::StateError;

#[derive(Clone, Debug, PartialEq)]
pub struct Webhook {
    /// The Python ID is a string of a counter.
    pub id: String,
    pub url: String,
    pub events: Vec<String>,
    pub created_at: String,
}

impl Webhook {
    /// The body of `POST /webhooks`, and one item of `GET /webhooks`.
    pub fn to_json(&self) -> Value {
        json!({"id": self.id, "url": self.url, "events": self.events,
               "created_at": self.created_at})
    }
}

pub struct Webhooks {
    conn: Mutex<Connection>,
}

fn row(r: &rusqlite::Row) -> rusqlite::Result<Webhook> {
    let events: String = r.get(2)?;
    Ok(Webhook {
        id: r.get::<_, i64>(0)?.to_string(),
        url: r.get(1)?,
        events: serde_json::from_str(&events).unwrap_or_default(),
        created_at: r.get(3)?,
    })
}

impl Webhooks {
    pub fn open(path: &Path) -> Result<Self, StateError> {
        let conn = Connection::open(path)?;
        conn.busy_timeout(Duration::from_secs(10))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        // AUTOINCREMENT: the ID of a deleted webhook is not given out again.
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS webhooks (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 url TEXT NOT NULL,
                 events TEXT NOT NULL,
                 created_at TEXT NOT NULL
             )",
        )?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    fn conn(&self) -> std::sync::MutexGuard<'_, Connection> {
        self.conn.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn create(
        &self,
        url: &str,
        events: &[String],
        created_at: &str,
    ) -> Result<Webhook, StateError> {
        let conn = self.conn();
        let text = serde_json::to_string(events).expect("a list of strings");
        conn.execute(
            "INSERT INTO webhooks (url, events, created_at) VALUES (?1, ?2, ?3)",
            params![url, text, created_at],
        )?;
        Ok(Webhook {
            id: conn.last_insert_rowid().to_string(),
            url: url.into(),
            events: events.to_vec(),
            created_at: created_at.into(),
        })
    }

    /// All webhooks, oldest first (the Python dict order).
    pub fn list(&self) -> Result<Vec<Webhook>, StateError> {
        let conn = self.conn();
        let mut st =
            conn.prepare("SELECT id, url, events, created_at FROM webhooks ORDER BY id")?;
        Ok(st.query_map([], row)?.collect::<Result<_, _>>()?)
    }

    /// True if a webhook had this ID. Python compares the ID strings, so
    /// "01" does not match "1".
    pub fn delete(&self, id: &str) -> Result<bool, StateError> {
        let Some(n) = id.parse::<i64>().ok().filter(|n| n.to_string() == id) else {
            return Ok(false);
        };
        Ok(self
            .conn()
            .execute("DELETE FROM webhooks WHERE id = ?1", [n])?
            > 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_db() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "memories-webhooks-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir.join("state.db")
    }

    #[test]
    fn ids_count_up_survive_a_reopen_and_are_not_given_again() {
        let path = temp_db();
        let w = Webhooks::open(&path).unwrap();
        let a = w
            .create("http://a/1", &["memory.added".into()], "t1")
            .unwrap();
        let b = w.create("http://b/2", &[], "t2").unwrap();
        assert_eq!((a.id.as_str(), b.id.as_str()), ("1", "2"));
        assert_eq!(
            a.to_json(),
            json!({"id": "1", "url": "http://a/1", "events": ["memory.added"], "created_at": "t1"})
        );
        assert!(!w.delete("02").unwrap());
        assert!(!w.delete("x").unwrap());
        assert!(w.delete("2").unwrap());
        assert!(!w.delete("2").unwrap());
        drop(w);
        let w = Webhooks::open(&path).unwrap();
        assert_eq!(w.list().unwrap(), vec![a]);
        assert_eq!(w.create("http://c", &[], "t3").unwrap().id, "3");
    }
}
