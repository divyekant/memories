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
            ks = KeyStore(db_path)
            app_module.key_store = ks

            yield TestClient(app_module.app), mock_engine, ks


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

    def test_missing_key_returns_401(self, app_with_keys):
        client, _, _ = app_with_keys
        resp = client.post("/search", json={"query": "test"})
        assert resp.status_code == 401


class TestManagedKeys:
    def test_managed_key_authenticates(self, app_with_keys):
        client, _, ks = app_with_keys
        created = ks.create_key(name="test", role="read-write", prefixes=["test/*"])
        resp = client.get("/api/keys/me", headers={"X-API-Key": created["key"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "read-write"
        assert body["type"] == "managed"

    def test_revoked_key_returns_401(self, app_with_keys):
        client, _, ks = app_with_keys
        created = ks.create_key(name="temp", role="read-write", prefixes=["x/*"])
        ks.revoke(created["id"])
        resp = client.post("/search", json={"query": "test"}, headers={"X-API-Key": created["key"]})
        assert resp.status_code == 401


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


class TestReadOnlyEnforcement:
    def test_read_only_can_search(self, app_with_keys):
        client, mock_engine, key_store = app_with_keys
        created = key_store.create_key(name="reader", role="read-only", prefixes=["claude-code/*"])
        resp = client.post(
            "/search", json={"query": "test", "k": 3},
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
        client, mock_engine, key_store = app_with_keys
        mock_engine.get_memory.return_value = {"id": 1, "text": "x", "source": "claude-code/test"}
        created = key_store.create_key(name="reader", role="read-only", prefixes=["claude-code/*"])
        resp = client.delete("/memory/1", headers={"X-API-Key": created["key"]})
        assert resp.status_code == 403


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

    def test_write_to_disallowed_prefix_returns_403(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="scoped", role="read-write", prefixes=["claude-code/*"])
        resp = client.post(
            "/memory/add",
            json={"text": "sneaky", "source": "kai/secret"},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 403

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

    def test_upsert_checks_source(self, app_with_keys):
        client, _, key_store = app_with_keys
        created = key_store.create_key(name="writer", role="read-write", prefixes=["claude-code/*"])
        resp = client.post(
            "/memory/upsert",
            json={"text": "hello", "source": "kai/secret", "key": "k1"},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 403
