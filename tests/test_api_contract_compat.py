"""API contract compatibility tests.

These tests lock public response shapes so storage backend changes do not
affect client-facing HTTP contracts.
"""

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
        mock_engine.stats_light.return_value = {
            "total_memories": 5,
            "dimension": 384,
            "model": "all-MiniLM-L6-v2",
        }
        mock_engine.search.return_value = [
            {"id": 1, "text": "python memory", "source": "test/source", "similarity": 0.9}
        ]
        mock_engine.hybrid_search.return_value = [
            {"id": 1, "text": "python memory", "source": "test/source", "rrf_score": 0.2}
        ]
        mock_engine.add_memories.return_value = [42]
        app_module.memory = mock_engine

        yield TestClient(app_module.app), mock_engine


def test_health_contract_keys(client):
    test_client, _ = client
    # Authenticated health check returns full stats
    response = test_client.get("/health", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "memories"
    assert {"status", "service", "version", "total_memories", "dimension", "model"} <= set(body)


def test_health_unauthenticated_returns_minimal(client):
    test_client, _ = client
    # Unauthenticated health check returns minimal info (no stats leakage)
    response = test_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "memories", "version": "5.19.0"}


def test_search_contract_shape(client):
    test_client, _ = client
    response = test_client.post(
        "/search",
        json={"query": "python", "k": 3, "hybrid": True},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert {"query", "results", "count"} <= set(body)
    assert isinstance(body["results"], list)
    assert body["count"] == len(body["results"])


def test_add_memory_contract_shape(client):
    test_client, _ = client
    response = test_client.post(
        "/memory/add",
        json={"text": "new memory", "source": "test/source"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert {"success", "id", "message"} <= set(body)


def test_backend_version_sources_agree(client):
    """The packaged BACKEND_VERSION marker must equal the version of the Rust
    server (server/Cargo.toml), because `memories doctor` and the hooks compare
    it with /health. The Python server stays at its own version (the rollback
    reference) and its /health, FastAPI metadata and OpenAPI version must agree.
    """
    import re
    from pathlib import Path

    root = Path(__file__).parent.parent
    marker = (root / "mcp-server" / "assets" / "backend" / "BACKEND_VERSION").read_text(encoding="utf-8").strip()
    cargo = re.search(r'^version = "([^"]+)"', (root / "server" / "Cargo.toml").read_text(), re.M).group(1)
    assert marker == cargo, f"BACKEND_VERSION {marker!r} != server/Cargo.toml {cargo!r}"

    test_client, _ = client
    reported = test_client.get("/health").json()["version"]
    metadata = test_client.app.version
    openapi = test_client.get("/openapi.json").json()["info"]["version"]
    assert reported == metadata == openapi, (reported, metadata, openapi)
