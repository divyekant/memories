"""Tests for POST /search/explain endpoint and hybrid_search_explain engine method."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked memory engine and auth."""
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {
            "total_memories": 5,
            "dimension": 384,
            "model": "all-MiniLM-L6-v2",
        }
        mock_engine.search.return_value = []
        mock_engine.hybrid_search.return_value = []
        mock_engine.hybrid_search_explain.return_value = {
            "results": [
                {"id": 1, "text": "Python is great", "source": "test/proj", "rrf_score": 0.012},
                {"id": 2, "text": "FastAPI rocks", "source": "test/proj", "rrf_score": 0.009},
            ],
            "explain": {
                "candidates_considered": 10,
                "vector_candidates": [
                    {"id": 1, "text": "Python is great", "score": 0.89},
                    {"id": 2, "text": "FastAPI rocks", "score": 0.72},
                ],
                "bm25_candidates": [
                    {"id": 1, "text": "Python is great", "score": 0.65},
                    {"id": 3, "text": "Django framework", "score": 0.45},
                ],
                "filtered_by_source": 2,
                "filtered_by_auth": 0,
                "scoring_weights": {"vector": 0.7, "bm25": 0.3, "recency": 0.0},
                "rrf_k": 60,
            },
        }
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


class TestSearchExplainEndpoint:
    """Test POST /search/explain."""

    def test_explain_returns_vector_and_bm25_candidates(self, client):
        test_client, mock_engine = client
        response = test_client.post(
            "/search/explain",
            json={"query": "python", "k": 5, "hybrid": True},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "results" in body
        assert "explain" in body
        explain = body["explain"]
        assert "vector_candidates" in explain
        assert "bm25_candidates" in explain
        assert len(explain["vector_candidates"]) > 0
        assert len(explain["bm25_candidates"]) > 0
        # Verify vector candidates have expected fields
        vc = explain["vector_candidates"][0]
        assert "id" in vc
        assert "text" in vc
        assert "score" in vc

    def test_explain_returns_scoring_weights(self, client):
        test_client, mock_engine = client
        response = test_client.post(
            "/search/explain",
            json={"query": "python", "k": 5, "hybrid": True, "vector_weight": 0.6},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200
        body = response.json()
        explain = body["explain"]
        assert "scoring_weights" in explain
        weights = explain["scoring_weights"]
        assert "vector" in weights
        assert "bm25" in weights
        assert "recency" in weights
        assert "rrf_k" in explain

    def test_explain_returns_candidates_considered(self, client):
        test_client, mock_engine = client
        response = test_client.post(
            "/search/explain",
            json={"query": "python", "k": 5, "hybrid": True},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200
        body = response.json()
        explain = body["explain"]
        assert "candidates_considered" in explain
        assert isinstance(explain["candidates_considered"], int)

    def test_explain_results_match_regular_search(self, client):
        """Explain results should be the same as regular hybrid_search."""
        test_client, mock_engine = client
        # Set up matching results for both
        expected_results = [
            {"id": 1, "text": "Python is great", "source": "test/proj", "rrf_score": 0.012},
        ]
        mock_engine.hybrid_search.return_value = expected_results
        mock_engine.hybrid_search_explain.return_value = {
            "results": expected_results,
            "explain": {
                "candidates_considered": 5,
                "vector_candidates": [],
                "bm25_candidates": [],
                "filtered_by_source": 0,
                "filtered_by_auth": 0,
                "scoring_weights": {"vector": 0.7, "bm25": 0.3, "recency": 0.0},
                "rrf_k": 60,
            },
        }

        # Get regular search results
        regular_resp = test_client.post(
            "/search",
            json={"query": "python", "k": 5, "hybrid": True},
            headers={"X-API-Key": "test-key"},
        )
        assert regular_resp.status_code == 200
        regular_results = regular_resp.json()["results"]

        # Get explain results
        explain_resp = test_client.post(
            "/search/explain",
            json={"query": "python", "k": 5, "hybrid": True},
            headers={"X-API-Key": "test-key"},
        )
        assert explain_resp.status_code == 200
        explain_results = explain_resp.json()["results"]

        assert explain_results == regular_results

    def test_explain_admin_key_succeeds(self, client):
        test_client, mock_engine = client
        response = test_client.post(
            "/search/explain",
            json={"query": "test", "k": 3},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200

    def test_explain_scoped_key_gets_403(self, client):
        """Scoped (non-admin) keys should get 403."""
        test_client, mock_engine = client

        from auth_context import AuthContext

        scoped_auth = AuthContext(
            role="read-write",
            prefixes=["test/"],
            key_type="managed",
            key_id="scoped-1",
            key_name="scoped-key",
        )

        import app as app_module

        original_get_auth = app_module._get_auth

        def mock_get_auth(request):
            return scoped_auth

        with patch.object(app_module, "_get_auth", mock_get_auth):
            response = test_client.post(
                "/search/explain",
                json={"query": "test", "k": 3},
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 403

    def test_explain_no_auth_unrestricted_succeeds(self):
        """When no API key is configured, unrestricted access (admin) should work."""
        with patch.dict(os.environ, {"API_KEY": "", "EXTRACT_PROVIDER": ""}):
            import app as app_module

            importlib.reload(app_module)
            mock_engine = MagicMock()
            mock_engine.stats_light.return_value = {"total_memories": 0}
            mock_engine.hybrid_search_explain.return_value = {
                "results": [],
                "explain": {
                    "candidates_considered": 0,
                    "vector_candidates": [],
                    "bm25_candidates": [],
                    "filtered_by_source": 0,
                    "filtered_by_auth": 0,
                    "scoring_weights": {"vector": 0.7, "bm25": 0.3, "recency": 0.0},
                    "rrf_k": 60,
                },
            }
            app_module.memory = mock_engine
            tc = TestClient(app_module.app)
            response = tc.post(
                "/search/explain",
                json={"query": "hello", "k": 1},
            )
            assert response.status_code == 200

    def test_explain_filtered_by_source_and_auth_present(self, client):
        test_client, mock_engine = client
        response = test_client.post(
            "/search/explain",
            json={"query": "python", "k": 5, "hybrid": True},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200
        explain = response.json()["explain"]
        assert "filtered_by_source" in explain
        assert "filtered_by_auth" in explain
