# Rust server: workspace, crates, toolchain and image

Date: 2026-11-16
Status: adopted (task P0-4 of the Rust rewrite plan)

## Decision

The Rust server is a cargo workspace in `server/` with six crates:
`memories-types`, `memories-embed`, `memories-store`, `memories-engine`,
`memories-extract` and `memories-server`. The plan doc gives the content of
each crate.

Each version below is the newest stable release that was 2 days old or more on
2026-11-16. The versions are in `[workspace.dependencies]` of
`server/Cargo.toml`. A crate goes into `Cargo.lock` only when a member crate
uses it. On this date, only `memories-server` uses crates (axum, tokio, clap,
serde_json, tracing).

| Crate | Version | Use | Note |
|---|---|---|---|
| axum | 0.8.9 | HTTP routes | |
| tokio | 1.54.1 | Async runtime | |
| tower | 0.5.3 | Middleware | |
| tower-http | 0.7.1 | CORS, static files, trace | |
| qdrant-client | 1.20.0 | Qdrant store | Must work with Qdrant 1.15.4 (P0-2, P1-3 check this). |
| ort | =2.0.0-rc.15 | ONNX embedding | No stable 2.x release exists. Exact pin, because cargo does not move between pre-releases safely. Feature `load-dynamic`: the library comes from the image, not from a build-time download. |
| tokenizers | 0.23.2 | Tokens for the embedder | 1.0.0-rc.4 is a pre-release. |
| rusqlite | 0.42.0 (bundled) | keys.db, usage.db, audit.db, state.db | 0.43.0 was less than 2 days old. |
| reqwest | 0.13.5 (rustls) | LLM providers, webhooks | |
| serde | 1.0.229 | Serialization | |
| serde_json | 1.0.154 | JSON | |
| tracing | 0.1.44 | Logs | |
| tracing-subscriber | 0.3.23 | Log output | |
| clap | 4.6.7 | Commands serve, migrate, verify, export-legacy | |
| thiserror | 2.0.21 | Error types | |
| anyhow | 1.0.104 | Errors in the binary | |
| sha2 | 0.11.1 | API key hashes | |

Other pins:

| Item | Version | Pin |
|---|---|---|
| Rust toolchain | 1.100.0 (released 2026-11-12) | `server/rust-toolchain.toml` |
| ONNX Runtime | 1.31.0, the version that ort 2.0.0-rc.15 targets | SHA-256 of the Microsoft release archive for x64 and aarch64, in `server/Dockerfile` |
| Build image | `rust:1.100.0-slim-trixie` | Digest in `server/Dockerfile` |
| Runtime image | `debian:trixie-slim` | Digest in `server/Dockerfile` |

## Supply-chain rules

- `server/Cargo.lock` is committed. Each build and each CI step uses `--locked`.
- `cargo deny` (`server/deny.toml`) checks advisories, yanked crates,
  licenses and sources. Only crates.io is a permitted source.
- `server/scripts/check_crate_age.py` fails CI if a crate in `Cargo.lock` was
  published less than 2 days ago. The script reads `pubtime` from the
  crates.io sparse index.
- Dependabot (`.github/dependabot.yml`) opens a grouped weekly PR into
  `rust-rewrite` with a cooldown of 2 days. Dependabot reads the file from the
  default branch only, so the updates start after this file reaches the
  default branch.

## Image

- `server/Dockerfile` builds on the native architecture. CI builds and tests
  linux/amd64 on `ubuntu-latest` and linux/arm64 on `ubuntu-24.04-arm`.
- Port 8000. `DATA_DIR=/data`. The user `memories` has UID and GID 999, the
  same as in the Python image, so an existing `/data` volume stays writable.
- The compose files of current installs run `python -c "...urlopen(...)..."`
  for the health check. The image links `python` and `python3` to
  `memories-server`. If the program name starts with `python` and the
  arguments are `-c <code>` with `urlopen(` in the code, the binary does an
  HTTP GET of the first `http://` URL in the code (default
  `http://localhost:8000/health`). It exits 0 for a status from 200 to 399,
  else 1. Each other `python` call prints a message and exits 127, so that a
  script call does not give a false success.
- `ORT_DYLIB_PATH` points to the ONNX Runtime library in the image.

## Consequences

- The workspace declares crates that no member uses yet. Cargo 1.100 prints
  an "unused workspace dependency" warning for each of them. The age check
  and `cargo deny` do not check them before a member uses them. On
  2026-11-16, a full resolve took `rand 0.10.4`, which was less than 2 days
  old. If the age check fails, pin the crate with
  `cargo update -p <crate> --precise <older version>`.
- Dependabot updates only the crates. The base image digests, the ONNX
  Runtime hash and `rust-toolchain.toml` have no automatic update. The
  GitHub Actions are pinned by commit SHA, as task P0-5 set up. Update them by hand, with the same 2-day rule.
- `docker-compose.eval.yml` checks `/health/ready`. The Rust image is not
  healthy with that file until task P1-1 adds the route.

- The workspace version is 6.0.0. The release bump of task P7-2 must also
  change `server/Cargo.toml`.
- A task that adds a crate to a member crate also adds the crate to
  `Cargo.lock`. The age check and `cargo deny` then check the crate.
