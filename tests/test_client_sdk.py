"""Tests for the Memories Python Client SDK — new endpoint coverage."""

from unittest.mock import MagicMock

import pytest

from cli.client import MemoriesClient


class FakeTransport:
    """Mock httpx transport that records requests and returns canned responses."""

    def __init__(self):
        self.calls = []
        self._responses = {}

    def set_response(self, method: str, path: str, json_data: dict, status: int = 200):
        self._responses[(method.upper(), path)] = (status, json_data)

    def handle_request(self, request):
        import httpx
        key = (request.method, request.url.raw_path.decode().split("?")[0])
        status, data = self._responses.get(key, (200, {}))
        self.calls.append({"method": request.method, "path": key[1], "content": request.content})
        import json
        return httpx.Response(status, json=data)


@pytest.fixture
def client():
    transport = FakeTransport()
    c = MemoriesClient("http://localhost:8900", transport=transport)
    c._transport = transport
    return c, transport


class TestLinkMethods:
    def test_add_link(self, client):
        c, t = client
        t.set_response("POST", "/memory/1/link", {"from_id": 1, "to_id": 2, "type": "related_to"})
        result = c.add_link(from_id=1, to_id=2, link_type="related_to")
        assert result["from_id"] == 1

    def test_get_links(self, client):
        c, t = client
        t.set_response("GET", "/memory/1/links", {"memory_id": 1, "links": []})
        result = c.get_links(memory_id=1)
        assert result["links"] == []

    def test_remove_link(self, client):
        c, t = client
        t.set_response("DELETE", "/memory/1/link/2", {"removed": True})
        result = c.remove_link(from_id=1, to_id=2)
        assert result["removed"] is True


class TestEventMethods:
    def test_recent_events(self, client):
        c, t = client
        t.set_response("GET", "/events/recent", {"events": [], "count": 0})
        result = c.recent_events()
        assert result["count"] == 0

    def test_register_webhook(self, client):
        c, t = client
        t.set_response("POST", "/webhooks", {"id": "1", "url": "http://test.com"})
        result = c.register_webhook("http://test.com")
        assert result["url"] == "http://test.com"

    def test_list_webhooks(self, client):
        c, t = client
        t.set_response("GET", "/webhooks", {"webhooks": []})
        result = c.list_webhooks()
        assert result["webhooks"] == []

    def test_delete_webhook(self, client):
        c, t = client
        t.set_response("DELETE", "/webhooks/1", {"deleted": True})
        result = c.delete_webhook("1")
        assert result["deleted"] is True


class TestMaintenanceMethods:
    def test_reembed(self, client):
        c, t = client
        t.set_response("POST", "/maintenance/reembed", {"status": "completed"})
        result = c.reembed(model="new-model")
        assert result["status"] == "completed"

    def test_reembed_no_model(self, client):
        c, t = client
        t.set_response("POST", "/maintenance/reembed", {"status": "completed"})
        result = c.reembed()
        assert result["status"] == "completed"


class TestTopLevelImport:
    def test_import_from_memories_client(self):
        from memories_client import MemoriesClient, ConnectionError, AuthError
        assert MemoriesClient is not None
        assert ConnectionError is not None
        assert AuthError is not None
