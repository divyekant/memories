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

## Run the server

```bash
cargo run --locked --bin memories-server -- serve --port 8000
curl http://localhost:8000/health
```

The server reads the port from `--port` or from the environment variable
`PORT`. The default is 8000.

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
