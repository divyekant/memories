---
id: feat-009
type: feature-doc
title: Memory Relationships
audience: external
generated: 2026-03-18
---

# Memory Relationships

You can create typed, directional links between memories to capture how they relate to each other. Links are first-class objects stored alongside the memory metadata — they survive exports, show up in API responses, and respect auth scoping.

## Link types

| Type | Meaning | Example |
|------|---------|---------|
| `supersedes` | This memory replaces an older one | "Use PostgreSQL" supersedes "Use MySQL" |
| `related_to` | These memories cover the same topic | Two memories about the auth system |
| `blocked_by` | This work is blocked by a condition | "Deploy to prod" blocked_by "Security audit" |
| `caused_by` | This memory exists because of another | "Switched to ONNX" caused_by "Docker image too large" |
| `reinforces` | This memory strengthens or confirms another | Two independent observations of the same preference |

Links are directional: memory A linking to memory B does not automatically create a link from B to A.

## Creating links

### POST /memory/{memory_id}/link

Create a link from one memory to another:

```bash
curl -s -X POST http://localhost:8900/memory/42/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "to_id": 17,
    "type": "supersedes"
  }'
```

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `to_id` | integer | Target memory ID |
| `type` | string | Link type (see table above) |

**Constraints:**
- A memory cannot link to itself
- Duplicate links (same from, to, and type) are rejected
- Both memories must exist
- You need write access to both memories' source prefixes

**Response:**

```json
{
  "from_id": 42,
  "to_id": 17,
  "type": "supersedes",
  "created_at": "2026-03-18T14:32:01+00:00"
}
```

## Querying links

### GET /memory/{memory_id}/links

Get links for a memory:

```bash
# Outgoing links only (default)
curl -s "http://localhost:8900/memory/42/links" \
  -H "X-API-Key: $API_KEY" | jq .

# Include incoming links
curl -s "http://localhost:8900/memory/42/links?include_incoming=true" \
  -H "X-API-Key: $API_KEY" | jq .

# Filter by link type
curl -s "http://localhost:8900/memory/42/links?type=supersedes" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `type` | (none) | Filter by link type |
| `include_incoming` | `false` | Also return links from other memories pointing to this one |

**Response:**

```json
{
  "memory_id": 42,
  "links": [
    {
      "from_id": 42,
      "to_id": 17,
      "type": "supersedes",
      "created_at": "2026-03-18T14:32:01+00:00",
      "direction": "outgoing"
    },
    {
      "from_id": 99,
      "to_id": 42,
      "type": "related_to",
      "created_at": "2026-03-18T10:15:30+00:00",
      "direction": "incoming"
    }
  ]
}
```

**Auth scoping:** Links to memories outside your allowed source prefixes are automatically filtered out. You will only see links to memories you can read.

## Removing links

### DELETE /memory/{memory_id}/link/{target_id}

Remove a specific link:

```bash
curl -s -X DELETE "http://localhost:8900/memory/42/link/17?type=supersedes" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `type` | `related_to` | The link type to remove |

**Response:**

```json
{
  "removed": true
}
```

## Automatic link cleanup

When a memory is deleted, all incoming links pointing to it are automatically scrubbed from other memories. You do not need to clean up links manually.

## Use cases

**Decision tracking** — When a decision changes, create the new memory and link it with `supersedes` to the old one. This preserves the history of why things changed.

**Dependency mapping** — Use `blocked_by` links to track deferred work and its prerequisites. Query incoming `blocked_by` links on a prerequisite to see everything waiting on it.

**Root cause analysis** — Use `caused_by` to trace the chain of decisions that led to a particular architectural choice.

**Confidence building** — Use `reinforces` when multiple independent observations confirm the same fact. This creates an explicit evidence trail beyond the implicit confidence decay score.
