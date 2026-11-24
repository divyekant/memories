//! The audit trail in `audit.db`, the same schema as the Python `AuditLog`.
//! `AUDIT_LOG=true` turns it on. When it is off, the routes act as `NullAuditLog`.

use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rusqlite::{Connection, ToSql};
use serde_json::{Map, Value};

use crate::{AppState, auth::AuthContext, keys::sql_to_json, shell::Client};

pub struct AuditLog {
    conn: Mutex<Connection>,
}

/// The filters of `GET /audit`. An empty value is no filter, as in Python.
#[derive(Debug, Default)]
pub struct Filter {
    pub action: Option<String>,
    pub key_id: Option<String>,
    pub resource_id: Option<String>,
}

impl Filter {
    fn clause(&self) -> (String, Vec<&dyn ToSql>) {
        let mut parts = Vec::new();
        let mut params: Vec<&dyn ToSql> = Vec::new();
        for (col, v) in [
            ("action", &self.action),
            ("key_id", &self.key_id),
            ("resource_id", &self.resource_id),
        ] {
            if let Some(v) = v.as_ref().filter(|v| !v.is_empty()) {
                parts.push(format!("{col} = ?"));
                params.push(v);
            }
        }
        let clause = if parts.is_empty() {
            String::new()
        } else {
            format!("WHERE {}", parts.join(" AND "))
        };
        (clause, params)
    }
}

impl AuditLog {
    /// Opens or creates the database and runs the same migration as the Python server.
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        let conn = Connection::open(path)?;
        conn.busy_timeout(Duration::from_secs(5))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                action TEXT NOT NULL,
                key_id TEXT DEFAULT '',
                key_name TEXT DEFAULT '',
                resource_id TEXT DEFAULT '',
                source_prefix TEXT DEFAULT '',
                ip TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
            CREATE INDEX IF NOT EXISTS idx_audit_key ON audit_log(key_id);
            CREATE INDEX IF NOT EXISTS idx_audit_resource_id ON audit_log(resource_id);",
        )?;
        let has_metadata = conn
            .prepare("SELECT 1 FROM pragma_table_info('audit_log') WHERE name = 'metadata_json'")?
            .exists([])?;
        if !has_metadata {
            conn.execute(
                "ALTER TABLE audit_log ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
                [],
            )?;
        }
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    /// `AuditLog.log`. A failed write is logged and dropped, as in Python.
    pub fn log(&self, e: &Entry) {
        let meta = Value::Object(e.metadata.clone()).to_string();
        let r = self.conn.lock().unwrap().execute(
            "INSERT INTO audit_log (action, key_id, key_name, resource_id, source_prefix, ip, metadata_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params![
                e.action,
                e.key_id,
                e.key_name,
                e.resource_id,
                e.source_prefix,
                e.ip,
                meta
            ],
        );
        if let Err(err) = r {
            tracing::debug!(error = %err, "failed to write audit entry");
        }
    }

    /// `AuditLog.query`: newest first. Each row has all its columns, with
    /// `metadata_json` replaced by the object `metadata`.
    pub fn query(&self, f: &Filter, limit: i64, offset: i64) -> rusqlite::Result<Vec<Value>> {
        let (clause, mut params) = f.clause();
        params.push(&limit);
        params.push(&offset);
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(&format!(
            "SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?"
        ))?;
        let names: Vec<String> = stmt.column_names().map(str::to_string).collect();
        stmt.query_map(params.as_slice(), |r| {
            let mut m = Map::new();
            for (i, n) in names.iter().enumerate() {
                let v = sql_to_json(r.get_ref(i)?);
                if n == "metadata_json" {
                    let meta = v
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .and_then(|s| serde_json::from_str::<Value>(s).ok())
                        .filter(Value::is_object)
                        .unwrap_or_else(|| Value::Object(Map::new()));
                    m.insert("metadata".into(), meta);
                } else {
                    m.insert(n.clone(), v);
                }
            }
            Ok(Value::Object(m))
        })?
        .collect()
    }

    pub fn count(&self, f: &Filter) -> rusqlite::Result<i64> {
        let (clause, params) = f.clause();
        self.conn.lock().unwrap().query_row(
            &format!("SELECT COUNT(*) FROM audit_log {clause}"),
            params.as_slice(),
            |r| r.get(0),
        )
    }

    /// `AuditLog.purge`: deletes the rows older than the retention period.
    /// A failure gives 0, as in Python.
    pub fn purge(&self, retention_days: i64) -> i64 {
        let r = self.conn.lock().unwrap().execute(
            "DELETE FROM audit_log WHERE ts < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?1)",
            [format!("-{retention_days} days")],
        );
        match r {
            Ok(n) => {
                if n > 0 {
                    tracing::info!("Purged {n} audit entries older than {retention_days} days");
                }
                n as i64
            }
            Err(e) => {
                tracing::debug!(error = %e, "failed to purge audit log");
                0
            }
        }
    }
}

/// One audit row. `metadata` is written with sorted keys and no spaces, as Python does.
#[derive(Debug, Default, Clone)]
pub struct Entry {
    pub action: String,
    pub key_id: String,
    pub key_name: String,
    pub resource_id: String,
    pub source_prefix: String,
    pub ip: String,
    pub metadata: Map<String, Value>,
}

impl Entry {
    /// Python `_audit`: the caller's key id (`env` for the admin key or no key),
    /// key name and client host.
    pub fn from_request(auth: &AuthContext, client: Option<&Client>, action: &str) -> Self {
        Self {
            action: action.into(),
            key_id: auth
                .key_id
                .clone()
                .filter(|k| !k.is_empty())
                .unwrap_or_else(|| "env".into()),
            key_name: auth.key_name.clone().unwrap_or_default(),
            ip: client.map(|c| c.host.clone()).unwrap_or_default(),
            ..Self::default()
        }
    }
}

impl AppState {
    /// Writes an audit row if `AUDIT_LOG` is on. Each route that the Python
    /// server audits calls this with the same action names.
    pub async fn audit(self: &Arc<Self>, entry: Entry) {
        if self.audit.is_none() {
            return;
        }
        let st = self.clone();
        let _ = tokio::task::spawn_blocking(move || {
            if let Some(a) = &st.audit {
                a.log(&entry);
            }
        })
        .await;
    }
}
