# Memories API Reference

Full REST API reference for the Memories service. Interactive Swagger UI is available at `http://localhost:8900/docs` when the service is running.

All endpoints accept/return JSON. Optional auth via `X-API-Key` header (enabled when `API_KEY` env var is set).

---

## Health & Status

### GET /health

Basic health check. Returns service status and summary stats.

**Response:**
```json
{"status": "ok", "version": "...", "total_memories": 150, "model": "all-MiniLM-L6-v2"}
```

### GET /health/ready

Readiness probe. Returns 200 when the service is fully loaded and ready to serve requests.

### GET /stats

Detailed index statistics: total memories, dimension, model name, index size, backup count, last updated.

### GET /metrics

Operational metrics: per-route latency percentiles, error counts, queue depth, memory trend, embedder reload state.

**Query params:**
- None

### GET /usage

Usage analytics over a time period.

**Query params:**
- `period` (string, default `"7d"`): One of `today`, `7d`, `30d`, `all`

---

## Search

### POST /search

Semantic or hybrid search over memories.

**Request body:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | *required* | Natural language search query |
| `k` | int | 5 | Number of results to return |
| `threshold` | float | 0.0 | Minimum similarity score (0-1) |
| `hybrid` | bool | true | Use hybrid BM25+vector search (recommended) |
| `vector_weight` | float | 0.7 | Weight for vector vs BM25 in hybrid mode |
| `source_prefix` | string | null | Filter results to memories whose source starts with this prefix |

**Response:**
```json
{
  "results": [
    {"id": 42, "text": "...", "source": "...", "similarity": 0.87, ...}
  ],
  "query": "...",
  "k": 5
}
```

### POST /search/batch

Execute multiple search queries in one request.

**Request body:**
```json
{
  "queries": [
    {"query": "first query", "k": 5},
    {"query": "second query", "hybrid": true}
  ]
}
```

**Response:** Array of search result sets, one per query.

---

## Add Memories

### POST /memory/add

Add a single memory.

**Request body:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | *required* | Memory content (1-50000 chars) |
| `source` | string | *required* | Source identifier (1-500 chars) |
| `deduplicate` | bool | true | Skip if a very similar memory exists |
| `metadata` | object | null | Optional key-value metadata |

**Response:**
```json
{"id": 42, "message": "Memory added", "deduplicated": false}
```

### POST /memory/add-batch

Add multiple memories at once. Batches are processed in internal chunks of 100 to avoid timeouts. Max 500 items per request.

**Request body:**
```json
{
  "memories": [
    {"text": "...", "source": "..."},
    {"text": "...", "source": "...", "metadata": {"tag": "v1"}}
  ],
  "deduplicate": true
}
```

**Response:**
```json
{"success": true, "ids": [10, 11], "count": 2, "message": "Added 2 memories"}
```

---

## Get Memories

### GET /memory/{id}

Fetch a single memory by ID.

**Response:**
```json
{"id": 42, "text": "...", "source": "...", "created_at": "...", "updated_at": "..."}
```

### POST /memory/get-batch

Fetch multiple memories by IDs.

**Request body:**
```json
{"ids": [1, 2, 3]}
```

**Response:**
```json
{"memories": [...], "count": 3}
```

---

## Delete Memories

### DELETE /memory/{id}

Delete a single memory by ID.

### DELETE /memories?source=\<prefix\>

Bulk delete all memories whose source starts with the given prefix. Single HTTP call replaces N individual deletes.

**Query params:**
- `source` (string, required): Source prefix to match

**Response:**
```json
{"count": 47}
```

**Example:**
```bash
curl -X DELETE "http://localhost:8900/memories?source=team/old-project/" \
  -H "X-API-Key: $API_KEY"
```

### POST /memory/delete-batch

Delete multiple memories by IDs.

**Request body:**
```json
{"ids": [1, 2, 3]}
```

### POST /memory/delete-by-source

Delete all memories matching a source substring pattern.

**Request body:**
```json
{"source_pattern": "credentials"}
```

### POST /memory/delete-by-prefix

Delete all memories whose source starts with a prefix.

**Request body:**
```json
{"source_prefix": "team/project/"}
```

---

## Update Memories

### PATCH /memory/{id}

Partial update of a memory.

**Request body:**
| Field | Type | Description |
|-------|------|-------------|
| `text` | string | New text (optional) |
| `source` | string | New source (optional) |
| `metadata_patch` | object | Key-value pairs to merge into metadata |

### POST /memory/upsert

Create or update a memory by source+key. If a memory with the same source and key exists, it is updated; otherwise a new one is created.

**Request body:**
```json
{
  "text": "...",
  "source": "team/project/file",
  "key": "entity-1",
  "metadata": {"owner": "team"}
}
```

### POST /memory/upsert-batch

Batch upsert.

**Request body:**
```json
{"memories": [{"text": "...", "source": "...", "key": "..."}]}
```

### POST /memory/supersede

Replace one memory with another (atomic delete + add).

---

## Novelty Check

### POST /memory/is-novel

Check if text is already known before adding it.

**Request body:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | *required* | Text to check |
| `threshold` | float | 0.88 | Similarity threshold (higher = stricter) |

**Response:**
```json
{"is_novel": true, "threshold": 0.88, "most_similar": {"text": "...", "similarity": 0.72}}
```

---

## Browse & Count

### GET /memories

List memories with offset-based pagination and optional source prefix filter.

**Query params:**
| Param | Type | Default | Max | Description |
|-------|------|---------|-----|-------------|
| `offset` | int | 0 | - | Starting position |
| `limit` | int | 20 | 5000 | Number of memories to return |
| `source` | string | null | - | Source prefix filter (returns memories whose source starts with this value) |

**Response:**
```json
{
  "memories": [...],
  "total": 2172,
  "offset": 0,
  "limit": 20
}
```

**Pagination example:**
```bash
# Page 1
curl "http://localhost:8900/memories?limit=100&source=team/"

# Page 2
curl "http://localhost:8900/memories?offset=100&limit=100&source=team/"
```

### GET /memories/count

Count memories, optionally filtered by source prefix. Lightweight alternative to listing when you only need the count.

**Query params:**
- `source` (string, optional): Source prefix filter

**Response:**
```json
{"count": 2172}
```

**Example:**
```bash
curl "http://localhost:8900/memories/count?source=team/project/"
```

### GET /folders

List unique source-based folders with memory counts.

**Response:**
```json
{"folders": [{"name": "team", "count": 50}], "total": 150}
```

### POST /folders/rename

Batch-rename a folder by updating the source prefix on all matching memories.

---

## Deduplication

### POST /memory/deduplicate

Find and optionally remove duplicate memories.

**Request body:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `threshold` | float | 0.90 | Similarity threshold for duplicate detection |
| `dry_run` | bool | true | Preview only; set false to actually remove |

---

## Index Operations

### POST /index/build

Rebuild the index from source files.

**Request body:**
```json
{"sources": ["file1.md", "file2.md"]}
```

### POST /maintenance/embedder/reload

Manually reload the embedder runtime (reclaims memory).

### POST /maintenance/consolidate

Consolidate memory index (compact IDs, rebuild vectors).

### POST /maintenance/prune

Prune stale or low-quality memories based on retrieval stats.

---

## Backups

### GET /backups

List available backups.

### POST /backup

Create a manual backup.

**Query params:**
- `prefix` (string, default `"manual"`): Backup name prefix

### POST /restore

Restore from a named backup.

**Request body:**
```json
{"backup_name": "manual_20260213_120000"}
```

---

## Cloud Sync (Optional)

### GET /sync/status

Cloud sync connection status.

### POST /sync/upload

Upload current state to cloud storage.

### POST /sync/download

Download state from cloud storage.

### GET /sync/snapshots

List available cloud snapshots.

### POST /sync/restore/{backup_name}

Restore from a specific cloud backup.

---

## Extraction (Optional)

Requires `EXTRACT_PROVIDER` to be configured. Async-first: returns 202 with a job ID.

### POST /memory/extract

Submit a conversation transcript for fact extraction.

**Request body:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `messages` | string | *required* | Conversation transcript |
| `source` | string | *required* | Source identifier (used for project context) |
| `context` | string | `"stop"` | Hook context: `stop`, `pre_compact`, `session_end`, `after_agent` |

**Response (202):**
```json
{"job_id": "abc-123", "status": "queued"}
```

### GET /memory/extract/{job_id}

Poll extraction job status.

**Response:**
```json
{"job_id": "abc-123", "status": "completed", "result": {"actions": [...], "extracted_count": 3, "stored_count": 2}}
```

### GET /extract/status

Extraction subsystem status: enabled/disabled, provider, model, queue depth.
