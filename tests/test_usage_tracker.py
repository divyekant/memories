"""Tests for usage_tracker module."""
import os
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
