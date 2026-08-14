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
        mock_engine.reload_embedder.return_value = {
            "reloaded": True,
            "model": "all-MiniLM-L6-v2",
            "dimension": 384,
            "gc_collected": 4,
        }
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
    assert data["requests"]["active_http_requests"] >= 1

    search_route = data["routes"].get("POST /search")
    assert search_route is not None
    assert search_route["error_count"] >= 1
    assert search_route["p95_latency_ms"] >= 0

    reload_metrics = data["embedder_reload"]
    assert reload_metrics["enabled"] is False
    assert reload_metrics["policy"]["rss_kb_threshold"] >= 100000
    assert reload_metrics["auto"]["checks_total"] >= 0
    assert reload_metrics["manual"]["requests_total"] >= 0


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


def test_metrics_tracks_manual_embedder_reload(client):
    test_client, _ = client

    reload_response = test_client.post(
        "/maintenance/embedder/reload",
        headers={"X-API-Key": "test-key"},
    )
    assert reload_response.status_code == 200

    metrics_response = test_client.get("/metrics", headers={"X-API-Key": "test-key"})
    assert metrics_response.status_code == 200
    data = metrics_response.json()
    manual = data["embedder_reload"]["manual"]
    assert manual["requests_total"] >= 1
    assert manual["succeeded_total"] >= 1
    assert manual["failed_total"] == 0
    assert manual["last_requested_at"] is not None
    assert manual["last_completed_at"] is not None


def test_metrics_includes_text_free_promotion_snapshot(client):
    test_client, _ = client
    import app as app_module

    service = MagicMock()
    service.metrics_snapshot.return_value = {
        "status_counts": {"candidate": 2},
        "alerts": {
            "unreviewable_rate_alert": False,
            "unreviewable_backlog_alert": False,
        },
    }
    with patch.object(app_module, "promotion_service", service):
        response = test_client.get("/metrics", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert response.json()["promotion"]["status_counts"] == {"candidate": 2}
