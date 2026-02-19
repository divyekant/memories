"""Tests for extraction API endpoints in app.py."""
import os
import time
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked memory engine and auth."""
    # Set test API key (app.py reads API_KEY env var)
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": "ollama"}):
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

    @staticmethod
    def _wait_for_terminal_job(test_client, job_id: str, timeout_sec: float = 2.0):
        deadline = time.time() + timeout_sec
        last_state = None
        while time.time() < deadline:
            response = test_client.get(
                f"/memory/extract/{job_id}",
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 200
            last_state = response.json()
            if last_state["status"] in {"completed", "failed"}:
                return last_state
            time.sleep(0.02)
        pytest.fail(f"Extraction job {job_id} did not finish in time; last_state={last_state}")

    def test_extract_returns_501_when_disabled(self, client):
        test_client, mock_engine = client
        with patch("app.extract_provider", None):
            response = test_client.post(
                "/memory/extract",
                json={"messages": "test", "source": "test", "context": "stop"},
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 501

    def test_extract_fallback_add_stores_memory_when_enabled(self, client):
        test_client, mock_engine = client
        mock_engine.is_novel.return_value = (True, None)
        mock_engine.add_memories.return_value = [123]
        with patch("app.extract_provider", None), \
             patch("app.run_extraction", None), \
             patch("app.EXTRACT_FALLBACK_ADD_ENABLED", True):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: We decided to use qdrant as the default vector store for production.",
                    "source": "test/proj",
                    "context": "stop",
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "completed"

            job_state = self._wait_for_terminal_job(test_client, data["job_id"])
            assert job_state["status"] == "completed"
            assert job_state["result"]["mode"] == "fallback_add"
            assert job_state["result"]["stored_count"] == 1
            assert job_state["result"]["extracted_count"] == 1

        mock_engine.is_novel.assert_called_once()
        mock_engine.add_memories.assert_called_once()

    def test_extract_fallback_add_skips_when_no_fact_candidate(self, client):
        test_client, mock_engine = client
        with patch("app.extract_provider", None), \
             patch("app.run_extraction", None), \
             patch("app.EXTRACT_FALLBACK_ADD_ENABLED", True):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: hi\nAssistant: hello",
                    "source": "test/proj",
                    "context": "stop",
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "completed"

            job_state = self._wait_for_terminal_job(test_client, data["job_id"])
            assert job_state["status"] == "completed"
            assert job_state["result"]["mode"] == "fallback_add"
            assert job_state["result"]["stored_count"] == 0
            assert job_state["result"]["extracted_count"] == 0

        mock_engine.is_novel.assert_not_called()
        mock_engine.add_memories.assert_not_called()

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
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "queued"
            assert "job_id" in data

            job_state = self._wait_for_terminal_job(test_client, data["job_id"])
            assert job_state["status"] == "completed"
            assert job_state["result"]["extracted_count"] == 1

    def test_extract_runtime_failure_uses_fallback_when_enabled(self, client):
        test_client, mock_engine = client
        mock_engine.is_novel.return_value = (True, None)
        mock_engine.add_memories.return_value = [777]
        with patch("app.extract_provider", MagicMock()), \
             patch("app.EXTRACT_FALLBACK_ADD_ENABLED", True), \
             patch(
                 "app.run_extraction",
                 return_value={
                     "actions": [],
                     "extracted_count": 0,
                     "stored_count": 0,
                     "updated_count": 0,
                     "deleted_count": 0,
                     "error": "provider_runtime_failure",
                     "error_stage": "extract_facts",
                     "error_message": "429 Too Many Requests",
                 },
             ):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: We decided to keep fallback enabled for quota failures.",
                    "source": "test/runtime-fallback",
                    "context": "stop",
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            job_state = self._wait_for_terminal_job(test_client, response.json()["job_id"])
            assert job_state["status"] == "completed"
            assert job_state["result"]["mode"] == "fallback_add"
            assert job_state["result"]["stored_count"] == 1
            assert job_state["result"]["fallback_triggered"] is True
            assert job_state["result"]["fallback_reason"] == "provider_runtime_failure"

        mock_engine.is_novel.assert_called_once()
        mock_engine.add_memories.assert_called_once()

    def test_extract_triggers_memory_trim(self, client):
        test_client, _ = client
        with patch("app.extract_provider", MagicMock()), \
             patch("app.run_extraction", return_value={"actions": [], "extracted_count": 0, "stored_count": 0, "updated_count": 0, "deleted_count": 0}), \
             patch("app.memory_trimmer.maybe_trim", return_value={"trimmed": False, "reason": "cooldown"}) as trim_mock:
            response = test_client.post(
                "/memory/extract",
                json={"messages": "test", "source": "test", "context": "stop"},
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            job_id = response.json()["job_id"]
            self._wait_for_terminal_job(test_client, job_id)
            trim_mock.assert_called_once()

    def test_extract_rejects_oversized_payload(self, client):
        test_client, _ = client
        import app as app_module

        oversized = "x" * (app_module.MAX_EXTRACT_MESSAGE_CHARS + 1)
        with patch("app.extract_provider", MagicMock()):
            response = test_client.post(
                "/memory/extract",
                json={"messages": oversized, "source": "test", "context": "stop"},
                headers={"X-API-Key": "test-key"},
            )
        assert response.status_code == 422

    def test_extract_job_not_found(self, client):
        test_client, _ = client
        response = test_client.get(
            "/memory/extract/not-a-real-job",
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 404

    def test_extract_returns_429_when_queue_full(self, client):
        test_client, _ = client
        import asyncio
        import app as app_module

        # Replace the queue with a tiny bounded queue that's already full
        tiny_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        tiny_queue.put_nowait({"job_id": "dummy", "request": {"messages": "", "source": "", "context": ""}})

        with patch("app.extract_provider", MagicMock()), \
             patch("app.run_extraction", MagicMock()), \
             patch.object(app_module, "extract_queue", tiny_queue):
            response = test_client.post(
                "/memory/extract",
                json={"messages": "test", "source": "test", "context": "stop"},
                headers={"X-API-Key": "test-key"},
            )
        assert response.status_code == 429
        assert "Retry-After" in response.headers
        detail = response.json()["detail"]
        assert detail["error"] == "extract_queue_full"
        assert detail["retry_after_sec"] >= 1


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
            data = response.json()
            assert data["enabled"] is False
            assert "queue_depth" in data
            assert "queue_max" in data
            assert "workers" in data

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
            assert "queue_depth" in data
            assert "queue_max" in data
            assert "workers" in data
