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

    def test_clean_home_strips_mcp_servers(self, executor, tmp_path, monkeypatch):
        """_create_clean_home copies claude config but removes mcpServers."""
        fake_real_home = str(tmp_path)
        monkeypatch.setenv("HOME", fake_real_home)

        # Create a fake ~/.claude.json with mcpServers
        config = {
            "apiKey": "sk-test",
            "mcpServers": {"memories": {"command": "node", "args": ["server.js"]}},
        }
        with open(os.path.join(fake_real_home, ".claude.json"), "w") as f:
            json.dump(config, f)

        clean_home = executor._create_clean_home()
        try:
            with open(os.path.join(clean_home, ".claude.json")) as f:
                clean_config = json.load(f)
            assert "apiKey" in clean_config
            assert "mcpServers" not in clean_config
        finally:
            shutil.rmtree(clean_home, ignore_errors=True)


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
