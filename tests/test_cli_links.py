"""Tests for the links CLI command group."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli import app
from cli.client import MemoriesClient


def _invoke(args, handler):
    original_init = MemoriesClient.__init__

    def patched_init(self, url=None, api_key=None, transport=None):
        original_init(self, url=url, api_key=api_key,
                      transport=httpx.MockTransport(handler))

    MemoriesClient.__init__ = patched_init
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["--json"] + args, catch_exceptions=False)
    finally:
        MemoriesClient.__init__ = original_init
    return result


# ---------------------------------------------------------------------------
# links list
# ---------------------------------------------------------------------------

class TestLinksList:
    def test_links_list_returns_links(self):
        def handler(request: httpx.Request):
            assert "/memory/1/links" in str(request.url)
            return httpx.Response(200, json={
                "links": [
                    {"from_id": 1, "to_id": 2, "link_type": "related_to", "direction": "outgoing"},
                    {"from_id": 3, "to_id": 1, "link_type": "reinforces", "direction": "incoming"},
                ]
            })

        result = _invoke(["links", "list", "1"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]["links"]) == 2

    def test_links_list_empty(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"links": []})

        result = _invoke(["links", "list", "5"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["links"] == []


# ---------------------------------------------------------------------------
# links add
# ---------------------------------------------------------------------------

class TestLinksAdd:
    def test_links_add_creates_link(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"from_id": 10, "to_id": 20, "type": "related_to"})

        result = _invoke(["links", "add", "10", "20"], handler)
        assert result.exit_code == 0
        assert "/memory/10/link" in captured["url"]
        assert captured["body"]["to_id"] == 20
        assert captured["body"]["type"] == "related_to"
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_links_add_custom_type(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"from_id": 1, "to_id": 2, "type": "supersedes"})

        result = _invoke(["links", "add", "--type", "supersedes", "1", "2"], handler)
        assert result.exit_code == 0
        assert captured["body"]["type"] == "supersedes"


# ---------------------------------------------------------------------------
# links remove
# ---------------------------------------------------------------------------

class TestLinksRemove:
    def test_links_remove_deletes_link(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            return httpx.Response(200, json={"removed": True})

        result = _invoke(["links", "remove", "10", "20"], handler)
        assert result.exit_code == 0
        assert captured["method"] == "DELETE"
        assert "/memory/10/link/20" in captured["url"]
        assert "type=related_to" in captured["url"]
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_links_remove_custom_type(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"removed": True})

        result = _invoke(["links", "remove", "--type", "blocked_by", "5", "6"], handler)
        assert result.exit_code == 0
        assert "type=blocked_by" in captured["url"]
