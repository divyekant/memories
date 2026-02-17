# Memories — Automatic Memory Layer Setup

> **This document is designed to be fed directly to an LLM (Claude Code, Codex, OpenClaw, or any AI coding assistant) so it can set up automatic memory hooks for you.**

## What This Does

Memories is a local semantic memory service running at `http://localhost:8900`. This guide sets up **automatic hooks** so your AI assistant:

1. **Retrieves** relevant memories at session start and on every prompt (no manual search)
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

Codex uses the same hooks format as Claude Code. Two options:

### Option A: Symlink (if you cloned the memories repo)

```bash
mkdir -p ~/.codex/hooks
ln -s ~/projects/memories/integrations/claude-code/hooks ~/.codex/hooks/memory
```

### Option B: Run the installer with --codex flag

```bash
cd ~/projects/memories
./integrations/claude-code/install.sh --codex
```

This writes hooks to `~/.codex/` instead of `~/.claude/`.

---

## Setup for OpenClaw

OpenClaw doesn't have hooks, so memory is agent-initiated via the skill. Update the skill file:

1. Copy `integrations/openclaw-skill.md` to your OpenClaw skills directory
2. The skill includes `memory_recall_memories` (called at task start) and `memory_extract_memories` (called after completing tasks)
3. The agent is instructed when to call these automatically

---

## How the Hooks Work

| Hook | Event | Sync? | What It Does |
|------|-------|-------|-------------|
| `memory-recall.sh` | SessionStart | Sync | Searches Memories for project-specific memories, injects top 8 as context |
| `memory-query.sh` | UserPromptSubmit | Sync | Searches Memories for memories relevant to the current prompt (skips short prompts) |
| `memory-extract.sh` | Stop | Async | POSTs the last exchange to `/memory/extract` for fact extraction |
| `memory-flush.sh` | PreCompact | Async | Same as extract but with `context=pre_compact` (more aggressive before context loss) |
| `memory-commit.sh` | SessionEnd | Async | Final extraction pass when session ends |

**Token cost:** ~1500 tokens/turn injected context (retrieval). Extraction is async and free if using Ollama, ~$0.001/turn with API providers.

---

## Extraction Provider Comparison

| Provider | Cost | AUDN Support | Speed | Quality |
|----------|------|-------------|-------|---------|
| **Anthropic** (recommended) | ~$0.001/turn | Full (Add/Update/Delete/Noop) | ~1-2s | Best |
| **OpenAI** | ~$0.001/turn | Full (Add/Update/Delete/Noop) | ~1-2s | Great |
| **Ollama** | Free | Extract only (Add/Noop via novelty check) | ~5s | Good |
| **Skip** | Free | None (retrieval only) | N/A | N/A |

- **Full AUDN** means the LLM compares new facts against existing memories and decides whether to add, update, delete, or skip
- **Ollama** can extract facts but uses cosine similarity for dedup instead of LLM reasoning — no updates or deletions of stale memories
- **Skip** means hooks only retrieve memories, never extract. Good for testing retrieval before enabling extraction.

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
| `MEMORIES_HOOKS_DIR` | `~/.claude/hooks/memory` | Override hooks location |

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

The retrieval hooks will still work. Extraction hooks will silently skip (the Memories endpoint returns 501 when extraction is not configured).

### Remove all hooks

```bash
# Remove hook scripts
rm -rf ~/.claude/hooks/memory/

# Remove hook entries from settings.json
# Edit ~/.claude/settings.json and remove the SessionStart, UserPromptSubmit,
# Stop, PreCompact, and SessionEnd entries that reference memory-*.sh

# Remove env vars from shell profile
# Edit ~/.zshrc and remove MEMORIES_URL, EXTRACT_PROVIDER, etc.
```

---

## Troubleshooting

### Hooks not firing

```bash
# Check hook scripts are executable
ls -la ~/.claude/hooks/memory/

# Test a hook manually
echo '{"cwd": "/Users/you/project", "session_type": "startup"}' | bash ~/.claude/hooks/memory/memory-recall.sh
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
