import pytest
from usage_tracker import UsageTracker


@pytest.fixture
def tracker(tmp_path):
    return UsageTracker(str(tmp_path / "usage.db"))


def test_get_feedback_scores_returns_net_scores(tracker):
    """get_feedback_scores returns useful minus not_useful per memory."""
    tracker.log_search_feedback(memory_id=1, query="test", signal="useful")
    tracker.log_search_feedback(memory_id=1, query="test", signal="useful")
    tracker.log_search_feedback(memory_id=1, query="test", signal="not_useful")
    tracker.log_search_feedback(memory_id=2, query="test", signal="not_useful")

    scores = tracker.get_feedback_scores([1, 2, 3])
    assert scores[1] == 1   # 2 useful - 1 not_useful
    assert scores[2] == -1  # 0 useful - 1 not_useful
    assert 3 not in scores  # no feedback = not in dict


def test_get_feedback_scores_empty_ids(tracker):
    """Empty ID list returns empty dict."""
    assert tracker.get_feedback_scores([]) == {}
