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

- Bump every version string (the eight, current as of v5.11.0: `pyproject.toml`,
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
  `main` → tag → push → GitHub release → deploy (compose build + up) → bump the
  marketplace pin and refresh the installed plugin (both below).
- Push the tag explicitly (`git push origin vX.Y.Z`) — `--follow-tags` skips
  lightweight tags — and create the release with `--verify-tag`:

  ```bash
  git push origin vX.Y.Z
  gh release create vX.Y.Z --verify-tag --title ... --notes ...
  ```

  `--verify-tag` is what enforces the invariant. Without it, `gh release
  create` creates any missing tag from the latest state of the default branch,
  so a forgotten or failed push publishes a release pointing at the wrong
  commit. During v5.11.0 `gh` did refuse — but only because the tag existed
  locally and not on the remote, which it treats as ambiguous; that refusal is
  a side effect of one particular state, not a guarantee to rely on.
- A client-only release (hooks, skills, CLI — no `app.py` or backend change
  beyond its version string) needs no deploy. Skipping it leaves the running
  backend reporting the previous version, which is cosmetic; redeploying a
  live backend is not, so do not do it for a change that cannot affect it.
- **Bump the marketplace pin.** `dk-marketplace`'s `memories` entry pins its
  `git-subdir` source to an immutable `sha`, so the plugin does NOT track
  `main` — a release is invisible to plugin consumers until that SHA is
  updated to the new tag's commit and pushed. Deliberate: those hooks read
  session transcripts and carry the backend credential, so a fresh clone must
  never execute upstream code that changed after review. Get the commit with
  `git rev-list -n1 vX.Y.Z`.
- **Then refresh the installed plugin — the pin bump alone does nothing to it.**
  An already-installed plugin keeps running from its own cache directory until
  explicitly updated, so bumping the pin makes the release available without
  delivering it. Verified the hard way during v5.11.0: the marketplace
  advertised 5.10.0 while `~/.claude/plugins/installed_plugins.json` still
  recorded `memories@dk-marketplace` at 5.9.0 (`gitCommitSha` = v5.9.0's merge
  commit), and the hooks actually firing were a release behind — with the very
  bug the newer version fixed. Both commands are non-interactive:

  ```bash
  claude plugin marketplace update dk-marketplace
  claude plugin update memories@dk-marketplace
  ```

  Then **restart Claude Code** (the update prints "Restart to apply changes").
  Confirm by checking that `installed_plugins.json`'s `gitCommitSha` equals the
  release commit — not just that `version` moved, which can advance while the
  files on disk do not.
- **Old cache directories linger and can still execute — do not blind-prune
  them.** `claude plugin update` adds a new versioned directory rather than
  replacing the old one, and long-lived sessions keep running from whichever
  path they started with. During v5.11.0 a v5.4.0 directory from four months
  earlier was still executing hooks and emitting a bogus "Backend Update
  Available — latest is v5.7.0" banner, sourced from an
  `assets/BACKEND_VERSION` that no current version even ships (see the
  dangling-path note in `memory-recall.sh`).

  `.in_use` is **a directory of per-process lease records**, not a stale
  boolean left behind by `update`: each entry is a PID whose contents look like
  `{"pid":11409,"procStart":"..."}`. Testing `[ -e .in_use ]` therefore says
  nothing about whether anything is actually using the directory — an empty
  lease directory persists after every session that held it exits. Count live
  leases instead, and note that restarting one session does not release
  another's:

  ```bash
  for d in ~/.claude/plugins/cache/dk-marketplace/memories/*/; do
    live=0
    for pid in $(ls "$d.in_use" 2>/dev/null); do
      kill -0 "$pid" 2>/dev/null && live=$((live+1))
    done
    echo "$(basename "$d"): $live live lease(s)"
  done
  ```

  (`ls` rather than a glob: under zsh's default `nomatch`, an empty
  `.in_use/*` aborts the loop instead of yielding zero iterations.)

  Delete a non-current directory only once it reports zero live leases. At the
  time of the v5.11.0 release the v5.4.0 directory held seven live leases, all
  with `--plugin-dir` pointing into it, so removing it would have pulled hook
  and skill files out from under seven running sessions.
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
