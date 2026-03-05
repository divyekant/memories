"""Backward compatibility -- existing single-key setups must work unchanged."""
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
        mock_engine.delete_memory.return_value = {"deleted": True}
        mock_engine.is_ready.return_value = {"ready": True}
        mock_engine.count_memories.return_value = 5
        mock_engine.list_memories.return_value = {"memories": [], "total": 0}
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
        mock_engine.count_memories.return_value = 0
        mock_engine.list_memories.return_value = {"memories": [], "total": 0}
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


class TestSingleKeyBackwardCompat:
    def test_existing_key_works_for_search(self, single_key_client):
        client, _ = single_key_client
        resp = client.post(
            "/search",
            json={"query": "test"},
            headers={"X-API-Key": "god-is-an-astronaut"},
        )
        assert resp.status_code == 200

    def test_existing_key_works_for_add(self, single_key_client):
        client, _ = single_key_client
        resp = client.post(
            "/memory/add",
            json={"text": "hello", "source": "test/x"},
            headers={"X-API-Key": "god-is-an-astronaut"},
        )
        assert resp.status_code == 200

    def test_existing_key_works_for_list(self, single_key_client):
        client, _ = single_key_client
        resp = client.get("/memories", headers={"X-API-Key": "god-is-an-astronaut"})
        assert resp.status_code == 200

    def test_existing_key_works_for_count(self, single_key_client):
        client, _ = single_key_client
        resp = client.get("/memories/count", headers={"X-API-Key": "god-is-an-astronaut"})
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

    def test_health_ready_still_unauthenticated(self, single_key_client):
        client, _ = single_key_client
        resp = client.get("/health/ready")
        assert resp.status_code == 200

    def test_me_endpoint_shows_admin(self, single_key_client):
        client, _ = single_key_client
        resp = client.get("/api/keys/me", headers={"X-API-Key": "god-is-an-astronaut"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "admin"
        assert body["type"] == "env"


class TestNoAuthBackwardCompat:
    def test_no_key_needed_for_search(self, no_auth_client):
        client, _ = no_auth_client
        resp = client.post("/search", json={"query": "test"})
        assert resp.status_code == 200

    def test_no_key_needed_for_add(self, no_auth_client):
        client, _ = no_auth_client
        resp = client.post(
            "/memory/add",
            json={"text": "hello", "source": "test/x"},
        )
        assert resp.status_code == 200

    def test_no_key_needed_for_list(self, no_auth_client):
        client, _ = no_auth_client
        resp = client.get("/memories")
        assert resp.status_code == 200

    def test_health_works(self, no_auth_client):
        client, _ = no_auth_client
        resp = client.get("/health")
        assert resp.status_code == 200
