"""Tests for delete-by CLI commands using httpx.MockTransport."""

import json

import httpx
import pytest
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


# ---------------------------------------------------------------------------
# delete-by source
# ---------------------------------------------------------------------------

class TestDeleteBySource:
    def test_delete_by_source_with_yes(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"deleted": 5})

        result = _invoke(
            ["delete-by", "source", "--yes", "test/"], handler,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["deleted"] == 5
        assert captured["body"]["source"] == "test/"

    def test_delete_by_source_abort_without_yes(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"deleted": 0})

        result = _invoke(
            ["delete-by", "source", "test/"], handler, input="n\n",
        )
        assert result.exit_code == 0
        assert "deleted" not in result.output.lower() or "Aborted" in result.output


# ---------------------------------------------------------------------------
# delete-by prefix
# ---------------------------------------------------------------------------

class TestDeleteByPrefix:
    def test_delete_by_prefix_with_yes(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"deleted": 3})

        result = _invoke(
            ["delete-by", "prefix", "--yes", "old/"], handler,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["deleted"] == 3
        assert captured["body"]["prefix"] == "old/"

    def test_delete_by_prefix_abort_without_yes(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"deleted": 0})

        result = _invoke(
            ["delete-by", "prefix", "old/"], handler, input="n\n",
        )
        assert result.exit_code == 0
        assert "deleted" not in result.output.lower() or "Aborted" in result.output
