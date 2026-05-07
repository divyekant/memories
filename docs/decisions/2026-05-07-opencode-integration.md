# ADR: OpenCode Integration Architecture

**Date:** 2026-05-07
**Status:** Accepted

## Context

Memories already supports Claude Code, Cursor, Codex, and OpenClaw through client-specific hooks, MCP, or skill flows. OpenCode needs first-class support, but it does not share Claude Code or Codex shell-hook lifecycles.

## Decision

Use OpenCode MCP plus a repo-local OpenCode plugin as the integration boundary.

The installer adds an `--opencode` target that merges `mcp.memories` and the plugin path into `~/.config/opencode/opencode.json`, installs the shared Memories skill under `~/.config/opencode/skills/memories`, and preserves custom user-owned Memories skill/MCP entries unless they carry the Memories OpenCode installer marker.

The plugin provides prompt-time recall context via `experimental.chat.system.transform` and active-search telemetry for memory tool calls via `tool.execute.after`. It searches exact project prefixes first: `opencode/{project}`, `claude-code/{project}`, `codex/{project}`, `learning/{project}`, and `wip/{project}`.

Automatic OpenCode extraction is not enabled by default. OpenCode-authored extracted memories should use `opencode/{project}` when extraction is added, but extraction remains gated until reliable OpenCode end-of-turn transcript access is proven.

## Consequences

- OpenCode gains retrieval guidance and MCP access without reusing Claude Code or Codex shell hooks.
- The installer can safely coexist with existing OpenCode user config because managed entries are marker-scoped.
- Active-search monitoring can identify OpenCode memory tool usage with `client=opencode`, but OpenCode does not yet contribute prompt classification or follow-up metrics.
- Extraction parity is deferred until OpenCode exposes a reliable lifecycle point with sufficient transcript context.
