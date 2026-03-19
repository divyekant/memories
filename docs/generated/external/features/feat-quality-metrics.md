---
id: feat-007
type: feature-doc
title: Quality Metrics
audience: external
generated: 2026-03-18
---

# Quality Metrics

Memories tracks retrieval precision and extraction accuracy so you can see whether your memory system is helping or hurting. Four endpoints expose quality data at different levels of detail — from a one-glance summary to individual failure cases for debugging.

## Endpoints

### GET /metrics/quality-summary

**Access**: Admin only.

Top-level efficacy metrics combining retrieval precision and extraction accuracy into a single view.

```bash
curl -s http://localhost:8900/metrics/quality-summary \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Options | Description |
|-----------|---------|---------|-------------|
| `period` | `7d` | `today`, `7d`, `30d`, `all` | Time window for aggregation |

**Example response:**

```json
{
  "period": "7d",
  "retrieval": {
    "total_searches": 142,
    "feedback_count": 23,
    "positive_rate": 0.87,
    "avg_rank": 1.4
  },
  "extraction": {
    "total_extractions": 56,
    "add_rate": 0.35,
    "update_rate": 0.15,
    "delete_rate": 0.05,
    "noop_rate": 0.45
  }
}
```

### GET /metrics/search-quality

**Access**: All authenticated users (scoped to accessible memories for non-admin keys).

Detailed search quality metrics including rank distribution, feedback ratios, and volume.

```bash
curl -s "http://localhost:8900/metrics/search-quality?period=7d" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `period` | `7d` | Time window: `today`, `7d`, `30d`, `all` |
| `source_prefix` | (none) | Filter metrics to memories matching this prefix |

For scoped (non-admin) keys, metrics are automatically filtered to only include memories within the caller's allowed prefixes.

### GET /metrics/extraction-quality

**Access**: Admin only.

Extraction outcome metrics — ADD/UPDATE/DELETE/NOOP ratios and per-source breakdown.

```bash
curl -s "http://localhost:8900/metrics/extraction-quality?period=30d" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `period` | `7d` | Time window: `today`, `7d`, `30d`, `all` |

### GET /metrics/failures

**Access**: Admin only.

Recent low-quality results for debugging. Returns either retrieval failures (negative search feedback) or extraction failures (high-noop extractions).

```bash
# Retrieval failures
curl -s "http://localhost:8900/metrics/failures?type=retrieval&limit=5" \
  -H "X-API-Key: $API_KEY" | jq .

# Extraction failures
curl -s "http://localhost:8900/metrics/failures?type=extraction&limit=5" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `type` | `retrieval` | Failure type: `retrieval` or `extraction` |
| `limit` | `10` | Number of failures to return (1-100) |

## Search feedback

Quality metrics are powered by explicit search feedback. You can submit feedback via the API or via the `memory_is_useful` MCP tool.

### POST /search/feedback

Record a positive or negative signal for a search result:

```bash
curl -s -X POST http://localhost:8900/search/feedback \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "memory_id": 42,
    "query": "TypeScript configuration",
    "signal": "positive"
  }'
```

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `memory_id` | integer | The memory ID that was returned in search results |
| `query` | string | The original search query |
| `signal` | string | `positive` or `negative` |
| `search_id` | string | (optional) Identifier for the search session |

For scoped keys, feedback is only accepted for memories within the caller's allowed source prefixes.

## Interpreting the data

**High NOOP rate** (extraction) — Your extraction hooks are running on conversations that contain mostly already-known information. Consider increasing extraction intervals or filtering out short sessions.

**Low positive rate** (retrieval) — Search results are not matching user expectations. Check your `vector_weight` setting, inspect low-scoring candidates with `/search/explain`, or review whether memories need better source attribution.

**Retrieval failures with specific queries** — Use `/metrics/failures?type=retrieval` to find the exact queries that received negative feedback, then reproduce them with `/search/explain` to diagnose the ranking issue.
