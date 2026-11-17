# Rust v6.0.0: release packaging

Date: 2026-11-17
Status: adopted on `rust-rewrite`. Not released.

## Decisions

1. **Version split.** The release version (6.0.0) is in `server/Cargo.toml`,
   `mcp-server/assets/backend/BACKEND_VERSION`, `mcp-server/package.json` and
   the Claude Code and Codex plugin manifests. `pyproject.toml` and `app.py`
   stay at 5.19.0, because the Python code does not change and the rollback
   image must report the v5 version. The tests check the new rule:
   `BACKEND_VERSION` equals `server/Cargo.toml`, and the Codex manifest equals
   `mcp-server/package.json`.
2. **Health check command.** `memories-server healthcheck [URL]` replaces the
   `python -c` probe in the image and in the repository compose files. The
   `python` shim stays in the image, so v5 compose files keep working. The
   standalone compose file of the npm package keeps the `python -c` probe,
   because a user can pin a v5 image tag with `MEMORIES_IMAGE_TAG`.
3. **Compose files.** `docker-compose.yml` and `docker-compose.snippet.yml`
   build `server/` with the build context `webui`. They drop the variables
   that v6 does not use and the two allocator variables that slow the Rust
   search (gate G5). They keep `MALLOC_ARENA_MAX=2`, the production setting.
   `start_period` is 120 s, for the first-start migration.
4. **Image workflow.** `publish-image.yml` builds each architecture on a
   native runner (no QEMU) with only `actions/checkout` as an action. It
   pushes `<version>-amd64` and `<version>-arm64`, then the multi-arch tag
   `<version>`. A second run with `latest` on builds nothing and points
   `latest` at the pushed digest. Thus the image that moves to `latest` is the
   image that ran on production for 30 minutes.
5. **Unused-variable log.** Six variables that the server reads were marked
   "not used" in `config.rs`, so the server logged a false line at start. They
   are now "supported".

## Not changed

- The Python code paths. Task P7-3 removes them after the rollback window.
- `release.yml`. It already makes the tag, the GitHub release and the npm
  publish. The image workflow is a separate step, so that the image goes to
  production before the npm package points users at it.
