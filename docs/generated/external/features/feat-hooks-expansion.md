---
id: feat-006
type: feature-doc
title: Expanded Hook System
audience: external
generated: 2026-03-18
---

# Expanded Hook System

Memories now ships 10 Claude Code hooks covering the full session lifecycle — from start to compaction to subagent teardown. The 5 new hooks handle post-compaction rehydration, subagent memory capture, tool usage observability, MEMORY.md write protection, and configuration drift detection.

## All 10 hooks at a glance

| Hook | Event | Mode | Purpose |
|------|-------|------|---------|
| `memory-recall.sh` | SessionStart | Sync | Load project memories + health check |
| `memory-query.sh` | UserPrompt | Sync | Search memories with transcript context |
| `memory-extract.sh` | PostResponse | Async | Extract facts via AUDN pipeline |
| `memory-flush.sh` | PreCompact | Async | Aggressive extraction before context loss |
| `memory-rehydrate.sh` | PostCompact | Sync | Re-inject memories using compact summary |
| `memory-subagent-capture.sh` | SubagentStop | Async | Capture Plan/Explore agent decisions |
| `memory-observe.sh` | PostToolUse | Async | Log MCP tool invocations |
| `memory-guard.sh` | PreToolUse | Sync | Block direct MEMORY.md writes |
| `memory-config-guard.sh` | ConfigChange | Sync | Warn if hooks removed from settings |
| `memory-commit.sh` | SessionEnd | Async | Final extraction pass |

**Sync hooks** block until they complete and can inject `additionalContext` into the conversation. **Async hooks** run fire-and-forget.

## New hooks in detail

### memory-rehydrate.sh (PostCompact)

When Claude Code compacts context, you lose the memories that were injected earlier in the session. This hook fires immediately after compaction, takes the `compact_summary` as a search query, and re-injects the most relevant memories into the fresh context.

**What it does:**
1. Reads `compact_summary` from the hook input
2. Searches across your configured source prefixes (default: `claude-code/{project}`, `learning/{project}`, `wip/{project}`)
3. Deduplicates and ranks results by similarity
4. Injects up to 6 memories as `additionalContext` under `## Re-Hydrated Memories (Post-Compaction)`

**Configuration:**

| Env var | Default | Description |
|---------|---------|-------------|
| `MEMORIES_SOURCE_PREFIXES` | `claude-code/{project},learning/{project},wip/{project}` | Comma-separated source prefix templates |

### memory-subagent-capture.sh (SubagentStop)

When Claude Code spawns Plan or Explore subagents, their decisions and findings are often valuable but ephemeral — they disappear when the subagent exits. This hook captures the last few messages from the subagent transcript and sends them for extraction.

**What it does:**
1. Reads `agent_type` from hook input
2. Only fires for `Plan` and `Explore` agents (skips all others)
3. Extracts the last 6 text messages (up to 4000 chars) from the subagent transcript
4. Sends them to `/memory/extract` with context `subagent_stop`

**Configuration:**

| Env var | Default | Description |
|---------|---------|-------------|
| `MEMORIES_EXTRACT_SOURCE` | `claude-code/{project}` | Source template for extracted memories |

### memory-observe.sh (PostToolUse)

A lightweight observer that appends a timestamped line to a log file every time an MCP tool is invoked. Useful for understanding tool usage patterns and debugging integration issues.

**What it does:**
1. Reads `tool_name` from hook input
2. Appends `TIMESTAMP TOOL_NAME` to the log file
3. Exits immediately (fire-and-forget)

**Configuration:**

| Env var | Default | Description |
|---------|---------|-------------|
| `MEMORIES_TOOL_LOG` | `~/.config/memories/tool-usage.log` | Path to tool usage log file |

**Example log output:**
```
2026-03-18T14:32:01Z memory_search
2026-03-18T14:32:15Z memory_add
2026-03-18T14:33:02Z memory_extract
```

### memory-guard.sh (PreToolUse)

Prevents Claude Code from writing directly to MEMORY.md files, which should be managed by the Memories MCP sync process. When a Write or Edit tool targets a MEMORY.md file, this hook blocks the operation and tells the agent to use `memory_add` or `memory_extract` instead.

**What it does:**
1. Reads `tool_input.file_path` from hook input
2. If the path ends with `MEMORY.md` or `memory/MEMORY.md`, exits with code 2 (block)
3. All other file writes pass through normally

**Behavior:** This hook exits with code 2 on blocked writes, which tells Claude Code to abort the tool call and show the agent an error message.

### memory-config-guard.sh (ConfigChange)

Monitors changes to `~/.claude/settings.json` and warns if memory hooks appear to have been removed. This catches accidental configuration drift — for example, if a settings update or reset drops the hook entries.

**What it does:**
1. Only fires on `user_settings` source changes
2. Checks `~/.claude/settings.json` for `memory-recall`, `memory-query`, and `memory-extract` entries
3. If any are missing, injects a warning as `additionalContext`

## Shared configuration

All hooks read their runtime configuration from `~/.config/memories/env`:

```bash
# Required
MEMORIES_URL=http://localhost:8900

# Optional
MEMORIES_API_KEY=your-api-key
MEMORIES_ENV_FILE=~/.config/memories/env
```

Hook logs are written to `~/.config/memories/hook.log` with automatic rotation (managed by `_lib.sh`).

## Installing

If you ran the installer after the hooks expansion, all 10 hooks are already configured:

```bash
./integrations/claude-code/install.sh --auto
```

To verify your hook configuration:

```bash
jq '.hooks' ~/.claude/settings.json
```
