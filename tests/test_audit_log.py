"""Tests for audit log — append-only trail with retention and query."""

import importlib
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestAuditLog:
    """Core audit log functionality."""

    @pytest.fixture
    def audit(self, tmp_path):
        from audit_log import AuditLog
        return AuditLog(str(tmp_path / "audit.db"))

    def test_log_entry(self, audit):
        audit.log(action="search", key_id="k1", key_name="test-key", source_prefix="claude-code/*", ip="127.0.0.1")
        entries = audit.query(limit=10)
        assert len(entries) == 1
        assert entries[0]["action"] == "search"
        assert entries[0]["key_id"] == "k1"

    def test_log_with_resource_id(self, audit):
        audit.log(action="delete", key_id="k1", resource_id="42")
        entries = audit.query(limit=10)
        assert entries[0]["resource_id"] == "42"

    def test_query_by_action(self, audit):
        audit.log(action="search", key_id="k1")
        audit.log(action="add", key_id="k1")
        audit.log(action="delete", key_id="k1")
        entries = audit.query(action="add")
        assert len(entries) == 1
        assert entries[0]["action"] == "add"

    def test_query_by_key_id(self, audit):
        audit.log(action="search", key_id="k1")
        audit.log(action="search", key_id="k2")
        entries = audit.query(key_id="k2")
        assert len(entries) == 1

    def test_query_limit_and_offset(self, audit):
        for i in range(10):
            audit.log(action="search", key_id="k1")
        entries = audit.query(limit=3, offset=2)
        assert len(entries) == 3

    def test_query_returns_newest_first(self, audit):
        audit.log(action="search", key_id="k1")
        audit.log(action="add", key_id="k2")
        entries = audit.query(limit=10)
        assert entries[0]["action"] == "add"  # most recent
        assert entries[1]["action"] == "search"

    def test_retention_purge(self, audit):
        # Insert an old entry by manipulating the DB directly
        import sqlite3
        conn = sqlite3.connect(audit._db_path)
        conn.execute(
            "INSERT INTO audit_log (ts, action, key_id) VALUES (datetime('now', '-100 days'), 'old_action', 'k1')"
        )
        conn.commit()
        conn.close()

        audit.log(action="new_action", key_id="k2")
        purged = audit.purge(retention_days=90)
        assert purged >= 1
        entries = audit.query(limit=100)
        assert all(e["action"] != "old_action" for e in entries)

    def test_count(self, audit):
        audit.log(action="search", key_id="k1")
        audit.log(action="add", key_id="k2")
        assert audit.count() == 2
        assert audit.count(action="search") == 1


class TestNullAuditLog:
    def test_null_log(self):
        from audit_log import NullAuditLog
        a = NullAuditLog()
        a.log(action="search", key_id="k1")

    def test_null_query(self):
        from audit_log import NullAuditLog
        a = NullAuditLog()
        assert a.query() == []

    def test_null_count(self):
        from audit_log import NullAuditLog
        a = NullAuditLog()
        assert a.count() == 0


class TestAuditEndpoint:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "admin-key", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir, "AUDIT_LOG": "true"}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                app_module.memory = mock_engine

                from audit_log import AuditLog
                app_module.audit_log = AuditLog(os.path.join(tmpdir, "audit.db"))

                yield TestClient(app_module.app), app_module

    def test_get_audit_log(self, client):
        tc, mod = client
        mod.audit_log.log(action="search", key_id="env", ip="127.0.0.1")
        resp = tc.get("/audit", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert "entries" in body
        assert len(body["entries"]) == 1

    def test_audit_filter_by_action(self, client):
        tc, mod = client
        mod.audit_log.log(action="search", key_id="k1")
        mod.audit_log.log(action="add", key_id="k1")
        resp = tc.get("/audit?action=search", headers={"X-API-Key": "admin-key"})
        assert len(resp.json()["entries"]) == 1

    def test_audit_requires_admin(self, client):
        tc, mod = client
        from key_store import KeyStore
        ks = KeyStore(os.path.join(mod.DATA_DIR, "keys.db"))
        mod.key_store = ks
        created = ks.create_key(name="reader", role="read-only", prefixes=["test/*"])
        resp = tc.get("/audit", headers={"X-API-Key": created["key"]})
        assert resp.status_code == 403

    def test_audit_purge_endpoint(self, client):
        tc, mod = client
        resp = tc.post("/audit/purge?retention_days=90", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        assert "purged" in resp.json()
