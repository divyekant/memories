"""Tests for batch CLI commands using httpx.MockTransport."""

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
# batch add
# ---------------------------------------------------------------------------

class TestBatchAdd:
    def test_batch_add_jsonl(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"added": 2})

        jsonl_input = (
            '{"text": "fact one", "source": "test/"}\n'
            '{"text": "fact two", "source": "test/"}\n'
        )
        result = _invoke(["batch", "add", "-"], handler, input=jsonl_input)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["added"] == 2
        assert len(captured["body"]["memories"]) == 2

    def test_batch_add_json_array(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"added": 3})

        json_input = json.dumps([
            {"text": "a", "source": "s/"},
            {"text": "b", "source": "s/"},
            {"text": "c", "source": "s/"},
        ])
        result = _invoke(["batch", "add", "-"], handler, input=json_input)
        assert result.exit_code == 0
        assert len(captured["body"]["memories"]) == 3


# ---------------------------------------------------------------------------
# batch get
# ---------------------------------------------------------------------------

class TestBatchGet:
    def test_batch_get_multiple_ids(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "memories": [
                    {"id": "1", "text": "a", "source": "s/"},
                    {"id": "2", "text": "b", "source": "s/"},
                    {"id": "3", "text": "c", "source": "s/"},
                ],
            })

        result = _invoke(["batch", "get", "1", "2", "3"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]["memories"]) == 3
        assert captured["body"]["ids"] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# batch delete
# ---------------------------------------------------------------------------

class TestBatchDelete:
    def test_batch_delete_multiple_ids(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"deleted": 2})

        result = _invoke(["batch", "delete", "5", "10"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["deleted"] == 2
        assert captured["body"]["ids"] == ["5", "10"]


# ---------------------------------------------------------------------------
# batch search
# ---------------------------------------------------------------------------

class TestBatchSearch:
    def test_batch_search_json(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "results": [
                    {"query": "q1", "results": [{"id": "1", "similarity": 0.9}]},
                    {"query": "q2", "results": []},
                ],
            })

        json_input = json.dumps([
            {"query": "q1", "k": 3},
            {"query": "q2", "k": 5},
        ])
        result = _invoke(["batch", "search", "-"], handler, input=json_input)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]["results"]) == 2
        assert len(captured["body"]["queries"]) == 2


# ---------------------------------------------------------------------------
# batch upsert
# ---------------------------------------------------------------------------

class TestBatchUpsert:
    def test_batch_upsert_jsonl(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"count": 2})

        jsonl_input = (
            '{"text": "fact one", "source": "test/", "key": "k1"}\n'
            '{"text": "fact two", "source": "test/", "key": "k2"}\n'
        )
        result = _invoke(["batch", "upsert", "-"], handler, input=jsonl_input)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["count"] == 2
        assert len(captured["body"]["memories"]) == 2
