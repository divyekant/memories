"""Tests for the event bus — emit, subscribe, webhooks, SSE formatting."""

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
import importlib
import os
import tempfile

import pytest

from event_bus import EventBus, Event, EVENT_TYPES


class TestEvent:
    def test_to_sse_format(self):
        e = Event(type="memory.added", data={"id": 1, "text": "hello"}, id="123")
        sse = e.to_sse()
        assert "id: 123\n" in sse
        assert "event: memory.added\n" in sse
        assert '"id": 1' in sse

    def test_to_dict(self):
        e = Event(type="memory.deleted", data={"id": 5})
        d = e.to_dict()
        assert d["type"] == "memory.deleted"
        assert d["data"]["id"] == 5
        assert "timestamp" in d


class TestEventBusEmit:
    def test_emit_valid_event(self):
        bus = EventBus()
        bus.emit("memory.added", {"id": 1})
        history = bus.recent_events()
        assert len(history) == 1
        assert history[0]["type"] == "memory.added"

    def test_emit_unknown_type_ignored(self):
        bus = EventBus()
        bus.emit("unknown.type", {"id": 1})
        assert len(bus.recent_events()) == 0

    def test_history_capped(self):
        bus = EventBus(max_history=3)
        for i in range(5):
            bus.emit("memory.added", {"id": i})
        assert len(bus.recent_events()) == 3

    def test_all_event_types_accepted(self):
        bus = EventBus()
        for et in EVENT_TYPES:
            bus.emit(et, {"test": True})
        assert len(bus.recent_events()) == len(EVENT_TYPES)


class TestSubscription:
    def test_subscriber_receives_events(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.emit("memory.added", {"id": 1})
        event = q.get_nowait()
        assert event.type == "memory.added"

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.emit("memory.added", {"id": 1})
        assert q.empty()

    def test_full_queue_drops_subscriber(self):
        bus = EventBus()
        q = bus.subscribe()
        # Fill the queue
        for i in range(257):
            bus.emit("memory.added", {"id": i})
        # Subscriber should have been dropped
        assert q not in bus._subscribers


class TestWebhooks:
    def test_register_webhook(self):
        bus = EventBus()
        wh = bus.register_webhook("http://example.com/hook")
        assert wh["url"] == "http://example.com/hook"
        assert "id" in wh

    def test_list_webhooks(self):
        bus = EventBus()
        bus.register_webhook("http://a.com")
        bus.register_webhook("http://b.com")
        assert len(bus.list_webhooks()) == 2

    def test_delete_webhook(self):
        bus = EventBus()
        wh = bus.register_webhook("http://a.com")
        assert bus.delete_webhook(wh["id"]) is True
        assert len(bus.list_webhooks()) == 0

    def test_delete_nonexistent_webhook(self):
        bus = EventBus()
        assert bus.delete_webhook("999") is False

    def test_webhook_event_filter(self):
        bus = EventBus()
        wh = bus.register_webhook("http://a.com", events=["memory.added"])
        assert wh["events"] == ["memory.added"]


class TestSSEEndpoint:
    """Test the SSE endpoint via the FastAPI app."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                app_module.memory = mock_engine

                from fastapi.testclient import TestClient
                yield TestClient(app_module.app), app_module

    def test_events_recent_endpoint(self, client):
        tc, mod = client
        resp = tc.get("/events/recent")
        assert resp.status_code == 200
        assert "events" in resp.json()

    def test_webhooks_crud(self, client):
        tc, mod = client
        # Create
        resp = tc.post("/webhooks", json={"url": "http://test.com/hook"})
        assert resp.status_code == 200
        wh_id = resp.json()["id"]

        # List
        resp = tc.get("/webhooks")
        assert resp.status_code == 200
        assert len(resp.json()["webhooks"]) == 1

        # Delete
        resp = tc.delete(f"/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
