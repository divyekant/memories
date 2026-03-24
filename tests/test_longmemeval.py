"""Tests for LongMemEval benchmark adapter and MemoriesClient extensions."""

import pytest
from unittest.mock import MagicMock


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


def test_memories_client_has_add_batch_method():
    """MemoriesClient should support batch add for benchmark seeding."""
    from eval.memories_client import MemoriesClient
    client = MemoriesClient(url="http://localhost:8900", api_key="test")
    assert hasattr(client, "add_batch")
    assert callable(client.add_batch)


def test_longmemeval_runner_init():
    """LongMemEvalRunner should accept client and judge config."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    runner = LongMemEvalRunner(client=client, judge_provider="anthropic")
    assert runner is not None


def test_longmemeval_seed_question_uses_haystack_sessions_and_batch_add():
    """LongMemEval seeding should store haystack sessions directly, not via extract."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    client.add_batch.return_value = [1, 2]
    runner = LongMemEvalRunner(client=client)

    question = {
        "question_id": "123",
        "question_type": "single-session-user",
        "haystack_sessions": [
            [{"role": "user", "content": "hello"}],
            [{"role": "assistant", "content": "world"}],
        ],
    }
    seeded_count = runner.seed_question(question, source_prefix="eval/test", max_chars=100)

    client.clear_by_prefix.assert_called_once_with("eval/test/q123")
    client.add_batch.assert_called_once()
    memories = client.add_batch.call_args.args[0]
    assert seeded_count == 2
    assert [m["source"] for m in memories] == [
        "eval/test/q123/s0/c0",
        "eval/test/q123/s1/c0",
    ]
    assert memories[0]["metadata"]["question_id"] == "123"
    assert memories[0]["metadata"]["question_type"] == "single-session-user"
    assert client.add_batch.call_args.kwargs["deduplicate"] is False


def test_longmemeval_seed_question_chunks_large_sessions():
    """Large sessions should be split on turn boundaries to stay under the API size cap."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    client.add_batch.return_value = [1, 2]
    runner = LongMemEvalRunner(client=client)

    question = {
        "question_id": "chunky",
        "haystack_sessions": [[
            {"role": "user", "content": "a" * 70},
            {"role": "assistant", "content": "b" * 70},
        ]],
    }
    runner.seed_question(question, source_prefix="eval/test", max_chars=100)

    memories = client.add_batch.call_args.args[0]
    assert len(memories) == 2
    assert all(len(m["text"]) <= 100 for m in memories)
    assert [m["source"] for m in memories] == [
        "eval/test/qchunky/s0/c0",
        "eval/test/qchunky/s0/c1",
    ]


def test_longmemeval_clear_question_uses_question_scope():
    """Per-question cleanup should delete only that question's scoped memories."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    client.clear_by_prefix.return_value = 7
    runner = LongMemEvalRunner(client=client)

    deleted = runner.clear_question({"question_id": "abc"}, source_prefix="eval/test")

    assert deleted == 7
    client.clear_by_prefix.assert_called_once_with("eval/test/qabc")


def test_longmemeval_run_question_uses_question_scope_and_dataset_fields():
    """Question execution should search within the question prefix and preserve dataset field names."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    client.search.return_value = [
        {"text": "Business Administration"},
        {"text": "Supporting context"},
        {"text": "Irrelevant tail"},
    ]
    runner = LongMemEvalRunner(client=client)

    question = {
        "question_id": "e47becba",
        "question_type": "single-session-user",
        "question": "What major did I mention?",
        "answer": "Business Administration",
    }
    result = runner.run_question(question, k=3, source_prefix="eval/test")

    client.search.assert_called_once_with(
        query="What major did I mention?",
        k=3,
        hybrid=True,
        source_prefix="eval/test/qe47becba",
    )
    assert result["question_id"] == "e47becba"
    assert result["category"] == "single-session-user"
    assert result["expected"] == "Business Administration"
    assert result["context"] == "Business Administration\nSupporting context"


def test_longmemeval_run_question_stringifies_numeric_answers():
    """Numeric expected answers should be normalized to strings for reporting and judging."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    client.search.return_value = [{"text": "You own 3 bikes."}]
    runner = LongMemEvalRunner(client=client)

    result = runner.run_question(
        {
            "question_id": "numeric",
            "question": "How many bikes do I own?",
            "answer": 3,
        },
        source_prefix="eval/test",
    )

    assert result["expected"] == "3"


def test_longmemeval_parse_judge_response_handles_fenced_json():
    """Judge parsing should tolerate fenced JSON responses."""
    from eval.longmemeval import LongMemEvalRunner

    score, reasoning = LongMemEvalRunner._parse_judge_response(
        '```json\n{"score": 0.95, "reasoning": "answer found"}\n```'
    )

    assert score == 0.95
    assert reasoning == "answer found"


def test_longmemeval_parse_judge_response_handles_trailing_text():
    """Judge parsing should tolerate extra prose after the JSON object."""
    from eval.longmemeval import LongMemEvalRunner

    score, reasoning = LongMemEvalRunner._parse_judge_response(
        '{"score": 0.8, "reasoning": "mostly right"}\nAdditional commentary.'
    )

    assert score == 0.8
    assert reasoning == "mostly right"


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
