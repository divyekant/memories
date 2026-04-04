---
name: memories:setup
description: "Set up or update the Memories backend. Use when the service is unreachable, when first installing, or to update the Docker containers. Triggers on 'memories setup', 'set up memories', 'install memories', or when SessionStart health check fails."
---

# Memories Setup

Interactive provisioning for the Memories backend service.

## Process

### Step 1: Check Docker

Verify Docker is available:

```bash
docker --version
```

If not found, tell the user to install Docker Desktop or OrbStack first.

### Step 2: Check if service is running

```bash
curl -sf http://localhost:8900/health
```

If running, show the version and ask if the user wants to update.

### Step 3: Deploy or update

If not running (fresh install):
1. Find and copy docker-compose.standalone.yml from the plugin's assets directory.
   The plugin is installed at one of these locations — check in order:
   - `~/.claude/plugins/marketplaces/dk-marketplace/plugins/memories/assets/`
   - `~/.claude/plugins/cache/dk-marketplace/memories/*/assets/`
   ```bash
   mkdir -p ~/.config/memories
   PLUGIN_ASSETS=$(find ~/.claude/plugins -path "*/memories/assets/docker-compose.standalone.yml" 2>/dev/null | head -1)
   cp "$PLUGIN_ASSETS" ~/.config/memories/docker-compose.yml
   ```
2. Ask about extraction provider: Anthropic (recommended) / OpenAI / Ollama / Skip
3. Write ~/.config/memories/env with chosen settings
4. Start: `cd ~/.config/memories && docker compose up -d`

If running (upgrade):
1. `cd ~/.config/memories && docker compose pull && docker compose up -d`

### Step 4: Health check

```bash
curl -sf http://localhost:8900/health
```

Confirm service is reachable and show version.

### Step 5: Configure MCP

Check if memories MCP is configured in ~/.claude/mcp.json. If not, add it:

```json
{
  "memories": {
    "type": "stdio",
    "command": "docker",
    "args": ["exec", "-i", "memories-mcp-1", "python", "-m", "mcp_server"]
  }
}
```

Or if the user prefers HTTP: guide them to the appropriate MCP config.

### Step 6: Ensure auto-update

Read ~/.claude/plugins/known_marketplaces.json. Find the marketplace entry that contains the memories plugin. If `autoUpdate` is not `true`, set it to `true` and save.

```bash
# Check current setting
cat ~/.claude/plugins/known_marketplaces.json | jq '.["dk-marketplace"].autoUpdate'
```

If false or missing, update it.
