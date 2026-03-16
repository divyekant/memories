"""Lightweight in-process event bus for memory lifecycle events.

Supports SSE subscribers and webhook callbacks. All operations are
non-blocking — event delivery never slows down the calling thread.
"""

import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

import httpx

logger = logging.getLogger("memories.events")

# Valid event types
EVENT_TYPES = {
    "memory.added",
    "memory.updated",
    "memory.deleted",
    "memory.linked",
    "extraction.completed",
}


@dataclass
class Event:
    """A single lifecycle event."""
    type: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    id: str = field(default_factory=lambda: f"{time.monotonic_ns()}")

    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        return f"id: {self.id}\nevent: {self.type}\ndata: {json.dumps(self.data)}\n\n"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "data": self.data, "timestamp": self.timestamp, "id": self.id}


class EventBus:
    """Thread-safe event bus with SSE subscriber support and webhook dispatch."""

    def __init__(self, max_history: int = 100):
        self._lock = threading.Lock()
        self._subscribers: List[asyncio.Queue] = []
        self._webhooks: Dict[str, Dict[str, Any]] = {}  # id -> {url, events, created_at}
        self._next_webhook_id = 0
        self._history: deque = deque(maxlen=max_history)
        self._http_client: Optional[httpx.AsyncClient] = None

    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit an event. Non-blocking — fires and forgets to subscribers."""
        if event_type not in EVENT_TYPES:
            logger.warning("Unknown event type: %s", event_type)
            return

        event = Event(type=event_type, data=data)
        logger.debug("Event: %s %s", event_type, event.id)

        with self._lock:
            self._history.append(event)
            dead: List[asyncio.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

        # Fire webhooks in background (best-effort)
        self._dispatch_webhooks(event)

    def subscribe(self, event_filter: Optional[Set[str]] = None) -> asyncio.Queue:
        """Create a new SSE subscriber queue. Caller reads events from it."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove an SSE subscriber."""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent event history."""
        with self._lock:
            events = list(self._history)
        return [e.to_dict() for e in events[-limit:]]

    # -- Webhooks --

    def register_webhook(self, url: str, events: Optional[List[str]] = None) -> Dict[str, Any]:
        """Register a webhook callback URL."""
        with self._lock:
            self._next_webhook_id += 1
            wh_id = str(self._next_webhook_id)
            self._webhooks[wh_id] = {
                "id": wh_id,
                "url": url,
                "events": events or list(EVENT_TYPES),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        logger.info("Webhook registered: %s -> %s", wh_id, url)
        return self._webhooks[wh_id]

    def list_webhooks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._webhooks.values())

    def delete_webhook(self, webhook_id: str) -> bool:
        with self._lock:
            return self._webhooks.pop(webhook_id, None) is not None

    def _dispatch_webhooks(self, event: Event) -> None:
        """Best-effort async webhook delivery."""
        with self._lock:
            targets = [
                wh for wh in self._webhooks.values()
                if event.type in wh["events"]
            ]
        if not targets:
            return

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._send_webhooks(targets, event))
            else:
                asyncio.run(self._send_webhooks(targets, event))
        except RuntimeError:
            # No event loop — skip webhook delivery
            pass

    async def _send_webhooks(self, targets: List[Dict], event: Event) -> None:
        """Send event to all matching webhook URLs."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=5.0)
        payload = event.to_dict()
        for wh in targets:
            try:
                await self._http_client.post(wh["url"], json=payload)
            except Exception as e:
                logger.warning("Webhook %s failed: %s", wh["id"], e)


# Singleton
event_bus = EventBus()
