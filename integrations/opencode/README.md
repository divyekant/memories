# OpenCode Integration

Memories supports OpenCode through OpenCode MCP plus repo-local plugin hooks. This is separate from Claude Code and Codex shell hooks.

## Setup

From the Memories repo root:

```bash
npm --prefix ./mcp-server install
./integrations/claude-code/install.sh --opencode
```

The installer merges config into `~/.config/opencode/opencode.json` and registers:

- `mcp.memories` as a local OpenCode MCP server.
- A `zsh -lc` command that sources `~/.config/memories/env` and runs `mcp-server/index.js`.
- The repo-local plugin path `integrations/opencode/plugin/memories.js`.
- The Memories skill at `~/.config/opencode/skills/memories/SKILL.md`.

Skill installation is marker-safe: an existing unmarked `~/.config/opencode/skills/memories` directory is preserved instead of overwritten.

## Behavior

The first OpenCode implementation provides:

- Prompt-time recall context through `experimental.chat.system.transform`.
- Exact project-prefix searches in this order: `opencode/{project}`, `claude-code/{project}`, `codex/{project}`, `learning/{project}`, `wip/{project}`.
- Active-search telemetry for memory MCP tool calls with `client=opencode`.

The plugin reads `~/.config/memories/env` by default for `MEMORIES_URL`, `MEMORIES_API_KEY`, `MEMORIES_ACTIVE_SEARCH_LOG`, and `MEMORIES_ACTIVE_SEARCH_METRICS`. It writes active-search `tool_call` telemetry to `~/.config/memories/active-search.jsonl` by default unless `MEMORIES_ACTIVE_SEARCH_METRICS` is disabled.

Automatic extraction is not enabled by default for OpenCode yet. OpenCode-authored extracted memories should use `opencode/{project}` when extraction is added, but extraction remains gated until reliable OpenCode end-of-turn transcript access is proven.

## Troubleshooting

- Check `~/.config/opencode/opencode.json` for `mcp.memories` and the plugin path.
- Check `~/.config/memories/env` for `MEMORIES_URL` and optional `MEMORIES_API_KEY`.
- Confirm the Memories service is running with `curl http://localhost:8900/health`.
- Confirm MCP server dependencies are installed with `npm --prefix ./mcp-server install`.
- Active-search telemetry writes to `~/.config/memories/active-search.jsonl` by default unless disabled with `MEMORIES_ACTIVE_SEARCH_METRICS=0`.

## Uninstall

From the Memories repo root:

```bash
./integrations/claude-code/install.sh --opencode --uninstall
```

The uninstaller removes OpenCode MCP/plugin entries and marker-managed skill files while preserving existing unmarked `~/.config/opencode/skills/memories` content.
