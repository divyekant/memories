# Memories CC Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the memories CC integration (hooks, skills, CLAUDE.md) as a CC plugin with native auto-update, replacing the current manual 4-location install.

**Architecture:** Create a `plugin/` directory in the memories repo following CC's plugin structure (package.json, hooks/, skills/, CLAUDE.md). Move canonical hook and skill sources into the plugin. Update all hook path resolution from `dirname "$0"` to `dirname "${BASH_SOURCE[0]}"` and hooks.json from `MEMORIES_HOOKS_DIR` to `CLAUDE_PLUGIN_ROOT`. Add a setup skill for backend provisioning and backend version checking in the recall hook.

**Tech Stack:** Bash (hooks), Markdown (skills, CLAUDE.md), JSON (package.json, hooks.json), Docker Compose (backend assets)

**Spec:** `docs/superpowers/specs/2026-04-04-memories-cc-plugin-design.md`

---

### Task 1: Create plugin skeleton

**Files:**
- Create: `plugin/package.json`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "memories",
  "version": "5.1.0",
  "description": "Persistent, searchable memory for Claude Code via Memories MCP"
}
```

- [ ] **Step 2: Verify structure**

Run: `ls plugin/`
Expected: `package.json`

- [ ] **Step 3: Commit**

```bash
git add plugin/package.json
git commit -m "feat(plugin): create plugin skeleton with package.json"
```

---

### Task 2: Move hooks into plugin

**Files:**
- Move: `integrations/claude-code/hooks/*` → `plugin/hooks/`
- Keep: `integrations/claude-code/hooks/` as symlink to `plugin/hooks/` for backward compat

- [ ] **Step 1: Copy all hook files to plugin directory**

```bash
mkdir -p plugin/hooks
cp integrations/claude-code/hooks/* plugin/hooks/
```

- [ ] **Step 2: Replace integrations hooks with symlink**

```bash
rm -rf integrations/claude-code/hooks
ln -s ../../plugin/hooks integrations/claude-code/hooks
```

- [ ] **Step 3: Verify symlink works**

Run: `ls -la integrations/claude-code/hooks/memory-recall.sh`
Expected: file exists and is readable

- [ ] **Step 4: Commit**

```bash
git add plugin/hooks/ integrations/claude-code/hooks
git commit -m "feat(plugin): move hooks to plugin/, symlink from integrations/"
```

---

### Task 3: Update hooks.json to use CLAUDE_PLUGIN_ROOT

**Files:**
- Modify: `plugin/hooks/hooks.json`

- [ ] **Step 1: Replace all MEMORIES_HOOKS_DIR references with CLAUDE_PLUGIN_ROOT**

In `plugin/hooks/hooks.json`, replace every occurrence of:
```
${MEMORIES_HOOKS_DIR:-~/.claude/hooks/memory}
```
with:
```
${CLAUDE_PLUGIN_ROOT}/hooks
```

There are 12 entries to update across SessionStart, UserPromptSubmit, Stop, PreCompact, PostCompact, PostToolUse (x2), PreToolUse, SubagentStart, SubagentStop, ConfigChange, and SessionEnd.

- [ ] **Step 2: Verify no old references remain**

Run: `grep -c 'MEMORIES_HOOKS_DIR' plugin/hooks/hooks.json`
Expected: `0`

Run: `grep -c 'CLAUDE_PLUGIN_ROOT' plugin/hooks/hooks.json`
Expected: `12`

- [ ] **Step 3: Commit**

```bash
git add plugin/hooks/hooks.json
git commit -m "feat(plugin): update hooks.json paths to CLAUDE_PLUGIN_ROOT"
```

---

### Task 4: Update all hook scripts path resolution

**Files:**
- Modify: all 12 `.sh` files in `plugin/hooks/`

- [ ] **Step 1: Fix _lib.sh resolution in all hooks**

In every hook script, replace:
```bash
_LIB="$(dirname "$0")/_lib.sh"
```
with:
```bash
_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
```

Scripts to update: `memory-recall.sh`, `memory-query.sh`, `memory-extract.sh`, `memory-flush.sh`, `memory-rehydrate.sh`, `memory-commit.sh`, `memory-subagent-recall.sh`, `memory-subagent-capture.sh`, `memory-tool-observe.sh`, `memory-observe.sh`, `memory-guard.sh`, `memory-config-guard.sh`.

- [ ] **Step 2: Fix response-hints.json resolution in memory-query.sh**

In `plugin/hooks/memory-query.sh`, replace:
```bash
local hints_file="$(dirname "$0")/response-hints.json"
```
with:
```bash
local hints_file="$(dirname "${BASH_SOURCE[0]}")/response-hints.json"
```

- [ ] **Step 3: Verify no old dirname "$0" references remain**

Run: `grep -rn 'dirname "$0"' plugin/hooks/*.sh`
Expected: no output (all replaced)

Run: `grep -c 'BASH_SOURCE' plugin/hooks/*.sh | grep -v ':0$'`
Expected: 12 files with at least 1 match each

- [ ] **Step 4: Commit**

```bash
git add plugin/hooks/
git commit -m "feat(plugin): update hook path resolution to BASH_SOURCE"
```

---

### Task 5: Move memories skill into plugin

**Files:**
- Move: `skills/memories/SKILL.md` → `plugin/skills/memories/SKILL.md`

- [ ] **Step 1: Copy skill to plugin**

```bash
mkdir -p plugin/skills/memories
cp skills/memories/SKILL.md plugin/skills/memories/SKILL.md
```

- [ ] **Step 2: Replace original with symlink**

```bash
rm skills/memories/SKILL.md
ln -s ../../plugin/skills/memories/SKILL.md skills/memories/SKILL.md
```

- [ ] **Step 3: Verify symlink**

Run: `head -3 skills/memories/SKILL.md`
Expected: frontmatter with `name: memories`

- [ ] **Step 4: Commit**

```bash
git add plugin/skills/memories/SKILL.md skills/memories/SKILL.md
git commit -m "feat(plugin): move memories skill to plugin/, symlink from skills/"
```

---

### Task 6: Create plugin CLAUDE.md

**Files:**
- Create: `plugin/CLAUDE.md`

- [ ] **Step 1: Write behavioral overrides**

Create `plugin/CLAUDE.md` with the memory behavior rules from the spec. These are the always-active instructions that make memory non-optional:

```markdown
# Memories

You have access to a persistent, semantically searchable memory system via Memories MCP.
Hooks automatically recall and capture memories at lifecycle boundaries.
Your job is to USE the recalled context and STORE decisions at natural breakpoints.

## Behavioral Rules (always active)

### Before responding
- **Search memories BEFORE answering** any question about: prior decisions, architecture, conventions, deferred work, past bugs, project history, or resuming a topic. Do not guess from code alone when context may exist in memories.
- **Search memories BEFORE asking a clarifying question.** The answer may already be stored. Check first.
- When hooks inject `## Retrieved Memories`, read them carefully. They are curated context, not noise.

### When responding with remembered context
- **Lead with the answer in sentence one.** Do not preamble with "Based on memories..." or "Let me check what we decided..." — just answer.
- **Never use meta phrases** like "memory confirms", "stored decision", "the remembered context says", "according to prior sessions". These are implementation details. Just state the fact.
- **Preserve boundary conditions.** When a memory includes `until`, `unless`, `because`, or `blocked on`, carry that clause into your answer verbatim. Do not compress "X until Y" into just "X".
- **Say `not yet`, `deferred`, or `blocked on` directly** when a memory shows incomplete work. Do not soften these into "we could consider" or "there's an opportunity to".

### Decisions
- **Do not ask the user to reconfirm** a remembered decision. If the memory says "we chose X because Y", answer as if X is current unless the user explicitly says otherwise.
- **Use `memory_extract` or `memory_add`** at natural breakpoints: architectural decisions, deferred work, non-obvious fixes, phase transitions. Don't batch up — capture as they happen.

### On short follow-ups
- When the user sends a short follow-up (< 30 words), the hooks inject recent transcript context into the memory search. Trust the retrieved results.
- **Keep the concrete choice and the trigger condition together** in the same sentence. Don't split them across paragraphs.

## Setup

If the Memories service is not reachable (health check fails at session start), run `/memories:setup` to provision the backend.
```

- [ ] **Step 2: Commit**

```bash
git add plugin/CLAUDE.md
git commit -m "feat(plugin): add CLAUDE.md with behavioral overrides"
```

---

### Task 7: Create setup skill

**Files:**
- Create: `plugin/skills/setup/SKILL.md`

- [ ] **Step 1: Write setup skill**

```markdown
---
name: setup
description: "Set up or update the Memories backend. Use when the service is unreachable, when first installing, or to update the Docker containers. Triggers on 'memories setup', 'set up memories', 'install memories', or when SessionStart health check fails."
---

# Memories Setup

Interactive provisioning for the Memories backend service.

## Process

### Step 1: Check Docker

Verify Docker is available:

\`\`\`bash
docker --version
\`\`\`

If not found, tell the user to install Docker Desktop or OrbStack first.

### Step 2: Check if service is running

\`\`\`bash
curl -sf http://localhost:8900/health
\`\`\`

If running, show the version and ask if the user wants to update.

### Step 3: Deploy or update

If not running (fresh install):
1. Find and copy docker-compose.standalone.yml from the plugin's assets directory.
   The plugin is installed at one of these locations — check in order:
   - \`~/.claude/plugins/marketplaces/dk-marketplace/plugins/memories/assets/\`
   - \`~/.claude/plugins/cache/dk-marketplace/memories/*/assets/\`
   \`\`\`bash
   mkdir -p ~/.config/memories
   PLUGIN_ASSETS=$(find ~/.claude/plugins -path "*/memories/assets/docker-compose.standalone.yml" 2>/dev/null | head -1)
   cp "$PLUGIN_ASSETS" ~/.config/memories/docker-compose.yml
   \`\`\`
2. Ask about extraction provider: Anthropic (recommended) / OpenAI / Ollama / Skip
3. Write ~/.config/memories/env with chosen settings
4. Start: \`cd ~/.config/memories && docker compose up -d\`

If running (upgrade):
1. \`cd ~/.config/memories && docker compose pull && docker compose up -d\`

### Step 4: Health check

\`\`\`bash
curl -sf http://localhost:8900/health
\`\`\`

Confirm service is reachable and show version.

### Step 5: Configure MCP

Check if memories MCP is configured in ~/.claude/mcp.json. If not, add it:

\`\`\`json
{
  "memories": {
    "type": "stdio",
    "command": "docker",
    "args": ["exec", "-i", "memories-mcp-1", "python", "-m", "mcp_server"]
  }
}
\`\`\`

Or if the user prefers HTTP: guide them to the appropriate MCP config.

### Step 6: Ensure auto-update

Read ~/.claude/plugins/known_marketplaces.json. Find the marketplace entry that contains the memories plugin. If \`autoUpdate\` is not \`true\`, set it to \`true\` and save.

\`\`\`bash
# Check current setting
cat ~/.claude/plugins/known_marketplaces.json | jq '.["dk-marketplace"].autoUpdate'
\`\`\`

If false or missing, update it.
```

- [ ] **Step 2: Commit**

```bash
git add plugin/skills/setup/SKILL.md
git commit -m "feat(plugin): add setup skill for backend provisioning"
```

---

### Task 8: Create docker-compose.standalone.yml

**Files:**
- Create: `plugin/assets/docker-compose.standalone.yml`

- [ ] **Step 1: Write standalone compose file**

Based on the existing `docker-compose.yml` but using pre-built GHCR images instead of local build. Key differences from source compose: GHCR image instead of local build, named volumes instead of `./data`, port mapping `8900:8000` (app listens on 8000 internally), `API_KEY` env var (not `MEMORIES_API_KEY`).

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.15.4
    restart: unless-stopped
    ports:
      - "127.0.0.1:6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage

  memories:
    image: ghcr.io/divyekant/memories:${MEMORIES_IMAGE_TAG:-latest}
    restart: unless-stopped
    depends_on:
      - qdrant
    ports:
      - "8900:8000"
    volumes:
      - memories_data:/data
    environment:
      - DATA_DIR=/data
      - API_KEY=${API_KEY:-}
      - MODEL_NAME=all-MiniLM-L6-v2
      - EMBED_PROVIDER=${EMBED_PROVIDER:-onnx}
      - EMBED_MODEL=${EMBED_MODEL:-}
      - QDRANT_URL=http://qdrant:6333
      - QDRANT_COLLECTION=${QDRANT_COLLECTION:-memories}
      - QDRANT_WAIT=true
      - EXTRACT_PROVIDER=${EXTRACT_PROVIDER:-}
      - EXTRACT_MODEL=${EXTRACT_MODEL:-}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - OLLAMA_URL=${OLLAMA_URL:-}
      - MALLOC_ARENA_MAX=2
    healthcheck:
      test: ["CMD", "python", "-c", "import sys,urllib.request; sys.exit(0 if 200 <= urllib.request.urlopen('http://localhost:8000/health', timeout=3).getcode() < 400 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s

volumes:
  qdrant_data:
  memories_data:
```

- [ ] **Step 2: Commit**

```bash
git add plugin/assets/docker-compose.standalone.yml
git commit -m "feat(plugin): add standalone docker-compose for backend provisioning"
```

---

### Task 9: Add backend version check to recall hook

**Files:**
- Modify: `plugin/hooks/memory-recall.sh`

- [ ] **Step 1: Add version check after health check**

In `plugin/hooks/memory-recall.sh`, after the `fi` on line 61 that closes the health check block, add backend version comparison:

```bash
# Backend version check
EXPECTED_VERSION_FILE="$(dirname "${BASH_SOURCE[0]}")/../assets/BACKEND_VERSION"
if [ -f "$EXPECTED_VERSION_FILE" ]; then
  EXPECTED_VERSION=$(cat "$EXPECTED_VERSION_FILE" | tr -d '[:space:]')
  RUNNING_VERSION=$(curl -sf --max-time 2 "$MEMORIES_URL/health" 2>/dev/null | jq -r '.version // empty')
  if [ -n "$RUNNING_VERSION" ] && [ -n "$EXPECTED_VERSION" ] && [ "$RUNNING_VERSION" != "$EXPECTED_VERSION" ]; then
    _log_warn "Backend version mismatch: running=$RUNNING_VERSION expected=$EXPECTED_VERSION"
    VERSION_WARNING=$(printf '## Memories Backend Update Available\n\nRunning v%s, latest is v%s. Run `/memories:setup` to update, or: `cd ~/.config/memories && docker compose pull && docker compose up -d`' "$RUNNING_VERSION" "$EXPECTED_VERSION")
    if [ -n "$HEALTH_WARNING" ]; then
      HEALTH_WARNING=$(printf '%s\n\n%s' "$HEALTH_WARNING" "$VERSION_WARNING")
    else
      HEALTH_WARNING="$VERSION_WARNING"
    fi
  fi
fi
```

- [ ] **Step 2: Create BACKEND_VERSION file**

```bash
echo "5.0.2" > plugin/assets/BACKEND_VERSION
```

This file gets updated on each release to track the expected backend version.

- [ ] **Step 3: Commit**

```bash
git add plugin/hooks/memory-recall.sh plugin/assets/BACKEND_VERSION
git commit -m "feat(plugin): add backend version check to recall hook"
```

---

### Task 10: Add marketplace manifest entry

**Files:**
- Note: This is in the dk-marketplace repo, not the memories repo. Document the required change.
- Create: `plugin/INSTALL.md` — installation instructions

- [ ] **Step 1: Write INSTALL.md**

```markdown
# Installing the Memories Plugin

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
```

- [ ] **Step 2: Commit**

```bash
git add plugin/INSTALL.md
git commit -m "docs(plugin): add installation instructions"
```

---

### Task 11: Verify plugin works end-to-end

- [ ] **Step 1: Symlink plugin to dk-marketplace**

```bash
ln -s /Users/dk/projects/memories/plugin ~/.claude/plugins/marketplaces/dk-marketplace/plugins/memories
```

- [ ] **Step 2: Start a new CC session in a test project**

Open a new CC session. Check that:
- `## Relevant Memories` appears in the SessionStart context (recall hook fired)
- `## Retrieved Memories` with `IMPORTANT:` prefix appears after typing a prompt (query hook fired)
- The memories skill is available (`/memories`)
- The setup skill is available (`/memories:setup`)

- [ ] **Step 3: Verify extraction fires**

Make a statement like "I decided to use Redis for caching because of pub/sub support." End the turn. Check `~/.config/memories/hook.log` for an extraction entry.

- [ ] **Step 4: Verify subagent recall**

Spawn a Plan subagent. Check that it receives `## Project Memories` in its context.

- [ ] **Step 5: Document any issues**

If anything fails, fix it before proceeding to migration.

---

### Task 12: Migrate from manual install

- [ ] **Step 1: Verify plugin is loaded (Task 11 passed)**

- [ ] **Step 2: Remove old hooks directory**

```bash
rm -rf ~/.claude/hooks/memory/
```

- [ ] **Step 3: Remove memory section from global CLAUDE.md**

Edit `~/.claude/CLAUDE.md` and remove the `## Memory Behavior (OVERRIDE)` section and the `## Memory Routing` section. These are now in the plugin's CLAUDE.md.

- [ ] **Step 4: Remove memory hooks from settings.json**

Edit `~/.claude/settings.json` and remove all hook entries that reference `memory-` scripts (SessionStart, UserPromptSubmit, Stop, PreCompact, PostCompact, PostToolUse, PreToolUse, SubagentStart, SubagentStop, ConfigChange, SessionEnd).

- [ ] **Step 5: Verify again**

Start a new CC session. Confirm recall, query, and extraction still work through the plugin path.

- [ ] **Step 6: Commit migration cleanup**

```bash
git add -A
git commit -m "chore(plugin): complete migration from manual install to plugin"
```
