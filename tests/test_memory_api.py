"""Tests for memory CRUD/search API endpoints."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5, "dimension": 384, "model": "all-MiniLM-L6-v2"}
        mock_engine.search.return_value = []
        mock_engine.hybrid_search.return_value = []
        mock_engine.delete_memories.return_value = {"deleted_count": 2, "deleted_ids": [1, 3], "missing_ids": []}
        mock_engine.get_memory.return_value = {"id": 1, "text": "hello", "source": "carto/poet-pads/db"}
        mock_engine.get_memories.return_value = {"memories": [{"id": 1}], "missing_ids": [2]}
        mock_engine.upsert_memory.return_value = {"id": 7, "action": "created"}
        mock_engine.upsert_memories.return_value = {
            "created": 1,
            "updated": 1,
            "errors": 0,
            "results": [{"id": 7, "action": "created"}, {"id": 8, "action": "updated"}],
        }
        mock_engine.delete_by_prefix.return_value = {"deleted_count": 4}
        mock_engine.update_memory.return_value = {"id": 4, "updated_fields": ["text"]}
        mock_engine.is_ready.return_value = {"ready": True, "status": "ready"}
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


def test_search_accepts_source_prefix_and_passes_to_engine(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/search",
        json={"query": "python", "k": 3, "hybrid": False, "source_prefix": "carto/poet-pads/"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    mock_engine.search.assert_called_once_with(
        query="python",
        k=3,
        threshold=None,
        source_prefix="carto/poet-pads/",
    )


def test_delete_batch_endpoint_deletes_multiple_ids(client):
    test_client, _ = client
    response = test_client.post(
        "/memory/delete-batch",
        json={"ids": [1, 3]},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["deleted_count"] == 2
    assert body["deleted_ids"] == [1, 3]


def test_get_memory_by_id(client):
    test_client, mock_engine = client
    response = test_client.get("/memory/1", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["id"] == 1
    mock_engine.get_memory.assert_called_once_with(1)


def test_get_memory_batch(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/get-batch",
        json={"ids": [1, 2]},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["missing_ids"] == [2]
    mock_engine.get_memories.assert_called_once_with([1, 2])


def test_upsert_memory(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/upsert",
        json={"text": "new text", "source": "carto/poet-pads/db", "key": "entity-1", "metadata": {"team": "carto"}},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["action"] == "created"
    mock_engine.upsert_memory.assert_called_once_with(
        text="new text",
        source="carto/poet-pads/db",
        key="entity-1",
        metadata={"team": "carto"},
    )


def test_upsert_batch_memory(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/upsert-batch",
        json={
            "memories": [
                {"text": "t1", "source": "a", "key": "k1"},
                {"text": "t2", "source": "b", "key": "k2"},
            ]
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["created"] == 1
    assert body["updated"] == 1
    mock_engine.upsert_memories.assert_called_once()


def test_search_batch(client):
    test_client, _ = client
    response = test_client.post(
        "/search/batch",
        json={
            "queries": [
                {"query": "python", "k": 2},
                {"query": "docker", "k": 2, "hybrid": True},
            ]
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["results"]) == 2


def test_delete_by_prefix(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/delete-by-prefix",
        json={"source_prefix": "carto/poet-pads/"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 4
    mock_engine.delete_by_prefix.assert_called_once_with("carto/poet-pads/")


def test_patch_memory(client):
    test_client, mock_engine = client
    response = test_client.patch(
        "/memory/4",
        json={"text": "updated"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == 4
    mock_engine.update_memory.assert_called_once_with(
        memory_id=4,
        text="updated",
        source=None,
        metadata_patch=None,
    )


def test_health_ready(client):
    test_client, mock_engine = client
    response = test_client.get("/health/ready", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    mock_engine.is_ready.assert_called_once()
