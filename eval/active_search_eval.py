"""Active memory-search behavior scoring.

This module scores the behavior gate that retrieval-only evals miss: whether an
agent chose to call memory_search when a normal user turn depended on prior
conversation or project context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MEMORY_SEARCH_TOOL_NAMES = {
    "memory_search",
    "mcp__memories__memory_search",
}


@dataclass(frozen=True)
class ActiveSearchCase:
    """A realistic user turn with the expected active-search behavior."""

    case_id: str
    user_prompt: str
    should_search: bool
    expected_source_prefixes: tuple[str, ...] = field(default_factory=tuple)
    expected_answer_terms: tuple[str, ...] = field(default_factory=tuple)
    seed_memories: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    agent: str = "unknown"


def _tool_calls(trace: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(trace, dict):
        return []
    calls = trace.get("tool_calls", [])
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, dict)]


def _is_memory_search_call(call: dict[str, Any]) -> bool:
    name = str(call.get("name", ""))
    return name in MEMORY_SEARCH_TOOL_NAMES or name.endswith("__memory_search")


def _source_matches(source_prefix: str, expected_prefixes: tuple[str, ...]) -> bool:
    if not source_prefix:
        return False
    for expected in expected_prefixes:
        if source_prefix == expected or source_prefix.startswith(f"{expected}/"):
            return True
    return False


def _answer_contains_terms(answer: str, expected_terms: tuple[str, ...]) -> bool:
    if not expected_terms:
        return False
    folded = answer.lower()
    return all(term.lower() in folded for term in expected_terms)


def score_turn(
    case: ActiveSearchCase,
    agent_response: str,
    agent_trace: dict[str, Any] | None,
) -> dict[str, Any]:
    """Score one user turn for active memory-search behavior."""

    calls = _tool_calls(agent_trace)
    search_calls = [call for call in calls if _is_memory_search_call(call)]
    searched_source_prefixes = [
        str(call.get("source_prefix", ""))
        for call in search_calls
        if str(call.get("source_prefix", ""))
    ]
    memory_search_called = bool(search_calls)

    if not case.expected_source_prefixes:
        source_prefix_score = 1.0 if memory_search_called else 0.0
    else:
        source_prefix_score = 1.0 if any(
            _source_matches(prefix, case.expected_source_prefixes)
            for prefix in searched_source_prefixes
        ) else 0.0

    answer_used_memory = _answer_contains_terms(agent_response or "", case.expected_answer_terms)
    passive_hook_only_failure = bool(case.should_search and not memory_search_called and answer_used_memory)

    issues: list[str] = []
    if case.should_search and not memory_search_called:
        issues.append("missing_memory_search")
    if case.should_search and memory_search_called and source_prefix_score == 0.0:
        issues.append("wrong_source_prefix")
    if case.should_search and memory_search_called and not answer_used_memory and case.expected_answer_terms:
        issues.append("answer_did_not_use_expected_memory")
    if passive_hook_only_failure:
        issues.append("passive_hook_only_failure")

    if case.should_search:
        if not memory_search_called:
            active_search_score = 0.0
        else:
            active_search_score = 0.5
            active_search_score += 0.25 * source_prefix_score
            active_search_score += 0.25 if answer_used_memory or not case.expected_answer_terms else 0.0
    else:
        active_search_score = 1.0
        if memory_search_called:
            issues.append("unnecessary_memory_search")

    return {
        "case_id": case.case_id,
        "agent": case.agent,
        "user_prompt": case.user_prompt,
        "should_search": case.should_search,
        "memory_search_called": memory_search_called,
        "searched_source_prefixes": searched_source_prefixes,
        "expected_source_prefixes": list(case.expected_source_prefixes),
        "source_prefix_score": source_prefix_score,
        "answer_used_memory": answer_used_memory,
        "passive_hook_only_failure": passive_hook_only_failure,
        "active_search_score": round(active_search_score, 4),
        "issues": issues,
        "tool_calls": [
            {
                "name": str(call.get("name", "")),
                **({"query": str(call.get("query", ""))} if "query" in call else {}),
                **({"source_prefix": str(call.get("source_prefix", ""))} if "source_prefix" in call else {}),
            }
            for call in search_calls
        ],
        "answer_excerpt": (agent_response or "")[:500],
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate active-search scores into an audit-friendly summary."""

    total = len(results)
    if not total:
        return {
            "cases": 0,
            "required_cases": 0,
            "active_search_rate": 0.0,
            "passive_hook_only_failures": 0,
            "wrong_source_prefix_failures": 0,
            "answer_use_rate": 0.0,
            "unnecessary_memory_searches": 0,
            "overall_active_search_score": 0.0,
        }

    required_results = [result for result in results if result.get("should_search", True)]
    required_total = len(required_results)
    active_calls = sum(1 for result in required_results if result.get("memory_search_called"))
    answer_used = sum(1 for result in results if result.get("answer_used_memory"))
    passive_failures = sum(1 for result in results if result.get("passive_hook_only_failure"))
    wrong_source_failures = sum(
        1 for result in results if "wrong_source_prefix" in result.get("issues", [])
    )
    unnecessary_searches = sum(
        1 for result in results
        if not result.get("should_search", True) and result.get("memory_search_called")
    )
    score = sum(float(result.get("active_search_score", 0.0)) for result in results) / total

    return {
        "cases": total,
        "required_cases": required_total,
        "active_search_rate": (active_calls / required_total) if required_total else 1.0,
        "passive_hook_only_failures": passive_failures,
        "wrong_source_prefix_failures": wrong_source_failures,
        "answer_use_rate": answer_used / total,
        "unnecessary_memory_searches": unnecessary_searches,
        "overall_active_search_score": round(score, 4),
    }
