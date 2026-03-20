"""Tests for extraction debug trace feature."""

import importlib
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_mock_provider(supports_audn=True):
    """Create a mock LLM provider that returns predictable results."""
    provider = MagicMock()
    provider.provider_name = "test"
    provider.model = "test-model"
    provider.supports_audn = supports_audn
    return provider


def _make_mock_extraction_result(debug=False):
    """Create a mock run_extraction result (what run_extraction returns)."""
    base = {
        "actions": [
            {"action": "add", "text": "Python uses indentation", "id": 42},
        ],
        "extracted_count": 1,
        "stored_count": 1,
        "updated_count": 0,
        "deleted_count": 0,
        "conflict_count": 0,
        "tokens": {
            "extract": {"input": 100, "output": 50},
            "audn": {"input": 80, "output": 40},
        },
    }
    if debug:
        base["debug_trace"] = {
            "extracted_facts": [
                {"text": "Python uses indentation", "category": "detail"},
            ],
            "audn_decisions": [
                {
                    "fact_index": 0,
                    "action": "ADD",
                    "similar_memories": [
                        {"id": 5, "text": "Python is popular", "similarity": 0.72},
                    ],
                    "new_id": 42,
                },
            ],
            "execution_summary": {
                "added": [42],
                "updated": [],
                "deleted": [],
                "noops": 0,
                "conflicts": 0,
            },
        }
    return base


@pytest.fixture
def client():
    """Create a test client with mocked memory engine and extraction."""
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": "anthropic"}):
        import app as app_module

        importlib.reload(app_module)

        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {
            "total_memories": 5,
            "dimension": 384,
            "model": "all-MiniLM-L6-v2",
        }
        mock_engine.is_ready.return_value = {"ready": True, "status": "ready"}
        app_module.memory = mock_engine

        mock_provider = _make_mock_provider()
        app_module.extract_provider = mock_provider

        yield TestClient(app_module.app), mock_engine, mock_provider


class TestExtractDebugMode:
    """Test debug mode for extraction trace."""

    @staticmethod
    def _wait_for_terminal_job(test_client, job_id: str, timeout_sec: float = 3.0):
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

    def test_debug_false_returns_no_trace(self, client):
        """When debug=False (default), no debug_trace should be in the job result."""
        test_client, mock_engine, mock_provider = client

        import app as app_module

        # Mock run_extraction to return result WITHOUT debug_trace
        result_no_debug = _make_mock_extraction_result(debug=False)

        def fake_run_extraction(provider, engine, messages, source, context, allowed_prefixes=None, debug=False, profile=None):
            return result_no_debug

        with patch.object(app_module, "run_extraction", fake_run_extraction):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: Python uses indentation for blocks.",
                    "source": "test/proj",
                    "context": "stop",
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            data = response.json()
            job_id = data["job_id"]

            job_state = self._wait_for_terminal_job(test_client, job_id)
            assert job_state["status"] == "completed"
            result = job_state.get("result", {})
            assert "debug_trace" not in result

    def test_debug_true_returns_trace_with_structure(self, client):
        """When debug=True, the job result should contain debug_trace."""
        test_client, mock_engine, mock_provider = client

        import app as app_module

        result_with_debug = _make_mock_extraction_result(debug=True)

        def fake_run_extraction(provider, engine, messages, source, context, allowed_prefixes=None, debug=False, profile=None):
            if debug:
                return result_with_debug
            return _make_mock_extraction_result(debug=False)

        with patch.object(app_module, "run_extraction", fake_run_extraction):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: Python uses indentation for blocks.",
                    "source": "test/proj",
                    "context": "stop",
                    "debug": True,
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            data = response.json()
            job_id = data["job_id"]

            job_state = self._wait_for_terminal_job(test_client, job_id)
            assert job_state["status"] == "completed"
            result = job_state.get("result", {})
            assert "debug_trace" in result

            trace = result["debug_trace"]
            assert "extracted_facts" in trace
            assert "audn_decisions" in trace
            assert "execution_summary" in trace

    def test_debug_trace_includes_extracted_facts(self, client):
        """Debug trace should include the extracted facts with text and category."""
        test_client, mock_engine, mock_provider = client

        import app as app_module

        result_with_debug = _make_mock_extraction_result(debug=True)

        def fake_run_extraction(provider, engine, messages, source, context, allowed_prefixes=None, debug=False, profile=None):
            if debug:
                return result_with_debug
            return _make_mock_extraction_result(debug=False)

        with patch.object(app_module, "run_extraction", fake_run_extraction):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: Python uses indentation.",
                    "source": "test/proj",
                    "context": "stop",
                    "debug": True,
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            job_state = self._wait_for_terminal_job(test_client, response.json()["job_id"])
            trace = job_state["result"]["debug_trace"]

            facts = trace["extracted_facts"]
            assert len(facts) > 0
            assert "text" in facts[0]
            assert "category" in facts[0]

    def test_debug_trace_includes_audn_decisions(self, client):
        """Debug trace should include AUDN decisions with similar memories."""
        test_client, mock_engine, mock_provider = client

        import app as app_module

        result_with_debug = _make_mock_extraction_result(debug=True)

        def fake_run_extraction(provider, engine, messages, source, context, allowed_prefixes=None, debug=False, profile=None):
            if debug:
                return result_with_debug
            return _make_mock_extraction_result(debug=False)

        with patch.object(app_module, "run_extraction", fake_run_extraction):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: Python uses indentation.",
                    "source": "test/proj",
                    "context": "stop",
                    "debug": True,
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            job_state = self._wait_for_terminal_job(test_client, response.json()["job_id"])
            trace = job_state["result"]["debug_trace"]

            decisions = trace["audn_decisions"]
            assert len(decisions) > 0
            d = decisions[0]
            assert "fact_index" in d
            assert "action" in d
            assert "similar_memories" in d

    def test_debug_trace_includes_execution_summary(self, client):
        """Debug trace should include execution summary."""
        test_client, mock_engine, mock_provider = client

        import app as app_module

        result_with_debug = _make_mock_extraction_result(debug=True)

        def fake_run_extraction(provider, engine, messages, source, context, allowed_prefixes=None, debug=False, profile=None):
            if debug:
                return result_with_debug
            return _make_mock_extraction_result(debug=False)

        with patch.object(app_module, "run_extraction", fake_run_extraction):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: Python uses indentation.",
                    "source": "test/proj",
                    "context": "stop",
                    "debug": True,
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            job_state = self._wait_for_terminal_job(test_client, response.json()["job_id"])
            trace = job_state["result"]["debug_trace"]

            summary = trace["execution_summary"]
            assert "added" in summary
            assert "updated" in summary
            assert "deleted" in summary
            assert "noops" in summary
            assert "conflicts" in summary

    def test_debug_field_defaults_to_false(self, client):
        """The debug field should default to False when not specified."""
        test_client, mock_engine, mock_provider = client

        import app as app_module

        called_with_debug = []

        def fake_run_extraction(provider, engine, messages, source, context, allowed_prefixes=None, debug=False, profile=None):
            called_with_debug.append(debug)
            return _make_mock_extraction_result(debug=False)

        with patch.object(app_module, "run_extraction", fake_run_extraction):
            response = test_client.post(
                "/memory/extract",
                json={
                    "messages": "User: Hello world.",
                    "source": "test/proj",
                    "context": "stop",
                },
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 202
            self._wait_for_terminal_job(test_client, response.json()["job_id"])
            assert len(called_with_debug) > 0
            assert called_with_debug[0] is False
