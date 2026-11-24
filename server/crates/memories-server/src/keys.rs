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

/// The reply of `POST /api/keys`: the raw key is in it only this one time.
#[derive(Debug)]
pub struct NewKey {
    pub id: String,
    pub key: String,
    pub key_prefix: String,
    pub name: String,
    pub principal_id: Option<String>,
    pub role: String,
    pub prefixes: Vec<String>,
    pub created_at: String,
}

/// The fields that `PATCH /api/keys/{key_id}` changes. `None` keeps the value.
#[derive(Debug, Default)]
pub struct KeyUpdate {
    pub name: Option<String>,
    pub principal_id: Option<String>,
    pub role: Option<String>,
    pub prefixes: Option<Vec<String>>,
}

/// Why a change to a key did not occur. The text is the Python `ValueError` text.
#[derive(Debug)]
pub enum KeyError {
    NotFound(String),
    Revoked(String),
    AlreadyRevoked(String),
    Db(rusqlite::Error),
}

impl From<rusqlite::Error> for KeyError {
    fn from(e: rusqlite::Error) -> Self {
        Self::Db(e)
    }
}

impl KeyStore {
    /// `KeyStore.create_key`. The caller checks the role and the principal slug.
    pub fn create(
        &self,
        name: &str,
        role: &str,
        prefixes: Vec<String>,
        principal_id: Option<String>,
    ) -> rusqlite::Result<NewKey> {
        let principal_id = principal_id.or_else(|| is_valid_slug(name).then(|| name.to_string()));
        // Admin keys ignore prefix scope.
        let prefixes = if role == "admin" {
            Vec::new()
        } else {
            prefixes
        };
        let key = format!("mem_{}", hex(&random_bytes::<16>()));
        let new = NewKey {
            id: uuid4(),
            key_prefix: key[..8].to_string(),
            name: name.to_string(),
            principal_id,
            role: role.to_string(),
            created_at: utc_now(),
            prefixes,
            key,
        };
        self.conn.lock().unwrap().execute(
            "INSERT INTO api_keys (id, name, principal_id, key_hash, key_prefix, role, prefixes, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                new.id,
                new.name,
                new.principal_id,
                hash_key(&new.key),
                new.key_prefix,
                new.role,
                py_json_list(&new.prefixes),
                new.created_at
            ],
        )?;
        Ok(new)
    }

    /// `KeyStore.list_keys`: all keys, revoked keys too, with no raw key or hash.
    /// The buffered uses go to the database first, so the counts are current.
    pub fn list(&self) -> rusqlite::Result<Vec<serde_json::Value>> {
        if let Err(e) = self.flush_usage() {
            tracing::warn!(error = %e, "keys.db: usage write failed before the key list");
        }
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, name, principal_id, key_prefix, role, prefixes, created_at,
                    last_used_at, usage_count, revoked FROM api_keys ORDER BY created_at",
        )?;
        let names: Vec<String> = stmt.column_names().map(str::to_string).collect();
        stmt.query_map([], |r| {
            let mut m = serde_json::Map::new();
            for (i, n) in names.iter().enumerate() {
                let v = sql_to_json(r.get_ref(i)?);
                let v = if n == "prefixes" {
                    // Python json.loads: a value that is not JSON fails the request.
                    let text = v.as_str().unwrap_or_default().to_string();
                    serde_json::from_str(&text).map_err(|e| {
                        rusqlite::Error::FromSqlConversionFailure(
                            i,
                            rusqlite::types::Type::Text,
                            Box::new(e),
                        )
                    })?
                } else {
                    v
                };
                m.insert(n.clone(), v);
            }
            Ok(serde_json::Value::Object(m))
        })?
        .collect()
    }

    /// `KeyStore.update_key`: changes the given fields of a live key.
    pub fn update(&self, key_id: &str, u: &KeyUpdate) -> Result<(), KeyError> {
        let conn = self.conn.lock().unwrap();
        let tx = conn.unchecked_transaction()?;
        live_key(&tx, key_id, || {
            KeyError::Revoked(format!("Key '{key_id}' is revoked"))
        })?;
        let fields = [
            ("name", u.name.clone()),
            ("principal_id", u.principal_id.clone()),
            ("role", u.role.clone()),
            ("prefixes", u.prefixes.as_deref().map(py_json_list)),
        ];
        for (col, v) in fields {
            if let Some(v) = v {
                tx.execute(
                    &format!("UPDATE api_keys SET {col} = ?1 WHERE id = ?2"),
                    params![v, key_id],
                )?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    /// `KeyStore.revoke`: a soft delete. The key fails auth at the next request.
    pub fn revoke(&self, key_id: &str) -> Result<(), KeyError> {
        let conn = self.conn.lock().unwrap();
        let tx = conn.unchecked_transaction()?;
        live_key(&tx, key_id, || {
            KeyError::AlreadyRevoked(format!("Key '{key_id}' is already revoked"))
        })?;
        tx.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?1", [key_id])?;
        tx.commit()?;
        Ok(())
    }
}

fn live_key(
    conn: &Connection,
    key_id: &str,
    revoked: impl FnOnce() -> KeyError,
) -> Result<(), KeyError> {
    let row: Option<Option<i64>> = conn
        .query_row(
            "SELECT revoked FROM api_keys WHERE id = ?1",
            [key_id],
            |r| r.get(0),
        )
        .optional()?;
    match row {
        None => Err(KeyError::NotFound(format!("Key '{key_id}' not found"))),
        // Python: `if row["revoked"]`, so NULL and 0 are live.
        Some(Some(r)) if r != 0 => Err(revoked()),
        Some(_) => Ok(()),
    }
}

/// A SQLite value as JSON, as Python `sqlite3` and `json` give it.
pub fn sql_to_json(v: rusqlite::types::ValueRef<'_>) -> serde_json::Value {
    use rusqlite::types::ValueRef;
    match v {
        ValueRef::Null => serde_json::Value::Null,
        ValueRef::Integer(i) => i.into(),
        ValueRef::Real(f) => f.into(),
        ValueRef::Text(t) | ValueRef::Blob(t) => String::from_utf8_lossy(t).into(),
    }
}

/// Python `json.dumps(list_of_str)`: `", "` between items and only ASCII
/// (`\uXXXX` for each other character), so the stored text is the same.
pub fn py_json_list(items: &[String]) -> String {
    let mut out = String::from("[");
    for (i, s) in items.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        for c in serde_json::to_string(s).unwrap().chars() {
            if (' '..='~').contains(&c) || matches!(c, '\n' | '\r' | '\t') {
                out.push(c);
            } else {
                let mut buf = [0u16; 2];
                for u in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{u:04x}"));
                }
            }
        }
    }
    out.push(']');
    out
}

fn random_bytes<const N: usize>() -> [u8; N] {
    let mut b = [0u8; N];
    getrandom::fill(&mut b).expect("the OS random source failed");
    b
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Python `uuid.uuid4().hex`.
pub fn uuid4_hex() -> String {
    uuid4().replace('-', "")
}

/// Python `str(uuid.uuid4())`.
fn uuid4() -> String {
    let mut b = random_bytes::<16>();
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h = hex(&b);
    format!(
        "{}-{}-{}-{}-{}",
        &h[..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..]
    )
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
    format!("{}Z", format_utc(secs))
}

/// Python `datetime.now(timezone.utc).isoformat()`.
pub fn utc_now_iso() -> String {
    let d = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    format_iso(d.as_secs(), d.subsec_micros())
}

fn format_iso(secs: u64, micros: u32) -> String {
    if micros == 0 {
        format!("{}+00:00", format_utc(secs))
    } else {
        format!("{}.{micros:06}+00:00", format_utc(secs))
    }
}

/// `%Y-%m-%dT%H:%M:%S` in UTC.
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
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}",
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
        assert_eq!(format_utc(0), "1970-01-01T00:00:00");
        assert_eq!(format_utc(951_782_400), "2000-02-29T00:00:00");
        assert_eq!(format_utc(1_794_815_700), "2026-11-16T07:55:00");
        // Python: datetime.fromtimestamp(t, timezone.utc).isoformat()
        assert_eq!(format_iso(1_794_815_700, 0), "2026-11-16T07:55:00+00:00");
        assert_eq!(
            format_iso(1_794_815_700, 42),
            "2026-11-16T07:55:00.000042+00:00"
        );
        assert_eq!(uuid4_hex().len(), 32);
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
    fn python_json_dumps_of_prefixes() {
        let v = |a: &[&str]| py_json_list(&a.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert_eq!(v(&[]), "[]");
        assert_eq!(v(&["codex/", "wip/"]), r#"["codex/", "wip/"]"#);
        // Python: json.dumps(["é😀\x7f\n\"\\"]) == '["\\u00e9\\ud83d\\ude00\\u007f\\n\\"\\\\"]'
        assert_eq!(
            v(&["é😀\u{7f}\n\"\\"]),
            r#"["\u00e9\ud83d\ude00\u007f\n\"\\"]"#
        );
        assert_eq!(v(&["\u{1f}"]), r#"["\u001f"]"#);
    }

    #[test]
    fn new_key_and_id_shapes() {
        let id = uuid4();
        assert_eq!(id.len(), 36);
        assert_eq!(&id[14..15], "4");
        assert!("89ab".contains(&id[19..20]));
        assert_ne!(uuid4(), id);
    }

    #[test]
    fn sha256_hex() {
        assert_eq!(
            hash_key("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
