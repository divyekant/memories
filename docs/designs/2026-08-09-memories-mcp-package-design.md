# memories-mcp: Agent-Agnostic npm Package — Design

**Date:** 2026-08-09
**Status:** Approved (brainstorm w/ dk)
**Supersedes:** repo-checkout install flow (`integrations/claude-code/install.sh`), symlinked CC plugin, repo-local Codex plugin duplication

## Problem

Memories currently ships client integrations from three places — `plugin/` (Claude Code plugin, installed via dk-marketplace symlink), `plugins/memories/` (repo-local Codex plugin), and `integrations/` (install.sh + per-client hook copies). The hook sets in `plugin/hooks` and `integrations/claude-code/hooks` are byte-identical duplicates today, held in sync only by discipline. Installation requires a git checkout and a bash installer. A fresh user cannot get from zero to a working memory system without cloning the repo.

Prior decisions this design implements:
- MCP-server-once, agent-agnostic architecture; per-client plugin sprawl rejected (memory id 37911). The one exception stands: auto-recall-on-every-turn requires client hooks, so hooks remain per-client assets.
- MCP server deploys via npm/Docker; hooks/skills/CLAUDE.md are the per-client layer (id 16140, 16156).
- Interop targets requiring separate validation: Claude Code, Codex, generic MCP clients (id 28912).

## Goal

One npm package, `memories-mcp` (name verified available), that is the entire client-side distributable:

```
npx memories-mcp init
```

detects installed agents (Claude Code, Codex, Cursor, generic MCP), wires each one, health-checks the backend, and offers Docker bootstrap if the backend is down. No repo checkout. v1 targets: **Claude Code, Codex, Cursor + generic MCP**. OpenCode and OpenClaw stay on the existing `integrations/` path, untouched, out of scope.

## Non-negotiable constraint: nothing breaks

- Existing installs are safe by construction: install.sh **copies** hooks into `~/.claude` / client dirs — moving repo files does not affect deployed hooks.
- The dk-marketplace CC plugin entry points at `plugin/`. Rollout updates the marketplace `source` to the new path; a compatibility symlink `plugin → mcp-server/assets/claude-code` remains in-repo for one release so nothing that references the old path breaks.
- `install.sh` is **deprecated, not deleted**, in this release: it keeps working and prints a pointer to the npx flow. Deletion happens a release later.
- The MCP server (`mcp-server/index.js`) is unchanged; only packaging around it changes.
- Python backend, eval suite, and webui: untouched.

## Package layout

`mcp-server/` is already the npm package `memories-mcp@5.7.2`; it becomes the package root:

```
mcp-server/
  index.js                    # MCP server — unchanged
  cli/
    index.mjs                 # entry: init | doctor | update | uninstall
    detect.mjs                # agent detection (see below)
    backend.mjs               # /health check + docker compose bootstrap
    adapters/
      claude-code.mjs
      codex.mjs
      cursor.mjs
      generic.mjs
  assets/
    claude-code/              # hooks/ skills/ CLAUDE.md   ← moved from plugin/
    codex/                    # hooks + memory-codex-notify.sh ← from integrations/codex
    cursor/                   # hook set                    ← from integrations (cursor subset)
    backend/
      docker-compose.standalone.yml   ← from plugin/assets
  package.json                # bins: "memories-mcp" → index.js, "memories" → cli/index.mjs
```

Single copy of every hook/skill. `plugin/hooks`, `integrations/claude-code/hooks`, and `plugins/memories/skills` duplicates are collapsed into `mcp-server/assets/`.

## CLI behavior

### `init`
1. Detect agents: Claude Code (`~/.claude` + `claude` on PATH), Codex (`~/.codex/config.toml`), Cursor (`~/.cursor`), else generic.
2. Copy hook/skill assets to the **existing** stable destinations install.sh already uses — `~/.claude/hooks/memory`, `~/.codex/hooks/memory`, skills under `~/.claude/skills/` — and runtime config at `~/.config/memories/` (env, docker-compose). No new `~/.memories/` directory: reusing the current paths means existing installs are updated in place, not duplicated. (npx cache is ephemeral; that's why assets are copied out rather than referenced.)
3. Run each detected adapter's `install()`. `--claude/--codex/--cursor/--generic` flags override detection; `--dry-run` supported (parity with install.sh).
4. Backend: `GET <MEMORIES_URL>/health`. On failure, offer `docker compose -f <bundled compose> up -d` (OrbStack/Docker Desktop), then re-check. Decline path prints exact manual steps.
5. Idempotent: re-running `init` refreshes assets and configs in place (this is also what `update` does after an `npm cache`-busted `npx memories-mcp@latest init`).

### `doctor`
Read-only: per-agent install status (adapter `status()`), backend health, version of deployed assets vs package version.

### `uninstall`
Reverses `install()` per adapter (removes hook entries, MCP registrations, copied assets). Mirrors install.sh `--uninstall` semantics.

### `update`
Alias for `init` with a "refreshed" summary; exists so docs can say `npx memories-mcp@latest update`.

## Adapters

Each adapter is one module exporting `install(ctx)`, `uninstall(ctx)`, `status(ctx)`; `ctx` carries resolved paths, backend URL, API key, dry-run flag. Behavior is a Node port of the corresponding install.sh section — same settings keys, same hook events, same READONLY_MCP_TOOLS allowlist.

- **claude-code**: hooks into `~/.claude/settings.json`, hook scripts + skills + CLAUDE.md rules from assets, MCP registration.
- **codex**: `config.toml` MCP entry, notify script, settings hooks (markers preserved: "Memories Codex …" so existing installs are recognized and updated, not duplicated).
- **cursor**: hook set + `~/.cursor/mcp.json` entry.
- **generic**: prints the MCP config snippet (command: `npx`, args: `["-y", "memories-mcp"]`) — zero-install for any MCP client.

MCP server registration never references local files: all clients launch `npx -y memories-mcp`. Only hooks need the `~/.memories` copies.

## Versioning & publish

- Package version stays locked to the repo version (apollo manages bumps; release grep already checks all files for stale versions).
- Publish from GitHub Actions only, OIDC trusted publishing with `--provenance`; npm 2FA `auth-and-writes`. Never publish locally (projects-level supply-chain policy).
- `files` allowlist in package.json so the tarball ships only `index.js`, `cli/`, `assets/`, licenses — no smoke tests, no node_modules extras.

## Testing (TDD, per project convention)

- `node:test` suites under `mcp-server/test/`: detection against fixture home-dirs; each adapter's install/uninstall/status against temp dirs (assert exact JSON/TOML mutations); CLI arg parsing; idempotency (double-init produces identical state).
- Existing `smoke.mjs` / `smoke-write.mjs` / `smoke-redirect.mjs` continue covering the server.
- Backend bootstrap tested with compose mocked (no Docker in CI); one manual verification on the Mac Mini before release.
- Python pytest suite must stay green (it doesn't touch these paths, but it's the pre-commit gate).

## Migration / rollout (order matters)

1. Build package (assets moved, CLI + adapters + tests) on a feature branch.
2. Compatibility symlink `plugin → mcp-server/assets/claude-code`; update dk-marketplace `source` path.
3. Deprecation banner in install.sh; README/GETTING_STARTED/INSTALL repointed to npx flow.
4. Remove `plugins/memories/skills` duplicates (Codex repo-local plugin manifest repointed at canonical skills or absorbed).
5. Release via apollo; publish workflow added; first publish `memories-mcp@<repo version>`.
6. Next release (not this one): delete install.sh + compatibility symlink.

## Out of scope (v1)

- OpenCode / OpenClaw adapters (existing paths remain).
- MCP registry listing, package split (`memories-cli`) — easy later from this shape.
- Any backend/server behavior change.
- Windows support for hook installation (hooks are bash; CLI states this plainly on win32).
