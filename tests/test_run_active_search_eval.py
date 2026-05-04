"""Tests for the active-search behavior eval runner."""

from pathlib import Path
from unittest.mock import MagicMock

from eval.active_search_eval import ActiveSearchCase
from eval.run_active_search_eval import (
    CodexExecutor,
    install_claude_product_read_hooks,
    install_codex_product_read_hooks,
    load_cases,
    materialize_case,
    parse_codex_json_trace,
    run_case,
)


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


def test_install_codex_product_read_hooks_writes_temp_home(tmp_path: Path):
    codex_home = tmp_path / ".codex"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("", encoding="utf-8")

    install_codex_product_read_hooks(
        codex_home=str(codex_home),
        hooks_dir=str(hooks_dir),
        memories_url="http://localhost:8901",
        api_key="test-key",
        mcp_server_path=str(mcp_server),
    )

    hooks_json = (codex_home / "hooks.json").read_text(encoding="utf-8")
    config_toml = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert str(hooks_dir / "memory-recall.sh") in hooks_json
    assert str(hooks_dir / "memory-query.sh") in hooks_json
    assert "memory-extract.sh" not in hooks_json
    assert "[mcp_servers.memories]" in config_toml
    assert "http://localhost:8901" in config_toml
    assert "test-key" in config_toml
    assert "replace {project} with the current working directory basename" in config_toml
    assert "Do not use broad family prefixes" in config_toml
    assert "MEMORIES_ACTIVE_SEARCH_LOG" in (codex_home / "memories-eval-env").read_text(encoding="utf-8")


def test_parse_codex_json_trace_extracts_answer_and_memory_tool_calls():
    stdout = "\n".join(
        [
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"item.completed","item":{"type":"mcp_tool_call","name":"mcp__memories__memory_search","arguments":{"query":"release gate","source_prefix":"codex/project"}}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"Release is gated."}}',
        ]
    )

    answer, trace = parse_codex_json_trace(stdout)

    assert answer == "Release is gated."
    assert trace["tool_calls"] == [
        {
            "name": "mcp__memories__memory_search",
            "query": "release gate",
            "source_prefix": "codex/project",
        }
    ]


def test_parse_codex_json_trace_extracts_current_mcp_tool_call_shape():
    stdout = "\n".join(
        [
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"item.completed","item":{"type":"mcp_tool_call","server":"memories","tool":"memory_search","arguments":{"query":"release gate","source_prefix":"codex/project","hybrid":true}}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"Release is gated."}}',
        ]
    )

    answer, trace = parse_codex_json_trace(stdout)

    assert answer == "Release is gated."
    assert trace["tool_calls"] == [
        {
            "name": "mcp__memories__memory_search",
            "query": "release gate",
            "source_prefix": "codex/project",
        }
    ]


def test_codex_executor_uses_raw_prompt(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(cmd, capture_output, text, timeout, cwd, env):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        return MagicMock(
            returncode=0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","text":"4"}}\n',
            stderr="",
        )

    monkeypatch.setattr("eval.run_active_search_eval.subprocess.run", fake_run)
    executor = CodexExecutor(codex_home=str(tmp_path / ".codex"), timeout=30)

    answer = executor.run_prompt("What is 2 plus 2?", str(tmp_path))

    assert answer == "4"
    assert calls[0]["cmd"][-1] == "What is 2 plus 2?"
    assert calls[0]["env"]["CODEX_HOME"] == str(tmp_path / ".codex")
