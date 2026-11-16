//! Managed API keys in `keys.db`, the same schema as the Python `KeyStore`.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, OptionalExtension, params};
use sha2::{Digest, Sha256};

#[derive(Clone, Debug, PartialEq)]
pub struct KeyRecord {
    pub id: String,
    pub name: String,
    pub principal_id: Option<String>,
    pub role: String,
    pub prefixes: Vec<String>,
}

pub struct KeyStore {
    conn: Mutex<Connection>,
    /// key hash -> (uses since the last flush, time of the last use).
    usage: Mutex<HashMap<String, (i64, String)>>,
}

pub fn hash_key(raw: &str) -> String {
    Sha256::digest(raw.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// `project_memory.is_valid_slug`: `^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`.
pub fn is_valid_slug(s: &str) -> bool {
    let ok = |c: u8| c.is_ascii_lowercase() || c.is_ascii_digit();
    let b = s.as_bytes();
    !b.is_empty() && ok(b[0]) && ok(b[b.len() - 1]) && b.iter().all(|&c| ok(c) || c == b'-')
}

impl KeyStore {
    /// Opens or creates the database and runs the same migration as the Python server.
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        let conn = Connection::open(path)?;
        conn.busy_timeout(Duration::from_secs(5))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                principal_id TEXT,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                role TEXT NOT NULL,
                prefixes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                usage_count INTEGER DEFAULT 0,
                revoked INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);",
        )?;
        let tx = conn.unchecked_transaction()?;
        let has_principal: bool = tx
            .prepare("SELECT 1 FROM pragma_table_info('api_keys') WHERE name = 'principal_id'")?
            .exists([])?;
        if !has_principal {
            tx.execute("ALTER TABLE api_keys ADD COLUMN principal_id TEXT", [])?;
            for (id, name) in rows(&tx, "SELECT id, name FROM api_keys")? {
                if is_valid_slug(&name) {
                    tx.execute(
                        "UPDATE api_keys SET principal_id = ?1 WHERE id = ?2",
                        params![name, id],
                    )?;
                }
            }
        }
        for (id, principal) in rows(
            &tx,
            "SELECT id, principal_id FROM api_keys WHERE principal_id IS NOT NULL",
        )? {
            if !is_valid_slug(&principal) {
                tx.execute(
                    "UPDATE api_keys SET principal_id = NULL WHERE id = ?1",
                    [id],
                )?;
            }
        }
        tx.commit()?;
        Ok(Self {
            conn: Mutex::new(conn),
            usage: Mutex::new(HashMap::new()),
        })
    }

    /// Returns the live (not revoked) key. The use goes to a buffer that
    /// [`KeyStore::flush_usage`] writes, not to the database on each request.
    pub fn lookup(&self, raw_key: &str) -> rusqlite::Result<Option<KeyRecord>> {
        let hash = hash_key(raw_key);
        let row = self
            .conn
            .lock()
            .unwrap()
            .query_row(
                "SELECT id, name, principal_id, role, prefixes FROM api_keys
                 WHERE key_hash = ?1 AND revoked = 0",
                [&hash],
                |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, Option<String>>(2)?,
                        r.get::<_, String>(3)?,
                        r.get::<_, String>(4)?,
                    ))
                },
            )
            .optional()?;
        let Some((id, name, principal_id, role, prefixes)) = row else {
            return Ok(None);
        };
        let entry = &mut *self.usage.lock().unwrap();
        let e = entry.entry(hash).or_insert((0, String::new()));
        e.0 += 1;
        e.1 = utc_now();
        // A value that is not a list of strings gives no prefixes: the key can read nothing.
        let prefixes = serde_json::from_str(&prefixes).unwrap_or_else(|_| {
            tracing::warn!(key_id = %id, "keys.db: prefixes is not a JSON list of strings");
            Vec::new()
        });
        Ok(Some(KeyRecord {
            id,
            name,
            principal_id,
            role,
            prefixes,
        }))
    }

    /// Writes the buffered `last_used_at` and `usage_count` values. Returns the
    /// number of keys written. If the write fails, the values stay in the buffer.
    pub fn flush_usage(&self) -> rusqlite::Result<usize> {
        let pending = std::mem::take(&mut *self.usage.lock().unwrap());
        if pending.is_empty() {
            return Ok(0);
        }
        let result = (|| {
            let conn = self.conn.lock().unwrap();
            let tx = conn.unchecked_transaction()?;
            for (hash, (count, last)) in &pending {
                tx.execute(
                    "UPDATE api_keys SET last_used_at = ?1, usage_count = usage_count + ?2
                     WHERE key_hash = ?3 AND revoked = 0",
                    params![last, count, hash],
                )?;
            }
            tx.commit()
        })();
        if let Err(e) = result {
            let mut usage = self.usage.lock().unwrap();
            for (hash, (count, last)) in pending {
                let e = usage.entry(hash).or_insert((0, String::new()));
                e.0 += count;
                if e.1 < last {
                    e.1 = last;
                }
            }
            return Err(e);
        }
        Ok(pending.len())
    }
}

fn rows(conn: &Connection, sql: &str) -> rusqlite::Result<Vec<(String, String)>> {
    conn.prepare(sql)?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect()
}

/// UTC time as `%Y-%m-%dT%H:%M:%SZ`, the format of the Python key store.
// shortcut: hand-written date code; use the date crate that a later task picks.
pub fn utc_now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    format_utc(secs)
}

fn format_utc(secs: u64) -> String {
    let (days, rem) = (secs / 86_400, secs % 86_400);
    // Days to civil date (Howard Hinnant's algorithm).
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_format() {
        assert_eq!(format_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(format_utc(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(format_utc(1_794_815_700), "2026-11-16T07:55:00Z");
    }

    #[test]
    fn slug_rule() {
        for ok in ["a", "parity-ro", "k8s", "0-x"] {
            assert!(is_valid_slug(ok), "{ok}");
        }
        for bad in ["", "-a", "a-", "A", "a_b", "a b", "é"] {
            assert!(!is_valid_slug(bad), "{bad}");
        }
    }

    #[test]
    fn sha256_hex() {
        assert_eq!(
            hash_key("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
