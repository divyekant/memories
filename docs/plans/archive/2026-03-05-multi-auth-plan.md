# Multi-Auth Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add prefix-scoped API keys with three role tiers (read-only, read-write, admin) and a UI management page, while keeping the existing single API_KEY env var as backward-compatible implicit admin.

**Architecture:** New `key_store.py` module handles SQLite key table, hashing, and lookups. The `verify_api_key` function in `app.py` gains a two-path check (env var first, then DB). A request-scoped `AuthContext` dataclass flows role+prefixes through every endpoint. Key management endpoints are admin-gated. The Web UI API Keys page is conditionally shown based on caller role.

**Tech Stack:** Python 3.11, FastAPI, SQLite (via existing `usage_tracker.py` pattern), SHA-256 (hashlib), secrets module, pytest, existing TestClient fixtures.

---

### Task 1: Key Store Module — Schema and Key Generation

**Files:**
- Create: `key_store.py`
- Create: `tests/test_key_store.py`

**Step 1: Write the failing tests**

```python
# tests/test_key_store.py
"""Tests for API key store — generation, hashing, CRUD."""
import os
import tempfile

import pytest

from key_store import KeyStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "keys.db")
        yield KeyStore(db_path)


class TestKeyGeneration:
    def test_generate_key_has_mem_prefix(self, store):
        raw_key = store.generate_raw_key()
        assert raw_key.startswith("mem_")
        assert len(raw_key) == 36  # "mem_" + 32 hex chars

    def test_hash_key_is_deterministic(self, store):
        key = "mem_a3f8b2c1d4e5f6071829304a5b6c7d8e"
        assert store.hash_key(key) == store.hash_key(key)

    def test_hash_key_is_hex_sha256(self, store):
        import hashlib
        key = "mem_test1234"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert store.hash_key(key) == expected


class TestCreateKey:
    def test_create_returns_key_with_id(self, store):
        result = store.create_key(
            name="test-key",
            role="read-write",
            prefixes=["claude-code/*", "learning/*"],
        )
        assert "id" in result
        assert "key" in result
        assert result["key"].startswith("mem_")
        assert result["name"] == "test-key"
        assert result["role"] == "read-write"
        assert result["prefixes"] == ["claude-code/*", "learning/*"]

    def test_create_admin_key_ignores_prefixes(self, store):
        result = store.create_key(name="admin", role="admin", prefixes=[])
        assert result["role"] == "admin"
        assert result["prefixes"] == []

    def test_create_rejects_invalid_role(self, store):
        with pytest.raises(ValueError, match="Invalid role"):
            store.create_key(name="bad", role="superadmin", prefixes=[])


class TestLookupKey:
    def test_lookup_existing_key(self, store):
        created = store.create_key(name="lookup-test", role="read-only", prefixes=["kai/*"])
        found = store.lookup(created["key"])
        assert found is not None
        assert found["id"] == created["id"]
        assert found["role"] == "read-only"
        assert found["prefixes"] == ["kai/*"]

    def test_lookup_nonexistent_returns_none(self, store):
        assert store.lookup("mem_doesnotexist000000000000000000") is None

    def test_lookup_revoked_key_returns_none(self, store):
        created = store.create_key(name="to-revoke", role="read-write", prefixes=["x/*"])
        store.revoke(created["id"])
        assert store.lookup(created["key"]) is None

    def test_lookup_increments_usage(self, store):
        created = store.create_key(name="usage-test", role="read-write", prefixes=["a/*"])
        store.lookup(created["key"])
        store.lookup(created["key"])
        keys = store.list_keys()
        match = [k for k in keys if k["id"] == created["id"]][0]
        assert match["usage_count"] == 2
        assert match["last_used_at"] is not None
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_key_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'key_store'`

**Step 3: Write the implementation**

```python
# key_store.py
"""API key store — SQLite-backed key management with SHA-256 hashing."""
import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


VALID_ROLES = {"read-only", "read-write", "admin"}


class KeyStore:
    """Manages API keys in a SQLite database."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
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
                usage_count INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
        """)
        conn.commit()

    @staticmethod
    def generate_raw_key() -> str:
        return "mem_" + secrets.token_hex(16)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def create_key(
        self,
        name: str,
        role: str,
        prefixes: List[str],
    ) -> Dict[str, Any]:
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role!r}. Must be one of {VALID_ROLES}")

        key_id = str(uuid4())
        raw_key = self.generate_raw_key()
        key_hash = self.hash_key(raw_key)
        key_prefix = raw_key[:8]
        now = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        conn.execute(
            """INSERT INTO api_keys (id, name, key_hash, key_prefix, role, prefixes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key_id, name, key_hash, key_prefix, role, json.dumps(prefixes), now),
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

    def lookup(self, raw_key: str) -> Optional[Dict[str, Any]]:
        key_hash = self.hash_key(raw_key)
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0",
            (key_hash,),
        ).fetchone()
        if row is None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE api_keys SET last_used_at = ?, usage_count = usage_count + 1 WHERE id = ?",
            (now, row["id"]),
        )
        conn.commit()

        return {
            "id": row["id"],
            "name": row["name"],
            "key_prefix": row["key_prefix"],
            "role": row["role"],
            "prefixes": json.loads(row["prefixes"]),
            "created_at": row["created_at"],
            "last_used_at": now,
            "usage_count": row["usage_count"] + 1,
        }

    def list_keys(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "key_prefix": r["key_prefix"],
                "role": r["role"],
                "prefixes": json.loads(r["prefixes"]),
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "usage_count": r["usage_count"],
                "revoked": bool(r["revoked"]),
            }
            for r in rows
        ]

    def update_key(self, key_id: str, **fields) -> Dict[str, Any]:
        allowed = {"name", "role", "prefixes"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            raise ValueError("No valid fields to update")
        if "role" in updates and updates["role"] not in VALID_ROLES:
            raise ValueError(f"Invalid role: {updates['role']!r}")
        if "prefixes" in updates:
            updates["prefixes"] = json.dumps(updates["prefixes"])

        conn = self._connect()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [key_id]
        cursor = conn.execute(
            f"UPDATE api_keys SET {set_clause} WHERE id = ? AND revoked = 0",
            values,
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Key not found or already revoked: {key_id}")
        return {"id": key_id, "updated_fields": list(updates.keys())}

    def revoke(self, key_id: str) -> Dict[str, Any]:
        conn = self._connect()
        cursor = conn.execute(
            "UPDATE api_keys SET revoked = 1 WHERE id = ? AND revoked = 0",
            (key_id,),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Key not found or already revoked: {key_id}")
        return {"id": key_id, "revoked": True}
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_key_store.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add key_store.py tests/test_key_store.py
git commit -m "feat(auth): add KeyStore module — key generation, hashing, CRUD"
```

---

### Task 2: AuthContext and Prefix Matching

**Files:**
- Create: `auth_context.py`
- Create: `tests/test_auth_context.py`

**Step 1: Write the failing tests**

```python
# tests/test_auth_context.py
"""Tests for AuthContext — role checks and prefix enforcement."""
import pytest

from auth_context import AuthContext


class TestAuthContext:
    def test_admin_can_access_any_prefix(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        assert ctx.can_read("claude-code/foo")
        assert ctx.can_read("anything/at/all")

    def test_read_only_can_read_allowed_prefix(self):
        ctx = AuthContext(role="read-only", prefixes=["claude-code/*"], key_type="managed")
        assert ctx.can_read("claude-code/my-project")
        assert not ctx.can_read("kai/state")

    def test_read_only_cannot_write(self):
        ctx = AuthContext(role="read-only", prefixes=["claude-code/*"], key_type="managed")
        assert not ctx.can_write("claude-code/my-project")

    def test_read_write_can_read_and_write_allowed(self):
        ctx = AuthContext(role="read-write", prefixes=["claude-code/*", "learning/*"], key_type="managed")
        assert ctx.can_read("claude-code/foo")
        assert ctx.can_write("learning/bar")
        assert not ctx.can_write("kai/state")

    def test_admin_can_manage_keys(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        assert ctx.can_manage_keys

    def test_non_admin_cannot_manage_keys(self):
        ctx = AuthContext(role="read-write", prefixes=["a/*"], key_type="managed")
        assert not ctx.can_manage_keys

    def test_prefix_match_uses_startswith(self):
        ctx = AuthContext(role="read-write", prefixes=["claude-code/*"], key_type="managed")
        assert ctx.can_read("claude-code/")
        assert ctx.can_read("claude-code/deep/nested/path")
        assert not ctx.can_read("claude-codex/other")

    def test_filter_results_keeps_only_allowed(self):
        ctx = AuthContext(role="read-only", prefixes=["claude-code/*"], key_type="managed")
        results = [
            {"source": "claude-code/foo", "text": "a"},
            {"source": "kai/bar", "text": "b"},
            {"source": "claude-code/baz", "text": "c"},
        ]
        filtered = ctx.filter_results(results)
        assert len(filtered) == 2
        assert all(r["source"].startswith("claude-code/") for r in filtered)

    def test_admin_filter_results_returns_all(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        results = [{"source": "a/1"}, {"source": "b/2"}]
        assert len(ctx.filter_results(results)) == 2

    def test_no_auth_context_is_unrestricted(self):
        ctx = AuthContext.unrestricted()
        assert ctx.can_read("anything")
        assert ctx.can_write("anything")
        assert ctx.can_manage_keys
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_auth_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth_context'`

**Step 3: Write the implementation**

```python
# auth_context.py
"""Request-scoped auth context — role checks and prefix enforcement."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuthContext:
    role: str  # "admin", "read-write", "read-only"
    prefixes: Optional[List[str]]  # None = unrestricted (admin/env)
    key_type: str  # "env", "managed", "none"
    key_id: Optional[str] = None
    key_name: Optional[str] = None

    @classmethod
    def unrestricted(cls) -> "AuthContext":
        return cls(role="admin", prefixes=None, key_type="none")

    def _matches_prefix(self, source: str) -> bool:
        if self.prefixes is None:
            return True
        for p in self.prefixes:
            # "claude-code/*" matches source starting with "claude-code/"
            base = p.rstrip("*").rstrip("/") + "/"
            if source == base.rstrip("/") or source.startswith(base):
                return True
        return False

    def can_read(self, source: str) -> bool:
        return self._matches_prefix(source)

    def can_write(self, source: str) -> bool:
        if self.role == "read-only":
            return False
        return self._matches_prefix(source)

    @property
    def can_manage_keys(self) -> bool:
        return self.role == "admin"

    def filter_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.prefixes is None:
            return results
        return [r for r in results if self._matches_prefix(r.get("source", ""))]

    def to_me_response(self) -> Dict[str, Any]:
        resp: Dict[str, Any] = {"type": self.key_type, "role": self.role}
        if self.prefixes is not None:
            resp["prefixes"] = self.prefixes
        if self.key_id:
            resp["id"] = self.key_id
            resp["name"] = self.key_name
        return resp
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_auth_context.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add auth_context.py tests/test_auth_context.py
git commit -m "feat(auth): add AuthContext — role checks and prefix enforcement"
```

---

### Task 3: Wire Auth Into verify_api_key

**Files:**
- Modify: `app.py:62` (API_KEY config), `app.py:213-238` (verify_api_key), `app.py:763-825` (lifespan)
- Modify: `tests/test_security.py` (update fixtures)
- Create: `tests/test_multi_auth.py`

**Step 1: Write the failing tests**

```python
# tests/test_multi_auth.py
"""Tests for multi-auth — env key fallback, DB key lookup, role enforcement."""
import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_keys():
    """App fixture with both env key and DB-managed keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "keys.db")
        env = {"API_KEY": "admin-env-key", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
        with patch.dict(os.environ, env):
            import app as app_module
            importlib.reload(app_module)

            mock_engine = MagicMock()
            mock_engine.stats_light.return_value = {"total_memories": 5, "dimension": 384, "model": "test"}
            mock_engine.search.return_value = []
            mock_engine.hybrid_search.return_value = []
            mock_engine.is_ready.return_value = {"ready": True}
            app_module.memory = mock_engine

            from key_store import KeyStore
            key_store = KeyStore(db_path)
            app_module.key_store = key_store

            yield TestClient(app_module.app), mock_engine, key_store


class TestEnvKeyFallback:
    def test_env_key_works_as_admin(self, app_with_keys):
        client, _, _ = app_with_keys
        resp = client.get("/api/keys/me", headers={"X-API-Key": "admin-env-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "admin"
        assert body["type"] == "env"

    def test_wrong_key_returns_401(self, app_with_keys):
        client, _, _ = app_with_keys
        resp = client.post("/search", json={"query": "test"}, headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401


class TestManagedKeys:
    def test_managed_key_authenticates(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="test", role="read-write", prefixes=["test/*"])
        resp = client.get("/api/keys/me", headers={"X-API-Key": created["key"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "read-write"
        assert body["type"] == "managed"

    def test_revoked_key_returns_401(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="temp", role="read-write", prefixes=["x/*"])
        key_store.revoke(created["id"])
        resp = client.post("/search", json={"query": "test"}, headers={"X-API-Key": created["key"]})
        assert resp.status_code == 401


class TestReadOnlyEnforcement:
    def test_read_only_can_search(self, app_with_keys):
        client, mock_engine, key_store = app_with_keys
        created = key_store.create_key(name="reader", role="read-only", prefixes=["claude-code/*"])
        resp = client.post(
            "/search",
            json={"query": "test", "k": 3},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 200

    def test_read_only_cannot_add(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="reader", role="read-only", prefixes=["claude-code/*"])
        resp = client.post(
            "/memory/add",
            json={"text": "hello", "source": "claude-code/test"},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 403

    def test_read_only_cannot_delete(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="reader", role="read-only", prefixes=["claude-code/*"])
        resp = client.delete("/memory/1", headers={"X-API-Key": created["key"]})
        assert resp.status_code == 403


class TestPrefixEnforcement:
    def test_write_to_disallowed_prefix_returns_403(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="scoped", role="read-write", prefixes=["claude-code/*"])
        resp = client.post(
            "/memory/add",
            json={"text": "sneaky", "source": "kai/secret"},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 403
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_multi_auth.py -v`
Expected: FAIL — missing `/api/keys/me` endpoint, missing `key_store` in app

**Step 3: Modify app.py**

Changes required in `app.py`:

1. **Import key_store and auth_context** (top of file, after existing imports):

```python
from auth_context import AuthContext
from key_store import KeyStore
```

2. **Add key_store global** (after `API_KEY` line ~62):

```python
key_store: KeyStore = None  # type: ignore  — initialized in lifespan
```

3. **Replace verify_api_key** (lines ~217-238) with:

```python
async def verify_api_key(request: Request):
    """Check X-API-Key header against env key and DB keys.

    Attaches AuthContext to request.state for downstream use.
    """
    if not API_KEY and key_store is None:
        request.state.auth = AuthContext.unrestricted()
        return

    path = request.url.path
    if path in {"/health", "/health/ready", "/ui"} or path.startswith("/ui/"):
        request.state.auth = AuthContext.unrestricted()
        return

    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < 60]
    if len(_auth_failures[ip]) >= 10:
        raise HTTPException(status_code=429, detail="Too many failed authentication attempts")

    raw_key = request.headers.get("X-API-Key", "")
    if not raw_key:
        if not API_KEY:
            request.state.auth = AuthContext.unrestricted()
            return
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Path 1: env var admin key
    if API_KEY and hmac.compare_digest(raw_key.encode(), API_KEY.encode()):
        request.state.auth = AuthContext(role="admin", prefixes=None, key_type="env")
        return

    # Path 2: DB-managed key
    if key_store is not None:
        found = key_store.lookup(raw_key)
        if found:
            request.state.auth = AuthContext(
                role=found["role"],
                prefixes=found["prefixes"],
                key_type="managed",
                key_id=found["id"],
                key_name=found["name"],
            )
            return

    _auth_failures[ip].append(now)
    raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

4. **Initialize key_store in lifespan** (in the lifespan function, after `usage_tracker` init ~793-796):

```python
    global key_store
    key_store = KeyStore(os.path.join(DATA_DIR, "keys.db"))
    logger.info("Key store initialized")
```

5. **Add auth helper for endpoints** (after verify_api_key):

```python
def _get_auth(request: Request) -> AuthContext:
    return getattr(request.state, "auth", AuthContext.unrestricted())


def _require_write(auth: AuthContext, source: str) -> None:
    if not auth.can_write(source):
        raise HTTPException(status_code=403, detail=f"Key does not have write access to source: {source}")


def _require_admin(auth: AuthContext) -> None:
    if not auth.can_manage_keys:
        raise HTTPException(status_code=403, detail="Admin key required")
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_multi_auth.py -v`
Expected: All PASS

**Step 5: Run existing tests to verify no regressions**

Run: `python -m pytest tests/test_security.py tests/test_memory_api.py -v`
Expected: All PASS (existing tests use `API_KEY=test-key` header which still works as env admin)

**Step 6: Commit**

```bash
git add app.py tests/test_multi_auth.py
git commit -m "feat(auth): wire multi-key auth into verify_api_key with AuthContext"
```

---

### Task 4: Enforce Prefix Scoping on Read Endpoints

**Files:**
- Modify: `app.py` — search, list, count, get, folders endpoints
- Modify: `tests/test_multi_auth.py` — add prefix filtering tests

**Step 1: Write the failing tests**

Add to `tests/test_multi_auth.py`:

```python
class TestPrefixFilteringOnSearch:
    def test_search_results_filtered_by_prefix(self, app_with_keys):
        client, mock_engine, key_store = app_with_keys
        mock_engine.hybrid_search.return_value = [
            {"source": "claude-code/foo", "text": "allowed", "similarity": 0.9},
            {"source": "kai/bar", "text": "blocked", "similarity": 0.8},
        ]
        created = key_store.create_key(name="scoped", role="read-only", prefixes=["claude-code/*"])
        resp = client.post(
            "/search",
            json={"query": "test", "hybrid": True},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["source"] == "claude-code/foo"

    def test_admin_sees_all_search_results(self, app_with_keys):
        client, mock_engine, _ = app_with_keys
        mock_engine.hybrid_search.return_value = [
            {"source": "claude-code/foo", "text": "a", "similarity": 0.9},
            {"source": "kai/bar", "text": "b", "similarity": 0.8},
        ]
        resp = client.post(
            "/search",
            json={"query": "test", "hybrid": True},
            headers={"X-API-Key": "admin-env-key"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 2
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_multi_auth.py::TestPrefixFilteringOnSearch -v`
Expected: FAIL — search results not yet filtered

**Step 3: Add filtering to read endpoints in app.py**

Update `/search` endpoint (~line 1128):

```python
@app.post("/search")
async def search(request_body: SearchRequest, request: Request):
    auth = _get_auth(request)
    logger.info("Search: q=%r k=%d hybrid=%s", request_body.query[:80], request_body.k, request_body.hybrid)
    try:
        if request_body.hybrid:
            results = memory.hybrid_search(
                query=request_body.query,
                k=request_body.k,
                threshold=request_body.threshold,
                vector_weight=request_body.vector_weight,
                source_prefix=request_body.source_prefix,
            )
        else:
            results = memory.search(
                query=request_body.query,
                k=request_body.k,
                threshold=request_body.threshold,
                source_prefix=request_body.source_prefix,
            )
        results = auth.filter_results(results)
        usage_tracker.log_api_event("search", request_body.source)
        for r in results:
            if "id" in r:
                usage_tracker.log_retrieval(memory_id=r["id"], query=request_body.query[:200], source=request_body.source)
        return {"query": request_body.query, "results": results, "count": len(results)}
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail="Internal server error")
```

Apply similar `auth.filter_results()` to: `/search/batch`, `/memories` (list), `/memory/{id}` (single get — check `auth.can_read(source)`), `/memory/get-batch`, `/memories/count`, `/folders`.

For single-memory get, check access:

```python
@app.get("/memory/{memory_id}")
async def get_memory(memory_id: int, request: Request):
    auth = _get_auth(request)
    try:
        result = memory.get_memory(memory_id)
        if not auth.can_read(result.get("source", "")):
            raise HTTPException(status_code=403, detail="Access denied to this memory's source prefix")
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_multi_auth.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app.py tests/test_multi_auth.py
git commit -m "feat(auth): enforce prefix scoping on all read endpoints"
```

---

### Task 5: Enforce Prefix Scoping on Write Endpoints

**Files:**
- Modify: `app.py` — add, delete, patch, upsert, extract endpoints
- Modify: `tests/test_multi_auth.py`

**Step 1: Write the failing tests**

Add to `tests/test_multi_auth.py`:

```python
class TestWriteEnforcement:
    def test_write_to_allowed_prefix_succeeds(self, app_with_keys):
        client, mock_engine, key_store = app_with_keys
        mock_engine.add_memories.return_value = [42]
        created = key_store.create_key(name="writer", role="read-write", prefixes=["claude-code/*"])
        resp = client.post(
            "/memory/add",
            json={"text": "hello", "source": "claude-code/test"},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 200

    def test_delete_checks_memory_source(self, app_with_keys):
        client, mock_engine, key_store = app_with_keys
        mock_engine.get_memory.return_value = {"id": 1, "text": "x", "source": "kai/secret"}
        created = key_store.create_key(name="writer", role="read-write", prefixes=["claude-code/*"])
        resp = client.delete("/memory/1", headers={"X-API-Key": created["key"]})
        assert resp.status_code == 403

    def test_batch_add_rejects_if_any_source_disallowed(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="writer", role="read-write", prefixes=["claude-code/*"])
        resp = client.post(
            "/memory/add-batch",
            json={"memories": [
                {"text": "ok", "source": "claude-code/a"},
                {"text": "sneaky", "source": "kai/b"},
            ]},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 403
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_multi_auth.py::TestWriteEnforcement -v`
Expected: FAIL — write endpoints not yet checking auth

**Step 3: Add enforcement to write endpoints**

For each write endpoint, add `_require_write(auth, source)` check. Example for `/memory/add`:

```python
@app.post("/memory/add")
async def add_memory(request_body: AddMemoryRequest, request: Request):
    auth = _get_auth(request)
    _require_write(auth, request_body.source)
    # ... existing logic
```

For `/memory/{memory_id}` DELETE — fetch memory first, check source:

```python
@app.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int, request: Request):
    auth = _get_auth(request)
    if auth.prefixes is not None:
        existing = memory.get_memory(memory_id)
        _require_write(auth, existing.get("source", ""))
    # ... existing delete logic
```

For batch add — check all sources before proceeding:

```python
@app.post("/memory/add-batch")
async def add_batch(request_body: AddBatchRequest, request: Request):
    auth = _get_auth(request)
    for m in request_body.memories:
        _require_write(auth, m.source)
    # ... existing logic
```

Apply similar pattern to: `/memory/delete-batch`, `/memory/delete-by-source`, `/memory/delete-by-prefix`, `/memory/upsert`, `/memory/upsert-batch`, `/memory/patch`, `/memory/supersede`, `/extract`.

**Step 4: Run tests**

Run: `python -m pytest tests/test_multi_auth.py -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --ignore=tests/test_web_ui.py`
Expected: All PASS

**Step 6: Commit**

```bash
git add app.py tests/test_multi_auth.py
git commit -m "feat(auth): enforce prefix scoping on all write/delete endpoints"
```

---

### Task 6: Key Management API Endpoints

**Files:**
- Modify: `app.py` — add `/api/keys/*` endpoints
- Modify: `tests/test_multi_auth.py`

**Step 1: Write the failing tests**

Add to `tests/test_multi_auth.py`:

```python
class TestKeyManagementAPI:
    def test_create_key_with_admin(self, app_with_keys):
        client, _, _ = app_with_keys
        resp = client.post(
            "/api/keys",
            json={"name": "new-key", "role": "read-write", "prefixes": ["test/*"]},
            headers={"X-API-Key": "admin-env-key"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"].startswith("mem_")
        assert body["name"] == "new-key"
        assert body["role"] == "read-write"

    def test_create_key_without_admin_returns_403(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="non-admin", role="read-write", prefixes=["a/*"])
        resp = client.post(
            "/api/keys",
            json={"name": "sneaky", "role": "admin", "prefixes": []},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 403

    def test_list_keys_with_admin(self, app_with_keys):
        client, _, key_store = app_with_keys
        key_store.create_key(name="k1", role="read-only", prefixes=["a/*"])
        resp = client.get("/api/keys", headers={"X-API-Key": "admin-env-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["keys"]) >= 1
        # Ensure raw key is NOT returned in list
        for k in body["keys"]:
            assert "key" not in k or not k.get("key", "").startswith("mem_")

    def test_revoke_key_with_admin(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="to-revoke", role="read-write", prefixes=["b/*"])
        resp = client.delete(
            f"/api/keys/{created['id']}",
            headers={"X-API-Key": "admin-env-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

    def test_update_key_with_admin(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="old-name", role="read-only", prefixes=["c/*"])
        resp = client.patch(
            f"/api/keys/{created['id']}",
            json={"name": "new-name", "role": "read-write"},
            headers={"X-API-Key": "admin-env-key"},
        )
        assert resp.status_code == 200

    def test_me_endpoint_for_managed_key(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="my-key", role="read-write", prefixes=["d/*"])
        resp = client.get("/api/keys/me", headers={"X-API-Key": created["key"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "managed"
        assert body["role"] == "read-write"
        assert body["name"] == "my-key"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_multi_auth.py::TestKeyManagementAPI -v`
Expected: FAIL — endpoints don't exist

**Step 3: Add key management endpoints to app.py**

```python
# -- Key Management -----------------------------------------------------------

class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    role: str = Field(..., pattern="^(read-only|read-write|admin)$")
    prefixes: List[str] = Field(default_factory=list)


class UpdateKeyRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    role: Optional[str] = Field(None, pattern="^(read-only|read-write|admin)$")
    prefixes: Optional[List[str]] = None


@app.get("/api/keys/me")
async def get_my_key(request: Request):
    auth = _get_auth(request)
    return auth.to_me_response()


@app.post("/api/keys")
async def create_key(request_body: CreateKeyRequest, request: Request):
    auth = _get_auth(request)
    _require_admin(auth)
    result = key_store.create_key(
        name=request_body.name,
        role=request_body.role,
        prefixes=request_body.prefixes,
    )
    return result


@app.get("/api/keys")
async def list_keys(request: Request):
    auth = _get_auth(request)
    _require_admin(auth)
    keys = key_store.list_keys()
    return {"keys": keys, "count": len(keys)}


@app.patch("/api/keys/{key_id}")
async def update_key(key_id: str, request_body: UpdateKeyRequest, request: Request):
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        result = key_store.update_key(
            key_id,
            name=request_body.name,
            role=request_body.role,
            prefixes=request_body.prefixes,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/keys/{key_id}")
async def revoke_key(key_id: str, request: Request):
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        return key_store.revoke(key_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_multi_auth.py -v`
Expected: All PASS

**Step 5: Run full suite**

Run: `python -m pytest tests/ -v --ignore=tests/test_web_ui.py`
Expected: All PASS

**Step 6: Commit**

```bash
git add app.py tests/test_multi_auth.py
git commit -m "feat(auth): add key management API endpoints (CRUD + /me)"
```

---

### Task 7: Web UI — API Keys Page

**Files:**
- Modify: `webui/index.html` — add API Keys page, admin gate

**Step 1: Understand current UI structure**

Read `webui/index.html` to understand the existing page/navigation pattern. The v2 UI has sidebar nav with pages (Dashboard, Memories, Extractions, API Keys, Settings). The API Keys page likely already has a placeholder.

**Step 2: Implement the API Keys page**

The page needs:
- On load: call `GET /api/keys/me` to check role
- If not admin: show "Admin access required" message, hide page from sidebar
- If admin: show key table with columns (name, prefix, role, prefixes, created, last used, usage count, actions)
- Create button: modal with name, role dropdown, prefix input
- On create: `POST /api/keys` → show key in copy-to-clipboard modal (one-time display)
- Revoke button: confirm dialog → `DELETE /api/keys/{id}`
- Edit button: inline edit name/role/prefixes → `PATCH /api/keys/{id}`

**Step 3: Add admin gate to sidebar**

In the sidebar initialization JS, after loading: call `/api/keys/me`. If role !== "admin", hide the "API Keys" nav item.

**Step 4: Test manually**

1. Start server: `docker compose up`
2. Open `http://localhost:8900/ui`
3. Enter admin key → API Keys page visible
4. Create a scoped key → verify key shown once
5. Switch to non-admin key → API Keys page hidden

**Step 5: Commit**

```bash
git add webui/index.html
git commit -m "feat(ui): add API Keys management page with admin gate"
```

---

### Task 8: Integration Test and Backward Compatibility

**Files:**
- Create: `tests/test_auth_backward_compat.py`

**Step 1: Write backward compatibility tests**

```python
# tests/test_auth_backward_compat.py
"""Backward compatibility — existing single-key setups must work unchanged."""
import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def single_key_client():
    """Existing setup: just API_KEY env var, no managed keys."""
    with patch.dict(os.environ, {"API_KEY": "god-is-an-astronaut", "EXTRACT_PROVIDER": ""}):
        import app as app_module
        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5, "dimension": 384, "model": "test"}
        mock_engine.search.return_value = [{"source": "claude-code/x", "text": "a", "similarity": 0.9}]
        mock_engine.hybrid_search.return_value = []
        mock_engine.add_memories.return_value = [1]
        mock_engine.is_ready.return_value = {"ready": True}
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


@pytest.fixture
def no_auth_client():
    """Local-only setup: no API_KEY configured."""
    with patch.dict(os.environ, {"API_KEY": "", "EXTRACT_PROVIDER": ""}, clear=False):
        import app as app_module
        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 0, "dimension": 384, "model": "test"}
        mock_engine.search.return_value = []
        mock_engine.hybrid_search.return_value = []
        mock_engine.add_memories.return_value = [1]
        mock_engine.is_ready.return_value = {"ready": True}
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


class TestSingleKeyBackwardCompat:
    def test_existing_key_still_works(self, single_key_client):
        client, _ = single_key_client
        resp = client.post(
            "/search",
            json={"query": "test"},
            headers={"X-API-Key": "god-is-an-astronaut"},
        )
        assert resp.status_code == 200

    def test_wrong_key_still_rejected(self, single_key_client):
        client, _ = single_key_client
        resp = client.post(
            "/search",
            json={"query": "test"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_health_still_unauthenticated(self, single_key_client):
        client, _ = single_key_client
        resp = client.get("/health")
        assert resp.status_code == 200


class TestNoAuthBackwardCompat:
    def test_no_key_needed_when_api_key_empty(self, no_auth_client):
        client, _ = no_auth_client
        resp = client.post("/search", json={"query": "test"})
        assert resp.status_code == 200

    def test_add_works_without_key(self, no_auth_client):
        client, _ = no_auth_client
        resp = client.post(
            "/memory/add",
            json={"text": "hello", "source": "test/x"},
        )
        assert resp.status_code == 200
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_auth_backward_compat.py -v`
Expected: All PASS

**Step 3: Run full suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add tests/test_auth_backward_compat.py
git commit -m "test(auth): add backward compatibility tests for single-key and no-auth modes"
```

---

### Task 9: Documentation and Final Polish

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md` (if multi-auth section needed)

**Step 1: Update CHANGELOG.md**

Add entry for the new version:

```markdown
## [1.5.0] - 2026-03-05

### Added
- **Multi-auth**: prefix-scoped API keys with three role tiers (`read-only`, `read-write`, `admin`)
  - `POST /api/keys` — create keys (admin-only, shown once)
  - `GET /api/keys` — list keys with usage stats
  - `PATCH /api/keys/{id}` — update name, role, prefixes
  - `DELETE /api/keys/{id}` — revoke keys (soft-delete)
  - `GET /api/keys/me` — caller identity and role
- Prefix enforcement on all read/write endpoints — scoped keys only see/modify their allowed prefixes
- Web UI: API Keys management page (admin-gated)
- `key_store.py` — SQLite-backed key store with SHA-256 hashing
- `auth_context.py` — request-scoped role and prefix enforcement

### Changed
- `verify_api_key` now checks both env `API_KEY` (implicit admin) and DB-managed keys
- All API endpoints now receive `AuthContext` via `request.state.auth`
- Existing `API_KEY` env var continues to work unchanged (backward compatible)
```

**Step 2: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "docs: add multi-auth to changelog and readme"
```

---

## Summary

| Task | Description | New Files | Modified Files |
|------|-------------|-----------|----------------|
| 1 | KeyStore module | `key_store.py`, `tests/test_key_store.py` | — |
| 2 | AuthContext | `auth_context.py`, `tests/test_auth_context.py` | — |
| 3 | Wire into verify_api_key | `tests/test_multi_auth.py` | `app.py` |
| 4 | Prefix scoping on reads | — | `app.py`, `tests/test_multi_auth.py` |
| 5 | Prefix scoping on writes | — | `app.py`, `tests/test_multi_auth.py` |
| 6 | Key management endpoints | — | `app.py`, `tests/test_multi_auth.py` |
| 7 | Web UI keys page | — | `webui/index.html` |
| 8 | Backward compat tests | `tests/test_auth_backward_compat.py` | — |
| 9 | Docs + changelog | — | `CHANGELOG.md`, `README.md` |
