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
1. Prefer the CLI bootstrapper — it fetches the compose file, prompts for consent, and wires the backend for you:
   ```bash
   npx memories-mcp@latest init
   ```
   This is the primary path. Only fall back to the manual steps below if `npx` is unavailable or the bootstrapper fails.
2. Manual fallback — find and copy docker-compose.standalone.yml:
   The plugin symlink resolves to `assets/claude-code`, which does **not** contain `backend/` — so look in the plugin's parent asset tree and the npm package layout, in order:
   ```bash
   mkdir -p ~/.config/memories
   PLUGIN_ASSETS=$(find ~/.claude/plugins -path "*/memories/**/backend/docker-compose.standalone.yml" 2>/dev/null | head -1)
   NPM_ASSETS=$(find / -path "*/memories-mcp/assets/backend/docker-compose.standalone.yml" 2>/dev/null | head -1)
   if [ -n "$PLUGIN_ASSETS" ]; then
     cp "$PLUGIN_ASSETS" ~/.config/memories/docker-compose.yml
   elif [ -n "$NPM_ASSETS" ]; then
     cp "$NPM_ASSETS" ~/.config/memories/docker-compose.yml
   else
     # Always-safe fallback: pull directly from the repo
     curl -fsSL https://raw.githubusercontent.com/divyekant/memories/main/mcp-server/assets/backend/docker-compose.standalone.yml -o ~/.config/memories/docker-compose.yml
   fi
   ```
3. Ask about extraction provider: Anthropic (recommended) / OpenAI / Ollama / Skip
4. Write ~/.config/memories/env with chosen settings
5. Start: `cd ~/.config/memories && docker compose up -d`

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
