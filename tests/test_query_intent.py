"""Tests for temporal query intent detection"""

from datetime import datetime, timezone

import pytest

from query_intent import SearchAdjustments, TemporalIntent, classify_query, detect_temporal_intent

# Fixed reference: Wednesday 2026-04-08 12:00 UTC
REF = datetime(2026, 4, 8, 12, 0, 0, tzinfo=timezone.utc)


class TestRelativeTemporalExpressions:
    def test_last_week(self):
        result = detect_temporal_intent("what did I work on last week", REF)
        assert result is not None
        assert "2026-03-30" in result.since
        assert "2026-04-05" in result.until
        assert result.suppress_graph is True

    def test_last_month(self):
        result = detect_temporal_intent("changes from last month", REF)
        assert result is not None
        assert "2026-03-01" in result.since
        assert "2026-03-31" in result.until
        assert result.suppress_graph is True

    def test_yesterday(self):
        result = detect_temporal_intent("what happened yesterday", REF)
        assert result is not None
        assert "2026-04-07" in result.since
        assert "2026-04-07" in result.until
        assert result.suppress_graph is True

    def test_n_days_ago(self):
        result = detect_temporal_intent("what was discussed 3 days ago", REF)
        assert result is not None
        assert "2026-04-05" in result.since
        assert result.suppress_graph is True

    def test_n_weeks_ago(self):
        result = detect_temporal_intent("decisions two weeks ago", REF)
        assert result is not None
        assert "2026-03-23" in result.since
        assert result.suppress_graph is True

    def test_past_few_months(self):
        result = detect_temporal_intent("what changed in the past few months", REF)
        assert result is not None
        assert "2026-01" in result.since
        assert result.until is None
        assert result.suppress_graph is True

    def test_past_two_weeks(self):
        result = detect_temporal_intent("updates from the past two weeks", REF)
        assert result is not None
        assert "2026-03-25" in result.since
        assert result.until is None
        assert result.suppress_graph is True

    def test_no_temporal_intent(self):
        result = detect_temporal_intent("project architecture decisions", REF)
        assert result is None

    def test_no_temporal_in_factual_question(self):
        result = detect_temporal_intent("what database does OrderService use?", REF)
        assert result is None


class TestMonthExpressions:
    def test_in_month_past(self):
        result = detect_temporal_intent("what happened in March", REF)
        assert result is not None
        assert "2026-03-01" in result.since
        assert "2026-03-31" in result.until
        assert result.suppress_graph is True

    def test_in_month_future_wraps_to_previous_year(self):
        result = detect_temporal_intent("things from November", REF)
        assert result is not None
        assert "2025-11-01" in result.since
        assert "2025-11-30" in result.until

    def test_month_range(self):
        result = detect_temporal_intent("from July to October changes", REF)
        assert result is not None
        assert "07-01" in result.since
        assert "10-31" in result.until
        assert result.suppress_graph is True

    def test_month_range_between(self):
        result = detect_temporal_intent("between March and June", REF)
        assert result is not None
        assert "03-01" in result.since
        assert "06-30" in result.until


class TestThisPeriod:
    def test_this_week(self):
        result = detect_temporal_intent("what happened this week", REF)
        assert result is not None
        assert "2026-04-06" in result.since
        assert result.until is None
        assert result.suppress_graph is True

    def test_this_month(self):
        result = detect_temporal_intent("this month's changes", REF)
        assert result is not None
        assert "2026-04-01" in result.since
        assert result.until is None
        assert result.suppress_graph is True


class TestRecency:
    def test_recently(self):
        result = detect_temporal_intent("what did I recently add", REF)
        assert result is not None
        assert result.recency_boost is True
        assert result.since is None
        assert result.until is None
        assert result.suppress_graph is False

    def test_latest(self):
        result = detect_temporal_intent("show me the latest decisions", REF)
        assert result is not None
        assert result.recency_boost is True
        assert result.suppress_graph is False

    def test_most_recent(self):
        result = detect_temporal_intent("most recent architecture notes", REF)
        assert result is not None
        assert result.recency_boost is True


class TestLastWeekday:
    def test_last_thursday(self):
        # Ref is Wed Apr 8, so last Thursday = Apr 2
        result = detect_temporal_intent("what happened last Thursday", REF)
        assert result is not None
        assert "2026-04-02" in result.since
        assert "2026-04-02" in result.until
        assert result.suppress_graph is True

    def test_last_saturday(self):
        # Ref is Wed Apr 8, so last Saturday = Apr 4
        result = detect_temporal_intent("meeting notes from last Saturday", REF)
        assert result is not None
        assert "2026-04-04" in result.since
        assert "2026-04-04" in result.until


class TestEdgeCases:
    def test_default_reference_date(self):
        result = detect_temporal_intent("yesterday")
        assert result is not None
        assert result.since is not None

    def test_a_month_ago(self):
        result = detect_temporal_intent("a month ago", REF)
        assert result is not None
        assert "2026-03" in result.since
        assert result.suppress_graph is True

    def test_a_week_ago(self):
        result = detect_temporal_intent("a week ago", REF)
        assert result is not None
        assert "2026-03-30" in result.since

    def test_last_year(self):
        result = detect_temporal_intent("last year's decisions", REF)
        assert result is not None
        assert "2025-01-01" in result.since
        assert "2025-12-31" in result.until

    def test_couple_days_ago(self):
        result = detect_temporal_intent("couple days ago", REF)
        assert result is not None
        assert "2026-04-06" in result.since


class TestNoFalsePositives:
    """Queries that should NOT trigger temporal detection."""

    def test_time_in_non_temporal_context(self):
        """'time' as a concept, not a temporal filter."""
        result = detect_temporal_intent("How much time do I dedicate to guitar?", REF)
        assert result is None

    def test_when_as_question_word(self):
        result = detect_temporal_intent("When did I volunteer at the shelter?", REF)
        assert result is None

    def test_before_as_event_reference(self):
        result = detect_temporal_intent("What was my last name before I changed it?", REF)
        assert result is None

    def test_age_reference(self):
        result = detect_temporal_intent("How old was I when grandma gave me the necklace?", REF)
        assert result is None

    def test_pure_factual(self):
        result = detect_temporal_intent("What database does the project use?", REF)
        assert result is None

    def test_hook_style_query(self):
        result = detect_temporal_intent("project memories architecture decisions conventions patterns", REF)
        assert result is None

    def test_last_name_not_temporal(self):
        """'last' in 'last name' is NOT temporal."""
        result = detect_temporal_intent("What was my last name before I got married?", REF)
        assert result is None


class TestStrongerAssertions:
    """Pin full dates including year to catch year-assignment bugs."""

    def test_month_range_pins_year(self):
        # July-Oct from Apr 2026 ref → should be 2025 (in the past)
        result = detect_temporal_intent("from July to October changes", REF)
        assert result is not None
        assert "2025-07-01" in result.since
        assert "2025-10-31" in result.until

    def test_this_year(self):
        result = detect_temporal_intent("this year's decisions", REF)
        assert result is not None
        assert "2026-01-01" in result.since
        assert result.until is None
        assert result.suppress_graph is True

    def test_abbreviated_month(self):
        result = detect_temporal_intent("what happened in Mar?", REF)
        assert result is not None
        assert "2026-03-01" in result.since
        assert "2026-03-31" in result.until


class TestClassifyQuery:
    def test_temporal_query_adjusts_since_until(self):
        adj = classify_query("what did we decide last week?", REF)
        assert adj.since is not None
        assert adj.until is not None
        assert adj.graph_weight == 0.0  # suppressed for temporal
        assert adj.auto_detected is True

    def test_explicit_caller_graph_weight_preserved(self):
        adj = classify_query("what did we decide last week?", REF, caller_graph_weight=0.2)
        assert adj.graph_weight == 0.2

    def test_explicit_caller_since_preserved(self):
        adj = classify_query("what did we decide last week?", REF, caller_since="2025-01-01T00:00:00Z")
        assert adj.since == "2025-01-01T00:00:00Z"

    def test_recency_query_adjusts_weight(self):
        adj = classify_query("latest architecture decisions", REF)
        assert adj.recency_weight > 0.0
        assert adj.since is None
        assert adj.auto_detected is True

    def test_no_intent_returns_defaults(self):
        adj = classify_query("what database does the project use?", REF)
        assert adj.since is None
        assert adj.until is None
        assert adj.graph_weight is None
        assert adj.recency_weight is None
        assert adj.auto_detected is False


class TestSearchEndpointIntegration:
    """Test /search applies query intent classification."""

    @pytest.fixture
    def client(self):
        from unittest.mock import MagicMock, patch
        from fastapi.testclient import TestClient
        from app import app

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []
        mock_engine.search.return_value = []
        mock_engine.metadata = []

        with patch("app.memory", mock_engine), patch("app.API_KEY", "test-key"):
            tc = TestClient(app)
            yield tc, mock_engine

    def test_temporal_query_injects_since(self, client):
        tc, mock = client
        tc.post("/search", json={
            "query": "what did we decide last week?",
        }, headers={"X-API-Key": "test-key"})
        call_kwargs = mock.hybrid_search.call_args[1]
        assert call_kwargs["since"] is not None
        assert "2026" in call_kwargs["since"]

    def test_temporal_query_suppresses_graph(self, client):
        tc, mock = client
        tc.post("/search", json={
            "query": "what did we decide last week?",
        }, headers={"X-API-Key": "test-key"})
        call_kwargs = mock.hybrid_search.call_args[1]
        assert call_kwargs["graph_weight"] == 0.0

    def test_explicit_since_skips_classification(self, client):
        tc, mock = client
        tc.post("/search", json={
            "query": "what did we decide last week?",
            "since": "2025-01-01T00:00:00Z",
        }, headers={"X-API-Key": "test-key"})
        call_kwargs = mock.hybrid_search.call_args[1]
        assert call_kwargs["since"] == "2025-01-01T00:00:00Z"

    def test_auto_intent_false_skips_classification(self, client):
        tc, mock = client
        tc.post("/search", json={
            "query": "what did we decide last week?",
            "auto_intent": False,
        }, headers={"X-API-Key": "test-key"})
        call_kwargs = mock.hybrid_search.call_args[1]
        assert call_kwargs.get("since") is None

    def test_non_temporal_query_no_changes(self, client):
        tc, mock = client
        tc.post("/search", json={
            "query": "project architecture decisions",
        }, headers={"X-API-Key": "test-key"})
        call_kwargs = mock.hybrid_search.call_args[1]
        assert call_kwargs.get("since") is None
        assert call_kwargs.get("until") is None


class TestEndToEndClassification:
    """Full-path tests: query text → classify → adjusted params."""

    def test_last_week_produces_valid_iso_dates(self):
        """Classified dates must be parseable ISO 8601."""
        from datetime import date
        adj = classify_query("what happened last week?", REF)
        assert adj.auto_detected is True
        # since/until must be valid date strings
        since_d = date.fromisoformat(adj.since[:10])
        until_d = date.fromisoformat(adj.until[:10])
        assert since_d < until_d
        assert since_d < REF.date()
        assert until_d < REF.date()

    def test_recency_only_sets_weight_no_dates(self):
        adj = classify_query("show me the latest changes", REF)
        assert adj.auto_detected is True
        assert adj.recency_weight == 0.2
        assert adj.since is None
        assert adj.until is None
        assert adj.graph_weight is None  # recency doesn't suppress graph

    def test_multiple_temporal_signals_first_wins(self):
        """When query has multiple temporal hints, first matching pattern wins."""
        # "from July to October" (month range) should beat "last month"
        adj = classify_query("from July to October changes last month", REF)
        assert adj.auto_detected is True
        # Month range matched first — July to October, not last month
        assert "07" in adj.since
        assert "10" in adj.until

    def test_classify_with_no_reference_date_uses_now(self):
        """Default reference_date is current UTC time."""
        adj = classify_query("what happened yesterday")
        assert adj.auto_detected is True
        assert adj.since is not None
