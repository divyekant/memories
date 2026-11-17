# Rust server configuration (v6.0.0)

The Rust server reads the environment variables of the Python server v5.19.0,
with the same names and the same defaults. The source of truth is `VARS` in
`server/crates/memories-types/src/config.rs`. If this page and the code
disagree, the code is correct.

Each variable has one of three states:

| State | Effect at start |
|---|---|
| Supported | The server uses the value. |
| Not used | The server starts. If the variable is set, the server logs `<NAME> is set, but this server version does not use it`. |
| Refused value | The server stops with a message. This applies to a value that the Rust server cannot serve, for example `EMBED_PROVIDER=openai`. |

## Supported

| Group | Variables | Notes |
|---|---|---|
| Server | `PORT`, `DATA_DIR`, `FORWARDED_ALLOW_IPS`, `MAX_BACKUPS` | `PORT` default 8000. `--port` has priority. |
| Auth | `API_KEY`, `AUTH_RATE_LIMIT` | Managed keys stay in `$DATA_DIR/keys.db`. |
| Qdrant | `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `QDRANT_WAIT`, `QDRANT_WRITE_ORDERING`, `QDRANT_READ_CONSISTENCY`, `QDRANT_REPLICATION_FACTOR`, `QDRANT_WRITE_CONSISTENCY_FACTOR` | The server writes to `<QDRANT_COLLECTION>_v2`. Without `QDRANT_URL`, the memory routes (search, reads and writes) give HTTP 503. |
| Embedder | `EMBED_PROVIDER`, `EMBED_MODEL`, `MODEL_NAME`, `EMBED_DIMENSION`, `EMBED_QUERY_PREFIX`, `EMBED_DOC_PREFIX`, `MODEL_CACHE_DIR`, `PRELOADED_MODEL_CACHE_DIR` | `EMBED_PROVIDER` accepts only `onnx`. Any other value stops the start. |
| Extraction | `EXTRACT_PROVIDER`, `EXTRACT_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_OAUTH_TOKEN`, `ANTHROPIC_AUTH`, `ANTHROPIC_WORKSPACE_ID`, `OPENAI_API_KEY`, `OLLAMA_URL`, `OMLX_URL`, `OMLX_API_KEY` | Providers: `anthropic`, `openai`, `ollama`, `omlx`. `chatgpt-subscription` stops the start. |
| Extraction tuning | `EXTRACT_MAX_INFLIGHT`, `EXTRACT_QUEUE_MAX`, `EXTRACT_JOBS_MAX`, `EXTRACT_JOB_RETENTION_SEC`, `MAX_EXTRACT_MESSAGE_CHARS`, `EXTRACT_MAX_FACT_CHARS`, `EXTRACT_MAX_LINKS`, `EXTRACT_MIN_LINK_SCORE`, `EXTRACT_NOVELTY_GATE`, `EXTRACT_NOVELTY_THRESHOLD`, `EXTRACT_REDACT_SECRETS`, `EXTRACT_SIMILAR_PER_FACT`, `EXTRACT_SIMILAR_TEXT_CHARS`, `EXTRACT_FOREIGN_TARGET_ADD` | The job queue is on disk. `EXTRACT_FOREIGN_TARGET_ADD` (default `true`) is new in v6 and is not in `VARS`: it adds a fact as a new memory when the AUDN target belongs to another source. |
| Extraction fallback | `EXTRACT_FALLBACK_ADD`, `EXTRACT_FALLBACK_MAX_FACTS`, `EXTRACT_FALLBACK_MIN_FACT_CHARS`, `EXTRACT_FALLBACK_MAX_FACT_CHARS`, `EXTRACT_FALLBACK_NOVELTY_THRESHOLD` | |
| Writes | `DOCTRINE_IDENTICAL_THRESHOLD` | A value that is not a number stops the start. |
| Search graph | `SEARCH_GRAPH_MAX_NEIGHBORS`, `SEARCH_GRAPH_RESERVED_SLOTS`, `SEARCH_PPR_ALPHA`, `SEARCH_PPR_MAX_ITERS`, `SEARCH_PPR_MIN_RELATIVE` | |
| Maintenance | `MAINTENANCE_ENABLED`, `MAINTENANCE_CONFLICT_DRAIN`, `MAINTENANCE_CONFLICT_MAX` | The scheduler also needs an extraction provider. |
| Promotion | `PROJECT_PROMOTION_MODE`, `PROJECT_PROMOTION_REJECTED_RETENTION_DAYS` | Only the `off` mode. `shadow` or `auto` stops the start. |
| Metrics and logs | `METRICS_LATENCY_SAMPLES`, `METRICS_TREND_SAMPLES`, `USAGE_TRACKING`, `AUDIT_LOG` | `AUDIT_LOG=true` opens `$DATA_DIR/audit.db`. |

The image also sets `UI_DIR` (default `/app/webui`) and `ORT_DYLIB_PATH` (the
ONNX Runtime library). Do not change these two in the image.

## Not used

The server starts with these variables, but it does not use them. Remove them
from the compose files and `.env` of an install.

| Variables | Reason |
|---|---|
| `EXTRACT_TRAINING_DATA_DIR` | The Rust server does not write training pairs. While an install runs Rust, no new pairs are collected. |
| `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_COLLECTION`, `EMBED_ALLOW_SPACE_REBIND` | No OpenAI embedder (`docs/decisions/2026-11-16-rust-sync-off.md`). |
| `CLOUD_SYNC_*` (7 variables) | No cloud sync. The `/sync` routes answer "Cloud sync not configured". |
| `CHATGPT_REFRESH_TOKEN`, `CHATGPT_CLIENT_ID` | No `chatgpt-subscription` provider. |
| `EMBEDDER_AUTO_RELOAD_*` (9 variables) | The Rust embedder does not grow, so it needs no reload. `GET /metrics` shows the values for the clients. |
| `MEMORY_TRIM_ENABLED`, `MEMORY_TRIM_COOLDOWN_SEC`, `MEMORY_TRIM_PERIODIC_SEC` | Python garbage collector control. |
| `MEMORIES_PERSIST_DELAY_SEC` | The Rust server writes each change to Qdrant at once. It has no `metadata.json` save. |
| `SHADOW_PROVIDERS`, `SHADOW_LOG_DIR`, `SHADOW_OLLAMA_URL`, `SHADOW_OMLX_URL`, `SHADOW_OMLX_API_KEY` | No shadow extraction. |
| The other `PROJECT_PROMOTION_*` variables | Used only in the `shadow` and `auto` modes. |
| `EXTRACT_MAX_FACTS` | The extraction profile always gives `max_facts` (`docs/decisions/2026-11-16-rust-extract-pipeline.md`). |
| `SEARCH_GRAPH_DECAY`, `SEARCH_GRAPH_SEED_K` | The Rust graph search does not read them. If an install sets them, its graph search can rank differently on Rust. |
| `WORKSPACE_DIR`, `STORAGE_BACKEND`, `PARITY_CAPTURE_DIR`, `PARITY_CAPTURE_MAX_MB` | Python only. |
| `RSS_KB_THRESHOLD` | Not in the list. The Rust server ignores it with no log line. |

## Allocator variables

These are glibc settings, not server settings.

| Variable | Rust recommendation |
|---|---|
| `MALLOC_MMAP_THRESHOLD_`, `MALLOC_TRIM_THRESHOLD_` | Do not set. In gate G5, they made the search p95 40% to 100% slower and saved about 60 MB. |
| `MALLOC_ARENA_MAX=2` | Optional. The compose files and production keep it. It saves about 50 MB of peak memory. |

## Health check

- `memories-server healthcheck [URL]` gives exit code 0 if the URL (default
  `http://localhost:8000/health`) answers with a status from 200 to 399.
  The image `HEALTHCHECK` and the repository compose files use it.
- The image links `python` and `python3` to the binary. A call of the form
  `python -c "...urlopen('<url>')..."` runs the same check, so compose files of
  v5 installs keep working. Each other `python` call fails with exit code 127.
- The bundled standalone compose file
  (`mcp-server/assets/backend/docker-compose.standalone.yml`) keeps the
  `python -c` form, because it also starts a pinned v5 image.
