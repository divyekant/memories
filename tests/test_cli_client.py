"""Tests for CLI HTTP client using httpx.MockTransport."""

import json

import httpx
import pytest

from cli.client import (
    MemoriesClient,
    CliConnectionError,
    CliAuthError,
    CliNotFoundError,
    CliServerError,
)


def _make_transport(handler):
    """Create a MockTransport from a handler function."""
    return httpx.MockTransport(handler)


def _json_response(data, status_code=200):
    return httpx.Response(status_code, json=data)


class TestSearch:
    def test_search_sends_correct_body(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            captured["url"] = str(request.url)
            return _json_response({"results": []})

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        client.search("hello world", k=3, hybrid=False)
        assert captured["body"]["query"] == "hello world"
        assert captured["body"]["k"] == 3
        assert captured["body"]["hybrid"] is False

    def test_search_with_threshold(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return _json_response({"results": []})

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        client.search("test", threshold=0.5)
        assert captured["body"]["threshold"] == 0.5


class TestAdd:
    def test_add_sends_correct_body(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return _json_response({"id": "abc123"})

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        client.add("remember this", source="test-source")
        assert captured["body"]["text"] == "remember this"
        assert captured["body"]["source"] == "test-source"
        assert captured["body"]["deduplicate"] is True


class TestAuthHeader:
    def test_api_key_sent_in_header(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["headers"] = dict(request.headers)
            return _json_response({"status": "ok"})

        client = MemoriesClient(
            url="http://test:8900",
            api_key="my-secret",
            transport=_make_transport(handler),
        )
        client.health()
        assert captured["headers"]["x-api-key"] == "my-secret"


class TestErrorHandling:
    def test_connection_error(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("refused")

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        with pytest.raises(CliConnectionError, match="Cannot connect"):
            client.health()

    def test_auth_error_401(self):
        def handler(request: httpx.Request):
            return httpx.Response(401, json={"detail": "unauthorized"})

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        with pytest.raises(CliAuthError, match="401"):
            client.health()

    def test_auth_error_403(self):
        def handler(request: httpx.Request):
            return httpx.Response(403, json={"detail": "forbidden"})

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        with pytest.raises(CliAuthError, match="403"):
            client.stats()

    def test_not_found_404(self):
        def handler(request: httpx.Request):
            return httpx.Response(404, json={"detail": "not found"})

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        with pytest.raises(CliNotFoundError, match="Not found"):
            client.get_memory("missing-id")

    def test_server_error_500(self):
        def handler(request: httpx.Request):
            return httpx.Response(500, text="Internal Server Error")

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        with pytest.raises(CliServerError, match="500"):
            client.health()

    def test_validation_error_422(self):
        def handler(request: httpx.Request):
            return httpx.Response(422, json={"detail": "missing field"})

        client = MemoriesClient(
            url="http://test:8900",
            transport=_make_transport(handler),
        )
        with pytest.raises(ValueError, match="missing field"):
            client.add("")
