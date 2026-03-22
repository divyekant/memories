import pytest
from usage_tracker import UsageTracker

@pytest.fixture
def tracker(tmp_path):
    return UsageTracker(str(tmp_path / "usage.db"))

def test_get_feedback_history(tracker):
    tracker.log_search_feedback(memory_id=1, query="q1", signal="useful")
    tracker.log_search_feedback(memory_id=1, query="q2", signal="not_useful")
    tracker.log_search_feedback(memory_id=2, query="q3", signal="useful")
    history = tracker.get_feedback_history(memory_id=1)
    assert len(history) == 2
    assert all(h["memory_id"] == 1 for h in history)

def test_delete_feedback(tracker):
    tracker.log_search_feedback(memory_id=1, query="q1", signal="useful")
    history = tracker.get_feedback_history(memory_id=1)
    assert len(history) == 1
    deleted = tracker.delete_feedback(history[0]["id"])
    assert deleted is True
    assert len(tracker.get_feedback_history(memory_id=1)) == 0

def test_delete_feedback_nonexistent(tracker):
    assert tracker.delete_feedback(9999) is False
