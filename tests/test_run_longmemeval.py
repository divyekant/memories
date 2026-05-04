"""Tests for the LongMemEval command runner."""

from unittest.mock import MagicMock

from eval.run_longmemeval import _process_question
from eval.run_longmemeval import _setup_report_evidence


def test_setup_report_evidence_keeps_preflight_without_secrets():
    """Result artifacts should include setup proof while redacting credentials."""
    report = MagicMock()
    report.ok = True
    report.info = ["Eval Memories target: http://localhost:8901", "Eval API key: set"]
    report.warnings = []
    report.errors = []

    evidence = _setup_report_evidence(report)

    assert evidence == {
        "ok": True,
        "info": ["Eval Memories target: http://localhost:8901", "Eval API key: set"],
        "warnings": [],
        "errors": [],
    }
    assert "secret" not in str(evidence).lower()


def test_process_question_returns_auditable_system_detail():
    """Saved system-eval rows should include answer, trace, and recall evidence."""
    runner = MagicMock()
    runner.judge_provider = "anthropic"
    runner.judge_model = None
    runner.seed_question.return_value = 2
    runner.run_question_system.return_value = {
        "question_id": "q1",
        "category": "temporal-reasoning",
        "question": "What color did I choose?",
        "expected": "blue",
        "context": "The answer is blue.",
        "eval_mode": "system",
        "recall_any_at_5": 1.0,
        "recall_all_at_5": 1.0,
        "recall_top_sessions_at_5": [2, 0],
        "agent_trace": {
            "tool_calls": [
                {
                    "name": "mcp__memories__memory_search",
                    "source_prefix": "eval/longmemeval/q1",
                }
            ]
        },
    }
    runner._judge_single.return_value = (1.0, "correct")

    result = _process_question(
        0,
        1,
        {
            "question_id": "q1",
            "question_type": "temporal-reasoning",
            "question": "What color did I choose?",
            "answer": "blue",
        },
        runner,
        "system",
        "eval/longmemeval",
        cc_executor=object(),
    )

    assert result["qid"] == "q1"
    assert result["answer_chars"] == len("The answer is blue.")
    assert result["answer_excerpt"] == "The answer is blue."
    assert result["error_kind"] == ""
    assert result["agent_trace"]["tool_calls"][0]["name"] == "mcp__memories__memory_search"
    assert result["recall_top_sessions_at_5"] == [2, 0]


def test_process_question_classifies_agent_timeout_without_judging():
    """Agent infrastructure failures should be explicit, not hidden as bad answers."""
    runner = MagicMock()
    runner.judge_provider = "anthropic"
    runner.judge_model = None
    runner.seed_question.return_value = 1
    runner.run_question_system.return_value = {
        "question_id": "q-timeout",
        "category": "temporal-reasoning",
        "question": "When did I attend?",
        "expected": "May 1",
        "context": "[TIMEOUT] Claude Code timed out after 120s",
        "eval_mode": "system",
        "recall_any_at_5": 1.0,
        "recall_all_at_5": 1.0,
    }

    result = _process_question(
        0,
        1,
        {
            "question_id": "q-timeout",
            "question_type": "temporal-reasoning",
            "question": "When did I attend?",
            "answer": "May 1",
        },
        runner,
        "system",
        "eval/longmemeval",
        cc_executor=object(),
    )

    assert result["error_kind"] == "timeout"
    assert result["score"] == 0.0
    assert "Agent error: timeout" in result["reasoning"]
    runner._judge_single.assert_not_called()


def test_process_question_retries_transient_agent_auth_error():
    """System eval should retry one transient agent infra failure before scoring."""
    runner = MagicMock()
    runner.judge_provider = "anthropic"
    runner.judge_model = None
    runner.seed_question.return_value = 1
    runner.run_question_system.side_effect = [
        {
            "question_id": "q-auth",
            "category": "temporal-reasoning",
            "question": "When did I attend?",
            "expected": "May 1",
            "context": "Invalid API key · Fix external API key",
            "eval_mode": "system",
            "recall_any_at_5": 1.0,
            "recall_all_at_5": 1.0,
        },
        {
            "question_id": "q-auth",
            "category": "temporal-reasoning",
            "question": "When did I attend?",
            "expected": "May 1",
            "context": "You attended on May 1.",
            "eval_mode": "system",
            "recall_any_at_5": 1.0,
            "recall_all_at_5": 1.0,
        },
    ]
    runner._judge_single.return_value = (0.95, "correct")

    result = _process_question(
        0,
        1,
        {
            "question_id": "q-auth",
            "question_type": "temporal-reasoning",
            "question": "When did I attend?",
            "answer": "May 1",
        },
        runner,
        "system",
        "eval/longmemeval",
        cc_executor=object(),
    )

    assert runner.run_question_system.call_count == 2
    assert result["score"] == 0.95
    assert result["error_kind"] == ""
    assert result["answer_excerpt"] == "You attended on May 1."
