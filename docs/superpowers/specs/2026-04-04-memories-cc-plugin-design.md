# Memories CC Plugin — Design Spec

**Date:** 2026-04-04
**Status:** Approved
**PR:** TBD (branch: `feat/cc-plugin`)

## Problem

The Memories CC integration is scattered across 4 locations requiring manual installation:
- `~/.claude/hooks/memory/` — 12 hook scripts + hooks.json
- `~/.claude/CLAUDE.md` — memory behavior overrides (hand-edited into global config)
- `~/projects/memories/skills/memories/SKILL.md` — symlinked or manually placed
- `~/.config/memories/env` — hand-written config

CC plugins auto-discover hooks, skills, and CLAUDE.md from a standard directory structure. Packaging memories as a plugin eliminates manual wiring.

## Design

### Plugin Directory Structure

```
plugin/
  package.json                       # name, version, description
  CLAUDE.md                          # behavioral overrides (always loaded)
  skills/
    memories/SKILL.md                # detailed memory discipline (on-demand)
    setup/SKILL.md                   # backend provisioning (/memories:setup)
  hooks/
    hooks.json                       # all 12 hook entries
    _lib.sh                          # shared utilities (support file)
    memory-recall.sh                 # SessionStart
    memory-query.sh                  # UserPromptSubmit
    memory-extract.sh                # Stop
    memory-flush.sh                  # PreCompact
    memory-rehydrate.sh              # PostCompact
    memory-commit.sh                 # SessionEnd
    memory-subagent-recall.sh        # SubagentStart
    memory-subagent-capture.sh       # SubagentStop
    memory-tool-observe.sh           # PostToolUse (Write|Edit|Bash)
    memory-observe.sh                # PostToolUse (mcp__memories__)
    memory-guard.sh                  # PreToolUse (Write|Edit)
    memory-config-guard.sh           # ConfigChange
    response-hints.json              # pattern-based response hints (support file)
  assets/
    docker-compose.standalone.yml    # backend provisioning template
```

Note: `_lib.sh` and `response-hints.json` are support files, not hooks. The 12 hooks are the `.sh` scripts listed above.

### package.json

```json
{
  "name": "memories",
  "version": "5.1.0",
  "description": "Persistent, searchable memory for Claude Code via Memories MCP"
}
```

Version synced with the memories repo release cycle.

### Hook Path Resolution

All hooks.json command paths MUST use `${CLAUDE_PLUGIN_ROOT}` instead of `${MEMORIES_HOOKS_DIR}`:

```json
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/hooks/memory-recall.sh",
  "timeout": 5
}
```

All hook scripts MUST resolve `_lib.sh` and `response-hints.json` via `$(dirname "${BASH_SOURCE[0]}")` instead of `$(dirname "$0")` for reliable resolution when invoked through the plugin system:

```bash
_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
```

This applies to all 12 hook scripts. The `MEMORIES_HOOKS_DIR` env var becomes a legacy fallback for non-plugin installs.

### CLAUDE.md — Behavioral Overrides (always loaded)

Contains the rules that make memory non-optional:

- Search memories BEFORE answering questions about prior decisions, architecture, conventions, deferred work, past bugs, or project history
- Search memories BEFORE asking a clarifying question — the answer may already be stored
- When hooks inject `## Retrieved Memories`, read them carefully — they are curated context
- Lead with the answer in sentence one. No preamble ("Based on memories...", "Let me check...")
- Never use meta phrases: "memory confirms", "stored decision", "the remembered context says"
- Preserve boundary conditions (`until`, `unless`, `because`) verbatim
- Say `not yet`, `deferred`, or `blocked on` directly for incomplete work
- Don't reconfirm remembered decisions — if the memory says "we chose X because Y", answer as if X is current
- Use `memory_extract` or `memory_add` at natural breakpoints: decisions, deferred work, non-obvious fixes

Does NOT contain: tool-level details (source prefix conventions, AUDN, memory_is_novel), hook lifecycle, multi-backend routing — that stays in the skill.

### skills/memories/SKILL.md — Detailed Discipline (on-demand)

Existing skill, moved from `skills/memories/SKILL.md` in the repo root. Contains:
- Three Responsibilities (Read, Write, Maintain)
- Tool selection guide (memory_add vs memory_extract)
- Source prefix conventions
- Hook lifecycle reference
- Multi-backend routing
- Auto-memory hydration details

No content changes — just relocated into the plugin structure.

### skills/setup/SKILL.md — Backend Provisioning

Interactive first-run skill (`/memories:setup`). Steps:

1. Check if Docker is available (OrbStack or Docker Desktop)
2. Detect if memories service is already running (`curl localhost:8900/health`)
3. If not running:
   - Copy `docker-compose.standalone.yml` from plugin assets to `~/.config/memories/`
   - Prompt for extraction provider (Anthropic / OpenAI / Ollama / skip)
   - Write `~/.config/memories/env` with URL, API key, extraction config
   - Run `docker compose up -d`
4. Health check → confirm service is reachable
5. Configure MCP server entry if not present
6. **Ensure auto-update is enabled** — read `~/.claude/plugins/known_marketplaces.json`, find the marketplace entry that contains the memories plugin, and set `autoUpdate: true` if not already set. This guarantees all users get plugin updates automatically on CC startup regardless of how they installed.

Trigger: first SessionStart health check failure injects a warning suggesting `/memories:setup`.

### hooks/ — All 12 Hooks

Canonical source moves from `integrations/claude-code/hooks/` to `plugin/hooks/`. Includes all changes from PR #66:

| Hook | Event | Behavior |
|------|-------|----------|
| memory-recall.sh | SessionStart | Project recall + MEMORY.md hydration |
| memory-query.sh | UserPromptSubmit | Assertive injection with OVERRIDE framing |
| memory-extract.sh | Stop | Unconditional extraction (no keyword filter) |
| memory-flush.sh | PreCompact | Aggressive extraction before context loss |
| memory-rehydrate.sh | PostCompact | Re-inject memories using compact summary |
| memory-commit.sh | SessionEnd | Final extraction pass |
| memory-subagent-recall.sh | SubagentStart | Inject project memories into subagents |
| memory-subagent-capture.sh | SubagentStop | Extract from all subagent types |
| memory-tool-observe.sh | PostToolUse (Write\|Edit\|Bash) | Log tool observations to session file |
| memory-observe.sh | PostToolUse (mcp__memories__) | Observability for memory tool calls |
| memory-guard.sh | PreToolUse (Write\|Edit) | Block direct MEMORY.md writes |
| memory-config-guard.sh | ConfigChange | Warn if memory hooks removed |

### Config: ~/.config/memories/env

No change. Single config location sourced by all hooks:

```bash
MEMORIES_URL=http://localhost:8900
MEMORIES_API_KEY=
MEMORIES_EXTRACT_PROVIDER=anthropic
MEMORIES_EXTRACT_MODEL=claude-haiku-4-5-20251001
```

### What Moves

| From | To | Action |
|------|----|--------|
| `integrations/claude-code/hooks/*` | `plugin/hooks/*` | Move (canonical source becomes plugin) |
| `skills/memories/SKILL.md` | `plugin/skills/memories/SKILL.md` | Move |
| Memory section in `~/.claude/CLAUDE.md` | `plugin/CLAUDE.md` | Extract into plugin, remove from global |
| `integrations/claude-code/install.sh` | Deprecated | Plugin auto-discovery replaces manual install |

### What Gets Cherry-Picked from PR #65

- `docker-compose.standalone.yml` → `plugin/assets/`
- `.github/workflows/docker-publish.yml` → stays at repo root (GHCR publishing)
- Nothing else — npm package approach is replaced by plugin

### Distribution

Published to dk-marketplace. Requires adding a `memories` entry to the marketplace's `.claude-plugin/marketplace.json` manifest so `claude plugins list` shows it and auto-update works.

Install via symlink:

```bash
ln -s /path/to/memories/plugin ~/.claude/plugins/marketplaces/dk-marketplace/plugins/memories
```

Or copy for standalone distribution.

### Migration from Current Setup

For existing users (us):
1. Install the plugin (symlink)
2. **Verify plugin loaded** — start a new CC session, check that SessionStart recall fires (memories appear in context). If not, stop — the plugin isn't auto-discovered correctly.
3. Remove `~/.claude/hooks/memory/` directory
4. Remove memory section from `~/.claude/CLAUDE.md`
5. Remove memory hooks from `~/.claude/settings.json`
6. CC auto-discovers everything from the plugin

Step 2 is critical — without verification, steps 3-5 destroy the working setup with no fallback.

### Auto-Update

Both the plugin (hooks/skills/CLAUDE.md) and the backend (Docker images) must auto-update.

**Plugin updates** — handled natively by CC's built-in auto-updater. `dk-marketplace` already has `autoUpdate: true` in `~/.claude/plugins/known_marketplaces.json`. On every CC startup:

1. CC calls `autoUpdateMarketplacesAndPluginsInBackground()`
2. `dk-marketplace` has `autoUpdate: true` → CC does `git pull` on the marketplace repo
3. Compares installed memories plugin version against marketplace manifest
4. If version bumped → auto-downloads updated plugin files to cache
5. Notifies user: "Plugin(s) updated: memories · Run /reload-plugins to apply"

No custom code needed — this works automatically for any plugin in an `autoUpdate: true` marketplace. Bumping the version in the marketplace manifest triggers the update.

**Backend updates** — Docker isn't a CC concept, so the plugin adds its own version check:

1. `memory-recall.sh` (SessionStart) already calls the health endpoint. Extend it to also call `/version` (or parse the existing `/health` response) to get the running backend version.
2. Compare against the plugin's expected backend version (stored in `plugin/assets/BACKEND_VERSION`).
3. If mismatched, inject a warning: "Memories backend v{running} is behind v{expected}. Run `/memories:setup` to update, or: `docker compose pull && docker compose up -d`"

This gives automatic version awareness on every session start for both layers:
- **Plugin out of date** → CC auto-updater pulls new version → `/reload-plugins`
- **Backend out of date** → recall hook notifies → `/memories:setup` or manual docker pull

The setup skill (`/memories:setup`) handles both fresh installs and upgrades — it detects an existing compose file and does `docker compose pull && docker compose up -d` instead of full provisioning.

### Dependencies

- `docker-compose.standalone.yml` does not exist in the repo yet. Must be created (from PR #65 or written fresh) before the setup skill can work.
- Marketplace manifest entry must be added for auto-discovery.
- GHCR Docker publish workflow (from PR #65) needed for pre-built images.
- Backend `/health` or `/version` endpoint must return a version string.

## Non-Goals

- npm package distribution (replaced by plugin)
- MCP server inside the plugin (separate Docker concern)
- Codex/Cursor/OpenClaw integration (future plugins, different hook formats)
