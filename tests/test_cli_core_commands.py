"""Tests for core CLI commands using httpx.MockTransport."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli import app
from cli.client import MemoriesClient


def _make_client(handler):
    """Create a MemoriesClient backed by a mock transport."""
    transport = httpx.MockTransport(handler)
    return MemoriesClient(url="http://test:8900", transport=transport)


def _invoke(args, handler, input=None):
    """Invoke the CLI app with a mock transport backing the client.

    Patches MemoriesClient.__init__ so the app group callback
    creates a client that uses our MockTransport.
    """
    import cli as cli_module

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
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_returns_results(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "query": "test",
                "results": [
                    {"id": "1", "text": "hello", "similarity": 0.95, "source": "cli/"},
                ],
                "count": 1,
            })

        result = _invoke(["search", "test"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]["results"]) == 1

    def test_search_empty(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "query": "nothing", "results": [], "count": 0,
            })

        result = _invoke(["search", "nothing"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["results"] == []

    def test_search_with_options(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"results": [], "count": 0})

        result = _invoke(
            ["search", "-k", "3", "--no-hybrid", "--threshold", "0.5",
             "--source", "test/", "myquery"],
            handler,
        )
        assert result.exit_code == 0
        assert captured["body"]["k"] == 3
        assert captured["body"]["hybrid"] is False
        assert captured["body"]["threshold"] == 0.5
        assert captured["body"]["source_prefix"] == "test/"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

class TestAdd:
    def test_add_text(self):
        def handler(request: httpx.Request):
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": "42", "text": body["text"]})

        result = _invoke(["add", "-s", "test/", "remember this"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["id"] == "42"

    def test_add_from_stdin(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "43"})

        result = _invoke(["add", "-s", "test/", "-"], handler, input="piped text\n")
        assert result.exit_code == 0
        assert captured["body"]["text"] == "piped text"

    def test_add_no_deduplicate(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "44"})

        result = _invoke(
            ["add", "-s", "test/", "--no-deduplicate", "text"], handler,
        )
        assert result.exit_code == 0
        assert captured["body"]["deduplicate"] is False


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

class TestGet:
    def test_get_memory(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "id": "7", "text": "some memory", "source": "cli/",
                "created_at": "2025-01-01T00:00:00",
            })

        result = _invoke(["get", "7"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["id"] == "7"
        assert data["data"]["text"] == "some memory"

    def test_get_not_found(self):
        def handler(request: httpx.Request):
            return httpx.Response(404, json={"detail": "not found"})

        result = _invoke(["get", "999"], handler)
        assert result.exit_code == 2
        err = json.loads(result.stderr)
        assert err["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:
    def test_list_memories(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "memories": [
                    {"id": "1", "text": "a", "source": "cli/"},
                    {"id": "2", "text": "b", "source": "cli/"},
                ],
                "total": 2,
            })

        result = _invoke(["list"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]["memories"]) == 2

    def test_list_with_source_filter(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"memories": [], "total": 0})

        result = _invoke(["list", "--source", "test/"], handler)
        assert result.exit_code == 0
        assert "source=test" in captured["url"]


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_memory(self):
        def handler(request: httpx.Request):
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={})

        result = _invoke(["delete", "7"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------

class TestCount:
    def test_count_all(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"count": 42})

        result = _invoke(["count"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["count"] == 42

    def test_count_with_source(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"count": 5})

        result = _invoke(["count", "--source", "test/"], handler)
        assert result.exit_code == 0
        assert "source=test" in captured["url"]


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_upsert_creates(self):
        def handler(request: httpx.Request):
            body = json.loads(request.content)
            return httpx.Response(200, json={
                "id": "50", "updated": False, "text": body["text"],
            })

        result = _invoke(
            ["upsert", "-s", "test/", "-k", "mykey", "some text"], handler,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["id"] == "50"

    def test_upsert_updates(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"id": "50", "updated": True})

        result = _invoke(
            ["upsert", "-s", "test/", "-k", "mykey", "new text"], handler,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["updated"] is True


# ---------------------------------------------------------------------------
# is-novel
# ---------------------------------------------------------------------------

class TestIsNovel:
    def test_novel_text(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"is_novel": True})

        result = _invoke(["is-novel", "something new"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["is_novel"] is True

    def test_not_novel_text(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "is_novel": False,
                "most_similar": {
                    "text": "something old",
                    "similarity": 0.95,
                },
            })

        result = _invoke(["is-novel", "something old"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["is_novel"] is False

    def test_novel_custom_threshold(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"is_novel": True})

        result = _invoke(
            ["is-novel", "--threshold", "0.75", "test"], handler,
        )
        assert result.exit_code == 0
        assert captured["body"]["threshold"] == 0.75


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------

class TestFolders:
    def test_folders(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "folders": [
                    {"folder": "cli/", "count": 10},
                    {"folder": "mcp/", "count": 5},
                ],
            })

        result = _invoke(["folders"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]["folders"]) == 2

    def test_folders_empty(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"folders": []})

        result = _invoke(["folders"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["folders"] == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_connection_error_exit_3(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("refused")

        result = _invoke(["search", "test"], handler)
        assert result.exit_code == 3
        err = json.loads(result.stderr)
        assert err["code"] == "CONNECTION_ERROR"

    def test_auth_error_exit_4(self):
        def handler(request: httpx.Request):
            return httpx.Response(401, json={"detail": "unauthorized"})

        result = _invoke(["search", "test"], handler)
        assert result.exit_code == 4
        err = json.loads(result.stderr)
        assert err["code"] == "AUTH_REQUIRED"

    def test_not_found_error_exit_2(self):
        def handler(request: httpx.Request):
            return httpx.Response(404, json={"detail": "not found"})

        result = _invoke(["get", "999"], handler)
        assert result.exit_code == 2
        err = json.loads(result.stderr)
        assert err["code"] == "NOT_FOUND"
