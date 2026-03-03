"""Tests for LLM-as-judge evaluator."""

import json
from unittest.mock import MagicMock

import pytest

from eval.judge import JUDGE_SYSTEM_PROMPT, LLMJudge
from eval.models import Rubric, RubricResult


def _make_provider(response_text: str) -> MagicMock:
    """Create a mock provider whose .complete() returns an object with .text."""
    provider = MagicMock()
    completion = MagicMock()
    completion.text = response_text
    provider.complete.return_value = completion
    return provider


class TestLLMJudge:
    def test_judge_returns_score(self):
        """Mock returns valid JSON, verify score extracted."""
        response = json.dumps({"score": 0.85, "reasoning": "Output addresses the root cause."})
        provider = _make_provider(response)
        judge = LLMJudge(provider)

        rubric = Rubric(type="correct_fix", description="Fix addresses root cause", weight=0.3)
        result = judge.evaluate(rubric, prompt="Fix the bug", output="Added null check for auth middleware")

        assert isinstance(result, RubricResult)
        assert result.score == pytest.approx(0.85)
        assert result.rubric_type == "correct_fix"
        assert result.weight == 0.3
        assert "root cause" in result.reasoning

    def test_judge_handles_unstructured_response(self):
        """Mock returns 'Score: 0.6. Blah', verify number extracted via regex fallback."""
        provider = _make_provider("Score: 0.6. The output partially addresses the issue.")
        judge = LLMJudge(provider)

        rubric = Rubric(type="recall_accuracy", description="Recalls decision", weight=1.0)
        result = judge.evaluate(rubric, prompt="Why SQLite?", output="We chose SQLite for simplicity.")

        assert result.score == pytest.approx(0.6)
        assert result.rubric_type == "recall_accuracy"
        assert result.weight == 1.0

    def test_judge_returns_0_on_parse_failure(self):
        """Mock returns gibberish, verify score=0.0."""
        provider = _make_provider("I cannot evaluate this %%%^^^")
        judge = LLMJudge(provider)

        rubric = Rubric(type="match_convention", description="Follows naming convention", weight=0.5)
        result = judge.evaluate(rubric, prompt="Check naming", output="some output")

        assert result.score == 0.0
        assert "Could not parse" in result.reasoning

    def test_judge_called_with_correct_prompt(self):
        """Verify system mentions scoring range, user contains prompt+output."""
        response = json.dumps({"score": 0.5, "reasoning": "Average."})
        provider = _make_provider(response)
        judge = LLMJudge(provider)

        rubric = Rubric(type="correct_fix", description="Fix is correct", weight=0.4)
        judge.evaluate(rubric, prompt="Fix the TypeError", output="Added try/except block")

        provider.complete.assert_called_once()
        call_kwargs = provider.complete.call_args
        system_arg = call_kwargs.kwargs.get("system") or call_kwargs.args[0]
        user_arg = call_kwargs.kwargs.get("user") or call_kwargs.args[1]

        # System prompt should mention the 0-1 scoring range
        assert "0" in system_arg and "1" in system_arg
        assert "score" in system_arg.lower()

        # User message should contain the prompt and output being evaluated
        assert "Fix the TypeError" in user_arg
        assert "Added try/except block" in user_arg

        # User message should contain the rubric description
        assert "Fix is correct" in user_arg
