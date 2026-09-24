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


def test_get_feedback_scores_without_ids_returns_all_feedback(tracker):
    """Search reads the small feedback table, not one parameter per memory."""
    tracker.log_search_feedback(memory_id=1, query="test", signal="useful")
    tracker.log_search_feedback(memory_id=7, query="test", signal="not_useful")

    assert tracker.get_feedback_scores() == {1: 1, 7: -1}


def test_search_endpoint_does_not_send_corpus_ids_to_feedback_lookup(monkeypatch):
    import importlib
    import os
    from unittest.mock import MagicMock, patch
    from fastapi.testclient import TestClient

    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        engine = MagicMock()
        engine.metadata = [{"id": i} for i in range(5000)]
        engine.hybrid_search.return_value = []
        app_module.memory = engine
        tracker = MagicMock()
        tracker.get_feedback_scores.return_value = {3: 1}
        app_module.usage_tracker = tracker

        client = TestClient(app_module.app)
        for path, body in [
            ("/search", {"query": "q", "hybrid": True, "feedback_weight": 0.1}),
            ("/search/evidence", {"query": "q", "hybrid": True, "feedback_weight": 0.1}),
        ]:
            tracker.get_feedback_scores.reset_mock()
            response = client.post(path, json=body, headers={"X-API-Key": "test-key"})
            assert response.status_code == 200, (path, response.text)
            tracker.get_feedback_scores.assert_called_once_with()
            assert engine.hybrid_search.call_args.kwargs["feedback_scores"] == {3: 1}


def test_hybrid_search_with_feedback_scores(tracker):
    """hybrid_search should accept feedback_weight and feedback_scores params."""
    # This test verifies the parameter exists — integration tested via API
    # For now, just verify the method signature accepts the params
    import inspect
    from memory_engine import MemoryEngine
    sig = inspect.signature(MemoryEngine.hybrid_search)
    param_names = list(sig.parameters.keys())
    assert "feedback_weight" in param_names
    assert "feedback_scores" in param_names
