"""Tests for /metrics endpoint."""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import importlib
        import app as app_module

        importlib.reload(app_module)

        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5}
        mock_engine.stats.return_value = {"total_memories": 5}
        app_module.memory = mock_engine

        yield TestClient(app_module.app), mock_engine


def test_metrics_includes_latency_error_queue_and_memory_sections(client):
    test_client, _ = client

    # Successful request
    health_response = test_client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json()["service"] == "memories"

    # Error request (validation failure)
    bad_search = test_client.post(
        "/search",
        json={"query": ""},
        headers={"X-API-Key": "test-key"},
    )
    assert bad_search.status_code == 422

    metrics_response = test_client.get("/metrics", headers={"X-API-Key": "test-key"})
    assert metrics_response.status_code == 200

    data = metrics_response.json()
    assert "uptime_sec" in data

    assert data["extract"]["queue_depth"] >= 0
    assert data["extract"]["queue_max"] >= 1
    assert "queue_remaining" in data["extract"]

    assert data["memory"]["current_total"] == 5
    assert data["memory"]["trend"]["samples"]
    process = data["memory"]["process"]
    assert process["rss_kb"] >= 0
    assert process["rss_anon_kb"] >= 0
    assert process["rss_file_kb"] >= 0
    assert process["rss_high_water_kb"] >= 0
    assert process["vmsize_kb"] >= 0

    assert data["requests"]["total_count"] >= 2
    assert data["requests"]["error_count"] >= 1

    search_route = data["routes"].get("POST /search")
    assert search_route is not None
    assert search_route["error_count"] >= 1
    assert search_route["p95_latency_ms"] >= 0


def test_metrics_memory_trend_tracks_deltas(client):
    test_client, mock_engine = client

    mock_engine.stats_light.side_effect = [
        {"total_memories": 5},
        {"total_memories": 7},
    ]

    first = test_client.get("/metrics", headers={"X-API-Key": "test-key"})
    second = test_client.get("/metrics", headers={"X-API-Key": "test-key"})

    assert first.status_code == 200
    assert second.status_code == 200

    second_data = second.json()
    trend = second_data["memory"]["trend"]
    assert second_data["memory"]["current_total"] == 7
    assert trend["delta"] == 2
    assert len(trend["samples"]) >= 2
