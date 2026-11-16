# Memories Rust server

This folder holds the Rust server that replaces the Python server of
Memories. The server has the same HTTP API. The work is in progress. Do not
use this server in production before release v6.0.0.

## Requirements

- Rust toolchain 1.100.0. `rustup` installs the toolchain from
  `rust-toolchain.toml`.
- Docker, for the image.
- Python 3.9 or newer, for the scripts in `scripts/`.

## Build and test

Run these commands in the `server/` folder:

```bash
cargo fmt --all --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo deny --locked check
python3 -m unittest discover -s scripts -v
python3 scripts/check_crate_age.py Cargo.lock --days 2
```

The embedding parity test (gate G2) needs ONNX Runtime 1.31.0. It downloads
the model if the model is not in the Hugging Face cache:

```bash
ORT_DYLIB_PATH=/path/to/libonnxruntime.so \
  cargo test --locked -p memories-embed --test parity -- --ignored --nocapture
```

The record store tests (task P1-3) need a Qdrant server, version 1.15.4 or
newer. The tests make and delete their own collections:

```bash
docker run -d -p 6333:6333 -p 6334:6334 qdrant/qdrant:v1.15.4
QDRANT_TEST_URL=http://127.0.0.1:6333 \
  cargo test --locked -p memories-store --test qdrant_store -- --ignored
```

The migration tests (task P1-4) use the same Qdrant server:

```bash
QDRANT_TEST_URL=http://127.0.0.1:6333 \
  cargo test --locked -p memories-server --test migrate -- --ignored
```

## Run the server

```bash
cargo run --locked --bin memories-server -- serve --port 8000
curl http://localhost:8000/health
```

The server reads the port from `--port` or from the environment variable
`PORT`. The default is 8000.

## Migrate the data of the Python server

The server reads the records of the Python server from
`$DATA_DIR/metadata.json` and writes them to the collection
`<QDRANT_COLLECTION>_v2`. It never changes `metadata.json`, `config.json` or
the old collection.

- At the first start, `serve` runs the migration if `metadata.json` exists and
  no migration has finished. During the migration, only `GET /health`
  answers. The other routes give HTTP 503.
- If the migration or the check fails, the server stops with a message.
- If `metadata.json` changed after the migration, the server does not start.
  The message names the command `memories-server migrate --merge`. See
  "Return to the Python server".

To run the steps by hand:

```bash
memories-server migrate            # copy the records, then verify
memories-server migrate --shadow   # also replace changed and delete removed records
memories-server verify --sample 1000
```

Each command writes a JSON report. `verify` gives exit code 1 if a count, a
field or a sampled vector is different. Keep `state.db` on the data volume:
it records the finished migration.

## Return to the Python server

Two commands help an install that goes back to the Python server. The rules
are in `docs/decisions/2026-11-16-rust-rollback.md`.

CAUTION: Stop the Rust server and the Python server before these commands.
The commands take the lock of `DATA_DIR`, but they cannot see a Python server.

```bash
memories-server export-legacy      # write memories_v2 back to metadata.json and the old collection
memories-server migrate --merge    # bring the writes of a Python server into memories_v2
```

- `export-legacy` exports only when `memories_v2` is the canonical store. If
  a Python server changed `metadata.json`, the command skips the export.
- `export-legacy` moves each waiting extract job to
  `$DATA_DIR/rollback-extract-jobs.ndjson`. After the Python server starts,
  send the jobs with
  `MEMORIES_API_KEY=<admin key> scripts/resend_extract_jobs.py <file> --base-url <url>`.
- The rollback image (`rollback/Dockerfile`) runs `export-legacy` at each
  start, then the Python server.

To build the rollback image, run these commands from the repository root:

```bash
docker build --target core -t memories-python .
docker build -f server/rollback/Dockerfile --build-arg BASE_IMAGE=memories-python -t memories-rollback .
```

The rollback drill (task P7-1) runs the two cases of a return:

```bash
QDRANT_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=<path to libonnxruntime.so> \
  server/scripts/rollback_drill.sh server/target/debug/memories-server
```

## Configuration

The server reads the environment variables of the Python server, with the
same names and the same defaults. `crates/memories-types/src/config.rs` lists
each variable with one of these states:

| State | Effect at start |
|---|---|
| Supported | The server uses the value. |
| Not used | The server starts. If the variable is set, the server logs one line. |
| Refused | The server stops with a message. This applies to a value that the Python server also cannot start with, for example `AUTH_RATE_LIMIT=abc`. |

## Auth

- Send the key in the header `X-API-Key`. An empty header is the same as no
  header.
- `API_KEY` is the admin key. If `API_KEY` is empty, a request with no key
  has admin access.
- Managed keys are in `$DATA_DIR/keys.db`, the same file and schema as the
  Python server. The server stores the SHA-256 hash of each key.
- The roles are `admin`, `read-write` and `read-only`. A prefix such as
  `codex/` matches `codex` and `codex/...`, but not `codex-other`.
- After `AUTH_RATE_LIMIT` (default 50) failed attempts in 60 s, a client gets
  HTTP 429. The client address follows uvicorn: a request from an address in
  `FORWARDED_ALLOW_IPS` (default `127.0.0.1`) uses `X-Forwarded-For`.
- The server writes `last_used_at` and `usage_count` of the managed keys
  every 5 s and at shutdown, not at each request. `GET /api/keys` writes the
  pending values first, so the list shows each use.
- An admin key manages the keys with `POST /api/keys`, `GET /api/keys`,
  `PATCH /api/keys/{key_id}` and `DELETE /api/keys/{key_id}`. A key that one
  server makes works on the other server.
- If `AUDIT_LOG=true`, the server opens `$DATA_DIR/audit.db`, the same file
  and schema as the Python server. `GET /audit` reads the rows and
  `POST /audit/purge` deletes the old rows. If `AUDIT_LOG` is off, the two
  routes give no rows.

The core write routes (add, add-batch, upsert, upsert-batch, PATCH and
DELETE `/memory/{memory_id}`, delete-batch) need `QDRANT_URL`. Without it,
they give HTTP 503. The rules and the differences from the Python server are
in `docs/decisions/2026-11-16-rust-core-writes.md`. The bulk writes and the
relation writes (task P3-2) have the same need. Their rules are in
`docs/decisions/2026-11-16-rust-bulk-writes.md`.

A Python route that this server does not have yet gives HTTP 501. The route
table is `ROUTES` in `crates/memories-server/src/shell.rs`.

## Parity check

The scripts below replay the golden cases of a task against the Rust
server. The Python server first writes the data, then the Rust server reads
it. Run them from the repository root after `uv sync --frozen`:

```bash
server/scripts/parity_auth.sh server/target/debug/memories-server
server/scripts/parity_keys_audit.sh server/target/debug/memories-server
QDRANT_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=<path to libonnxruntime.so> \
  server/scripts/parity_writes.sh server/target/debug/memories-server
QDRANT_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=<path to libonnxruntime.so> \
  server/scripts/parity_bulk.sh server/target/debug/memories-server
```

`parity_auth.sh` is the check of task P1-1. `parity_keys_audit.sh` is the
check of task P5-2. It also makes a key on each server and uses the key on
the other server. `parity_writes.sh` is the check of task P3-1 (the core write
routes). It uses a new Qdrant collection and deletes it at the end.
`parity_bulk.sh` is the check of task P3-2. It replays the golden cases, then
sends the bulk scenario to a new Python server and to a new Rust server and
compares the replies.

The Qdrant call test of the writes needs Qdrant and ONNX Runtime:

```bash
QDRANT_TEST_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=<path to libonnxruntime.so> \
  cargo test --locked -p memories-engine --test writes -- --ignored
  cargo test --locked -p memories-engine --test bulk -- --ignored
```

## Build the image

```bash
docker build -t ghcr.io/divyekant/memories:rust-local server
```

To test the image with the bundled compose file, run this command from the
repository root:

```bash
server/scripts/compose_health_check.sh rust-local
```

The script starts `mcp-server/assets/backend/docker-compose.standalone.yml`
with the local image and no change to the file. The script passes when Docker
reports the `memories` container as healthy. The script then removes the
containers and the volumes of its own compose project.

The compose file uses the host ports 8900 and 6333. If a Memories install
runs on the same machine, stop the install before you run the script.

## Crates

| Crate | Content |
|---|---|
| `memories-types` | Record model, request and response types, configuration, errors |
| `memories-embed` | ONNX embedding |
| `memories-store` | Qdrant store, SQLite stores, ID counter, migration |
| `memories-engine` | Rank logic, search, writes, links, maintenance |
| `memories-extract` | Transcript cleanup, prompts, LLM providers, queue workers |
| `memories-server` | Routes, auth, middleware, the `memories-server` binary |

## Dependency rules

- Use only crate releases that are 2 days old or more. CI checks this rule.
- Keep `Cargo.lock` in the repository. Use `--locked` for each build.
- Record each new crate and its version in
  `docs/decisions/2026-11-16-rust-workspace-crates.md`.
