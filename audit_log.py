"""Append-only audit trail for multi-user memory operations.

Enabled via AUDIT_LOG=true env var. Records who did what, when, from where.
"""

import logging
import sqlite3
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("memories.audit")


class NullAuditLog:
    """No-op when audit logging is disabled."""

    def log(self, **kwargs) -> None:
        pass

    def query(self, **kwargs) -> List[Dict[str, Any]]:
        return []

    def count(self, action=None, key_id=None, **kwargs) -> int:
        return 0

    def purge(self, retention_days: int = 90) -> int:
        return 0


class AuditLog:
    """SQLite-backed append-only audit trail."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
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
            CREATE INDEX IF NOT EXISTS idx_audit_resource_id ON audit_log(resource_id);
        """)
        conn.close()
        logger.info("Audit log initialized: %s", db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._connect()
        return self._local.conn

    def log(
        self,
        action: str,
        key_id: str = "",
        key_name: str = "",
        resource_id: str = "",
        source_prefix: str = "",
        ip: str = "",
    ) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO audit_log (action, key_id, key_name, resource_id, source_prefix, ip) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (action, key_id, key_name, resource_id, source_prefix, ip),
            )
            conn.commit()
        except Exception:
            logger.debug("Failed to write audit entry", exc_info=True)

    def query(
        self,
        action: Optional[str] = None,
        key_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            clauses = []
            params: list = []
            if action:
                clauses.append("action = ?")
                params.append(action)
            if key_id:
                clauses.append("key_id = ?")
                params.append(key_id)
            if resource_id:
                clauses.append("resource_id = ?")
                params.append(resource_id)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count(self, action: Optional[str] = None, key_id: Optional[str] = None) -> int:
        conn = self._connect()
        try:
            clauses: list = []
            params: list = []
            if action:
                clauses.append("action = ?")
                params.append(action)
            if key_id:
                clauses.append("key_id = ?")
                params.append(key_id)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            row = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()
            return row[0]
        finally:
            conn.close()

    def purge(self, retention_days: int = 90) -> int:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM audit_log WHERE ts < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
                (f"-{retention_days} days",),
            )
            conn.commit()
            purged = cursor.rowcount
            if purged > 0:
                logger.info("Purged %d audit entries older than %d days", purged, retention_days)
            return purged
        except Exception:
            logger.debug("Failed to purge audit log", exc_info=True)
            return 0
