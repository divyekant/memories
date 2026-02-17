"""Tests for extraction API endpoints in app.py."""
import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked memory engine and auth."""
    # Set test API key (app.py reads API_KEY env var)
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        # Need to reimport app to pick up env changes
        import importlib
        import app as app_module
        importlib.reload(app_module)

        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5}
        app_module.memory = mock_engine

        yield TestClient(app_module.app), mock_engine


class TestExtractEndpoint:
    """Test POST /memory/extract."""

    def test_extract_returns_501_when_disabled(self, client):
        test_client, mock_engine = client
        with patch("app.extract_provider", None):
            response = test_client.post(
                "/memory/extract",
                json={"messages": "test", "source": "test", "context": "stop"},
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 501

    def test_extract_returns_results(self, client):
        test_client, mock_engine = client
        mock_result = {
            "actions": [{"action": "add", "text": "test fact", "id": 1}],
            "extracted_count": 1,
            "stored_count": 1,
            "updated_count": 0,
            "deleted_count": 0,
        }
        with patch("app.extract_provider", MagicMock()), \
             patch("app.run_extraction", return_value=mock_result):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: test\nAssistant: ok",
                    "source": "test/proj",
                    "context": "stop",
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["extracted_count"] == 1


class TestSupersedeEndpoint:
    """Test POST /memory/supersede."""

    def test_supersede_success(self, client):
        test_client, mock_engine = client
        mock_engine.supersede.return_value = {
            "old_id": 42,
            "new_id": 100,
            "previous_text": "old text",
        }
        response = test_client.post(
            "/memory/supersede",
            json={"old_id": 42, "new_text": "new text", "source": "test"},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200
        assert response.json()["new_id"] == 100

    def test_supersede_not_found(self, client):
        test_client, mock_engine = client
        mock_engine.supersede.side_effect = ValueError("Memory 999 not found")
        response = test_client.post(
            "/memory/supersede",
            json={"old_id": 999, "new_text": "new text", "source": "test"},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 404


class TestExtractStatusEndpoint:
    """Test GET /extract/status."""

    def test_status_when_disabled(self, client):
        test_client, _ = client
        with patch("app.extract_provider", None):
            response = test_client.get(
                "/extract/status",
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 200
            assert response.json()["enabled"] is False

    def test_status_when_enabled(self, client):
        test_client, _ = client
        mock_provider = MagicMock()
        mock_provider.provider_name = "anthropic"
        mock_provider.model = "claude-haiku-4-5-20251001"
        mock_provider.health_check.return_value = True

        with patch("app.extract_provider", mock_provider):
            response = test_client.get(
                "/extract/status",
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True
            assert data["provider"] == "anthropic"
            assert data["status"] == "healthy"
