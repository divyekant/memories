"""SQLite-backed API key store for multi-auth support.

Manages API key lifecycle: generation, hashing, lookup, update, and revocation.
Uses WAL mode and thread-local connections following the usage_tracker.py pattern.
"""
import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

VALID_ROLES = {"read-only", "read-write", "admin"}


class KeyStore:
    """SQLite-backed API key store with WAL mode for non-blocking writes."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                role TEXT NOT NULL,
                prefixes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                usage_count INTEGER DEFAULT 0,
                revoked INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
        """)
        conn.close()
        logger.info("KeyStore initialized: %s", db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        """Thread-local connection for write operations."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._connect()
        return self._local.conn

    @staticmethod
    def generate_raw_key() -> str:
        """Generate a raw API key: 'mem_' + 32 hex chars = 36 chars total."""
        return "mem_" + secrets.token_hex(16)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """SHA-256 hex digest of a raw API key."""
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def create_key(self, name: str, role: str, prefixes: list[str]) -> dict[str, Any]:
        """Create a new API key. Returns dict including raw key (shown once)."""
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {VALID_ROLES}")

        # Admin keys ignore prefix scoping
        if role == "admin":
            prefixes = []

        raw_key = self.generate_raw_key()
        key_hash = self.hash_key(raw_key)
        key_id = str(uuid.uuid4())
        key_prefix = raw_key[:8]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prefixes_json = json.dumps(prefixes)

        conn = self._get_conn()
        conn.execute(
            "INSERT INTO api_keys (id, name, key_hash, key_prefix, role, prefixes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key_id, name, key_hash, key_prefix, role, prefixes_json, now),
        )
        conn.commit()

        return {
            "id": key_id,
            "key": raw_key,
            "key_prefix": key_prefix,
            "name": name,
            "role": role,
            "prefixes": prefixes,
            "created_at": now,
        }

    def lookup(self, raw_key: str) -> dict[str, Any] | None:
        """Look up a key by its raw value. Returns None if not found or revoked."""
        key_hash = self.hash_key(raw_key)
        conn = self._get_conn()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE api_keys SET last_used_at = ?, usage_count = usage_count + 1 "
            "WHERE key_hash = ? AND revoked = 0",
            (now, key_hash),
        )
        conn.commit()

        row = conn.execute(
            "SELECT id, name, key_prefix, role, prefixes, created_at, last_used_at, "
            "usage_count, revoked FROM api_keys WHERE key_hash = ? AND revoked = 0",
            (key_hash,),
        ).fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "name": row["name"],
            "key_prefix": row["key_prefix"],
            "role": row["role"],
            "prefixes": json.loads(row["prefixes"]),
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
            "usage_count": row["usage_count"],
            "revoked": row["revoked"],
        }

    def list_keys(self) -> list[dict[str, Any]]:
        """List all keys (including revoked), without exposing raw key or hash."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, key_prefix, role, prefixes, created_at, "
                "last_used_at, usage_count, revoked FROM api_keys ORDER BY created_at"
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "key_prefix": row["key_prefix"],
                    "role": row["role"],
                    "prefixes": json.loads(row["prefixes"]),
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"],
                    "usage_count": row["usage_count"],
                    "revoked": row["revoked"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def update_key(self, key_id: str, **fields: Any) -> None:
        """Update name, role, or prefixes on a non-revoked key."""
        allowed = {"name", "role", "prefixes"}
        updates = {k: v for k, v in fields.items() if k in allowed}

        if "role" in updates and updates["role"] not in VALID_ROLES:
            raise ValueError(f"Invalid role '{updates['role']}'. Must be one of: {VALID_ROLES}")

        conn = self._get_conn()
        row = conn.execute(
            "SELECT revoked FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()

        if row is None:
            raise ValueError(f"Key '{key_id}' not found")
        if row["revoked"]:
            raise ValueError(f"Key '{key_id}' is revoked")

        set_parts = []
        params: list[Any] = []
        for k, v in updates.items():
            if k == "prefixes":
                set_parts.append("prefixes = ?")
                params.append(json.dumps(v))
            else:
                set_parts.append(f"{k} = ?")
                params.append(v)

        if set_parts:
            params.append(key_id)
            conn.execute(
                f"UPDATE api_keys SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )
            conn.commit()

    def revoke(self, key_id: str) -> None:
        """Revoke a key. Raises ValueError if not found or already revoked."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT revoked FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()

        if row is None:
            raise ValueError(f"Key '{key_id}' not found")
        if row["revoked"]:
            raise ValueError(f"Key '{key_id}' is already revoked")

        conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        conn.commit()
