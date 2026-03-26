---
id: feat-graph-search
title: Graph-Aware Search
version: 5.0.0
audience: external
type: feature-doc
---

# Graph-Aware Search

Memories automatically builds a knowledge graph from your extracted memories. Related memories are linked together, and search uses these links to surface connected context you might otherwise miss.

## How it works

When you extract memories (via hooks or the API), the extraction pipeline automatically creates `related_to` links between new memories and their similar existing neighbors. During search, the engine follows these links to discover related content that wouldn't appear in a standard keyword or vector search.

The graph expansion uses **Personalized PageRank (PPR)** — the same algorithm that powers web search ranking — adapted for memory retrieval. It naturally handles multi-hop connections (A is related to B, B is related to C, so C appears when you search for A's topic).

## Using graph search

### MCP (Claude Code, Codex, etc.)

Graph search is **enabled by default** in the MCP `memory_search` tool (`graph_weight=0.1`). No configuration needed.

### HTTP API

Add `graph_weight` to your search request:

```json
POST /search
{
  "query": "database architecture",
  "hybrid": true,
  "graph_weight": 0.1
}
```

### Result annotations

When graph expansion is active, each result includes:

| Field | Description |
|-------|-------------|
| `match_type` | `"direct"`, `"graph"`, or `"direct+graph"` |
| `base_rrf_score` | Score before graph boost |
| `graph_support` | Graph bonus added to the score |
| `graph_via` | IDs of seed memories that linked to this result |

### Configuration

| Parameter | Default (HTTP) | Default (MCP) | Description |
|-----------|---------------|---------------|-------------|
| `graph_weight` | 0.0 (off) | 0.1 (on) | Graph expansion weight |

Engine-level tuning (environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `SEARCH_PPR_ALPHA` | 0.85 | PPR damping factor |
| `SEARCH_PPR_MAX_ITERS` | 3 | PPR iterations |
| `SEARCH_GRAPH_RESERVED_SLOTS` | 2 | Reserved top-k slots for graph results |

## Scope safety

Graph expansion respects `source_prefix` filtering. If you scope a search to `wip/myproject`, graph-linked memories from other prefixes will not appear — even if a link exists.

## Auto-linking configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRACT_MAX_LINKS` | 3 | Max `related_to` links created per new memory during extraction |
| `EXTRACT_MIN_LINK_SCORE` | 0.005 | Minimum RRF score for auto-linking |

Set `EXTRACT_MAX_LINKS=0` to disable auto-linking entirely.
