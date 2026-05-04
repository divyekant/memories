"""Tests for active memory-search behavior scoring."""

from eval.active_search_eval import ActiveSearchCase, score_turn, summarize_results


def test_scores_required_memory_search_with_good_source_and_answer_use():
    case = ActiveSearchCase(
        case_id="continue-fplguru",
        user_prompt="continue where we left off on the PR reviews",
        should_search=True,
        expected_source_prefixes=(
            "codex/fplguru",
            "claude-code/fplguru",
            "learning/fplguru",
            "wip/fplguru",
        ),
        expected_answer_terms=("PR #107", "review"),
    )
    trace = {
        "tool_calls": [
            {
                "name": "mcp__memories__memory_search",
                "query": "fplguru PR review previous context",
                "source_prefix": "learning/fplguru",
            }
        ]
    }

    result = score_turn(case, "We were reviewing PR #107 and the review notes still apply.", trace)

    assert result["memory_search_called"] is True
    assert result["searched_source_prefixes"] == ["learning/fplguru"]
    assert result["source_prefix_score"] == 1.0
    assert result["answer_used_memory"] is True
    assert result["passive_hook_only_failure"] is False
    assert result["active_search_score"] == 1.0


def test_detects_passive_hook_only_failure_even_when_answer_is_right():
    case = ActiveSearchCase(
        case_id="where-left-off",
        user_prompt="where did we last leave this?",
        should_search=True,
        expected_source_prefixes=("codex/memories", "claude-code/memories", "wip/memories"),
        expected_answer_terms=("temporal eval",),
    )

    result = score_turn(case, "We last left off on the temporal eval.", {"tool_calls": []})

    assert result["memory_search_called"] is False
    assert result["answer_used_memory"] is True
    assert result["passive_hook_only_failure"] is True
    assert result["active_search_score"] == 0.0
    assert "missing_memory_search" in result["issues"]


def test_detects_passive_hook_only_failure_when_any_expected_term_leaks():
    case = ActiveSearchCase(
        case_id="temporal-paraphrase",
        user_prompt="did we solve the temporal issue?",
        should_search=True,
        expected_source_prefixes=("codex/memories",),
        expected_answer_terms=("memory_timeline", "user_facts_only"),
    )

    result = score_turn(case, "Use memory_timeline with user fact filtering.", {"tool_calls": []})

    assert result["memory_search_called"] is False
    assert result["passive_hook_only_failure"] is True
    assert result["answer_used_memory"] is False
    assert "passive_hook_only_failure" in result["issues"]


def test_memory_get_does_not_satisfy_active_search_gate():
    case = ActiveSearchCase(
        case_id="memory-get-bypass",
        user_prompt="where did we leave this?",
        should_search=True,
        expected_source_prefixes=("codex/memories",),
        expected_answer_terms=("release gate",),
    )
    trace = {
        "tool_calls": [
            {
                "name": "mcp__memories__memory_get",
                "memory_id": "42",
            }
        ]
    }

    result = score_turn(case, "The release gate was setup validation.", trace)

    assert result["memory_search_called"] is False
    assert result["tool_calls"] == []
    assert "missing_memory_search" in result["issues"]


def test_empty_expected_terms_count_as_answer_not_required():
    case = ActiveSearchCase(
        case_id="no-answer-terms",
        user_prompt="what was the project decision?",
        should_search=True,
        expected_source_prefixes=("codex/memories",),
    )
    trace = {
        "tool_calls": [
            {
                "name": "mcp__memories__memory_search",
                "source_prefix": "codex/memories",
            }
        ]
    }

    result = score_turn(case, "Found the relevant decision.", trace)

    assert result["answer_used_memory"] is True
    assert result["active_search_score"] == 1.0


def test_wrong_source_prefix_is_scored_separately_from_search_call():
    case = ActiveSearchCase(
        case_id="cross-client-miss",
        user_prompt="what did Claude Code decide here?",
        should_search=True,
        expected_source_prefixes=("claude-code/fplReco", "learning/fplReco", "wip/fplReco"),
        expected_answer_terms=("data pipeline",),
    )
    trace = {
        "tool_calls": [
            {
                "name": "memory_search",
                "query": "fplReco decision",
                "source_prefix": "codex/fplReco",
            }
        ]
    }

    result = score_turn(case, "The data pipeline was the key decision.", trace)

    assert result["memory_search_called"] is True
    assert result["source_prefix_score"] == 0.0
    assert result["answer_used_memory"] is True
    assert result["active_search_score"] == 0.75
    assert "wrong_source_prefix" in result["issues"]


def test_unnecessary_memory_search_penalizes_control_case_score():
    case = ActiveSearchCase(
        case_id="trivial-control",
        user_prompt="What is 2 plus 2?",
        should_search=False,
        expected_answer_terms=("4",),
    )
    trace = {
        "tool_calls": [
            {
                "name": "mcp__memories__memory_search",
                "query": "project context",
                "source_prefix": "codex/project",
            }
        ]
    }

    result = score_turn(case, "4", trace)

    assert result["active_search_score"] == 0.0
    assert "unnecessary_memory_search" in result["issues"]


def test_summarize_results_reports_passive_hook_and_source_failures():
    results = [
        {
            "active_search_score": 1.0,
            "memory_search_called": True,
            "source_prefix_score": 1.0,
            "answer_used_memory": True,
            "passive_hook_only_failure": False,
            "issues": [],
        },
        {
            "active_search_score": 0.0,
            "memory_search_called": False,
            "source_prefix_score": 0.0,
            "answer_used_memory": True,
            "passive_hook_only_failure": True,
            "issues": ["missing_memory_search"],
        },
        {
            "active_search_score": 0.75,
            "memory_search_called": True,
            "source_prefix_score": 0.0,
            "answer_used_memory": True,
            "passive_hook_only_failure": False,
            "issues": ["wrong_source_prefix"],
        },
    ]

    summary = summarize_results(results)

    assert summary["cases"] == 3
    assert summary["active_search_rate"] == 2 / 3
    assert summary["passive_hook_only_failures"] == 1
    assert summary["wrong_source_prefix_failures"] == 1
    assert summary["overall_active_search_score"] == round((1.0 + 0.0 + 0.75) / 3, 4)
