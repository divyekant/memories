---
id: feat-008
type: feature-doc
title: Events and Webhooks
audience: external
generated: 2026-03-18
---

# Events and Webhooks

Memories emits lifecycle events whenever memories are created, updated, deleted, linked, or extracted. You can consume these events in two ways: a real-time SSE stream for live monitoring, or webhook callbacks for integrating with external systems.

## Event types

| Event | Fired when |
|-------|-----------|
| `memory.added` | A new memory is created |
| `memory.updated` | An existing memory is modified or superseded |
| `memory.deleted` | A memory is deleted |
| `memory.linked` | A link is created between two memories |
| `extraction.completed` | An extraction job finishes |

Every event carries a `data` payload with context-specific fields (e.g., `memory_id`, `source`, `job_id`).

## SSE event stream

### GET /events/stream

Open a persistent connection to receive events as Server-Sent Events.

```bash
curl -N http://localhost:8900/events/stream \
  -H "X-API-Key: $API_KEY"
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `event_type` | (none) | Filter to a specific event type (e.g., `memory.added`) |

**SSE format:**

```
id: 123456789
event: memory.added
data: {"memory_id": 42, "source": "claude-code/my-project", "text": "Team uses PostgreSQL"}

id: 123456790
event: extraction.completed
data: {"job_id": "abc123", "source": "claude-code/my-project", "stored_count": 2}
```

The stream sends a keepalive comment (`: keepalive`) every 30 seconds if no events are emitted. The client is advised to reconnect after 5 seconds on disconnect (`retry: 5000`).

**Auth scoping:** Scoped keys only receive events for memories within their allowed source prefixes. Admin keys see all events.

### Example: watch for new memories in real time

```bash
curl -N "http://localhost:8900/events/stream?event_type=memory.added" \
  -H "X-API-Key: $API_KEY"
```

### GET /events/recent

Fetch recent event history without a persistent connection.

```bash
curl -s "http://localhost:8900/events/recent?limit=10" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `50` | Number of recent events to return |

**Response:**

```json
{
  "events": [
    {
      "type": "memory.added",
      "data": {"memory_id": 42, "source": "claude-code/my-project"},
      "timestamp": "2026-03-18T14:32:01+00:00",
      "id": "123456789"
    }
  ],
  "count": 1
}
```

Event history is kept in memory (up to 100 events by default). It does not persist across restarts.

## Webhooks

Register callback URLs to receive event notifications via HTTP POST. Webhook management is admin-only.

### POST /webhooks

Register a new webhook:

```bash
curl -s -X POST http://localhost:8900/webhooks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "url": "https://your-server.com/memories-hook",
    "events": ["memory.added", "memory.deleted"]
  }'
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | (required) | Callback URL that will receive POST requests |
| `events` | array | all events | Event types to subscribe to |

**Response:**

```json
{
  "id": "1",
  "url": "https://your-server.com/memories-hook",
  "events": ["memory.added", "memory.deleted"],
  "created_at": "2026-03-18T14:32:01+00:00"
}
```

### Webhook delivery

When a matching event fires, Memories sends an HTTP POST to your URL with the event payload as JSON:

```json
{
  "type": "memory.added",
  "data": {"memory_id": 42, "source": "claude-code/my-project"},
  "timestamp": "2026-03-18T14:32:01+00:00",
  "id": "123456789"
}
```

Delivery is best-effort with a 5-second timeout. Failed deliveries are logged but not retried.

### GET /webhooks

List all registered webhooks:

```bash
curl -s http://localhost:8900/webhooks \
  -H "X-API-Key: $API_KEY" | jq .
```

### DELETE /webhooks/{webhook_id}

Remove a webhook:

```bash
curl -s -X DELETE http://localhost:8900/webhooks/1 \
  -H "X-API-Key: $API_KEY" | jq .
```

## Use cases

**Slack notifications** — Register a webhook pointing at a Slack incoming webhook URL to get notified when new memories are added.

**Dashboard updates** — Connect the SSE stream to a live dashboard that shows memory activity in real time.

**Audit integration** — Forward events to an external logging system for compliance or debugging.

**Cross-system sync** — Trigger downstream actions (e.g., update a knowledge base, notify team members) when memories change.

## Notes

- Webhooks are stored in memory and do not persist across server restarts. Re-register them on startup if needed.
- The SSE stream uses `asyncio.Queue` with a 256-event buffer per subscriber. Slow consumers that fall behind are automatically disconnected.
- Event history (`/events/recent`) keeps the last 100 events in memory.
