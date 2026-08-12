# memories-mcp

Agent-agnostic, persistent memory for AI coding agents — an [MCP](https://modelcontextprotocol.io) server plus a one-command installer for Claude Code, Codex, Cursor, and any other MCP client.

Your agents remember decisions, conventions, deferred work, and project history across sessions. Memories are stored in a self-hosted backend (FastAPI + Qdrant, semantic search) that you control.

## Quick start

```bash
npx memories-mcp init
```

`init` detects the agents installed on your machine and wires each one: MCP registration, auto-recall/extract hooks, skills, and behavioral rules. If no backend is reachable it offers to provision one with Docker (consent-gated).

Other commands:

```bash
npx memories-mcp doctor      # per-agent install status + backend health
npx memories-mcp update      # refresh wiring after an upgrade
npx memories-mcp uninstall   # reverse everything init did
```

Flags: `--claude` / `--codex` / `--cursor` / `--generic` restrict targets (default: auto-detect), `--url` / `--api-key` configure the backend, `--dry-run` prints the plan without writing, `--yes` runs non-interactive.

### Codex setup

For a local Codex install, run `npx memories-mcp init --codex --yes`. This uses
local stdio MCP and installs the Codex hooks and developer instructions. A
`codex-cli` version at or above `0.146.0` receives the ten-event lifecycle
profile; older or unparseable versions use the compatible five-event profile.

To keep a backend key out of the local MCP entry, add
`--no-persist-api-key`; the hooks can read `MEMORIES_API_KEY` from the process
environment instead.

For a remote Streamable HTTP MCP server with OAuth, use
`npx memories-mcp init --codex --mcp-url https://memory.example/mcp --yes`, then
run `codex mcp login memories`. Remote mode does not copy a backend API key into
Codex configuration.

## Use as a plain MCP server

Any MCP client can run the server directly — no installer needed:

```json
{
  "mcpServers": {
    "memories": {
      "command": "npx",
      "args": ["-y", "memories-mcp"],
      "env": {
        "MEMORIES_URL": "http://localhost:8900",
        "MEMORIES_API_KEY": ""
      }
    }
  }
}
```

## What the agents get

- **Tools**: `memory_search` (hybrid semantic + keyword), `memory_add`, `memory_extract` (LLM extraction with add/update/delete/noop), `memory_list`, `memory_get`, `memory_delete`, `memory_timeline`, `memory_evidence`, conflict detection, novelty checks, stats, and more.
- **Hooks** (Claude Code, Codex, Cursor): automatic recall at session start and per prompt, automatic extraction at session end — memory becomes non-optional instead of something the model must remember to do.
- **Skills + rules**: memory discipline (search before answering about prior work, capture decisions at breakpoints, preserve boundary conditions).

## Supported agents

| Agent | Integration |
|-------|-------------|
| Claude Code | MCP + hooks + skills + CLAUDE.md rules |
| Codex | MCP + hooks + developer instructions (`config.toml`) |
| Cursor | MCP + shared hooks via Third-party skills |
| Any MCP client | `npx -y memories-mcp` config snippet |

Windows: hooks require bash, so only the generic MCP config applies there.

## Backend

The server talks to a self-hosted Memories backend over HTTP. Provision it with `npx memories-mcp init` (Docker, single compose file) or see the [repository](https://github.com/divyekant/memories) for manual deployment, architecture, and the full documentation.

## License

MIT
