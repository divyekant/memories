---
id: feat-temporal-reasoning
title: Temporal Reasoning
version: 5.0.0
audience: external
type: feature-doc
---

# Temporal Reasoning

Memories now supports time-aware search and version tracking. You can filter by date range, track when content was created, and access previous versions of updated memories.

## Document dates

Set `document_at` when adding memories to record when the source content was created:

```json
POST /memory/add
{
  "text": "Decided to use Redis for caching — faster than Memcached for our data structure needs",
  "source": "decisions/2026-Q1",
  "metadata": {
    "document_at": "2026-01-15T14:30:00+00:00"
  }
}
```

If `document_at` is not set, the memory uses `created_at` (ingestion time) as the temporal anchor.

## Date-range search

Filter memories to a specific time window with `since` and `until`:

```json
POST /search
{
  "query": "caching decisions",
  "hybrid": true,
  "since": "2026-01-01T00:00:00Z",
  "until": "2026-03-31T23:59:59Z"
}
```

Both parameters are optional ISO 8601 strings. Memories without a parseable date pass through the filter (backward compatible).

Available in:
- HTTP `/search`, `/search/explain`, `/search/batch`
- MCP `memory_search` tool

## Version tracking

When extraction updates a memory (AUDN UPDATE action), the old version is **archived** instead of deleted. A `supersedes` link connects the new version to the old one.

| Field | New memory | Old memory |
|-------|-----------|------------|
| `is_latest` | `true` | `false` |
| `archived` | `false` | `true` |
| `supersedes` link | points to old | — |

### Viewing version history

Search with `include_archived=true` to see superseded versions:

```json
POST /search
{
  "query": "caching decisions",
  "hybrid": true,
  "include_archived": true
}
```

In MCP: `memory_search` now accepts `include_archived` parameter.

## Recency scoring

The recency signal in hybrid search now prefers `document_at` over `created_at`:

1. `document_at` (if set) — when the content was created
2. `created_at` — when the memory was ingested
3. `timestamp` — backward compatibility

Use `recency_weight` to boost recent content:

```json
POST /search
{
  "query": "latest decisions",
  "hybrid": true,
  "recency_weight": 0.3,
  "recency_half_life_days": 30
}
```

## Reinforcement changes

`reinforce()` (called on search result access) now updates `last_reinforced_at` instead of `updated_at`. This means:

- `updated_at` only changes when memory content or metadata actually changes
- Confidence scoring uses `last_reinforced_at` for decay (preserving reinforcement semantics)
- `created_at` and `updated_at` remain stable content timestamps
