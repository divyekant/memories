# Production baseline (task P0-3)

This document records the state of the production Memories server before the
Rust rewrite. The Rust server must match this baseline, or do better.

- Host: the droplet `dk-services-sfo3` (4 vCPU, 7.8 GiB RAM, 2 GiB swap).
- Measured on: 2026-11-16, from 07:18 to 07:21 UTC.
- Server version: v5.19.0. The container started at 07:07:55 UTC on the same
  day, after the v5.19.0 deploy.
- Method: read-only commands only. No deploy, no restart, no configuration
  change, and no write to production data.

This document holds no secret, no key value and no memory text. For a variable
that holds a secret, the document gives only the name and "set" or "empty".

## Answers to the exit check

| Feature | Used in production | Evidence |
|---|---|---|
| `PROJECT_PROMOTION_MODE` | No. The mode is `off`. | No `PROJECT_PROMOTION_*` variable is in the container environment. The default of `load_promotion_config()` is `PromotionMode.OFF`. |
| Cloud sync | No | `CLOUD_SYNC_ENABLED` is not set. The image has no `boto3`. The start log says `boto3 not installed - cloud sync disabled`. |
| OpenAI embedder | No | `EMBED_PROVIDER=onnx`. `OPENAI_API_KEY` and `EMBED_API_KEY` are empty. `config.json` and `embedding_spaces.json` record `onnx:all-MiniLM-L6-v2:384d`. |
| `chatgpt-subscription` provider | No | `EXTRACT_PROVIDER=anthropic`. `CHATGPT_REFRESH_TOKEN` and `CHATGPT_CLIENT_ID` are empty. `SHADOW_PROVIDERS` is empty. `extraction_tokens` has no row for this provider. |
| Embedded Qdrant mode | No | `QDRANT_URL=http://qdrant:6333`. The server uses the Qdrant server 1.15.4 in the container `qdrant`. |

Effects on the task list:

- P2-4 (promotions): port the four `/promotions` routes with the replies of the
  `off` mode only. The full port is a task after the cutover. In `off` mode,
  the list and get routes still read candidate rows from `metadata.json`.
  Production has no such row: no row has a `promotion` field. So these routes
  return empty results in production.
- P1-2 (providers): do not port `chatgpt-subscription`, unless dk asks for it.
- The Rust server does not need cloud sync, the OpenAI embedder or the
  embedded Qdrant mode for the cutover of this host.

CAUTION: The directory `/opt/dk-services/data/memories/qdrant` (462 MB, last
change 2026-07-27) is an old store of the embedded mode. The server does not
use it. Do not read it as live data in a migration.

## Environment variables

The container environment has these names. Values that are not secret are
given. The Docker base image sets `GPG_KEY`, `LANG`, `PATH`, `PYTHON_SHA256`
and `PYTHON_VERSION`.

| Group | Variable and value |
|---|---|
| Storage | `DATA_DIR=/data`, `MAX_BACKUPS=10` |
| Qdrant | `QDRANT_URL=http://qdrant:6333`, `QDRANT_COLLECTION=memories`, `QDRANT_WAIT=true`, `QDRANT_WRITE_ORDERING=strong`, `QDRANT_READ_CONSISTENCY=majority`, `QDRANT_REPLICATION_FACTOR`, `QDRANT_WRITE_CONSISTENCY_FACTOR`, `QDRANT_API_KEY` (empty) |
| Embedder | `EMBED_PROVIDER=onnx`, `MODEL_NAME=all-MiniLM-L6-v2`, `MODEL_CACHE_DIR`, `PRELOADED_MODEL_CACHE_DIR`. Empty: `EMBED_MODEL`, `EMBED_BASE_URL`, `EMBED_DIMENSION`, `EMBED_COLLECTION`, `EMBED_QUERY_PREFIX`, `EMBED_DOC_PREFIX`, `EMBED_ALLOW_SPACE_REBIND`, `EMBED_API_KEY` |
| Embedder reload | `EMBEDDER_AUTO_RELOAD_ENABLED=true`, and the eight other `EMBEDDER_AUTO_RELOAD_*` limits at the compose defaults |
| Extraction | `EXTRACT_PROVIDER=anthropic`, `EXTRACT_MODEL` (empty, so the default `claude-haiku-5-5`), `EXTRACT_MAX_INFLIGHT=1`, `EXTRACT_FALLBACK_ADD=true`, the four other `EXTRACT_FALLBACK_*` limits, `EXTRACT_TRAINING_DATA_DIR=/data/training-pairs` |
| Anthropic | `ANTHROPIC_API_KEY` (set), `ANTHROPIC_OAUTH_TOKEN` (set), `ANTHROPIC_WORKSPACE_ID` (set), `ANTHROPIC_AUTH` (empty) |
| Other providers | `OLLAMA_URL` (empty), `OPENAI_API_KEY` (empty), `CHATGPT_CLIENT_ID` (empty), `CHATGPT_REFRESH_TOKEN` (empty), `TYPESAFE_API_KEY` (set) |
| Shadow runs | `SHADOW_PROVIDERS` (empty), `JEV_SHADOW_FLOWS=all`, `SHADOW_OMLX_URL` (empty), `SHADOW_OMLX_API_KEY` (empty), `SHADOW_LOG_DIR`, `RERANK_SHADOW_ENABLED=false`, `RERANK_SHADOW_SAMPLE_RATE` |
| Operations | `API_KEY` (set), `USAGE_TRACKING=true`, `MAINTENANCE_ENABLED=true`, `AUDIT_LOG=false` |
| Allocator | `MALLOC_ARENA_MAX`, `MALLOC_TRIM_THRESHOLD_`, `MALLOC_MMAP_THRESHOLD_` |

The `.env` file also sets `MEMORIES_IMAGE_TARGET` (image tag `memories:extract`)
and `MEMORIES_MEM_LIMIT`. The container memory limit is 4 GiB.

Notes for the Rust server:

- Both Anthropic credentials are set and `ANTHROPIC_AUTH` is empty. In this
  state, v5.19.0 uses `ANTHROPIC_API_KEY` (`llm_provider.py`). The Rust server
  must do the same.
- The default extraction model depends on the key type. A key that starts with
  `sk-ant-oat01-` gets `claude-haiku-4-5-20251001`. Other keys get
  `claude-haiku-5-5`. The production `ANTHROPIC_API_KEY` is not of the
  `sk-ant-oat01-` type, so production uses `claude-haiku-5-5`.
- `TYPESAFE_API_KEY` is set, but no Python code in v5.19.0 reads it.
- `USAGE_TRACKING=true` and `MAINTENANCE_ENABLED=true` are on. The Rust server
  must write `usage.db` and run the maintenance jobs.
- `EXTRACT_TRAINING_DATA_DIR` is on. The directory holds 790 MB.

## Qdrant

- Version 1.15.4. One collection: `memories`. Status `green`.
- Vectors: size 384, distance `Cosine`, one unnamed vector.
- `shard_number` 1, `replication_factor` 1, `write_consistency_factor` 1,
  `on_disk_payload` true.
- HNSW: `m` 16, `ef_construct` 100, `full_scan_threshold` 10000, `on_disk` false.
- Optimizer: `indexing_threshold` 10000, `deleted_threshold` 0.2,
  `flush_interval_sec` 5. No quantization. Strict mode off.
- Payload indexes: `source` (keyword), `document_at` (keyword), `is_latest`
  (bool), `archived` (bool).
- Points: 91,052 at 07:21 UTC. Segments: 2.
- Storage on disk: 245 MB in `/opt/dk-services/data/qdrant`.

## Data files

Directory: `/opt/dk-services/data/memories`.

| File | Size | Note |
|---|---|---|
| `metadata.json` | 76.9 MB | 91,052 rows, all IDs unique. Highest ID: 117,374. |
| `usage.db` | 250.0 MB | Plus a WAL file of 4.1 MB. |
| `keys.db` | 20 KB | |
| `audit.db` | 0 B | `AUDIT_LOG=false`. |
| `config.json` | 302 B | `storage_backend=qdrant`, `embed_provider=onnx`, `dimension=384`. |
| `embedding_spaces.json` | 155 B | `memories`: `onnx:all-MiniLM-L6-v2:384d`. |
| `backups/` | 2.1 GB | |
| `training-pairs/` | 790 MB | |
| `model-cache/` | 158 MB | |
| `shadow-logs/` | 63 MB | |

At 07:21 UTC, the ID sets of `metadata.json` and of Qdrant were equal
(91,052 each). Two counts at 07:19 UTC gave 91,047 points against 91,046 rows.
This difference is normal during writes: Qdrant takes a write at once, and
`metadata.json` saves after `MEMORIES_PERSIST_DELAY_SEC` (default 2 seconds).
A count check must allow for this delay.

Three old `metadata.json.*.tmp` files (2026-06-29 and 2026-09-24) are known
leftovers. The disk of the droplet is 88% full (20 GB free).

## Process memory

| Container | Memory |
|---|---|
| `memories` | RSS 1.37 GB at 07:19 UTC and 1.20 GB at 07:20 UTC. Peak (VmHWM) 1.48 GB. Limit 4 GiB. |
| `qdrant` | 168 MB |
| `remote-mcp` | 132 MB |

The `memories` values are from the first 13 minutes after a start, with
extraction jobs in the queue. The steady value from 2026-09-25 is about 0.9 GB.

## Request counts

The server keeps no request count for each route, and the reverse proxy
(`cloud-caddy`) keeps no access log. The uvicorn log of the container
starts again at each container start. The Cloudflare token of the cloud session
cannot read zone analytics. This document gives two sources instead.

### Operations in `usage.db` (`api_events`)

These are operations, not routes. One operation can come from more than one
route. A row can count more than one event (column `count`). The table holds
382,578 rows from 2026-02-20. The sum of `count` is 385,827. The all-time
column below is from 07:19 UTC, so it is a little lower.

| Operation | Last 30 days | Per day | All time |
|---|---|---|---|
| `search` | 70,573 | about 2,350 | 312,302 |
| `extract` | 12,523 | about 417 | 64,949 |
| `add` | 224 | about 7 | 4,898 |
| `get` | 107 | about 4 | 2,676 |
| `supersede` | 8 | | 38 |
| `is_novel` | 2 | | 327 |
| `delete` | 1 | | 562 |
| `search_evidence` | 0 | | 19 |

Clients in the last 30 days: no client name 79,161, `codex` 4,011,
`claude-web` 161, `mcp` 105.

Extraction tokens in the last 30 days, all `anthropic` and `claude-haiku-5-5`:

| Stage | Calls | Input tokens | Output tokens |
|---|---|---|---|
| `extract` | 11,086 | 19,159,692 | 3,845,096 |
| `audn` | 6,212 | 21,721,842 | 4,938,356 |

All time, `ollama` has 18 rows (2026-03-27 only). All other rows are
`anthropic`.

### Routes in the uvicorn log of the container since the start (07:07:55 to 07:20:41 UTC)

| Route | Status | Count |
|---|---|---|
| `POST /search` | 200 | 71 |
| `POST /memory/extract` | 202 | 57 |
| `GET /health` | 200 | 38 |
| `POST /search/batch` | 200 | 7 |
| `POST /memory/add` | 200 | 2 |

This sample is short. The capture middleware gives the full counts for each
route (see "Open work").

## Open work

- The capture middleware for 7 days is not on yet. It needs the parity kit of
  P0-1 in the Python server and one deploy to production. That deploy needs a
  new approval from dk, and it must follow the droplet deploy rules. When the
  capture ends, add the route counts to this document.
- The capture directory on the droplet does not exist yet, for the same reason.
