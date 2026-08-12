---
name: memories:setup
description: "Guide portable Memories setup for Codex. The published memories-mcp npm installer owns hooks and MCP wiring."
---

# Memories Setup For Codex

This is a thin, portable guide. The published `memories-mcp` npm package owns
Codex hooks and MCP configuration; this plugin does not bundle or install a
second copy of either. No repository checkout is required.

## Local stdio setup (recommended)

Run this in the user's terminal:

```bash
npx -y memories-mcp@latest init --codex
```

The installer owns the local stdio MCP registration and all supported Codex
hooks. It may use the default local backend (`http://localhost:8900`) or the
backend URL configured by the user's local environment. If authentication is
enabled, enter the API key only in the local installer prompt or environment;
never paste a key into chat and never print one in a setup report.

### Direct remote MCP setup (OAuth)

For a hosted MCP endpoint, run:

```bash
npx -y memories-mcp@latest init --codex --mcp-url https://... --yes
codex mcp login memories
```

`--mcp-url` is an absolute HTTPS MCP URL and uses OAuth. Do not combine it
with `--url` or `--api-key`; the remote configuration contains no backend API
key. The second command completes Codex's OAuth login for the `memories`
server.

### Verify without exposing credentials

```bash
test -f ~/.codex/hooks.json && jq '.hooks' ~/.codex/hooks.json
rg -n 'mcp_servers\.memories|developer_instructions' ~/.codex/config.toml
```

These checks inspect hook and registration presence only. Do not dump
`config.toml` environment values or any API key.

### Lifecycle and coexistence notes

The npm installer selects the lifecycle supported by the installed Codex:
Codex `>= 0.146.0` gets ten events; older or unparseable versions get the
five-event legacy profile. `PostCompact` is silent (`suppressOutput` only),
while `SessionStart(source=compact)` is the recall surface. `SessionEnd`
performs one first-routed extract request and exits; the request has a 2-second
maximum and the manifest timeout is exactly 3 seconds, with no polling.

The plugin and installer do not configure Codex's optional native Memories
cache. External Memories remains the durable, searchable cross-client
authority; native Codex Memories is an optional local derived cache. If
desired, `memories.disable_on_external_context = true` can be set manually to
avoid duplicate context, but the installer never sets either setting.

The installer auto-approves six read-only MCP tools. `memory_is_useful` is a
feedback write and remains prompt-gated when it is mentioned or called.
