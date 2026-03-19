---
id: feat-010
type: feature-doc
title: Confidence Decay and Reinforcement
audience: external
generated: 2026-03-18
---

# Confidence Decay and Reinforcement

Every memory in the system carries a computed `confidence` score between 0.0 and 1.0. This score decays over time using an exponential half-life model — memories that are never retrieved gradually fade in confidence, while memories that are actively used stay strong.

## How it works

The confidence score is computed from the time elapsed since the memory was last reinforced:

```
confidence = 0.5 ^ (age_days / half_life_days)
```

- **Half-life**: 90 days (default). After 90 days without retrieval, confidence drops to 0.50. After 180 days, 0.25. After 270 days, 0.125.
- **Anchor timestamp**: The `updated_at` field (falls back to `created_at` if never updated).
- **Fresh memories** start at confidence ~1.0.

## Automatic reinforcement

Every time a memory is retrieved through search (vector or hybrid), its `updated_at` timestamp is refreshed. This resets the decay clock — frequently retrieved memories maintain high confidence indefinitely.

You do not need to do anything to enable this. Reinforcement happens automatically on every search hit.

## Confidence in API responses

The `confidence` field is included in every API response that returns memory data:

```bash
# Single memory
curl -s http://localhost:8900/memory/42 \
  -H "X-API-Key: $API_KEY" | jq '{text: .text, confidence: .confidence}'
```

```json
{
  "text": "Team prefers strict TypeScript mode",
  "confidence": 0.9512
}
```

```bash
# Search results
curl -s -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query": "TypeScript preferences", "k": 3, "hybrid": true}' \
  | jq '.results[] | {id, text, confidence}'
```

```bash
# Listing memories
curl -s "http://localhost:8900/memories?limit=5" \
  -H "X-API-Key: $API_KEY" | jq '.memories[] | {id, text, confidence}'
```

## Recency weighting in search

Confidence decay operates on individual memories. For search ranking, there is a separate but related mechanism: **recency weighting** in hybrid search. When you set `recency_weight` > 0, a time-based signal is blended into the RRF (Reciprocal Rank Fusion) scoring.

```bash
curl -s -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "query": "deployment strategy",
    "k": 5,
    "hybrid": true,
    "recency_weight": 0.2,
    "recency_half_life_days": 14
  }' | jq .
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `recency_weight` | `0.0` | Weight for recency in RRF (0.0 = disabled, 1.0 = recency only) |
| `recency_half_life_days` | `30.0` | Half-life for the recency score |

When `recency_weight` is non-zero, the effective vector and BM25 weights are scaled down proportionally so all three weights sum to 1.0:
- `effective_vector = vector_weight * (1 - recency_weight)`
- `effective_bm25 = (1 - vector_weight) * (1 - recency_weight)`

## Pruning low-confidence memories

You can use the prune endpoint to identify and remove memories that have decayed below useful confidence and have never been retrieved:

```bash
# Dry run — see what would be pruned
curl -s -X POST "http://localhost:8900/maintenance/prune?dry_run=true" \
  -H "X-API-Key: $API_KEY" | jq .

# Execute prune
curl -s -X POST "http://localhost:8900/maintenance/prune?dry_run=false" \
  -H "X-API-Key: $API_KEY" | jq .
```

Prune considers both decay age and retrieval history. A low-confidence memory that has been retrieved at least once is treated differently from one that was never useful.

## Notes

- Confidence is computed on read, not stored. It uses the current time and the memory's anchor timestamp. There is no background job recalculating scores.
- The 90-day half-life is currently not configurable via API or env var. It is a constant in the `compute_confidence` static method.
- Reinforcement updates `updated_at` in memory metadata. This is the same field used by export/import, so imported memories retain their original decay position.
- The `reinforces` link type (see Memory Relationships) is a separate, explicit mechanism. It does not affect the confidence score — it creates a typed relationship between memories.
