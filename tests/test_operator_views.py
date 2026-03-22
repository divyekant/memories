import pytest
from usage_tracker import UsageTracker


@pytest.fixture
def tracker(tmp_path):
    return UsageTracker(str(tmp_path / "usage.db"))


def test_get_problem_queries(tracker):
    for _ in range(3):
        tracker.log_search_feedback(memory_id=1, query="bad query", signal="not_useful")
    tracker.log_search_feedback(memory_id=2, query="ok query", signal="not_useful")
    tracker.log_search_feedback(memory_id=2, query="ok query", signal="useful")
    tracker.log_search_feedback(memory_id=2, query="ok query", signal="useful")

    problems = tracker.get_problem_queries(min_feedback=2, min_negative_ratio=0.5)
    assert len(problems) == 1
    assert problems[0]["query"] == "bad query"
    assert problems[0]["not_useful"] == 3


def test_get_problem_queries_threshold(tracker):
    """Queries below min_feedback threshold are excluded."""
    tracker.log_search_feedback(memory_id=1, query="rare", signal="not_useful")
    problems = tracker.get_problem_queries(min_feedback=2)
    assert len(problems) == 0


def test_get_stale_memories(tracker):
    for _ in range(5):
        tracker.log_retrieval(memory_id=1, query="q", source="test")
    tracker.log_search_feedback(memory_id=1, query="q", signal="not_useful")
    for _ in range(3):
        tracker.log_retrieval(memory_id=2, query="q", source="test")
    tracker.log_search_feedback(memory_id=2, query="q", signal="useful")

    stale = tracker.get_stale_memories(min_retrievals=3)
    assert len(stale) == 1
    assert stale[0]["memory_id"] == 1
    assert stale[0]["retrievals"] == 5


def test_get_stale_memories_below_threshold(tracker):
    tracker.log_retrieval(memory_id=1, query="q", source="test")
    stale = tracker.get_stale_memories(min_retrievals=3)
    assert len(stale) == 0


def test_get_stale_memories_unreviewed_not_stale(tracker):
    """Memories with many retrievals but zero feedback are unreviewed, not stale."""
    for _ in range(10):
        tracker.log_retrieval(memory_id=1, query="q", source="test")
    # No feedback at all — should NOT appear as stale
    stale = tracker.get_stale_memories(min_retrievals=3)
    assert len(stale) == 0


def test_get_problem_queries_min_feedback_total(tracker):
    """min_feedback applies to total feedback count, not just negative count."""
    tracker.log_search_feedback(memory_id=1, query="mixed", signal="not_useful")
    tracker.log_search_feedback(memory_id=1, query="mixed", signal="useful")
    # total=2 >= min_feedback=2, negative ratio = 0.5 >= 0.5 — should appear
    problems = tracker.get_problem_queries(min_feedback=2, min_negative_ratio=0.5)
    assert len(problems) == 1
    assert problems[0]["query"] == "mixed"
