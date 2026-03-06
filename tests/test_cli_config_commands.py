"""Tests for config CLI commands."""

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


class TestConfigShow:
    def test_show_default_config(self, monkeypatch):
        monkeypatch.delenv("MEMORIES_URL", raising=False)
        monkeypatch.delenv("MEMORIES_API_KEY", raising=False)

        result = _invoke(["config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["url"] == "http://localhost:8900"
        assert data["data"]["_sources"]["url"] == "default"

    def test_show_with_env_vars(self, monkeypatch):
        monkeypatch.setenv("MEMORIES_URL", "http://custom:9999")
        monkeypatch.setenv("MEMORIES_API_KEY", "my-secret-key")

        result = _invoke(["config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["url"] == "http://custom:9999"
        assert data["data"]["_sources"]["url"] == "env"
        assert data["data"]["api_key"] == "my-secret-key"
        assert data["data"]["_sources"]["api_key"] == "env"

    def test_show_human_output(self, monkeypatch):
        monkeypatch.delenv("MEMORIES_URL", raising=False)
        monkeypatch.delenv("MEMORIES_API_KEY", raising=False)

        original_init = MemoriesClient.__init__

        def patched_init(self, url=None, api_key=None, transport=None):
            original_init(self, url=url, api_key=api_key,
                          transport=httpx.MockTransport(
                              lambda req: httpx.Response(200, json={})))

        MemoriesClient.__init__ = patched_init
        try:
            runner = CliRunner()
            result = runner.invoke(app, ["--pretty", "config", "show"])
        finally:
            MemoriesClient.__init__ = original_init

        assert result.exit_code == 0
        assert "url" in result.output
        assert "default" in result.output


class TestConfigSet:
    def test_set_url(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMORIES_URL", raising=False)
        monkeypatch.delenv("MEMORIES_API_KEY", raising=False)

        # Patch write_config to use tmp_path
        import cli.commands.config_cmd as config_cmd_mod
        original_write = config_cmd_mod.write_config

        def patched_write(**kwargs):
            return original_write(config_dir=str(tmp_path), **kwargs)

        monkeypatch.setattr(config_cmd_mod, "write_config", patched_write)

        result = _invoke(["config", "set", "url", "http://new:1234"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["key"] == "url"
        assert data["data"]["value"] == "http://new:1234"

        # Verify it was written to file
        written = json.loads((tmp_path / "config.json").read_text())
        assert written["url"] == "http://new:1234"

    def test_set_api_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMORIES_URL", raising=False)
        monkeypatch.delenv("MEMORIES_API_KEY", raising=False)

        import cli.commands.config_cmd as config_cmd_mod
        original_write = config_cmd_mod.write_config

        def patched_write(**kwargs):
            return original_write(config_dir=str(tmp_path), **kwargs)

        monkeypatch.setattr(config_cmd_mod, "write_config", patched_write)

        result = _invoke(["config", "set", "api_key", "secret123"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["key"] == "api_key"

    def test_set_invalid_key(self):
        result = _invoke(["config", "set", "invalid_key", "value"])
        assert result.exit_code != 0
        assert "Invalid key" in result.output

    def test_set_default_source(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMORIES_URL", raising=False)
        monkeypatch.delenv("MEMORIES_API_KEY", raising=False)

        import cli.commands.config_cmd as config_cmd_mod
        original_write = config_cmd_mod.write_config

        def patched_write(**kwargs):
            return original_write(config_dir=str(tmp_path), **kwargs)

        monkeypatch.setattr(config_cmd_mod, "write_config", patched_write)

        result = _invoke(["config", "set", "default_source", "my-app/"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["key"] == "default_source"
        assert data["data"]["value"] == "my-app/"
