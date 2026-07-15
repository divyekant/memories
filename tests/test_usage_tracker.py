"""Tests for usage_tracker module."""
import os
import sqlite3
import tempfile
import pytest
from usage_tracker import UsageTracker, NullTracker


class TestRetrievalTracking:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "usage.db")
        self.tracker = UsageTracker(self.db_path)

    def test_log_retrieval_stores_record(self):
        self.tracker.log_retrieval(memory_id=42, query="test query", source="test")
        stats = self.tracker.get_retrieval_stats(memory_ids=[42])
        assert stats[42]["count"] == 1

    def test_log_retrieval_multiple_increments(self):
        self.tracker.log_retrieval(memory_id=10, query="q1")
        self.tracker.log_retrieval(memory_id=10, query="q2")
        stats = self.tracker.get_retrieval_stats(memory_ids=[10])
        assert stats[10]["count"] == 2

    def test_get_retrieval_stats_returns_last_ts(self):
        self.tracker.log_retrieval(memory_id=5, query="q1", source="test")
        stats = self.tracker.get_retrieval_stats(memory_ids=[5])
        assert stats[5]["last_retrieved_at"] is not None

    def test_get_retrieval_stats_missing_ids_return_zero(self):
        stats = self.tracker.get_retrieval_stats(memory_ids=[99, 100])
        assert stats[99]["count"] == 0
        assert stats[99]["last_retrieved_at"] is None
        assert stats[100]["count"] == 0

    def test_get_unretrieved_memory_ids(self):
        self.tracker.log_retrieval(memory_id=1, query="q1")
        unretrieved = self.tracker.get_unretrieved_memory_ids(
            all_memory_ids=[1, 2, 3]
        )
        assert set(unretrieved) == {2, 3}

    def test_get_unretrieved_memory_ids_all_retrieved(self):
        self.tracker.log_retrieval(memory_id=1, query="q1")
        self.tracker.log_retrieval(memory_id=2, query="q2")
        unretrieved = self.tracker.get_unretrieved_memory_ids(
            all_memory_ids=[1, 2]
        )
        assert unretrieved == []

    def test_get_unretrieved_memory_ids_none_retrieved(self):
        unretrieved = self.tracker.get_unretrieved_memory_ids(
            all_memory_ids=[1, 2, 3]
        )
        assert set(unretrieved) == {1, 2, 3}

    def test_log_retrieval_truncates_long_query(self):
        long_query = "x" * 1000
        self.tracker.log_retrieval(memory_id=7, query=long_query, source="test")
        stats = self.tracker.get_retrieval_stats(memory_ids=[7])
        assert stats[7]["count"] == 1

    def test_null_tracker_noop(self):
        tracker = NullTracker()
        tracker.log_retrieval(memory_id=1, query="q")
        # Should not raise

    def test_null_tracker_get_retrieval_stats(self):
        tracker = NullTracker()
        assert tracker.get_retrieval_stats(memory_ids=[1, 2]) == {}

    def test_null_tracker_get_unretrieved_memory_ids(self):
        tracker = NullTracker()
        assert tracker.get_unretrieved_memory_ids(all_memory_ids=[1, 2, 3]) == []


class TestOperationAttribution:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "usage.db")
        self.tracker = UsageTracker(self.db_path)

    def test_usage_records_client_session_and_invocation(self):
        self.tracker.log_api_event(
            "search",
            source="hook:codex:memory-query",
            client="codex",
            session_id="session-a",
            invocation="memory-query",
        )

        usage = self.tracker.get_usage(period="all")

        search = usage["operations"]["search"]
        assert search["total"] == 1
        assert search["by_source"] == {"hook:codex:memory-query": 1}
        assert search["by_client"] == {"codex": 1}
        assert search["by_invocation"] == {"memory-query": 1}
        assert search["by_session"] == {"session-a": 1}

    def test_usage_can_filter_one_session(self):
        self.tracker.log_api_event("search", client="codex", session_id="session-a")
        self.tracker.log_api_event("search", client="codex", session_id="session-b")

        usage = self.tracker.get_usage(period="all", session_id="session-a")

        assert usage["session_id"] == "session-a"
        assert usage["operations"]["search"]["total"] == 1

    def test_existing_usage_database_migrates_attribution_columns(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE api_events")
        conn.execute(
            "CREATE TABLE api_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), "
            "operation TEXT NOT NULL, source TEXT DEFAULT '', count INTEGER DEFAULT 1)"
        )
        conn.commit()
        conn.close()

        tracker = UsageTracker(self.db_path)
        tracker.log_api_event(
            "search",
            client="codex",
            session_id="migrated-session",
            invocation="memory-query",
        )

        usage = tracker.get_usage(period="all", session_id="migrated-session")
        assert usage["operations"]["search"]["by_client"] == {"codex": 1}
