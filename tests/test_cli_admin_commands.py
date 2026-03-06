"""Tests for admin CLI commands using httpx.MockTransport."""

import json

import httpx
from click.testing import CliRunner

from cli import app
from cli.client import MemoriesClient


def _invoke(args, handler, input=None):
    """Invoke the CLI app with a mock transport backing the client."""
    original_init = MemoriesClient.__init__

    def patched_init(self, url=None, api_key=None, transport=None):
        original_init(self, url=url, api_key=api_key,
                      transport=httpx.MockTransport(handler))

    MemoriesClient.__init__ = patched_init
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["--json"] + args, input=input)
    finally:
        MemoriesClient.__init__ = original_init
    return result


class TestAdminStats:
    def test_stats(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "total_memories": 42, "total_sources": 5,
            })

        result = _invoke(["admin", "stats"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["total_memories"] == 42


class TestAdminHealth:
    def test_health(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "status": "healthy", "version": "1.5.0",
                "total_memories": 100,
            })

        result = _invoke(["admin", "health"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "healthy"
        assert data["data"]["version"] == "1.5.0"

    def test_health_connection_error(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("refused")

        result = _invoke(["admin", "health"], handler)
        assert result.exit_code == 3


class TestAdminDeduplicate:
    def test_deduplicate_dry_run(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"duplicates": 7})

        result = _invoke(["admin", "deduplicate"], handler)
        assert result.exit_code == 0
        assert captured["body"]["dry_run"] is True
        data = json.loads(result.output)
        assert data["data"]["duplicates"] == 7

    def test_deduplicate_execute(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"duplicates": 3})

        result = _invoke(["admin", "deduplicate", "--execute"], handler)
        assert result.exit_code == 0
        assert captured["body"]["dry_run"] is False

    def test_deduplicate_custom_threshold(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"duplicates": 0})

        result = _invoke(
            ["admin", "deduplicate", "--threshold", "0.80"], handler,
        )
        assert result.exit_code == 0
        assert captured["body"]["threshold"] == 0.80


class TestAdminUsage:
    def test_usage_default_period(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"requests": 150, "period": "7d"})

        result = _invoke(["admin", "usage"], handler)
        assert result.exit_code == 0
        assert "period=7d" in captured["url"]


class TestAdminConsolidate:
    def test_consolidate(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"message": "Consolidation complete"})

        result = _invoke(["admin", "consolidate"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["message"] == "Consolidation complete"


class TestAdminPrune:
    def test_prune(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"pruned": 12})

        result = _invoke(["admin", "prune"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["pruned"] == 12


class TestAdminReloadEmbedder:
    def test_reload_embedder(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"status": "reloaded"})

        result = _invoke(["admin", "reload-embedder"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "reloaded"
