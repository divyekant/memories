"""Deterministic rubric scorer for the Memories efficacy eval harness."""

from __future__ import annotations

from eval.models import Rubric, RubricResult

LLM_JUDGE_TYPES = {"correct_fix", "recall_accuracy", "match_convention"}


def score_rubric(rubric: Rubric, output: str) -> RubricResult:
    """Score a single rubric against model output.

    Deterministic rubric types are scored immediately.
    LLM judge types return a sentinel (score=-1.0, reasoning="pending_llm_judge").
    """
    rtype = rubric.type.value if hasattr(rubric.type, "value") else rubric.type

    if rtype in LLM_JUDGE_TYPES:
        return RubricResult(
            rubric_type=rtype,
            score=-1.0,
            weight=rubric.weight,
            reasoning="pending_llm_judge",
        )

    if rtype == "contains":
        hit = rubric.value.lower() in output.lower() if rubric.value else False
        return RubricResult(
            rubric_type=rtype,
            score=1.0 if hit else 0.0,
            weight=rubric.weight,
            reasoning=f"value {'found' if hit else 'not found'} in output",
        )

    if rtype == "not_contains":
        hit = rubric.value.lower() in output.lower() if rubric.value else False
        return RubricResult(
            rubric_type=rtype,
            score=0.0 if hit else 1.0,
            weight=rubric.weight,
            reasoning=f"value {'found' if hit else 'not found'} in output",
        )

    if rtype == "no_retry":
        has_question = "?" in output
        return RubricResult(
            rubric_type=rtype,
            score=0.0 if has_question else 1.0,
            weight=rubric.weight,
            reasoning="question mark found" if has_question else "no question mark",
        )

    raise ValueError(f"Unknown rubric type: {rtype}")


def score_all_rubrics(
    rubrics: list[Rubric], output: str
) -> tuple[float, list[RubricResult]]:
    """Score all rubrics and return (weighted_average, details).

    The weighted average is computed only over deterministic rubrics (score >= 0).
    LLM judge rubrics (score == -1) are excluded from the average but included
    in the details list.
    """
    if not rubrics:
        return 0.0, []

    details = [score_rubric(r, output) for r in rubrics]

    scored = [d for d in details if d.score >= 0]
    if not scored:
        return 0.0, details

    total_weight = sum(d.weight for d in scored)
    if total_weight == 0:
        return 0.0, details

    weighted_sum = sum(d.score * d.weight for d in scored)
    return weighted_sum / total_weight, details
