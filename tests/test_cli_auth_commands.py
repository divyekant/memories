"""Tests for auth CLI commands."""

import json

import httpx
from click.testing import CliRunner

from cli import app
from cli.client import MemoriesClient


def _invoke(args, handler=None):
    """Invoke the CLI app with a mock transport backing the client."""
    if handler is None:
        handler = lambda req: httpx.Response(200, json={})

    original_init = MemoriesClient.__init__

    def patched_init(self, url=None, api_key=None, transport=None):
        original_init(self, url=url, api_key=api_key,
                      transport=httpx.MockTransport(handler))

    MemoriesClient.__init__ = patched_init
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["--json"] + args)
    finally:
        MemoriesClient.__init__ = original_init
    return result


class TestAuthStatus:
    def test_status_configured(self, monkeypatch):
        monkeypatch.setenv("EXTRACT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123456789")

        result = _invoke(["auth", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["provider"] == "anthropic"
        assert data["data"]["configured"] is True
        assert "key_preview" in data["data"]

    def test_status_not_configured(self, monkeypatch):
        monkeypatch.delenv("EXTRACT_PROVIDER", raising=False)

        result = _invoke(["auth", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["configured"] is False

    def test_status_ollama(self, monkeypatch):
        monkeypatch.setenv("EXTRACT_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")

        result = _invoke(["auth", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["provider"] == "ollama"
        assert data["data"]["ollama_url"] == "http://localhost:11434"

    def test_status_with_model(self, monkeypatch):
        monkeypatch.setenv("EXTRACT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123456789")
        monkeypatch.setenv("EXTRACT_MODEL", "claude-3-haiku")

        result = _invoke(["auth", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["model"] == "claude-3-haiku"

    def test_status_human_output_configured(self, monkeypatch):
        monkeypatch.setenv("EXTRACT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123456789")

        original_init = MemoriesClient.__init__

        def patched_init(self, url=None, api_key=None, transport=None):
            original_init(self, url=url, api_key=api_key,
                          transport=httpx.MockTransport(
                              lambda req: httpx.Response(200, json={})))

        MemoriesClient.__init__ = patched_init
        try:
            runner = CliRunner()
            result = runner.invoke(app, ["--pretty", "auth", "status"])
        finally:
            MemoriesClient.__init__ = original_init

        assert result.exit_code == 0
        assert "Provider:" in result.output

    def test_status_human_output_not_configured(self, monkeypatch):
        monkeypatch.delenv("EXTRACT_PROVIDER", raising=False)

        original_init = MemoriesClient.__init__

        def patched_init(self, url=None, api_key=None, transport=None):
            original_init(self, url=url, api_key=api_key,
                          transport=httpx.MockTransport(
                              lambda req: httpx.Response(200, json={})))

        MemoriesClient.__init__ = patched_init
        try:
            runner = CliRunner()
            result = runner.invoke(app, ["--pretty", "auth", "status"])
        finally:
            MemoriesClient.__init__ = original_init

        assert result.exit_code == 0
        assert "No extraction provider configured" in result.output
