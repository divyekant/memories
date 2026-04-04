---
name: memories:setup
description: "Bootstrap the existing Memories Codex integration from this repository checkout. Use after installing the repo-local Memories plugin in Codex, or when Codex needs the hooks/MCP wiring refreshed."
---

# Memories Setup For Codex

This plugin does not try to hard-code machine-specific paths into a cached plugin copy.
Instead, it bootstraps the real Codex integration from the current repository checkout.

## Goal

Find the local `memories` repo that contains `mcp-server/index.js` and `integrations/claude-code/install.sh`, then run the existing installer for the Codex target.

## Process

### 1. Locate the repository root

Search upward from the current working directory until you find both:

- `mcp-server/index.js`
- `integrations/claude-code/install.sh`

If you cannot find them, stop and tell the user this plugin is intended to be used from inside a checkout of the Memories repository.

### 2. Ensure MCP dependencies are installed

From that repository root, run:

```bash
npm --prefix ./mcp-server install
```

This ensures `mcp-server/index.js` has its Node dependencies available before Codex tries to launch it.

### 3. Run the Codex installer

From the repository root, run:

```bash
./integrations/claude-code/install.sh --codex
```

This is the canonical setup path. It configures:

- hook scripts under `~/.codex/hooks/memory/`
- hook registration in `~/.codex/hooks.json`
- read-only memory tool permissions in `~/.codex/settings.json`
- Memories MCP and `developer_instructions` in `~/.codex/config.toml`

### 4. Verify the key outputs

Check these files after the installer completes:

```bash
ls -la ~/.codex/hooks/memory/
jq '.hooks' ~/.codex/hooks.json
rg -n 'mcp_servers\\.memories|developer_instructions' ~/.codex/config.toml
```

### 5. Report exactly what changed

Tell the user:

- which repo root you used
- whether `npm --prefix ./mcp-server install` succeeded
- whether `./integrations/claude-code/install.sh --codex` succeeded
- whether `~/.codex/hooks.json` and the `mcp_servers.memories` block now exist

If any step fails, stop at the failing step and surface the exact command and error.
