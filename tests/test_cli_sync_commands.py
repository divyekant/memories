"""Tests for sync CLI commands using httpx.MockTransport."""

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


class TestSyncStatus:
    def test_status(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "enabled": True,
                "latest_remote": "snap-2026-03-05",
                "latest_local": "snap-2026-03-04",
                "remote_count": 3,
                "local_count": 5,
            })

        result = _invoke(["sync", "status"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["enabled"] is True
        assert data["data"]["remote_count"] == 3


class TestSyncUpload:
    def test_upload(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "status": "uploaded", "message": "Upload complete",
            })

        result = _invoke(["sync", "upload"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "uploaded"


class TestSyncSnapshots:
    def test_snapshots(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "snapshots": [
                    {"name": "snap-2026-03-01"},
                    {"name": "snap-2026-03-05"},
                ],
            })

        result = _invoke(["sync", "snapshots"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]["snapshots"]) == 2

    def test_snapshots_empty(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"snapshots": []})

        result = _invoke(["sync", "snapshots"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["snapshots"] == []


class TestSyncDownload:
    def test_download_with_confirm(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "downloaded"})

        result = _invoke(["sync", "download", "--confirm"], handler)
        assert result.exit_code == 0
        assert captured["body"]["confirm"] is True


class TestSyncRestore:
    def test_restore(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "status": "restored", "message": "Restore complete",
            })

        result = _invoke(["sync", "restore", "snap-2026-03-05", "--confirm"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "restored"
