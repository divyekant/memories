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

    # run_question bumps k to max(k, 50) for session dedup headroom
    client.search.assert_called_once_with(
        query="What major did I mention?",
        k=50,
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


# -- System eval mode tests ------------------------------------------------


def test_run_question_system_calls_cc_executor():
    """run_question_system should create project, run prompt, and cleanup."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    client.search.return_value = []
    runner = LongMemEvalRunner(client=client)

    cc_executor = MagicMock()
    cc_executor.create_isolated_project.return_value = "/tmp/cc_eval_test"
    cc_executor.run_prompt.return_value = "Business Administration"

    question = {
        "question_id": "sys1",
        "question_type": "single-session-user",
        "question": "What degree did I get?",
        "answer": "Business Administration",
    }
    result = runner.run_question_system(question, cc_executor=cc_executor, source_prefix="eval/test")

    cc_executor.create_isolated_project.assert_called_once_with(with_memories=True)
    cc_executor.run_prompt.assert_called_once()
    cc_executor.cleanup_project.assert_called_once_with("/tmp/cc_eval_test")
    assert result["eval_mode"] == "system"
    assert result["context"] == "Business Administration"
    assert result["question_id"] == "sys1"


def test_run_question_system_computes_diagnostic_recall():
    """System eval should report raw retrieval R@5 alongside agent answer score."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    client.search.return_value = [
        {"text": "wrong session", "source": "eval/test/qsys1/s0/c0"},
        {"text": "answer session", "source": "eval/test/qsys1/s1/c0"},
    ]
    runner = LongMemEvalRunner(client=client)

    cc_executor = MagicMock()
    cc_executor.create_isolated_project.return_value = "/tmp/cc_eval_test"
    cc_executor.run_prompt.return_value = "Business Administration"

    question = {
        "question_id": "sys1",
        "question_type": "single-session-user",
        "question": "What degree did I get?",
        "answer": "Business Administration",
        "haystack_session_ids": ["session-a", "session-b"],
        "answer_session_ids": ["session-b"],
    }
    result = runner.run_question_system(question, cc_executor=cc_executor, source_prefix="eval/test")

    client.search.assert_called_once_with(
        query="What degree did I get?",
        k=50,
        hybrid=True,
        source_prefix="eval/test/qsys1",
    )
    assert result["context"] == "Business Administration"
    assert result["search_results"] == client.search.return_value
    assert result["recall_any_at_5"] == 1.0
    assert result["recall_all_at_5"] == 1.0


def test_run_question_system_cleanup_on_error():
    """CCExecutor project should be cleaned up even if run_prompt raises."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    client.search.return_value = []
    runner = LongMemEvalRunner(client=client)

    cc_executor = MagicMock()
    cc_executor.create_isolated_project.return_value = "/tmp/cc_eval_err"
    cc_executor.run_prompt.side_effect = RuntimeError("timeout")

    question = {"question_id": "err1", "question": "test", "answer": "test"}
    with pytest.raises(RuntimeError):
        runner.run_question_system(question, cc_executor=cc_executor)

    cc_executor.cleanup_project.assert_called_once_with("/tmp/cc_eval_err")


def test_judge_uses_system_rubric_for_system_mode():
    """_judge_single should use system-mode rubric when eval_mode=system."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    runner = LongMemEvalRunner(client=client)
    runner._judge = MagicMock()
    runner._judge.complete.return_value = MagicMock(
        text='{"score": 0.95, "reasoning": "correct answer"}'
    )

    result = {
        "question": "What color is the sky?",
        "expected": "blue",
        "context": "The sky is blue.",
        "eval_mode": "system",
    }
    score, reasoning = runner._judge_single(result)

    assert score == 0.95
    # Verify system prompt was used (checks "assistant correctly answered")
    call_args = runner._judge.complete.call_args
    assert "assistant correctly answered" in call_args.kwargs.get("system", call_args.args[0] if call_args.args else "")


def test_judge_uses_tool_rubric_for_tool_mode():
    """_judge_single should use tool-mode rubric when eval_mode=tool."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    runner = LongMemEvalRunner(client=client)
    runner._judge = MagicMock()
    runner._judge.complete.return_value = MagicMock(
        text='{"score": 0.8, "reasoning": "context contains answer"}'
    )

    result = {
        "question": "What color is the sky?",
        "expected": "blue",
        "context": "The sky is blue.",
        "eval_mode": "tool",
    }
    score, reasoning = runner._judge_single(result)

    assert score == 0.8
    call_args = runner._judge.complete.call_args
    assert "retrieved memory context" in call_args.kwargs.get("system", call_args.args[0] if call_args.args else "")


def test_judge_defaults_to_tool_when_eval_mode_missing():
    """_judge_single should default to tool mode when eval_mode is not set."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    runner = LongMemEvalRunner(client=client)
    runner._judge = MagicMock()
    runner._judge.complete.return_value = MagicMock(
        text='{"score": 0.5, "reasoning": "partial"}'
    )

    result = {
        "question": "test",
        "expected": "answer",
        "context": "some context",
        # no eval_mode key
    }
    score, _ = runner._judge_single(result)

    assert score == 0.5
    call_args = runner._judge.complete.call_args
    assert "retrieved memory context" in call_args.kwargs.get("system", call_args.args[0] if call_args.args else "")


def test_run_question_includes_eval_mode_tool():
    """run_question should explicitly set eval_mode=tool in its return dict."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    client.search.return_value = [{"text": "test"}]
    runner = LongMemEvalRunner(client=client)

    result = runner.run_question(
        {"question_id": "mode1", "question": "test", "answer": "test"},
        source_prefix="eval/test",
    )
    assert result["eval_mode"] == "tool"


def test_result_from_json_handles_extra_keys():
    """LongMemEvalResult.from_json should ignore unknown keys without crashing."""
    import json
    import tempfile
    from eval.longmemeval import LongMemEvalResult

    data = {
        "version": "4.0.0",
        "eval_mode": "system",
        "overall": 0.633,
        "categories": {},
        "unknown_future_key": True,
        "another_extra": [1, 2, 3],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name

    result = LongMemEvalResult.from_json(path)
    assert result.version == "4.0.0"
    assert result.eval_mode == "system"
    assert result.overall == 0.633


def test_report_includes_eval_mode():
    """report() should include eval_mode in the result."""
    from eval.longmemeval import LongMemEvalRunner

    client = MagicMock()
    runner = LongMemEvalRunner(client=client)

    scored = [{"question_id": "1", "category": "test", "score": 0.9}]
    report = runner.report(scored, version="4.0.0", eval_mode="system")
    assert report.eval_mode == "system"

    report_tool = runner.report(scored, version="4.0.0", eval_mode="tool")
    assert report_tool.eval_mode == "tool"


def test_report_warns_on_mode_mismatch(tmp_path, caplog):
    """report() should warn when comparing results across different eval modes."""
    import logging
    from eval.longmemeval import LongMemEvalRunner, LongMemEvalResult

    prev = LongMemEvalResult(version="4.0.0", eval_mode="tool", overall=0.32)
    prev_path = str(tmp_path / "prev.json")
    (tmp_path / "prev.json").write_text(prev.to_json())

    client = MagicMock()
    runner = LongMemEvalRunner(client=client)

    scored = [{"question_id": "1", "category": "test", "score": 0.6}]

    with caplog.at_level(logging.WARNING, logger="eval.longmemeval"):
        report = runner.report(scored, version="4.0.0", previous=prev_path, eval_mode="system")

    assert report.delta.get("vs_eval_mode") == "tool"
    assert any("meaningless" in r.message for r in caplog.records)


# -- MCP backend pinning test ----------------------------------------------


def test_cc_executor_sets_backends_file_env():
    """CCExecutor should set MEMORIES_BACKENDS_FILE to force single-backend mode."""
    import json
    from eval.cc_executor import CCExecutor

    executor = CCExecutor(
        memories_url="http://localhost:8901",
        memories_api_key="test-key",
        mcp_server_path="/path/to/index.js",
    )
    project_dir = executor.create_isolated_project(with_memories=True)

    try:
        import os
        mcp_path = os.path.join(project_dir, ".mcp.json")
        assert os.path.exists(mcp_path)

        with open(mcp_path) as f:
            config = json.load(f)

        env = config["mcpServers"]["memories"]["env"]
        assert env["MEMORIES_URL"] == "http://localhost:8901"
        assert env["MEMORIES_API_KEY"] == "test-key"
        assert "MEMORIES_BACKENDS_FILE" in env
        # The key point: MEMORIES_BACKENDS_FILE is set, which tells the MCP
        # server to skip project/global config resolution
        assert env["MEMORIES_BACKENDS_FILE"] != ""
    finally:
        executor.cleanup_project(project_dir)
