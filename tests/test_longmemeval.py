"""Tests for LongMemEval benchmark adapter and MemoriesClient extensions."""

import pytest
from unittest.mock import patch, MagicMock
import json


def test_memories_client_has_search_method():
    """MemoriesClient should have a search() method."""
    from eval.memories_client import MemoriesClient
    client = MemoriesClient(url="http://localhost:8900", api_key="test")
    assert hasattr(client, "search")
    assert callable(client.search)


def test_memories_client_has_extract_method():
    """MemoriesClient should have an extract() method."""
    from eval.memories_client import MemoriesClient
    client = MemoriesClient(url="http://localhost:8900", api_key="test")
    assert hasattr(client, "extract")
    assert callable(client.extract)


def test_longmemeval_runner_init():
    """LongMemEvalRunner should accept client and judge config."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    runner = LongMemEvalRunner(client=client, judge_provider="anthropic")
    assert runner is not None


def test_longmemeval_seed_memories_scoped_per_session():
    """seed_memories should create per-session source prefixes and build _session_map."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    client.extract.return_value = {"status": "completed"}
    runner = LongMemEvalRunner(client=client)

    conversations = [
        {"id": "conv_a", "turns": [{"role": "user", "content": "hello"}]},
        {"id": "conv_b", "turns": [{"role": "user", "content": "world"}]},
    ]
    runner.seed_memories(conversations, source_prefix="eval/test")

    # Should clear the prefix once
    client.clear_by_prefix.assert_called_once_with("eval/test")
    # Should extract once per conversation with session-scoped source
    assert client.extract.call_count == 2
    sources = [call.kwargs.get("source", call.args[0] if call.args else None)
               for call in client.extract.call_args_list]
    # extract is called with keyword args
    actual_sources = [call[1]["source"] for call in client.extract.call_args_list]
    assert actual_sources == ["eval/test/session_0", "eval/test/session_1"]
    # session_map should map conversation ids to session sources
    assert runner._session_map == {
        "conv_a": "eval/test/session_0",
        "conv_b": "eval/test/session_1",
    }


def test_longmemeval_run_questions_uses_session_scope():
    """run_questions should search within the correct session prefix."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    client.search.return_value = [{"text": "found"}]
    runner = LongMemEvalRunner(client=client)
    runner._session_map = {"conv_a": "eval/test/session_0"}

    questions = [
        {"id": "q1", "conversation_id": "conv_a", "question": "what?", "answer": "yes", "category": "info"},
    ]
    results = runner.run_questions(questions, k=3, source_prefix="eval/test")

    # search should be called with the session-scoped source_prefix
    client.search.assert_called_once_with(
        query="what?", k=3, hybrid=True, source_prefix="eval/test/session_0",
    )
    assert results[0]["question"] == "what?"


def test_longmemeval_run_questions_fallback_to_full_prefix():
    """run_questions falls back to full source_prefix when no session mapping exists."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    client.search.return_value = []
    runner = LongMemEvalRunner(client=client)
    runner._session_map = {}

    questions = [
        {"id": "q1", "question": "unknown session?", "answer": "n/a", "category": "info"},
    ]
    results = runner.run_questions(questions, k=5, source_prefix="eval/test")

    client.search.assert_called_once_with(
        query="unknown session?", k=5, hybrid=True, source_prefix="eval/test",
    )


def test_longmemeval_report_format():
    """Report should include overall score, categories, and delta."""
    from eval.longmemeval import LongMemEvalResult
    result = LongMemEvalResult(
        version="3.2.2",
        overall=0.724,
        categories={"information_extraction": 0.78},
    )
    assert result.overall == 0.724
    assert "information_extraction" in result.categories
