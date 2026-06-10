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

- Bump every version string (the seven: `pyproject.toml`,
  `plugin/package.json`, `mcp-server/package.json` + lockfile,
  `plugin/assets/BACKEND_VERSION`, `app.py` (FastAPI + /health),
  `tests/test_api_contract_compat.py`, `plugins/memories/.codex-plugin/plugin.json`)
  and grep the old version project-wide before committing.
- Retitle CHANGELOG `[Unreleased]` to the version + date.
- Full suite green → `chore: release vX.Y.Z` on develop → `--no-ff` merge to
  `main` → tag → push → GitHub release → deploy (compose build + up) → sync
  the installed plugin cache if hook/skill files changed.
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
