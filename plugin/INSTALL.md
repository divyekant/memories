# Installing the Memories Plugin

This file covers the Claude Code plugin package in `plugin/`. For the repo-local Codex plugin shipped from this repository, use `.agents/plugins/marketplace.json` from the repo root and run `$memories:setup` inside Codex.

## Prerequisites

- Claude Code CLI installed
- Docker (OrbStack or Docker Desktop)

## Quick Install

1. Add the memories plugin to your dk-marketplace:

   Add this entry to `~/.claude/plugins/marketplaces/dk-marketplace/.claude-plugin/marketplace.json` under `plugins`:

   ```json
   {
     "name": "memories",
     "description": "Persistent, searchable memory across sessions via Memories MCP. Auto-recalls project context, extracts decisions, and makes memory non-optional.",
     "version": "5.1.0",
     "source": "./plugins/memories",
     "category": "memory",
     "strict": false
   }
   ```

2. Symlink or copy the plugin:

   ```bash
   ln -s /path/to/memories/plugin ~/.claude/plugins/marketplaces/dk-marketplace/plugins/memories
   ```

3. Start a new CC session. The plugin auto-loads.

4. Run `/memories:setup` to provision the backend.

## Auto-Update

With `autoUpdate: true` on the marketplace (default for dk-marketplace), plugin updates are pulled automatically on CC startup.
