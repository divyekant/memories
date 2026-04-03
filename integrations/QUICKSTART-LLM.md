# Memories — Automatic Memory Layer Setup

> **This document is designed to be fed directly to an LLM (Claude Code, Codex, Cursor, OpenClaw, or any AI coding assistant) so it can set up automatic memory integration for you.**

## What This Does

Memories is a local semantic memory service running at `http://localhost:8900`. This guide sets up automatic memory integrations so your AI assistant:

1. **Retrieves** relevant memories during coding flow (fully automatic with Claude hooks; MCP-guided on Codex/Cursor/OpenClaw)
2. **Extracts** facts from conversations and stores them automatically (no manual save)
3. **Updates** stale memories intelligently using AUDN (Add/Update/Delete/Noop)

After setup, memory works invisibly — the assistant gets context from past sessions automatically.

---

## Prerequisites

Before starting, verify:

```bash
# 1. Memories service is running
curl -s http://localhost:8900/health

# 2. Hook env file (installer writes this)
grep -E '^(MEMORIES_URL|MEMORIES_API_KEY)=' ~/.config/memories/env 2>/dev/null || echo "No hook env file yet (installer will create it)"

# 3. Node/npm available (for npx)
node --version
```

If the service isn't running, start it — no clone required:

```bash
curl -fsSL https://raw.githubusercontent.com/divyekant/memories/main/docker-compose.standalone.yml -o docker-compose.yml
docker compose up -d
```

Or if you have the repo cloned:
```bash
cd ~/projects/memories
docker compose up -d memories
```

---

## Setup for Claude Code

### Option A: Run the Installer (Recommended)

```bash
npx memories-mcp install --claude
```

The installer will:
1. Check Memories service health
2. Ask which extraction provider to use (Anthropic, OpenAI, Ollama, or skip)
3. Copy hook scripts to `~/.claude/hooks/memory/`
4. Merge hook configuration into `~/.claude/settings.json`
5. Write `~/.config/memories/env` with `MEMORIES_URL` and extraction vars

No clone required. No `jq` or `curl` dependencies.

### Option B: Manual Setup

**Step 1: Copy hook scripts**

```bash
mkdir -p ~/.claude/hooks/memory
cp ~/projects/memories/integrations/claude-code/hooks/*.sh ~/.claude/hooks/memory/
chmod +x ~/.claude/hooks/memory/*.sh
```

**Step 2: Create hook env file (`~/.config/memories/env`)**

```bash
mkdir -p ~/.config/memories
cat > ~/.config/memories/env <<'EOF'
MEMORIES_URL="http://localhost:8900"
MEMORIES_API_KEY="your-api-key-here"  # optional if API auth is disabled
EOF
```

**Step 2b: Configure extraction provider**

Add to `~/.config/memories/env` (also set these in your docker-compose environment or `.env` so the service picks them up):

```bash
# Choose one provider (or omit all for retrieval-only mode)
EXTRACT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# EXTRACT_PROVIDER=openai
# OPENAI_API_KEY=sk-...

# EXTRACT_PROVIDER=ollama
# OLLAMA_URL=http://localhost:11434
```

**Step 3: Add hooks to Claude Code settings**

Edit `~/.claude/settings.json` and merge in:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/memory/memory-recall.sh",
        "timeout": 5
      }]
    }],
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/memory/memory-query.sh",
        "timeout": 3
      }]
    }],
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/memory/memory-extract.sh",
        "timeout": 30
      }]
    }],
    "PreCompact": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/memory/memory-flush.sh",
        "timeout": 30
      }]
    }],
    "SessionEnd": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/memory/memory-commit.sh",
        "timeout": 30
      }]
    }]
  }
}
```

If you already have hooks in `settings.json`, merge the arrays — don't replace them.

**Step 4: Route memory to Memories MCP**

Claude Code has built-in auto-memory that writes to `MEMORY.md` files. With Memories running, this creates duplicate stores and bloated files. Add this to your global `~/.claude/CLAUDE.md`:

```markdown
## Memory Routing

This environment has Memories MCP for persistent, searchable memory.
Keep MEMORY.md for quick-reference only (ports, credentials, commands).
Store decisions, learnings, deferred work, and architecture context
via Memories MCP tools (memory_add, memory_extract) — NOT in MEMORY.md.
```

**Step 5: Verify**

Start a new Claude Code session. You should see "Relevant Memories" injected at the top if you have existing memories for the project.

---

## Setup for Cursor

Cursor supports full automatic memory via its **Third-party skills** feature, which reads Claude Code's `~/.claude/settings.json` directly. All 5 hook events work: `SessionStart`, `UserPromptSubmit`, `Stop`, `PreCompact`, and `SessionEnd`.

### Step 1: Run the installer

```bash
npx memories-mcp install --cursor
```

This copies hook scripts to `~/.claude/hooks/memory/` and merges hook config into `~/.claude/settings.json`.
It also writes Cursor MCP config at `~/.cursor/mcp.json` using `npx memories-mcp` so no local path is needed.

### Step 2: Enable Third-party skills in Cursor

Go to **Cursor Settings → Features → Third-party skills** and toggle it **ON**, then restart Cursor.

That's it — Cursor will automatically load and run the memory hooks from `~/.claude/settings.json`.

### Manual setup (optional)

Follow the Claude Code manual setup steps above (copy hooks, add env vars, edit `~/.claude/settings.json`), then enable Third-party skills in Cursor Settings.

If you prefer MCP-only manual config, add this to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "memories": {
      "command": "npx",
      "args": ["-y", "memories-mcp"],
      "env": {
        "MEMORIES_URL": "http://localhost:8900",
        "MEMORIES_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

---

## Setup for Codex

Codex uses:
- `~/.codex/settings.json` for a 5-hook lifecycle (`SessionStart`, `UserPromptSubmit`, `Stop`, `PreCompact`, `SessionEnd`)
- `~/.codex/config.toml` for MCP + developer instructions
- hook defaults that read/write `codex/{project}` memories unless you override them

### Option A: Run the installer (recommended)

```bash
npx memories-mcp install --codex
```

The installer will:
1. Install memory hook scripts to `~/.codex/hooks/memory/`
2. Merge the 5 Codex hook events into `~/.codex/settings.json`
3. Add MCP server config for `memories` to `~/.codex/config.toml` using `npx memories-mcp`
4. Add default `developer_instructions` (if not already set) to bias `memory_search` usage

The hooks load `MEMORIES_URL` / `MEMORIES_API_KEY` from `~/.config/memories/env` (or `MEMORIES_ENV_FILE` override).
Default scoped prefixes are `codex/{project},learning/{project},wip/{project}` for retrieval and `codex/{project}` for extraction.
For scoped API keys, override them with `MEMORIES_SOURCE_PREFIXES` and `MEMORIES_EXTRACT_SOURCE`.

No clone required. No `jq` or `curl` dependencies.

### Option B: Manual setup

**Step 1: Install hook scripts**

```bash
# Option: use npx to get hook scripts from the package
node -e "
const src = require('path').join(require.resolve('memories-mcp'), '..', 'hooks');
require('fs').mkdirSync(process.env.HOME + '/.codex/hooks/memory', {recursive:true});
require('child_process').execSync('cp -r ' + src + '/. ' + process.env.HOME + '/.codex/hooks/memory/');
console.log('Done');
"
chmod +x ~/.codex/hooks/memory/*.sh
```

Or if you have the repo cloned:
```bash
mkdir -p ~/.codex/hooks/memory
cp ~/projects/memories/integrations/claude-code/hooks/memory-*.sh ~/.codex/hooks/memory/
cp ~/projects/memories/integrations/claude-code/hooks/_lib.sh ~/.codex/hooks/memory/
cp ~/projects/memories/integrations/claude-code/hooks/response-hints.json ~/.codex/hooks/memory/
chmod +x ~/.codex/hooks/memory/*.sh
```

**Step 2: Edit `~/.codex/settings.json`**

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "/Users/you/.codex/hooks/memory/memory-recall.sh",
        "timeout": 5
      }]
    }],
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "/Users/you/.codex/hooks/memory/memory-query.sh",
        "timeout": 3
      }]
    }],
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "/Users/you/.codex/hooks/memory/memory-extract.sh",
        "timeout": 30
      }]
    }],
    "PreCompact": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "/Users/you/.codex/hooks/memory/memory-flush.sh",
        "timeout": 30
      }]
    }],
    "SessionEnd": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "/Users/you/.codex/hooks/memory/memory-commit.sh",
        "timeout": 30
      }]
    }]
  }
}
```

If `~/.codex/settings.json` already has hooks, merge these entries instead of replacing the file.

**Step 3: Edit `~/.codex/config.toml`**

```toml
[mcp_servers.memories]
command = "npx"
args = ["-y", "memories-mcp"]

[mcp_servers.memories.env]
MEMORIES_URL = "http://localhost:8900"
MEMORIES_API_KEY = "your-api-key-here"
```

**Step 4: Optional source overrides for scoped keys**

If your API key is prefix-scoped and does not allow `codex/*`, set these in `~/.config/memories/env`:

```bash
MEMORIES_SOURCE_PREFIXES="your-authorized-prefix/{project},learning/{project},wip/{project}"
MEMORIES_EXTRACT_SOURCE="your-authorized-prefix/{project}"
```

**Step 5: Restart Codex**

Codex will expose `memory_search`, `memory_add`, `memory_delete`, `memory_delete_by_source`, `memory_count`, `memory_list`, `memory_stats`, `memory_is_novel`, and other tools via MCP.

---

## Setup for OpenClaw

OpenClaw doesn't have hooks, so memory is agent-initiated via the skill. Update the skill file:

1. Copy `integrations/openclaw-skill.md` to your OpenClaw skills directory
2. Add `MEMORIES_URL` / `MEMORIES_API_KEY` to the OpenClaw gateway config (`openclaw config patch ...`) so skill exec calls can authenticate
3. The skill instructs the agent to call `memory_recall_memories` at task start, `memory_extract_memories` after significant work, and the QMD sync script from heartbeat/maintenance flows

### Capturing context on compaction

OpenClaw supports `compaction.memoryFlush`, which lets you run a prompt before the gateway compacts context. You can use that to write a markdown summary and send the same summary to Memories:

```bash
openclaw config patch '{
  "agents": {
    "defaults": {
      "compaction": {
        "memoryFlush": {
          "prompt": "Session nearing compaction. Do ALL of the following:\n1. Write key context to memory/YYYY-MM-DD.md.\n2. Extract to Memories: curl -s -X POST $MEMORIES_URL/memory/extract -H '\''Content-Type: application/json'\'' -H '\''X-API-Key: $MEMORIES_API_KEY'\'' -d '\''{\"messages\":\"<session summary>\",\"source\":\"openclaw/jack\",\"context\":\"pre_compact\"}'\''\n3. Reply NO_REPLY."
        }
      }
    }
  }
}'
```

This makes compaction events populate Memories automatically instead of relying only on manual extraction.

---

## Multi-Backend Setup (Optional)

Multi-backend lets a single agent session talk to **multiple Memories instances** at once. This is entirely optional — if you skip it, everything works with a single backend from your existing env vars.

### When you'd want this

- **Dev + Prod**: search both your local dev instance and a remote production instance; extract new memories to dev only
- **Personal + Shared**: search both personal and team memories; route architecture decisions to the shared store

### Config file format

Create `~/.config/memories/backends.yaml` (global) or `.memories/backends.yaml` (per-project, should be gitignored):

```yaml
# Scenario: dev + prod
# Searches both backends, extracts to dev only
backends:
  dev:
    url: http://localhost:8900
    api_key: ${MEMORIES_DEV_KEY}
    scenario: dev
  prod:
    url: https://memory.yourdomain.com
    api_key: ${MEMORIES_PROD_KEY}
    scenario: prod
```

```yaml
# Scenario: personal + shared
# Searches both, routes decisions to shared
backends:
  personal:
    url: http://localhost:8900
    api_key: ${MEMORIES_PERSONAL_KEY}
    scenario: personal
  shared:
    url: https://team-memory.yourdomain.com
    api_key: ${MEMORIES_SHARED_KEY}
    scenario: shared
```

```yaml
# Scenario: single instance (explicit, same as no config)
backends:
  default:
    url: http://localhost:8900
    api_key: ${MEMORIES_API_KEY}
    scenario: single
```

### Env var interpolation

API keys and URLs support `${VAR_NAME}` interpolation. Set the actual values in your shell environment or in `~/.config/memories/env`:

```bash
# In ~/.config/memories/env (or export in your shell profile)
MEMORIES_DEV_KEY="dev-key-here"
MEMORIES_PROD_KEY="prod-key-here"
```

### How to verify it works

After creating the config file, start a new session in your AI assistant. The hook scripts will detect `backends.yaml` and route accordingly. Check the hook log for routing info:

```bash
# Check that backends were loaded
grep -i "backend" ~/.config/memories/hook.log | tail -5

# Verify search hits both backends
curl -s http://localhost:8900/health   # dev
curl -s https://memory.yourdomain.com/health   # prod
```

### Client compatibility

| Client | Multi-backend supported? | Notes |
|--------|--------------------------|-------|
| Claude Code | Yes | Uses hook scripts that read `backends.yaml` |
| Codex | Yes | Same hook scripts as Claude Code |
| Cursor | Yes | Same hook scripts via Third-party skills |
| OpenClaw | Not yet | Uses skill-based extraction, not hooks |

---

## How Integrations Work

### Claude Code

| Hook | Event | Sync? | What It Does |
|------|-------|-------|-------------|
| `memory-recall.sh` | SessionStart | Sync | Searches project-scoped memories, injects top results, and adds a short recall playbook for the session |
| `memory-query.sh` | UserPromptSubmit | Sync | Searches project-scoped memories first and uses recent transcript context so short follow-up prompts still retrieve useful memories |
| `memory-extract.sh` | Stop | Async | POSTs the last exchange to `/memory/extract` for fact extraction |
| `memory-flush.sh` | PreCompact | Async | Same as extract but with `context=pre_compact` (more aggressive before context loss) |
| `memory-commit.sh` | SessionEnd | Async | Final extraction pass when session ends |

### Codex

| Mechanism | Event | What It Does |
|-----------|-------|--------------|
| `settings.json` + `memory-recall.sh` | Session start | Loads project-scoped memories and recall guidance |
| `settings.json` + `memory-query.sh` | Before each prompt | Searches relevant memories using transcript context when available |
| `settings.json` + `memory-extract.sh` | After response | Sends the last exchange to `/memory/extract` (`context=stop`) |
| `settings.json` + `memory-flush.sh` | Pre-compact | Sends a larger transcript slice to `/memory/extract` (`context=pre_compact`) |
| `settings.json` + `memory-commit.sh` | Session end | Final extraction pass when the session closes |
| MCP tools + developer instructions | Each new user turn | Drives `memory_search` usage before implementation-heavy responses |

Codex uses `~/.codex/settings.json` for hooks and `~/.codex/config.toml` for MCP + developer instructions.

**Token cost:** ~1500 tokens/turn injected context (retrieval). Extraction is async and free if using Ollama, ~$0.001/turn with API providers.

---

## Extraction Provider Comparison

| Provider | Cost | AUDN Support | Speed | Quality |
|----------|------|-------------|-------|---------|
| **Anthropic** (recommended) | ~$0.001/turn | Full (Add/Update/Delete/Noop) | ~1-2s | Best |
| **OpenAI** | ~$0.001/turn | Full (Add/Update/Delete/Noop) | ~1-2s | Great |
| **ChatGPT Subscription** | Free (your subscription) | Full (Add/Update/Delete/Noop) | ~1-2s | Great |
| **Ollama** | Free | Full (Add/Update/Delete/Noop) | ~5s | Good |
| **Skip** | Free | None by default (retrieval only) | N/A | N/A |

- **Full AUDN** means the LLM compares new facts against existing memories and decides whether to add, update, delete, or skip
- **ChatGPT Subscription** requires one-time OAuth setup: `python -m memories auth chatgpt --client-id <your-client-id>`
- **Ollama** uses JSON format constraint to produce structured AUDN decisions from local models
- **Skip** means hooks retrieve memories. By default no new memories are added; optional fallback add mode exists (`EXTRACT_FALLBACK_ADD=true`) and also activates on provider runtime failures (for example 429/timeouts).

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORIES_URL` | `http://localhost:8900` | Memories service URL |
| `MEMORIES_API_KEY` | (empty) | API key for Memories service auth |
| `MEMORIES_ENV_FILE` | `~/.config/memories/env` | Hook env file path for Claude/Codex hooks and OpenClaw QMD sync snippets |
| `MEMORIES_SOURCE_PREFIXES` | client-specific | Retrieval prefixes for settings-based hooks. Defaults to `claude-code/{project},learning/{project},wip/{project}` for Claude/Cursor and `codex/{project},learning/{project},wip/{project}` under `~/.codex/hooks/memory`. |
| `MEMORIES_EXTRACT_SOURCE` | client-specific | Extraction source for settings-based hooks. Defaults to `claude-code/{project}` for Claude/Cursor and `codex/{project}` under `~/.codex/hooks/memory`. |
| `MEMORIES_SOURCE_PREFIX` | `codex` | Legacy notify-hook prefix used only by `memory-codex-notify.sh` |
| `MEMORIES_SOURCE` | (empty) | Legacy notify-hook full source override used only by `memory-codex-notify.sh` |
| `EXTRACT_PROVIDER` | (none) | `anthropic`, `openai`, `chatgpt-subscription`, `ollama`, or empty to disable |
| `EXTRACT_MODEL` | (per provider) | Override model. Defaults: `claude-haiku-4-5-20251001`, `gpt-4.1-nano`, `gemma3:4b` |
| `ANTHROPIC_API_KEY` | (none) | Required when `EXTRACT_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | (none) | Required when `EXTRACT_PROVIDER=openai` |
| `CHATGPT_REFRESH_TOKEN` | (none) | Required when `EXTRACT_PROVIDER=chatgpt-subscription` (from `python -m memories auth chatgpt`) |
| `CHATGPT_CLIENT_ID` | (none) | Required when `EXTRACT_PROVIDER=chatgpt-subscription` |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server URL (on Linux, use `http://localhost:11434`) |
| `EXTRACT_FALLBACK_ADD` | `false` | Enable add-only fallback when extraction is disabled or provider calls fail at runtime |
| `EXTRACT_FALLBACK_MAX_FACTS` | `1` | Max fallback facts per request |
| `EXTRACT_FALLBACK_MIN_FACT_CHARS` | `24` | Minimum candidate fact length |
| `EXTRACT_FALLBACK_MAX_FACT_CHARS` | `280` | Maximum candidate fact length |
| `EXTRACT_FALLBACK_NOVELTY_THRESHOLD` | `0.88` | Novelty threshold for fallback adds |
| `MEMORIES_HOOKS_DIR` | `~/.claude/hooks/memory` | Override Claude hooks location |

Service-level runtime guardrails (set in Docker compose env):
- `EMBEDDER_AUTO_RELOAD_ENABLED` (`true`/`false`)
- `EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD`
- `EMBEDDER_AUTO_RELOAD_CHECK_SEC`
- `EMBEDDER_AUTO_RELOAD_HIGH_STREAK`
- `EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC`
- `EMBEDDER_AUTO_RELOAD_WINDOW_SEC`
- `EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW`
- `EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS`
- `EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH`

---

## Verifying It Works

### Check retrieval is working

```bash
# Add a test memory
curl -s -X POST http://localhost:8900/memory/add \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d '{"text": "This project uses TypeScript strict mode", "source": "test/setup"}'

# Start a new Claude Code session in a project directory
# You should see "## Relevant Memories" in the context
```

### Check extraction is working

```bash
# Check extraction status
curl -s -H "X-API-Key: $MEMORIES_API_KEY" http://localhost:8900/extract/status | jq .

# Check auto-reload metrics
curl -s -H "X-API-Key: $MEMORIES_API_KEY" http://localhost:8900/metrics | jq '.embedder_reload'

# Expected (if configured):
# {"enabled": true, "provider": "anthropic", "model": "claude-haiku-4-5-20251001", "status": "healthy"}

# Test extraction manually (async-first: returns 202 + job_id)
JOB_ID=$(curl -s -X POST http://localhost:8900/memory/extract \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d '{
    "messages": "User: We should use Drizzle instead of Prisma for the ORM.\nAssistant: Good call, Drizzle is lighter and has better TypeScript inference.",
    "source": "test/extraction",
    "context": "stop"
  }' | jq -r '.job_id')

# Poll job status/result
curl -s -H "X-API-Key: $MEMORIES_API_KEY" "http://localhost:8900/memory/extract/$JOB_ID" | jq .

# Expected terminal payload includes:
# {"status":"completed", "result":{"actions":[...], "extracted_count":N, "stored_count":N, ...}}
```

---

## Disabling / Uninstalling

### Disable extraction only (keep retrieval)

Remove or comment out `EXTRACT_PROVIDER` in repo `.env`:
```bash
# EXTRACT_PROVIDER=anthropic
```

Retrieval still works. Extraction paths will return "not configured" unless fallback mode is enabled.

Optional fallback mode:
```bash
export EXTRACT_FALLBACK_ADD=true
```
This enables strict add-only fallback writes (no AUDN update/delete behavior), including runtime provider failures such as quota/rate-limit errors.

### Remove all integrations

The easiest way is to use the installer:

```bash
npx memories-mcp uninstall
```

Or manually:

```bash
# Remove Claude/Cursor hooks
rm -rf ~/.claude/hooks/memory/

# Remove Codex hooks
rm -rf ~/.codex/hooks/memory/

# Remove Codex hook entries from ~/.codex/settings.json

# Remove Codex config entries from ~/.codex/config.toml:
# - [mcp_servers.memories] section
# - [mcp_servers.memories.env] section

# Remove env vars from hook env file
# Edit ~/.config/memories/env and remove MEMORIES_URL/MEMORIES_API_KEY/MEMORIES_SOURCE_PREFIXES/MEMORIES_EXTRACT_SOURCE/EXTRACT_PROVIDER/provider keys
```

---

## Troubleshooting

### Hooks not firing

```bash
# Claude: check hook scripts are executable
ls -la ~/.claude/hooks/memory/

# Claude: test recall hook manually
echo '{"cwd": "/Users/you/project", "session_type": "startup"}' | bash ~/.claude/hooks/memory/memory-recall.sh

# Codex: check hook scripts and merged config
ls -la ~/.codex/hooks/memory/
jq '.hooks' ~/.codex/settings.json

# Codex: test recall hook manually
echo '{"cwd": "/Users/you/project", "session_type": "startup"}' | bash ~/.codex/hooks/memory/memory-recall.sh
```

### Extraction returning 501

```bash
# Extraction is disabled. Set EXTRACT_PROVIDER in your docker-compose environment:
# Add to your .env file next to docker-compose.yml (or pass via docker compose env):
echo 'EXTRACT_PROVIDER=anthropic' >> .env   # or openai, chatgpt-subscription, ollama
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
docker compose up -d memories  # restart to pick up new env

# Also add to ~/.config/memories/env so hook scripts know the provider:
echo 'EXTRACT_PROVIDER=anthropic' >> ~/.config/memories/env
```

### Slow retrieval hooks

```bash
# Check Memories service latency
time curl -s -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d '{"query": "test", "k": 5, "hybrid": true}'

# Should be <50ms. If slow, check Docker resources.
```

### Memories not appearing in context

```bash
# Check you have memories stored
curl -s -H "X-API-Key: $MEMORIES_API_KEY" http://localhost:8900/stats | jq '.total_memories'

# Check the similarity threshold isn't too high
# The recall hook uses 0.3 threshold, query hook uses 0.4
# If all memories have low similarity to your project name, they won't appear
```

---

## v5.0.0 New Features

### Graph-Aware Search
MCP `memory_search` now has `graph_weight=0.1` by default — related memories are automatically surfaced alongside direct search results. No configuration needed.

### Temporal Search
Filter by date range: `since` and `until` params on `memory_search`. Set `document_at` on `memory_add` to timestamp when content was created. Extraction hooks now pass session timestamps automatically.

### Version History
`include_archived=true` on `memory_search` to see previous versions of updated memories.
