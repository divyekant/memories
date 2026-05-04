"""Tests for the active-search behavior eval runner."""

from pathlib import Path
from unittest.mock import MagicMock

from eval.active_search_eval import ActiveSearchCase
from eval.run_active_search_eval import install_claude_product_read_hooks, load_cases, materialize_case, run_case


def test_load_cases_parses_seed_memories_and_expected_prefixes(tmp_path: Path):
    path = tmp_path / "cases.json"
    path.write_text(
        """
        [
          {
            "case_id": "resume-review",
            "agent": "claude-code",
            "user_prompt": "where did we leave the review?",
            "should_search": true,
            "expected_source_prefixes": ["claude-code/{project}", "learning/{project}"],
            "expected_answer_terms": ["PR #107"],
            "seed_memories": [
              {
                "source": "claude-code/{project}",
                "text": "We left off reviewing PR #107."
              }
            ]
          }
        ]
        """,
        encoding="utf-8",
    )

    cases = load_cases(path)

    assert cases == [
        ActiveSearchCase(
            case_id="resume-review",
            agent="claude-code",
            user_prompt="where did we leave the review?",
            should_search=True,
            expected_source_prefixes=("claude-code/{project}", "learning/{project}"),
            expected_answer_terms=("PR #107",),
            seed_memories=({"source": "claude-code/{project}", "text": "We left off reviewing PR #107."},),
        )
    ]


def test_materialize_case_replaces_project_token_in_prefixes_and_seed_sources():
    case = ActiveSearchCase(
        case_id="resume-review",
        user_prompt="where did we leave the review?",
        should_search=True,
        expected_source_prefixes=("claude-code/{project}", "learning/{project}"),
        expected_answer_terms=("PR #107",),
        seed_memories=({"source": "claude-code/{project}", "text": "We left off reviewing PR #107."},),
    )

    materialized = materialize_case(case, project="cc_eval_active_123")

    assert materialized.expected_source_prefixes == (
        "claude-code/cc_eval_active_123",
        "learning/cc_eval_active_123",
    )
    assert materialized.seed_memories == (
        {"source": "claude-code/cc_eval_active_123", "text": "We left off reviewing PR #107."},
    )


def test_run_case_uses_realistic_prompt_without_memory_tool_instruction():
    client = MagicMock()
    executor = MagicMock()
    executor.run_prompt.return_value = "We left off reviewing PR #107."
    executor.last_run_trace = {
        "tool_calls": [
            {
                "name": "mcp__memories__memory_search",
                "query": "review",
                "source_prefix": "claude-code/cc_eval_active_123",
            }
        ]
    }
    case = ActiveSearchCase(
        case_id="resume-review",
        user_prompt="where did we leave the review?",
        should_search=True,
        expected_source_prefixes=("claude-code/{project}", "learning/{project}"),
        expected_answer_terms=("PR #107",),
        seed_memories=({"source": "claude-code/{project}", "text": "We left off reviewing PR #107."},),
    )

    result = run_case(
        case,
        client=client,
        executor=executor,
        project_dir="/tmp/cc_eval_active_123",
    )

    prompt = executor.run_prompt.call_args.args[0]
    assert prompt == "where did we leave the review?"
    assert "memory_search" not in prompt
    client.seed_memories.assert_called_once_with(
        [{"source": "claude-code/cc_eval_active_123", "text": "We left off reviewing PR #107."}]
    )
    assert result["memory_search_called"] is True
    assert result["active_search_score"] == 1.0


def test_install_claude_product_read_hooks_writes_project_settings(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "memory-recall.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (hooks_dir / "memory-query.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    instructions = tmp_path / "CLAUDE.md"
    instructions.write_text("# Memories\n", encoding="utf-8")

    install_claude_product_read_hooks(
        project_dir=str(project_dir),
        hooks_dir=str(hooks_dir),
        instructions_path=str(instructions),
    )

    settings = (project_dir / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert str(hooks_dir / "memory-recall.sh") in settings
    assert str(hooks_dir / "memory-query.sh") in settings
    assert "memory-extract.sh" not in settings
    assert (project_dir / "CLAUDE.md").read_text(encoding="utf-8") == "# Memories\n"
