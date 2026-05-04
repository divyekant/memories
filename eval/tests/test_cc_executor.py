"""Tests for Claude Code executor with project isolation."""

import json
import os
import shutil
import subprocess
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from eval.cc_executor import CCExecutor


@pytest.fixture
def executor():
    return CCExecutor(
        timeout=60,
        memories_url="http://localhost:8900",
        memories_api_key="test-key",
        mcp_server_path="/path/to/mcp/server.js",
    )


@pytest.fixture
def fake_claude_home(tmp_path):
    """Create a fake ~/.claude/projects/ for auto-memory tests."""
    projects_dir = tmp_path / ".claude" / "projects"
    projects_dir.mkdir(parents=True)
    return tmp_path


class TestCreateIsolatedProject:
    def test_creates_temp_project(self, executor):
        """Dir exists, no CLAUDE.md, no .claude/ inside."""
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            assert os.path.isdir(project_dir)
            assert not os.path.exists(os.path.join(project_dir, "CLAUDE.md"))
            assert not os.path.exists(os.path.join(project_dir, ".claude"))
        finally:
            executor.cleanup_project(project_dir)

    def test_creates_mcp_config(self, executor):
        """with_memories=True creates .mcp.json with correct structure."""
        project_dir = executor.create_isolated_project(with_memories=True)
        try:
            mcp_path = os.path.join(project_dir, ".mcp.json")
            assert os.path.exists(mcp_path)

            with open(mcp_path) as f:
                config = json.load(f)

            assert "mcpServers" in config
            assert "memories" in config["mcpServers"]
            server = config["mcpServers"]["memories"]
            assert server["command"] == "node"
            assert server["args"] == ["/path/to/mcp/server.js"]
            assert server["env"]["MEMORIES_URL"] == "http://localhost:8900"
            assert server["env"]["MEMORIES_API_KEY"] == "test-key"
        finally:
            executor.cleanup_project(project_dir)

    def test_no_mcp_config_without_memories(self, executor):
        """with_memories=False produces no .mcp.json."""
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            assert not os.path.exists(os.path.join(project_dir, ".mcp.json"))
        finally:
            executor.cleanup_project(project_dir)


class TestStrictMcpConfig:
    @patch("eval.cc_executor.subprocess.run")
    def test_without_memory_uses_empty_mcp(self, mock_run, executor):
        """Without-memory runs pass --strict-mcp-config with empty JSON."""
        mock_run.return_value = MagicMock(stdout="response")
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            executor.run_prompt("test", project_dir)
            cmd = mock_run.call_args[0][0]
            assert "--strict-mcp-config" in cmd
            mcp_idx = cmd.index("--mcp-config")
            mcp_arg = cmd[mcp_idx + 1]
            config = json.loads(mcp_arg)
            assert config == {"mcpServers": {}}
        finally:
            executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_with_memory_uses_mcp_file(self, mock_run, executor):
        """With-memory runs pass --strict-mcp-config with .mcp.json path."""
        mock_run.return_value = MagicMock(stdout="response")
        project_dir = executor.create_isolated_project(with_memories=True)
        try:
            executor.run_prompt("test", project_dir)
            cmd = mock_run.call_args[0][0]
            assert "--strict-mcp-config" in cmd
            mcp_idx = cmd.index("--mcp-config")
            mcp_arg = cmd[mcp_idx + 1]
            assert mcp_arg.endswith(".mcp.json")
        finally:
            executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_require_memories_raises_when_mcp_config_missing(self, mock_run, executor):
        """System-mode evals must fail loudly if MCP config was not installed."""
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            with pytest.raises(RuntimeError, match="requires Memories MCP"):
                executor.run_prompt("test", project_dir, require_memories=True)
            mock_run.assert_not_called()
        finally:
            executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_without_memory_disables_global_memory_hooks(self, mock_run):
        """Without-memory eval runs must not let global hooks recall or write memories."""
        mock_run.return_value = MagicMock(stdout="response")
        executor = CCExecutor(
            memories_url="http://localhost:8901",
            memories_api_key="eval-key",
            mcp_server_path="/path/to/mcp/server.js",
        )
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            executor.run_prompt("test", project_dir)
            env = mock_run.call_args.kwargs["env"]
            assert env["MEMORIES_DISABLED"] == "1"
            assert env["MEMORIES_URL"] == "http://localhost:8901"
            assert env["MEMORIES_API_KEY"] == "eval-key"
            assert env["MEMORIES_BACKENDS_FILE"] == "__eval_single_backend__"
            assert env["MEMORIES_ENV_FILE"].startswith(project_dir)
            assert not os.path.exists(env["MEMORIES_ENV_FILE"])
            assert env["MEMORIES_ACTIVE_SEARCH_LOG"].startswith(project_dir)
        finally:
            executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_with_memory_forces_hooks_to_eval_backend(self, mock_run):
        """With-memory eval runs may use hooks, but only against the eval backend."""
        captured = {}

        def fake_run(*args, **kwargs):
            env = kwargs["env"]
            captured["env_text"] = open(env["MEMORIES_ENV_FILE"]).read()
            return MagicMock(stdout="response")

        mock_run.side_effect = fake_run
        executor = CCExecutor(
            memories_url="http://localhost:8901",
            memories_api_key="eval-key",
            mcp_server_path="/path/to/mcp/server.js",
        )
        project_dir = executor.create_isolated_project(with_memories=True)
        try:
            executor.run_prompt("test", project_dir)
            env = mock_run.call_args.kwargs["env"]
            assert env["MEMORIES_DISABLED"] == "0"
            assert env["MEMORIES_URL"] == "http://localhost:8901"
            assert env["MEMORIES_API_KEY"] == "eval-key"
            assert env["MEMORIES_BACKENDS_FILE"] == "__eval_single_backend__"
            assert env["MEMORIES_ENV_FILE"].startswith(project_dir)
            assert not os.path.exists(env["MEMORIES_ENV_FILE"])
            env_text = captured["env_text"]
            assert "MEMORIES_URL=http://localhost:8901" in env_text
            assert "MEMORIES_DISABLED=0" in env_text
            assert "MEMORIES_ACTIVE_SEARCH_LOG=" in env_text
            assert env["MEMORIES_ACTIVE_SEARCH_LOG"].startswith(project_dir)
        finally:
            executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_run_prompt_strips_judge_api_keys_from_agent_env(self, mock_run, monkeypatch):
        """Judge/provider credentials should not contaminate Claude Code auth."""
        mock_run.return_value = MagicMock(stdout="response")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "judge-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-judge-key")
        executor = CCExecutor(
            memories_url="http://localhost:8901",
            memories_api_key="eval-key",
            mcp_server_path="/path/to/mcp/server.js",
        )
        project_dir = executor.create_isolated_project(with_memories=True)
        try:
            executor.run_prompt("test", project_dir)
            env = mock_run.call_args.kwargs["env"]
            assert "ANTHROPIC_API_KEY" not in env
            assert "OPENAI_API_KEY" not in env
            assert env["MEMORIES_API_KEY"] == "eval-key"
        finally:
            executor.cleanup_project(project_dir)


class TestRunPrompt:
    @patch("eval.cc_executor.subprocess.run")
    def test_run_prompt(self, mock_run, executor):
        """Mock subprocess returns stdout, verify 'claude' and '-p' in command."""
        mock_run.return_value = MagicMock(stdout="Hello from Claude")
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            result = executor.run_prompt("Say hello", project_dir)

            assert result == "Hello from Claude"
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "claude" in cmd
            assert "-p" in cmd
        finally:
            executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_capture_trace_records_stream_json_tool_calls(self, mock_run):
        """Trace mode should run Claude stream-json and save tool-call evidence."""
        stream = "\n".join([
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "mcp__memories__memory_search",
                            "input": {
                                "query": "roadmap",
                                "source_prefix": "eval/longmemeval/q1",
                            },
                        }
                    ]
                },
            }),
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "The answer is blue."}
                    ]
                },
            }),
            json.dumps({
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "The answer is blue.",
                "duration_ms": 1234,
            }),
        ])
        mock_run.return_value = MagicMock(stdout=stream, stderr="", returncode=0)
        traced = CCExecutor(
            timeout=60,
            memories_url="http://localhost:8901",
            memories_api_key="eval-key",
            mcp_server_path="/path/to/mcp/server.js",
            capture_trace=True,
        )
        project_dir = traced.create_isolated_project(with_memories=True)
        try:
            result = traced.run_prompt("Find the answer", project_dir)
            cmd = mock_run.call_args[0][0]

            assert result == "The answer is blue."
            assert "--output-format" in cmd
            assert cmd[cmd.index("--output-format") + 1] == "stream-json"
            assert "--verbose" in cmd
            assert traced.last_run_trace["output_format"] == "stream-json"
            assert traced.last_run_trace["event_count"] == 3
            assert traced.last_run_trace["tool_calls"] == [
                {
                    "id": "toolu_1",
                    "name": "mcp__memories__memory_search",
                    "input_keys": ["query", "source_prefix"],
                    "source_prefix": "eval/longmemeval/q1",
                    "query": "roadmap",
                }
            ]
            assert traced.last_run_trace["result"]["is_error"] is False
            assert traced.last_run_trace["duration_ms"] == 1234
        finally:
            traced.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_run_prompt_timeout(self, mock_run, executor):
        """Mock raises TimeoutExpired, verify 'timeout' in output."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=60)
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            result = executor.run_prompt("Long running prompt", project_dir)

            assert "[TIMEOUT]" in result
            assert "timeout" in result.lower() or "TIMEOUT" in result
        finally:
            executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_capture_trace_timeout_preserves_partial_stream_json(self, mock_run):
        """Timeout traces should retain partial Claude stream evidence."""
        partial_stream = "\n".join([
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_timeout",
                            "name": "mcp__memories__memory_evidence",
                            "input": {
                                "query": "The Nightingale Sapiens The Power",
                                "source_prefix": "eval/longmemeval/q1",
                                "reference_date": "2022-04-30T13:22:00+00:00",
                            },
                        }
                    ]
                },
            }),
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I found the first date."}
                    ]
                },
            }),
        ])
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="claude",
            timeout=60,
            output=partial_stream,
            stderr="partial stderr",
        )
        traced = CCExecutor(
            timeout=60,
            memories_url="http://localhost:8901",
            memories_api_key="eval-key",
            mcp_server_path="/path/to/mcp/server.js",
            capture_trace=True,
        )
        project_dir = traced.create_isolated_project(with_memories=True)
        try:
            result = traced.run_prompt("Find the answer", project_dir)

            assert result.startswith("[TIMEOUT]")
            assert traced.last_run_trace["error_kind"] == "timeout"
            assert traced.last_run_trace["event_count"] == 2
            assert traced.last_run_trace["tool_calls"][0]["name"] == "mcp__memories__memory_evidence"
            assert traced.last_run_trace["stderr_excerpt"] == "partial stderr"
        finally:
            traced.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_run_prompt_cli_not_found(self, mock_run, executor):
        """Mock raises FileNotFoundError, verify error message."""
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'claude'")
        project_dir = executor.create_isolated_project(with_memories=False)
        try:
            result = executor.run_prompt("Test prompt", project_dir)

            assert "[ERROR]" in result
            assert "not found" in result.lower()
        finally:
            executor.cleanup_project(project_dir)


class TestAutoMemoryCleanup:
    def test_cleanup_project_removes_auto_memory(self, executor, fake_claude_home):
        """cleanup_project removes the corresponding ~/.claude/projects/ entry."""
        project_dir = executor.create_isolated_project(with_memories=False)
        # Simulate Claude Code creating an auto-memory dir
        abs_path = os.path.realpath(project_dir)
        mangled = "-" + abs_path.strip("/").replace("/", "-")
        auto_memory_dir = fake_claude_home / ".claude" / "projects" / mangled
        auto_memory_dir.mkdir(parents=True)
        (auto_memory_dir / "MEMORY.md").write_text("stale voltis knowledge")

        with patch.dict(os.environ, {"HOME": str(fake_claude_home)}):
            executor.cleanup_project(project_dir)

        assert not auto_memory_dir.exists()
        assert not os.path.isdir(project_dir)

    def test_cleanup_stale_auto_memory(self, fake_claude_home):
        """cleanup_stale_auto_memory removes both cc_eval_ and cc-eval- entries."""
        projects_dir = fake_claude_home / ".claude" / "projects"
        # Create stale dirs — both underscore (raw) and hyphen (mangled) variants
        for name in [
            "-private-var-folders-T-cc_eval_abc123",  # underscore variant
            "-private-var-folders-T-cc-eval-def456",  # hyphen variant (mangled)
            "-private-var-folders-T-cc-eval-ghi789",  # hyphen variant
        ]:
            d = projects_dir / name
            d.mkdir()
            (d / "MEMORY.md").write_text("stale")
        # Create a non-eval dir that should NOT be deleted
        keep = projects_dir / "-Users-divyekant-Projects-memories"
        keep.mkdir()
        (keep / "MEMORY.md").write_text("keep this")

        with patch.dict(os.environ, {"HOME": str(fake_claude_home)}):
            CCExecutor.cleanup_stale_auto_memory()

        # All stale dirs gone (both variants)
        remaining = os.listdir(projects_dir)
        assert not any("cc_eval" in r or "cc-eval" in r for r in remaining)
        # Non-eval dir preserved
        assert keep.exists()

    def test_cleanup_stale_no_projects_dir(self, tmp_path):
        """cleanup_stale_auto_memory handles missing ~/.claude/projects/ gracefully."""
        with patch.dict(os.environ, {"HOME": str(tmp_path)}):
            CCExecutor.cleanup_stale_auto_memory()  # should not raise
