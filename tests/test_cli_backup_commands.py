"""Tests for backup CLI commands using httpx.MockTransport."""

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


class TestBackupCreate:
    def test_create_default_prefix(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "path": "/data/backups/manual-2026-03-05.db",
            })

        result = _invoke(["backup", "create"], handler)
        assert result.exit_code == 0
        assert captured["body"]["prefix"] == "manual"
        data = json.loads(result.output)
        assert "manual" in data["data"]["path"]

    def test_create_custom_prefix(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "path": "/data/backups/pre-deploy-2026-03-05.db",
            })

        result = _invoke(["backup", "create", "--prefix", "pre-deploy"], handler)
        assert result.exit_code == 0
        assert captured["body"]["prefix"] == "pre-deploy"


class TestBackupList:
    def test_list_backups(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "backups": [
                    {"name": "manual-2026-03-01.db", "created_at": "2026-03-01T10:00:00"},
                    {"name": "manual-2026-03-05.db", "created_at": "2026-03-05T10:00:00"},
                ],
            })

        result = _invoke(["backup", "list"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]["backups"]) == 2

    def test_list_empty(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"backups": []})

        result = _invoke(["backup", "list"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["backups"] == []


class TestBackupRestore:
    def test_restore_with_yes_flag(self):
        def handler(request: httpx.Request):
            body = json.loads(request.content)
            return httpx.Response(200, json={
                "restored": 50, "backup_name": body["backup_name"],
            })

        result = _invoke(
            ["backup", "restore", "manual-2026-03-05.db", "--yes"], handler,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["restored"] == 50

    def test_restore_aborted(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"restored": 0})

        # Use --pretty so confirmation prompt appears, then answer 'n'
        original_init = MemoriesClient.__init__

        def patched_init(self, url=None, api_key=None, transport=None):
            original_init(self, url=url, api_key=api_key,
                          transport=httpx.MockTransport(handler))

        MemoriesClient.__init__ = patched_init
        try:
            runner = CliRunner()
            result = runner.invoke(
                app, ["--pretty", "backup", "restore", "test.db"],
                input="n\n",
            )
        finally:
            MemoriesClient.__init__ = original_init
        assert "Aborted" in result.output
