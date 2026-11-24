# Contributing to Memories

Thank you for your help. This file tells you how to make a change.

## Before you start

- For a large change, open an issue first. Describe the problem and the
  change that you propose.
- Read `CLAUDE.md` for the project rules and `docs/architecture.md` for the
  design.

## Make a change

1. Fork the repository and make a branch from `main`: `feat/<name>`,
   `fix/<name>` or `docs/<name>`.
2. Write a test that fails without the change. Then write the change.
3. Run the checks for the part that you changed:

   | Part | Commands |
   |---|---|
   | Rust server (`server/`) | `cargo fmt --all --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked` |
   | MCP server and CLI (`mcp-server/`) | `npm ci`, `npm test` |
   | Python tools and the v5 server | `uv sync`, `uv run pytest` |

4. If you add or change a dependency, run
   `python3 scripts/third_party_notices.py` and commit
   `THIRD_PARTY_NOTICES.md`. The license of the new package must be on the
   allow list of that script.
5. Add an entry to `CHANGELOG.md` under `[Unreleased]`.
6. Use a conventional commit message, for example `fix(search): ...`.
7. Open a pull request into `main`. CI must pass.

## Rules for the server

- A change to a route keeps the v5 reply, or records the difference in
  `parity/intended.json` and a decision record in `docs/decisions/`.
- A new environment variable goes into
  `server/crates/memories-types/src/config.rs` and
  `docs/rust-rewrite/configuration.md`.

## Rules for all changes

- Do not commit a key, a token, a password or production data.
- Write documentation in short, simple sentences. Use the active voice.

## License

When you send a contribution, you agree that it is licensed under the MIT
License of this repository (`LICENSE`).
