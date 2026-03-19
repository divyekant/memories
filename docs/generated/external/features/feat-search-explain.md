---
id: feat-004
type: feature-doc
title: Search Explain API
audience: external
generated: 2026-03-18
---

# Search Explain API

When a search returns unexpected results, the explain API shows you exactly why. It exposes the full scoring pipeline — vector similarity, BM25 keyword scores, RRF fusion parameters, and auth filtering — so you can diagnose ranking issues without guessing.

## Endpoint

```
POST /search/explain
```

**Access**: Admin only.

## How it works

The explain endpoint runs the same hybrid search pipeline as `/search`, but returns an additional `explain` object alongside the results. This object contains:

- **`vector_candidates`** — every candidate retrieved by vector similarity, with raw scores
- **`bm25_candidates`** — every candidate retrieved by BM25 keyword matching, with raw scores
- **`scoring_weights`** — the effective vector, BM25, and recency weights used for RRF fusion
- **`rrf_k`** — the Reciprocal Rank Fusion smoothing constant (default: 60)
- **`candidates_considered`** — total unique candidates before filtering
- **`filtered_by_source`** — candidates removed by source prefix filter
- **`filtered_by_auth`** — candidates removed by auth scoping

## Request body

The request body is the same as `/search`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | (required) | Search query |
| `k` | integer | 5 | Number of results to return |
| `hybrid` | boolean | false | Ignored (explain always runs hybrid) |
| `vector_weight` | float | 0.7 | Weight for vector similarity in RRF |
| `threshold` | float | null | Minimum similarity threshold |
| `source_prefix` | string | null | Filter by source prefix |
| `recency_weight` | float | 0.0 | Weight for recency signal in RRF |
| `recency_half_life_days` | float | 30.0 | Half-life for recency decay |

## Example

Submit an explain query:

```bash
curl -s -X POST http://localhost:8900/search/explain \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "query": "TypeScript configuration preferences",
    "k": 3,
    "vector_weight": 0.7,
    "source_prefix": "claude-code/"
  }' | jq .
```

### Response structure

```json
{
  "results": [
    {
      "id": 42,
      "text": "Team prefers strict TypeScript mode with noImplicitAny enabled",
      "source": "claude-code/my-project",
      "rrf_score": 0.032456,
      "confidence": 0.9512
    }
  ],
  "explain": {
    "candidates_considered": 18,
    "vector_candidates": [
      { "id": 42, "text": "Team prefers strict TypeScript...", "score": 0.891234 },
      { "id": 15, "text": "ESLint config uses...", "score": 0.654321 }
    ],
    "bm25_candidates": [
      { "id": 42, "text": "Team prefers strict TypeScript...", "score": 8.234 },
      { "id": 99, "text": "TypeScript version pinned to...", "score": 5.112 }
    ],
    "filtered_by_source": 3,
    "filtered_by_auth": 0,
    "scoring_weights": {
      "vector": 0.7,
      "bm25": 0.3,
      "recency": 0.0
    },
    "rrf_k": 60
  }
}
```

## Use cases

**Debugging low recall** — If a memory you expect to appear is missing, the explain response shows whether it was retrieved by either vector or BM25 but scored too low in fusion, filtered by source prefix, or not retrieved at all (indicating an embedding mismatch).

**Tuning weights** — Compare results with different `vector_weight` values. If keyword-heavy queries underperform, you might lower vector weight to let BM25 contribute more.

**Recency experiments** — Set `recency_weight` to a non-zero value and inspect how the recency signal reshuffles rankings for time-sensitive queries.

## Notes

- This endpoint always runs hybrid search, regardless of the `hybrid` field value.
- Auth filtering is applied after scoring. The `filtered_by_auth` count tells you how many candidates your key could not see.
- Candidate text in the explain object is truncated to 200 characters.
