"""Tests for search quality feedback — rank tracking, explicit feedback, metrics."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# --- UsageTracker extensions ---

class TestRankTracking:
    """Retrieval log should capture result rank position."""

    @pytest.fixture
    def tracker(self, tmp_path):
        from usage_tracker import UsageTracker
        return UsageTracker(str(tmp_path / "usage.db"))

    def test_log_retrieval_with_rank(self, tracker):
        tracker.log_retrieval(memory_id=1, query="test", source="s", rank=1, result_count=5)
        tracker.log_retrieval(memory_id=2, query="test", source="s", rank=2, result_count=5)
        stats = tracker.get_retrieval_stats([1, 2])
        assert stats[1]["count"] == 1
        assert stats[2]["count"] == 1

    def test_log_retrieval_without_rank_defaults(self, tracker):
        """Backward compat: rank defaults to 0 (unknown)."""
        tracker.log_retrieval(memory_id=1, query="test")
        stats = tracker.get_retrieval_stats([1])
        assert stats[1]["count"] == 1


class TestSearchFeedback:
    """Explicit search quality signals stored in search_feedback table."""

    @pytest.fixture
    def tracker(self, tmp_path):
        from usage_tracker import UsageTracker
        return UsageTracker(str(tmp_path / "usage.db"))

    def test_log_feedback_useful(self, tracker):
        tracker.log_search_feedback(memory_id=1, query="auth", signal="useful")
        fb = tracker.get_search_quality()
        assert fb["feedback"]["useful"] >= 1

    def test_log_feedback_not_useful(self, tracker):
        tracker.log_search_feedback(memory_id=2, query="auth", signal="not_useful")
        fb = tracker.get_search_quality()
        assert fb["feedback"]["not_useful"] >= 1

    def test_feedback_with_search_id(self, tracker):
        tracker.log_search_feedback(memory_id=1, query="q", signal="useful", search_id="abc123")
        fb = tracker.get_search_quality()
        assert fb["feedback"]["useful"] >= 1

    def test_invalid_signal_ignored(self, tracker):
        tracker.log_search_feedback(memory_id=1, query="q", signal="invalid_value")
        fb = tracker.get_search_quality()
        assert fb["feedback"]["useful"] == 0
        assert fb["feedback"]["not_useful"] == 0


class TestSearchQualityMetrics:
    """GET /metrics/search-quality aggregation."""

    @pytest.fixture
    def tracker(self, tmp_path):
        from usage_tracker import UsageTracker
        return UsageTracker(str(tmp_path / "usage.db"))

    def test_empty_metrics(self, tracker):
        quality = tracker.get_search_quality()
        assert quality["total_searches"] == 0
        assert quality["feedback"]["useful"] == 0
        assert quality["feedback"]["not_useful"] == 0
        assert quality["rank_distribution"]["top_3"] == 0

    def test_rank_distribution(self, tracker):
        # 3 results at rank 1-3, 2 at rank 4+
        for i, rank in enumerate([1, 2, 3, 4, 5]):
            tracker.log_retrieval(memory_id=i, query="q", rank=rank, result_count=5)
        quality = tracker.get_search_quality()
        assert quality["rank_distribution"]["top_3"] == 3
        assert quality["rank_distribution"]["rank_4_plus"] == 2

    def test_feedback_ratio(self, tracker):
        tracker.log_search_feedback(memory_id=1, query="q", signal="useful")
        tracker.log_search_feedback(memory_id=2, query="q", signal="useful")
        tracker.log_search_feedback(memory_id=3, query="q", signal="not_useful")
        quality = tracker.get_search_quality()
        assert quality["feedback"]["useful"] == 2
        assert quality["feedback"]["not_useful"] == 1
        assert quality["feedback"]["useful_ratio"] == pytest.approx(2 / 3, rel=0.01)

    def test_search_volume(self, tracker):
        tracker.log_api_event("search", "s1")
        tracker.log_api_event("search", "s2")
        tracker.log_api_event("search", "s1")
        quality = tracker.get_search_quality()
        assert quality["total_searches"] == 3

    def test_quality_with_period(self, tracker):
        tracker.log_api_event("search", "s1")
        quality = tracker.get_search_quality(period="today")
        assert quality["total_searches"] >= 1
        assert quality["period"] == "today"


class TestNullTrackerFeedback:
    """NullTracker must have matching stubs."""

    def test_null_log_search_feedback(self):
        from usage_tracker import NullTracker
        t = NullTracker()
        t.log_search_feedback(memory_id=1, query="q", signal="useful")

    def test_null_get_search_quality(self):
        from usage_tracker import NullTracker
        t = NullTracker()
        result = t.get_search_quality()
        assert result == {"enabled": False}


# --- API Endpoints ---

class TestFeedbackEndpoint:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "API_KEY": "admin-key",
                "EXTRACT_PROVIDER": "",
                "DATA_DIR": tmpdir,
            }
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                app_module.memory = mock_engine

                # Set a real UsageTracker so feedback/quality endpoints work
                from usage_tracker import UsageTracker
                app_module.usage_tracker = UsageTracker(os.path.join(tmpdir, "usage.db"))

                yield TestClient(app_module.app), app_module

    def test_post_feedback(self, client):
        tc, _ = client
        resp = tc.post(
            "/search/feedback",
            json={"memory_id": 1, "query": "test query", "signal": "useful"},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "recorded"

    def test_post_feedback_not_useful(self, client):
        tc, _ = client
        resp = tc.post(
            "/search/feedback",
            json={"memory_id": 2, "query": "test", "signal": "not_useful"},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200

    def test_post_feedback_invalid_signal(self, client):
        tc, _ = client
        resp = tc.post(
            "/search/feedback",
            json={"memory_id": 1, "query": "test", "signal": "maybe"},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 422

    def test_get_search_quality(self, client):
        tc, _ = client
        resp = tc.get("/metrics/search-quality", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert "total_searches" in body
        assert "feedback" in body
        assert "rank_distribution" in body

    def test_search_quality_with_period(self, client):
        tc, _ = client
        resp = tc.get(
            "/metrics/search-quality?period=30d",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["period"] == "30d"

    def test_feedback_requires_auth(self, client):
        tc, _ = client
        resp = tc.post(
            "/search/feedback",
            json={"memory_id": 1, "query": "test", "signal": "useful"},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code in (401, 403)
