"""Tests for memories_auth CLI tool — env file writing and status display."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


class TestEnvFileWriter:
    """Test writing OAuth tokens to ~/.config/memories/env."""

    def test_writes_env_file(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "env"
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt-123",
                client_id="cid-456",
            )
            content = env_path.read_text()
            assert 'EXTRACT_PROVIDER="chatgpt-subscription"' in content
            assert 'CHATGPT_REFRESH_TOKEN="rt-123"' in content
            assert 'CHATGPT_CLIENT_ID="cid-456"' in content

    def test_preserves_existing_non_conflicting_vars(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "env"
            env_path.write_text('MEMORIES_URL="http://localhost:8900"\nMEMORIES_API_KEY="my-key"\n')
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt",
                client_id="cid",
            )
            content = env_path.read_text()
            assert 'MEMORIES_URL="http://localhost:8900"' in content
            assert 'MEMORIES_API_KEY="my-key"' in content
            assert 'CHATGPT_REFRESH_TOKEN="rt"' in content

    def test_overwrites_existing_provider_vars(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "env"
            env_path.write_text('EXTRACT_PROVIDER="openai"\nOPENAI_API_KEY="old"\n')
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt",
                client_id="cid",
            )
            content = env_path.read_text()
            assert 'EXTRACT_PROVIDER="chatgpt-subscription"' in content
            assert 'CHATGPT_REFRESH_TOKEN="rt"' in content
            assert "OPENAI_API_KEY" not in content

    def test_creates_parent_directories(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "subdir" / "nested" / "env"
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt",
                client_id="cid",
            )
            assert env_path.exists()


class TestAuthStatus:
    """Test the auth status display function."""

    def test_status_when_no_provider(self):
        from memories_auth import get_auth_status
        with patch.dict(os.environ, {}, clear=True):
            status = get_auth_status()
            assert status["provider"] is None
            assert status["configured"] is False

    def test_status_with_anthropic(self):
        from memories_auth import get_auth_status
        env = {"EXTRACT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-api03-test"}
        with patch.dict(os.environ, env):
            status = get_auth_status()
            assert status["provider"] == "anthropic"
            assert status["configured"] is True
            assert "sk-ant-api03****" in status["key_preview"]

    def test_status_with_chatgpt_subscription(self):
        from memories_auth import get_auth_status
        env = {
            "EXTRACT_PROVIDER": "chatgpt-subscription",
            "CHATGPT_REFRESH_TOKEN": "long-refresh-token-value",
            "CHATGPT_CLIENT_ID": "my-client-id",
        }
        with patch.dict(os.environ, env):
            status = get_auth_status()
            assert status["provider"] == "chatgpt-subscription"
            assert status["configured"] is True

    def test_status_with_ollama(self):
        from memories_auth import get_auth_status
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            status = get_auth_status()
            assert status["provider"] == "ollama"
            assert status["configured"] is True
            assert "ollama_url" in status

    def test_status_with_unconfigured_anthropic(self):
        from memories_auth import get_auth_status
        env = {"EXTRACT_PROVIDER": "anthropic"}
        with patch.dict(os.environ, env, clear=True):
            status = get_auth_status()
            assert status["provider"] == "anthropic"
            assert status["configured"] is False
