"""Tests for quality dashboard — /metrics/quality-summary and /metrics/failures endpoints."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# --- UsageTracker unit tests ---


class TestQualitySummary:
    """UsageTracker.get_quality_summary aggregation."""

    @pytest.fixture
    def tracker(self, tmp_path):
        from usage_tracker import UsageTracker
        return UsageTracker(str(tmp_path / "usage.db"))

    def test_empty_quality_summary(self, tracker):
        result = tracker.get_quality_summary()
        assert result["period"] == "7d"
        assert result["retrieval_precision"]["positive_feedback_rate"] == 0.0
        assert result["retrieval_precision"]["total_searches"] == 0
        assert result["retrieval_precision"]["searches_with_feedback"] == 0
        assert result["extraction_accuracy"]["total_extractions"] == 0
        assert result["extraction_accuracy"]["add_rate"] == 0.0

    def test_retrieval_precision_with_feedback(self, tracker):
        tracker.log_api_event("search", "src1")
        tracker.log_api_event("search", "src2")
        tracker.log_api_event("search", "src3")
        tracker.log_search_feedback(memory_id=1, query="q1", signal="useful")
        tracker.log_search_feedback(memory_id=2, query="q2", signal="useful")
        tracker.log_search_feedback(memory_id=3, query="q3", signal="not_useful")

        result = tracker.get_quality_summary()
        assert result["retrieval_precision"]["total_searches"] == 3
        assert result["retrieval_precision"]["searches_with_feedback"] == 3
        assert result["retrieval_precision"]["positive_feedback_rate"] == pytest.approx(
            2 / 3, rel=0.01
        )

    def test_extraction_accuracy_rates(self, tracker):
        tracker.log_extraction_outcome(
            source="proj/a", extracted=10, stored=4, updated=2, deleted=1, noop=3, conflict=0
        )
        result = tracker.get_quality_summary()
        acc = result["extraction_accuracy"]
        assert acc["total_extractions"] == 1
        assert acc["add_rate"] == pytest.approx(0.4, rel=0.01)
        assert acc["update_rate"] == pytest.approx(0.2, rel=0.01)
        assert acc["noop_rate"] == pytest.approx(0.3, rel=0.01)
        assert acc["delete_rate"] == pytest.approx(0.1, rel=0.01)
        assert acc["conflict_rate"] == 0.0

    def test_multiple_extractions_aggregate(self, tracker):
        tracker.log_extraction_outcome(
            source="a", extracted=4, stored=2, updated=1, deleted=0, noop=1, conflict=0
        )
        tracker.log_extraction_outcome(
            source="b", extracted=6, stored=3, updated=0, deleted=1, noop=2, conflict=0
        )
        result = tracker.get_quality_summary()
        acc = result["extraction_accuracy"]
        assert acc["total_extractions"] == 2
        # total extracted=10, stored=5, updated=1, deleted=1, noop=3
        assert acc["add_rate"] == pytest.approx(0.5, rel=0.01)
        assert acc["noop_rate"] == pytest.approx(0.3, rel=0.01)

    def test_quality_summary_with_period(self, tracker):
        tracker.log_api_event("search", "s")
        tracker.log_search_feedback(memory_id=1, query="q", signal="useful")
        result = tracker.get_quality_summary(period="today")
        assert result["period"] == "today"
        assert result["retrieval_precision"]["total_searches"] >= 1

    def test_quality_summary_period_30d(self, tracker):
        tracker.log_extraction_outcome(
            source="s", extracted=5, stored=3, updated=0, deleted=0, noop=2, conflict=0
        )
        result = tracker.get_quality_summary(period="30d")
        assert result["period"] == "30d"
        assert result["extraction_accuracy"]["total_extractions"] == 1

    def test_quality_summary_conflict_rate(self, tracker):
        tracker.log_extraction_outcome(
            source="s", extracted=20, stored=10, updated=3, deleted=1, noop=4, conflict=2
        )
        result = tracker.get_quality_summary()
        assert result["extraction_accuracy"]["conflict_rate"] == pytest.approx(0.1, rel=0.01)


class TestFailures:
    """UsageTracker.get_failures for debugging low-quality results."""

    @pytest.fixture
    def tracker(self, tmp_path):
        from usage_tracker import UsageTracker
        return UsageTracker(str(tmp_path / "usage.db"))

    def test_empty_retrieval_failures(self, tracker):
        result = tracker.get_failures(failure_type="retrieval")
        assert result["failures"] == []

    def test_empty_extraction_failures(self, tracker):
        result = tracker.get_failures(failure_type="extraction")
        assert result["failures"] == []

    def test_retrieval_failures_returns_negative_feedback(self, tracker):
        tracker.log_search_feedback(memory_id=1, query="auth setup", signal="useful")
        tracker.log_search_feedback(memory_id=2, query="deploy steps", signal="not_useful")
        tracker.log_search_feedback(memory_id=3, query="db config", signal="not_useful")

        result = tracker.get_failures(failure_type="retrieval")
        failures = result["failures"]
        assert len(failures) == 2
        for f in failures:
            assert f["type"] == "retrieval"
            assert f["feedback"] == "negative"
        queries = [f["query"] for f in failures]
        assert "deploy steps" in queries
        assert "db config" in queries

    def test_retrieval_failures_limit(self, tracker):
        for i in range(20):
            tracker.log_search_feedback(memory_id=i, query=f"q{i}", signal="not_useful")
        result = tracker.get_failures(failure_type="retrieval", limit=5)
        assert len(result["failures"]) == 5

    def test_extraction_failures_high_noop(self, tracker):
        # Low noop ratio - should still appear since noop > 0
        tracker.log_extraction_outcome(
            source="good/src", extracted=10, stored=8, updated=1, deleted=0, noop=1, conflict=0
        )
        # High noop ratio
        tracker.log_extraction_outcome(
            source="bad/src", extracted=10, stored=1, updated=0, deleted=0, noop=9, conflict=0
        )

        result = tracker.get_failures(failure_type="extraction")
        failures = result["failures"]
        assert len(failures) == 2
        # Highest noop ratio should be first
        assert failures[0]["source"] == "bad/src"
        assert failures[0]["noop_ratio"] == pytest.approx(0.9, rel=0.01)
        assert failures[0]["type"] == "extraction"

    def test_extraction_failures_limit(self, tracker):
        for i in range(15):
            tracker.log_extraction_outcome(
                source=f"src/{i}", extracted=5, stored=1, updated=0, deleted=0, noop=4, conflict=0
            )
        result = tracker.get_failures(failure_type="extraction", limit=3)
        assert len(result["failures"]) == 3

    def test_retrieval_failure_includes_memory_id(self, tracker):
        tracker.log_search_feedback(memory_id=42, query="test", signal="not_useful", search_id="sid-1")
        result = tracker.get_failures(failure_type="retrieval")
        f = result["failures"][0]
        assert f["memory_id"] == 42
        assert f["search_id"] == "sid-1"


class TestNullTrackerQualityDashboard:
    def test_null_quality_summary(self):
        from usage_tracker import NullTracker
        t = NullTracker()
        assert t.get_quality_summary() == {"enabled": False}

    def test_null_failures(self):
        from usage_tracker import NullTracker
        t = NullTracker()
        assert t.get_failures() == {"enabled": False}


# --- API endpoint tests ---


class TestQualitySummaryEndpoint:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "admin-key", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                app_module.memory = mock_engine

                from usage_tracker import UsageTracker
                tracker = UsageTracker(os.path.join(tmpdir, "usage.db"))
                app_module.usage_tracker = tracker

                yield TestClient(app_module.app), tracker

    def test_get_quality_summary_empty(self, client):
        tc, _ = client
        resp = tc.get("/metrics/quality-summary", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert "retrieval_precision" in body
        assert "extraction_accuracy" in body
        assert body["period"] == "7d"

    def test_get_quality_summary_with_data(self, client):
        tc, tracker = client
        tracker.log_api_event("search", "s")
        tracker.log_api_event("search", "s")
        tracker.log_search_feedback(memory_id=1, query="q", signal="useful")
        tracker.log_search_feedback(memory_id=2, query="q", signal="not_useful")
        tracker.log_extraction_outcome(
            source="test", extracted=10, stored=4, updated=2, deleted=1, noop=3, conflict=0
        )

        resp = tc.get("/metrics/quality-summary", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["retrieval_precision"]["total_searches"] == 2
        assert body["retrieval_precision"]["searches_with_feedback"] == 2
        assert body["retrieval_precision"]["positive_feedback_rate"] == pytest.approx(0.5, rel=0.01)
        assert body["extraction_accuracy"]["total_extractions"] == 1
        assert body["extraction_accuracy"]["add_rate"] == pytest.approx(0.4, rel=0.01)

    def test_get_quality_summary_with_period(self, client):
        tc, _ = client
        resp = tc.get(
            "/metrics/quality-summary?period=30d",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["period"] == "30d"

    def test_quality_summary_requires_auth(self, client):
        tc, _ = client
        resp = tc.get("/metrics/quality-summary", headers={"X-API-Key": "wrong"})
        assert resp.status_code in (401, 403)

    def test_quality_summary_invalid_period(self, client):
        tc, _ = client
        resp = tc.get(
            "/metrics/quality-summary?period=2h",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 422


class TestFailuresEndpoint:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "admin-key", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                app_module.memory = mock_engine

                from usage_tracker import UsageTracker
                tracker = UsageTracker(os.path.join(tmpdir, "usage.db"))
                app_module.usage_tracker = tracker

                yield TestClient(app_module.app), tracker

    def test_get_retrieval_failures_empty(self, client):
        tc, _ = client
        resp = tc.get("/metrics/failures", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["failures"] == []

    def test_get_retrieval_failures_with_data(self, client):
        tc, tracker = client
        tracker.log_search_feedback(memory_id=1, query="auth", signal="not_useful")
        tracker.log_search_feedback(memory_id=2, query="deploy", signal="useful")
        tracker.log_search_feedback(memory_id=3, query="config", signal="not_useful")

        resp = tc.get("/metrics/failures", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["failures"]) == 2
        for f in body["failures"]:
            assert f["type"] == "retrieval"
            assert f["feedback"] == "negative"

    def test_get_extraction_failures(self, client):
        tc, tracker = client
        tracker.log_extraction_outcome(
            source="noisy/src", extracted=10, stored=1, updated=0, deleted=0, noop=9, conflict=0
        )
        resp = tc.get(
            "/metrics/failures?type=extraction",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["failures"]) == 1
        assert body["failures"][0]["type"] == "extraction"
        assert body["failures"][0]["noop_ratio"] == pytest.approx(0.9, rel=0.01)

    def test_failures_with_limit(self, client):
        tc, tracker = client
        for i in range(20):
            tracker.log_search_feedback(memory_id=i, query=f"q{i}", signal="not_useful")
        resp = tc.get(
            "/metrics/failures?limit=5",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["failures"]) == 5

    def test_failures_requires_auth(self, client):
        tc, _ = client
        resp = tc.get("/metrics/failures", headers={"X-API-Key": "wrong"})
        assert resp.status_code in (401, 403)

    def test_failures_invalid_type(self, client):
        tc, _ = client
        resp = tc.get(
            "/metrics/failures?type=invalid",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 422

    def test_failures_default_limit(self, client):
        tc, tracker = client
        for i in range(15):
            tracker.log_search_feedback(memory_id=i, query=f"q{i}", signal="not_useful")
        resp = tc.get("/metrics/failures", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        # Default limit is 10
        assert len(resp.json()["failures"]) == 10


# --- Benchmark scenario loading tests ---


class TestBenchmarkScenarios:
    """Verify benchmark scenarios load correctly."""

    def test_load_all_benchmark_scenarios(self):
        from eval.loader import load_all_scenarios
        scenarios_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "eval", "scenarios"
        )
        scenarios = load_all_scenarios(scenarios_dir, category="benchmark")
        assert len(scenarios) == 6
        ids = [s.id for s in scenarios]
        for i in range(1, 7):
            assert f"benchmark-{i:03d}" in ids

    def test_benchmark_scenario_categories(self):
        from eval.loader import load_all_scenarios
        scenarios_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "eval", "scenarios"
        )
        scenarios = load_all_scenarios(scenarios_dir, category="benchmark")
        for s in scenarios:
            assert s.category == "benchmark"
            assert len(s.memories) >= 1
            assert len(s.expected) >= 1
            assert s.prompt

    def test_run_benchmarks(self):
        from eval.benchmarks import run_benchmarks
        scenarios_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "eval", "scenarios"
        )
        result = run_benchmarks(scenarios_dir=scenarios_dir)
        # No-memory baseline should score 0 on contains rubrics
        assert result.no_memory.overall_with_memory == 0.0
        # Naive retrieval uses raw memory text, should score well on contains
        assert result.naive_retrieval.overall_with_memory > 0.0
        # Full stack should match naive for deterministic rubrics
        assert result.full_stack.overall_with_memory > 0.0
        # Summary should be non-empty
        summary = result.summary()
        assert "BENCHMARK QUALITY REPORT" in summary

    def test_benchmark_no_memory_scores_zero_on_contains(self):
        from eval.benchmarks import _score_no_memory
        from eval.loader import load_scenario
        scenarios_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "eval", "scenarios"
        )
        s = load_scenario(
            os.path.join(scenarios_dir, "benchmark", "benchmark-001-arch-decision.yaml")
        )
        result = _score_no_memory(s)
        assert result.score_with_memory == 0.0

    def test_benchmark_naive_retrieval_contains_keywords(self):
        from eval.benchmarks import _score_naive_retrieval
        from eval.loader import load_scenario
        scenarios_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "eval", "scenarios"
        )
        s = load_scenario(
            os.path.join(scenarios_dir, "benchmark", "benchmark-001-arch-decision.yaml")
        )
        result = _score_naive_retrieval(s)
        # Memory text contains "CockroachDB", "multi-region", "ADR-017"
        assert result.score_with_memory > 0.5
