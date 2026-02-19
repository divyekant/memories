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
curl -s http://localhost:8900/health | jq .

# 2. API key is set (check your shell profile)
echo $MEMORIES_API_KEY

# 3. jq is installed
jq --version
```

If the service isn't running:
```bash
cd ~/projects/memories  # or wherever the repo lives
docker compose up -d memories
```

---

## Setup for Claude Code

### Option A: Run the Installer (Recommended)

```bash
cd ~/projects/memories
./integrations/claude-code/install.sh
```

The installer will:
1. Check Memories service health
2. Ask which extraction provider to use (Anthropic, OpenAI, Ollama, or skip)
3. Copy hook scripts to `~/.claude/hooks/memory/`
4. Merge hook configuration into `~/.claude/settings.json`
5. Add environment variables to your shell profile

### Option B: Manual Setup

**Step 1: Copy hook scripts**

```bash
mkdir -p ~/.claude/hooks/memory
cp ~/projects/memories/integrations/claude-code/hooks/*.sh ~/.claude/hooks/memory/
chmod +x ~/.claude/hooks/memory/*.sh
```

**Step 2: Add environment variables to `~/.zshrc` (or `~/.bashrc`)**

```bash
# Memories hooks
export MEMORIES_URL="http://localhost:8900"
export MEMORIES_API_KEY="your-api-key-here"

# Extraction provider (choose one, or omit to disable extraction)
# Option 1: Anthropic (recommended, ~$0.001/turn, full AUDN)
export EXTRACT_PROVIDER="anthropic"
export ANTHROPIC_API_KEY="sk-ant-..."

# Option 2: OpenAI (~$0.001/turn, full AUDN)
# export EXTRACT_PROVIDER="openai"
# export OPENAI_API_KEY="sk-..."

# Option 3: Ollama (free, local, extraction only — no AUDN)
# export EXTRACT_PROVIDER="ollama"
# export OLLAMA_URL="http://localhost:11434"
```

Source the profile: `source ~/.zshrc`

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

**Step 4: Verify**

Start a new Claude Code session. You should see "Relevant Memories" injected at the top if you have existing memories for the project.

---

## Setup for Codex

Codex does **not** use Claude's 5-hook `settings.json` format. Codex-native integration uses:
- `~/.codex/config.toml` for MCP + notify
- a notify script for after-turn extraction
- developer instructions to bias retrieval (`memory_search`) every turn

### Option A: Run the installer (recommended)

```bash
cd ~/projects/memories
./integrations/claude-code/install.sh --codex
```

The installer will:
1. Install `memory-codex-notify.sh` to `~/.codex/hooks/memory/`
2. Add `notify = ["/Users/you/.codex/hooks/memory/memory-codex-notify.sh"]` to `~/.codex/config.toml` when `notify` is not already set
3. Add MCP server config for `memories` to `~/.codex/config.toml` when missing
4. Add default `developer_instructions` (if not already set) to bias `memory_search` usage

### Option B: Manual setup

**Step 1: Install notify script**

```bash
mkdir -p ~/.codex/hooks/memory
cp ~/projects/memories/integrations/codex/memory-codex-notify.sh ~/.codex/hooks/memory/
chmod +x ~/.codex/hooks/memory/memory-codex-notify.sh
```

**Step 2: Edit `~/.codex/config.toml`**

```toml
notify = ["/Users/you/.codex/hooks/memory/memory-codex-notify.sh"]

[mcp_servers.memories]
command = "node"
args = ["/path/to/memories/mcp-server/index.js"]

[mcp_servers.memories.env]
MEMORIES_URL = "http://localhost:8900"
MEMORIES_API_KEY = "your-api-key-here"
```

**Step 3: Restart Codex**

Codex will expose `memory_search`, `memory_add`, and related tools via MCP.

---

## Setup for Cursor

Cursor is MCP-first today.

1. Install MCP server deps:

```bash
cd ~/projects/memories/mcp-server
npm install
```

2. Add config to one of:
- Global: `~/.cursor/mcp.json`
- Project: `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "memories": {
      "command": "node",
      "args": ["/path/to/memories/mcp-server/index.js"],
      "env": {
        "MEMORIES_URL": "http://localhost:8900",
        "MEMORIES_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

3. Restart Cursor.

---

## Setup for OpenClaw

OpenClaw doesn't have hooks, so memory is agent-initiated via the skill. Update the skill file:

1. Copy `integrations/openclaw-skill.md` to your OpenClaw skills directory
2. The skill includes `memory_recall_memories` (called at task start) and `memory_extract_memories` (called after completing tasks)
3. The agent is instructed when to call these automatically

---

## How Integrations Work

### Claude Code

| Hook | Event | Sync? | What It Does |
|------|-------|-------|-------------|
| `memory-recall.sh` | SessionStart | Sync | Searches Memories for project-specific memories, injects top 8 as context |
| `memory-query.sh` | UserPromptSubmit | Sync | Searches Memories for memories relevant to the current prompt (skips short prompts) |
| `memory-extract.sh` | Stop | Async | POSTs the last exchange to `/memory/extract` for fact extraction |
| `memory-flush.sh` | PreCompact | Async | Same as extract but with `context=pre_compact` (more aggressive before context loss) |
| `memory-commit.sh` | SessionEnd | Async | Final extraction pass when session ends |

### Codex

| Mechanism | Event | What It Does |
|-----------|-------|--------------|
| `notify` + `memory-codex-notify.sh` | After each completed turn | Sends user+assistant exchange to `/memory/extract` (`context=after_agent`) |
| MCP tools + developer instructions | Each new user turn | Drives `memory_search` usage before implementation-heavy responses |

Codex currently does not expose Claude-style SessionStart/UserPromptSubmit/PreCompact/SessionEnd hook callbacks in `config.toml`.

**Token cost:** ~1500 tokens/turn injected context (retrieval). Extraction is async and free if using Ollama, ~$0.001/turn with API providers.

---

## Extraction Provider Comparison

| Provider | Cost | AUDN Support | Speed | Quality |
|----------|------|-------------|-------|---------|
| **Anthropic** (recommended) | ~$0.001/turn | Full (Add/Update/Delete/Noop) | ~1-2s | Best |
| **OpenAI** | ~$0.001/turn | Full (Add/Update/Delete/Noop) | ~1-2s | Great |
| **Ollama** | Free | Extract only (Add/Noop via novelty check) | ~5s | Good |
| **Skip** | Free | None by default (retrieval only) | N/A | N/A |

- **Full AUDN** means the LLM compares new facts against existing memories and decides whether to add, update, delete, or skip
- **Ollama** can extract facts but uses cosine similarity for dedup instead of LLM reasoning — no updates or deletions of stale memories
- **Skip** means hooks retrieve memories. By default no new memories are added; optional fallback add mode exists (`EXTRACT_FALLBACK_ADD=true`) and also activates on provider runtime failures (for example 429/timeouts).

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORIES_URL` | `http://localhost:8900` | Memories service URL |
| `MEMORIES_API_KEY` | (empty) | API key for Memories service auth |
| `EXTRACT_PROVIDER` | (none) | `anthropic`, `openai`, `ollama`, or empty to disable |
| `EXTRACT_MODEL` | (per provider) | Override model. Defaults: `claude-haiku-4-5-20251001`, `gpt-4.1-nano`, `gemma3:4b` |
| `ANTHROPIC_API_KEY` | (none) | Required when `EXTRACT_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | (none) | Required when `EXTRACT_PROVIDER=openai` |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server URL (on Linux, use `http://localhost:11434`) |
| `EXTRACT_FALLBACK_ADD` | `false` | Enable add-only fallback when extraction is disabled or provider calls fail at runtime |
| `EXTRACT_FALLBACK_MAX_FACTS` | `1` | Max fallback facts per request |
| `EXTRACT_FALLBACK_MIN_FACT_CHARS` | `24` | Minimum candidate fact length |
| `EXTRACT_FALLBACK_MAX_FACT_CHARS` | `280` | Maximum candidate fact length |
| `EXTRACT_FALLBACK_NOVELTY_THRESHOLD` | `0.88` | Novelty threshold for fallback adds |
| `MEMORIES_HOOKS_DIR` | `~/.claude/hooks/memory` | Override Claude hooks location |

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

Remove or comment out `EXTRACT_PROVIDER` from your shell profile:
```bash
# export EXTRACT_PROVIDER="anthropic"  # commented out
```

Retrieval still works. Extraction paths will return "not configured" unless fallback mode is enabled.

Optional fallback mode:
```bash
export EXTRACT_FALLBACK_ADD=true
```
This enables strict add-only fallback writes (no AUDN update/delete behavior), including runtime provider failures such as quota/rate-limit errors.

### Remove all integrations

```bash
# Remove Claude hooks
rm -rf ~/.claude/hooks/memory/

# Remove Codex notify hook
rm -rf ~/.codex/hooks/memory/

# Remove Codex config entries
# Edit ~/.codex/config.toml and remove:
# - notify entry that points to memory-codex-notify.sh
# - [mcp_servers.memories] section
# - [mcp_servers.memories.env] section

# Remove env vars from shell profile (optional)
# Edit ~/.zshrc and remove MEMORIES_URL, EXTRACT_PROVIDER, etc.
```

---

## Troubleshooting

### Hooks / notify not firing

```bash
# Claude: check hook scripts are executable
ls -la ~/.claude/hooks/memory/

# Claude: test recall hook manually
echo '{"cwd": "/Users/you/project", "session_type": "startup"}' | bash ~/.claude/hooks/memory/memory-recall.sh

# Codex: check notify script
ls -la ~/.codex/hooks/memory/

# Codex: test notify script manually with a sample payload
bash ~/.codex/hooks/memory/memory-codex-notify.sh '{"type":"agent-turn-complete","cwd":"/Users/you/project","input-messages":["remember this decision"],"last-assistant-message":"stored"}'
```

### Extraction returning 501

```bash
# Extraction is disabled. Set EXTRACT_PROVIDER:
export EXTRACT_PROVIDER="anthropic"  # or openai, ollama
# Then restart your shell and Claude Code session
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
