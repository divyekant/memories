---
id: feat-011
type: feature-doc
title: Compact and Consolidate
audience: external
generated: 2026-03-18
---

# Compact and Consolidate

Over time, your memory store accumulates redundant entries — the same fact phrased differently, partial overlaps from multiple extraction runs, or obsolete memories that were never formally updated. The compact and consolidate endpoints let you clean this up in two phases: discover clusters of similar memories, then optionally merge them using an LLM.

## Two-phase workflow

1. **Compact** (read-only) — Find clusters of similar memories without modifying anything.
2. **Consolidate** (write) — Merge each cluster into 1-2 concise memories using the configured LLM provider.

This separation lets you inspect what would be merged before committing to changes.

## Phase 1: Compact

### POST /maintenance/compact

Discover clusters of similar memories. This endpoint is read-only — it never modifies your data.

**Access**: Admin only.

```bash
curl -s -X POST http://localhost:8900/maintenance/compact \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"threshold": 0.85}' | jq .
```

**Request body:**

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `threshold` | float | `0.85` | 0.5 - 1.0 | Similarity threshold for clustering |

A higher threshold means stricter matching — only very similar memories are grouped together. A lower threshold finds more clusters but may group loosely related memories.

**Response:**

```json
{
  "clusters": [
    {
      "ids": [12, 45, 78],
      "size": 3,
      "memories": [
        {"id": 12, "text": "Team uses PostgreSQL for analytics", "source": "claude-code/proj"},
        {"id": 45, "text": "Analytics database is PostgreSQL", "source": "claude-code/proj"},
        {"id": 78, "text": "PostgreSQL chosen for the analytics service", "source": "learning/proj"}
      ]
    }
  ],
  "cluster_count": 1,
  "total_memories_in_clusters": 3
}
```

## Phase 2: Consolidate

### POST /maintenance/consolidate

Merge redundant clusters using an LLM. Runs in dry-run mode by default.

**Access**: Admin only. Requires a configured LLM provider (`EXTRACT_PROVIDER`).

```bash
# Dry run — see what would be merged
curl -s -X POST "http://localhost:8900/maintenance/consolidate?dry_run=true" \
  -H "X-API-Key: $API_KEY" | jq .

# Execute the merge
curl -s -X POST "http://localhost:8900/maintenance/consolidate?dry_run=false" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `dry_run` | `true` | When true, returns what would be merged without modifying data |
| `source_prefix` | (empty) | Limit consolidation to memories matching this prefix |

**Response:**

```json
{
  "clusters_found": 2,
  "results": [
    {
      "cluster_ids": [12, 45, 78],
      "merged_text": "The analytics service uses PostgreSQL as its database",
      "source": "claude-code/proj",
      "dry_run": false,
      "new_id": 102
    }
  ],
  "dry_run": false
}
```

When `dry_run` is false, for each cluster the LLM:
1. Reads all memories in the cluster
2. Produces 1-2 concise consolidated memories
3. Creates the new memories
4. Deletes the original cluster members

## Phase 3: Prune (optional)

### POST /maintenance/prune

Remove stale memories that have never been retrieved and have decayed in confidence.

**Access**: Admin only.

```bash
# Dry run
curl -s -X POST "http://localhost:8900/maintenance/prune?dry_run=true" \
  -H "X-API-Key: $API_KEY" | jq .

# Execute
curl -s -X POST "http://localhost:8900/maintenance/prune?dry_run=false" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Response:**

```json
{
  "candidates": 15,
  "pruned": 15,
  "dry_run": false
}
```

Prune identifies memories that:
- Have never been retrieved (no search hits tracked by the usage tracker)
- Are old enough to have decayed below useful confidence

## Recommended workflow

Run this periodically (weekly or monthly) to keep your memory store clean:

```bash
# Step 1: See what would be consolidated
curl -s -X POST "http://localhost:8900/maintenance/consolidate?dry_run=true" \
  -H "X-API-Key: $API_KEY" | jq '.clusters_found, .results[].cluster_ids'

# Step 2: If the clusters look correct, execute
curl -s -X POST "http://localhost:8900/maintenance/consolidate?dry_run=false" \
  -H "X-API-Key: $API_KEY" | jq .

# Step 3: Prune unretrieved stale memories
curl -s -X POST "http://localhost:8900/maintenance/prune?dry_run=false" \
  -H "X-API-Key: $API_KEY" | jq .
```

## Notes

- **Compact is always safe.** It only reads data and never modifies anything.
- **Consolidate with `dry_run=true` is safe.** Only `dry_run=false` mutates data.
- **Back up first.** Run `POST /backup` before consolidating with `dry_run=false`.
- **Source prefix scoping** on consolidate limits which memories are considered. Use this to consolidate one project at a time.
- **LLM costs** — consolidation calls the configured LLM provider once per cluster. Costs scale with cluster count, not total memory count.
- The compact threshold controls discovery granularity. Start with the default (0.85) and lower it if you see clusters that should be merged but are not.
