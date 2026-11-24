# Rust server: no cloud sync and no OpenAI embedder

Date: 2026-11-16
Status: adopted (task P5-7 of the Rust rewrite plan)

## Decision

The Rust server has no cloud sync and no OpenAI embedder. The production
baseline (task P0-3, `docs/rust-rewrite/baseline.md`) shows that production
uses neither: `CLOUD_SYNC_ENABLED` is not set, the image has no `boto3`, and
`EMBED_PROVIDER=onnx`. A port of `cloud_sync.py` or `openai_embedder.py` is a
task after the cutover, if a host needs it.

## Scope

The five `/sync` routes give the replies of a Python server with no cloud sync
configuration.

| Route | Reply to an admin key |
|---|---|
| `GET /sync/status` | 200 `{"enabled": false, "message": "Cloud sync not configured"}` |
| `POST /sync/upload` | 400 `Cloud sync not configured` |
| `GET /sync/snapshots` | 400 `Cloud sync not configured` |
| `POST /sync/download` | 422 for a `confirm` that is not a boolean, else 400 `Cloud sync not configured` |
| `POST /sync/restore/{backup_name}` | The same as `POST /sync/download` |

- A key that is not an admin key gets 403 `Admin key required`, after the
  `confirm` check, as in Python.
- No route writes an audit row or a usage row. Python writes none for these
  routes.
- `EMBED_PROVIDER` with a value other than `onnx` stops the start, also when
  `QDRANT_URL` is empty. The log says
  `EMBED_PROVIDER=<value> is not supported: this server has only the onnx embedder; set EMBED_PROVIDER=onnx`.
- The `CLOUD_SYNC_*` variables stay "accepted but not used": the server
  starts and logs one line for each variable that is set.

## Differences from Python

- A Python install with the `cloud` extra (`boto3`), `CLOUD_SYNC_ENABLED=true`
  and `CLOUD_SYNC_BUCKET` uploads each backup and serves the `/sync` routes.
  The Rust server starts, but does not sync. The Docker image of Python has
  no `boto3`, so a Docker install has no cloud sync in Python either.
- A Python server with cloud sync and no `metadata.json` restores the newest
  cloud backup at start. The Rust server does not.
- Python with `EMBED_PROVIDER=openai` uses the OpenAI embeddings API. The Rust
  server refuses the setting.
- An encoded slash in a path parameter (`/sync/restore/..%2Fx`): Starlette
  decodes the path before it routes and gives 404. The Rust shell routes on
  the raw path and gives the reply of the route. This difference applies to
  each route with a path parameter, not only to `/sync`.

## Exit check

`server/scripts/parity_sync.sh` sends 16 sync requests with an admin key, a
read-only key, a read-write key and a wrong key to the Python server and to
the Rust server, and compares each status and body. Then it checks that
`EMBED_PROVIDER=openai` stops the start of the Rust server.
