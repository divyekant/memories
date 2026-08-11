# Release process: PR-per-item into develop, joint releases to main

Date: 2026-06-10
Status: adopted

## Decision

Stop cutting a release per work item. The flow is now:

1. **One branch + one PR per work item**, targeting `develop`. CI (pytest,
   MCP server install, hook syntax, Docker build + in-image import) gates the
   merge. Each PR carries its own `[Unreleased]` CHANGELOG entries and tests.
2. **`develop` accumulates items.** It must stay green at every merge — the
   full suite runs on every push.
3. **Joint releases promote `develop` → `main`** when a coherent set of work
   has landed (or something urgent needs to ship). One release = one version
   bump, one CHANGELOG fold ([Unreleased] → [X.Y.Z]), one tag, one GitHub
   release, one deploy.

## Mechanics of a promotion

- Bump every version string (the eight, current as of v5.10.0: `pyproject.toml`,
  `mcp-server/package.json` + lockfile, `uv.lock`,
  `mcp-server/assets/backend/BACKEND_VERSION`, `app.py` (FastAPI + /health),
  `tests/test_api_contract_compat.py`, `plugins/memories/.codex-plugin/plugin.json`,
  `mcp-server/assets/claude-code/.claude-plugin/plugin.json` (Claude Code plugin
  manifest — `dk-marketplace/sync.sh` reads its version into `marketplace.json`,
  so a stale value here silently mis-advertises the plugin), plus `README.md`'s
  "Key capabilities (vX.Y.Z)" line.
  `plugin/package.json` and `plugin/assets/BACKEND_VERSION` are GONE as of the
  v5.8.0 asset consolidation — `plugin/` is now a symlink to
  `mcp-server/assets/claude-code`.
  Grep the old version project-wide before committing, and include `*.mjs`/`*.js`
  in that grep: a hardcoded `version:` in `mcp-server/lib-tools.mjs` was missed
  for two releases because earlier sweeps only checked json/toml/py/md.
- Retitle CHANGELOG `[Unreleased]` to the version + date.
- Full suite green → `chore: release vX.Y.Z` on develop → `--no-ff` merge to
  `main` → tag → push → GitHub release → deploy (compose build + up) → sync
  the installed plugin cache if hook/skill files changed.
- **Bump the marketplace pin.** `dk-marketplace`'s `memories` entry pins its
  `git-subdir` source to an immutable `sha`, so the plugin does NOT track
  `main` — a release is invisible to plugin consumers until that SHA is
  updated to the new tag's commit and pushed. Deliberate: those hooks read
  session transcripts and carry the backend credential, so a fresh clone must
  never execute upstream code that changed after review. Get the commit with
  `git rev-list -n1 vX.Y.Z`.
- Anything user-behavior-changing ships behind an eval gate where one exists
  (active-search eval for hook behavior, tier-1 recall A/B for retrieval).

## Why

The 5.5.0 → 5.5.1 → 5.6.0 same-day sequence showed per-item releases generate
version-string churn and release overhead without user value. PRs into
develop give each item CI gating and a reviewable unit; joint releases keep
versions meaningful.

## Context

`main` is protected (no force-push/deletion, CI contexts required, admin
bypass keeps the promotion flow direct). Hotfixes for a broken release may
still go straight to a patch release — urgency beats batching.
