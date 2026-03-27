"""Tests for extraction quality dashboard — outcome tracking and metrics."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestExtractionOutcomeTracking:
    """Extraction outcome counts stored in usage DB."""

    @pytest.fixture
    def tracker(self, tmp_path):
        from usage_tracker import UsageTracker
        return UsageTracker(str(tmp_path / "usage.db"))

    def test_log_extraction_outcome(self, tracker):
        tracker.log_extraction_outcome(
            source="claude-code/proj",
            extracted=5, stored=3, updated=1, deleted=0, noop=1, conflict=0,
        )
        quality = tracker.get_extraction_quality()
        assert quality["totals"]["extracted"] == 5
        assert quality["totals"]["stored"] == 3
        assert quality["totals"]["noop"] == 1

    def test_multiple_extractions_aggregate(self, tracker):
        tracker.log_extraction_outcome(source="a", extracted=4, stored=2, updated=1, deleted=0, noop=1, conflict=0)
        tracker.log_extraction_outcome(source="b", extracted=6, stored=3, updated=0, deleted=1, noop=2, conflict=0)
        quality = tracker.get_extraction_quality()
        assert quality["totals"]["extracted"] == 10
        assert quality["totals"]["stored"] == 5
        assert quality["totals"]["noop"] == 3
        assert quality["totals"]["deleted"] == 1
        assert quality["extraction_count"] == 2

    def test_per_source_breakdown(self, tracker):
        tracker.log_extraction_outcome(source="claude-code/proj", extracted=5, stored=3, updated=0, deleted=0, noop=2, conflict=0)
        tracker.log_extraction_outcome(source="learning/proj", extracted=3, stored=1, updated=1, deleted=0, noop=1, conflict=0)
        quality = tracker.get_extraction_quality()
        by_source = quality["by_source"]
        assert "claude-code/proj" in by_source
        assert by_source["claude-code/proj"]["stored"] == 3
        assert "learning/proj" in by_source
        assert by_source["learning/proj"]["updated"] == 1

    def test_noop_ratio(self, tracker):
        tracker.log_extraction_outcome(source="s", extracted=10, stored=2, updated=1, deleted=0, noop=7, conflict=0)
        quality = tracker.get_extraction_quality()
        assert quality["totals"]["noop_ratio"] == pytest.approx(0.7, rel=0.01)

    def test_empty_metrics(self, tracker):
        quality = tracker.get_extraction_quality()
        assert quality["extraction_count"] == 0
        assert quality["totals"]["extracted"] == 0
        assert quality["totals"]["noop_ratio"] == 0.0

    def test_period_filter(self, tracker):
        tracker.log_extraction_outcome(source="s", extracted=5, stored=3, updated=0, deleted=0, noop=2, conflict=0)
        quality = tracker.get_extraction_quality(period="today")
        assert quality["period"] == "today"
        assert quality["extraction_count"] >= 1


class TestNullTrackerExtractionDashboard:
    def test_null_log_extraction_outcome(self):
        from usage_tracker import NullTracker
        t = NullTracker()
        t.log_extraction_outcome(source="s", extracted=5, stored=3, updated=0, deleted=0, noop=2, conflict=0)

    def test_null_get_extraction_quality(self):
        from usage_tracker import NullTracker
        t = NullTracker()
        result = t.get_extraction_quality()
        assert result == {"enabled": False}


class TestExtractionQualityEndpoint:
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

    def test_get_extraction_quality(self, client):
        tc, tracker = client
        tracker.log_extraction_outcome(source="test", extracted=5, stored=3, updated=1, deleted=0, noop=1, conflict=0)
        resp = tc.get("/metrics/extraction-quality", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["extraction_count"] == 1
        assert body["totals"]["stored"] == 3

    def test_extraction_quality_with_period(self, client):
        tc, _ = client
        resp = tc.get("/metrics/extraction-quality?period=30d", headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        assert resp.json()["period"] == "30d"

    def test_extraction_quality_requires_auth(self, client):
        tc, _ = client
        resp = tc.get("/metrics/extraction-quality", headers={"X-API-Key": "wrong"})
        assert resp.status_code in (401, 403)


class TestFallbackMetrics:
    """Test that fallback_add is tracked through the metrics pipeline."""

    @pytest.fixture
    def tracker(self, tmp_path):
        from usage_tracker import UsageTracker
        return UsageTracker(str(tmp_path / "usage.db"))

    def test_fallback_count_in_extraction_quality(self, tracker):
        tracker.log_extraction_outcome(
            source="test/proj", extracted=5, stored=3, updated=0, deleted=0,
            noop=1, conflict=0, fallback=2,
        )
        quality = tracker.get_extraction_quality()
        assert quality["totals"]["fallback"] == 2

    def test_fallback_in_per_source_breakdown(self, tracker):
        tracker.log_extraction_outcome(
            source="test/proj", extracted=3, stored=1, updated=0, deleted=0,
            noop=1, conflict=0, fallback=1,
        )
        quality = tracker.get_extraction_quality()
        assert quality["by_source"]["test/proj"]["fallback"] == 1

    def test_fallback_rate_in_quality_summary(self, tracker):
        tracker.log_extraction_outcome(
            source="test/proj", extracted=10, stored=5, updated=0, deleted=0,
            noop=3, conflict=0, fallback=2,
        )
        summary = tracker.get_quality_summary()
        assert summary["extraction_accuracy"]["fallback_rate"] == pytest.approx(0.2, rel=0.01)

    def test_fallback_zero_by_default(self, tracker):
        tracker.log_extraction_outcome(
            source="test/proj", extracted=5, stored=3, updated=0, deleted=0, noop=2, conflict=0,
        )
        quality = tracker.get_extraction_quality()
        assert quality["totals"]["fallback"] == 0

    def test_db_migration_adds_fallback_column(self, tmp_path):
        """Existing DB without fallback column should get it via migration."""
        import sqlite3
        db_path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE extraction_outcomes (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            source TEXT DEFAULT '', extracted INTEGER DEFAULT 0,
            stored INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0, noop INTEGER DEFAULT 0,
            conflict INTEGER DEFAULT 0
        )""")
        conn.commit()
        conn.close()
        from usage_tracker import UsageTracker
        tracker = UsageTracker(db_path)
        # Column should exist after migration
        tracker.log_extraction_outcome(source="new", extracted=5, stored=3, fallback=1)
        quality = tracker.get_extraction_quality()
        assert quality["totals"]["fallback"] == 1
