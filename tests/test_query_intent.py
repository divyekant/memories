"""Tests for temporal query intent detection"""

from datetime import datetime, timezone

import pytest

from query_intent import TemporalIntent, detect_temporal_intent

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
