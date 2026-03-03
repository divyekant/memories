"""LLM-as-judge evaluator for non-deterministic rubrics."""

from __future__ import annotations

import json
import re
from typing import Any

from eval.models import Rubric, RubricResult

JUDGE_SYSTEM_PROMPT = (
    "You are an evaluation judge. Given a rubric, a prompt, and an output, "
    "score the output on a scale from 0 to 1 where 0 means completely wrong "
    "and 1 means perfect.\n\n"
    "Return your evaluation as JSON with exactly two keys:\n"
    '  {"score": <float between 0 and 1>, "reasoning": "<brief explanation>"}\n\n'
    "Return ONLY the JSON object, no other text."
)


class LLMJudge:
    """Evaluate rubrics by delegating to an LLM provider."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def evaluate(self, rubric: Rubric, prompt: str, output: str) -> RubricResult:
        user_message = (
            f"## Rubric\n"
            f"Type: {rubric.type}\n"
            f"Description: {rubric.description or rubric.value or 'N/A'}\n\n"
            f"## Prompt\n{prompt}\n\n"
            f"## Output\n{output}"
        )

        completion = self._provider.complete(system=JUDGE_SYSTEM_PROMPT, user=user_message)
        score, reasoning = _parse_response(completion.text)

        return RubricResult(
            rubric_type=rubric.type,
            score=score,
            weight=rubric.weight,
            reasoning=reasoning,
        )


def _parse_response(text: str) -> tuple[float, str]:
    """Parse LLM response into (score, reasoning).

    Try JSON parse first, then regex fallback for a decimal number.
    If all fails, return (0.0, "Could not parse...").
    """
    # Try JSON parse first
    try:
        data = json.loads(text)
        return (float(data["score"]), str(data.get("reasoning", "")))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass

    # Fallback: regex to find a decimal number (0-1 range)
    match = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)
    if match:
        score = float(match.group(1))
        return (score, text)

    return (0.0, "Could not parse LLM response.")
