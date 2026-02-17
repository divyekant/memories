# Design: Automatic Memory Layer

**Date:** 2026-02-16
**Status:** Proposed
**Branch:** `feature/automatic-memory-layer`

## Problem

FAISS Memory works, but relies on the AI agent choosing to call `memory_search` and `memory_add`. In practice, agents frequently skip these calls — they forget to search for context at session start, they don't store decisions made during conversation, and they never update stale memories.

Mem0 and Supermemory solve this by making memory **automatic**: retrieve on every prompt, extract and store after every response. We can achieve the same using Claude Code's hooks system, Codex's compatible hooks, and OpenClaw's skill system — no new dependencies, no cloud services beyond what the user already has.

## Goals

1. **Automatic retrieval** — inject relevant memories into every prompt without the agent deciding to search
2. **Automatic extraction** — extract facts from every conversation turn without the agent deciding to store
3. **AUDN (Add/Update/Delete/Noop)** — when a new fact arrives, compare against existing memories and decide the right action (not just dedup by similarity)
4. **Optional** — the entire feature is opt-in; the base FAISS service works exactly as before without it
5. **Multi-client** — works with Claude Code, Codex, and OpenClaw from day one
6. **Configurable LLM provider** — Anthropic, OpenAI, or Ollama for extraction

## Non-Goals

- Knowledge graph / entity linking (flat memories are fine for single-user)
- Temporal decay scoring (not needed at our scale)
- Multi-user scoping (single-user system)

## Architecture

```
┌─────────────────────────────────────────────────┐
│              AI Coding Client                    │
│                                                  │
│  SessionStart ──→ [memory-recall hook]           │
│       │           GET /search (project context)  │
│       │           Inject as additionalContext     │
│       │                                          │
│  UserPromptSubmit ──→ [memory-query hook]        │
│       │               GET /search (prompt)       │
│       │               Inject relevant memories   │
│       │                                          │
│  Stop ──→ [memory-extract hook] (async)          │
│       │   Read transcript, POST /memory/extract  │
│       │                                          │
│  PreCompact ──→ [memory-flush hook] (async)      │
│       │         Aggressive extract before loss   │
│       │                                          │
│  SessionEnd ──→ [memory-commit hook] (async)     │
│                 Final extraction pass            │
└─────────────────────────────────────────────────┘
         │                    ▲
         ▼                    │
┌─────────────────────────────────────────────────┐
│         FAISS Memory Service (:8900)             │
│                                                  │
│  Existing:                                       │
│    POST /search        (hybrid BM25+vector)      │
│    POST /memory/add    (with auto-dedup)         │
│    POST /memory/is-novel                         │
│                                                  │
│  New:                                            │
│    POST /memory/extract    (LLM extraction+AUDN) │
│    POST /memory/supersede  (targeted update)     │
│    GET  /extract/status    (provider health)     │
│                                                  │
│  Extraction Pipeline (inside /memory/extract):   │
│    1. Call LLM → extract atomic facts            │
│    2. Per fact: search FAISS for top-5 similar   │
│    3. Call LLM → AUDN decision per fact          │
│    4. Execute: add / update / delete / noop      │
└─────────────────────────────────────────────────┘
```

## Part 1: Server-Side Changes

### New endpoint: `POST /memory/extract`

Accepts a conversation chunk, extracts atomic facts via LLM, runs AUDN against existing memories.

**Request:**
```json
{
  "messages": "User: use drizzle...\nAssistant: Good call...",
  "source": "claude-code/my-project",
  "context": "stop"
}
```

`context` is a hint for extraction aggressiveness:
- `stop` — normal turn, extract decisions/preferences/bugs
- `pre_compact` — context about to be lost, extract everything potentially useful
- `session_end` — final pass, same as stop

**Response:**
```json
{
  "actions": [
    {"action": "add", "text": "Switched from Prisma to Drizzle ORM", "id": 121},
    {"action": "update", "old_id": 45, "text": "Uses Drizzle ORM (previously Prisma)", "new_id": 122},
    {"action": "noop", "text": "TypeScript strict mode", "existing_id": 30}
  ],
  "extracted_count": 5,
  "stored_count": 2,
  "updated_count": 1
}
```

### Extraction pipeline

Implemented as a new module `llm_extract.py`:

1. **Fact extraction** — Single LLM call with extraction prompt. Input: conversation messages. Output: JSON array of atomic facts. The prompt focuses on: decisions made, preferences expressed, bugs found + fixes, architectural choices, tool/library selections.

2. **AUDN cycle** — For each extracted fact:
   a. Embed and search FAISS for top-5 similar existing memories
   b. Single LLM call with AUDN prompt: present the new fact + existing similar memories, ask the LLM to choose ADD / UPDATE / DELETE / NOOP
   c. Execute the chosen action

3. **Batching optimization** — For the common case (3-5 facts), batch the AUDN decisions into a single LLM call rather than one per fact. Send all facts + their similar memories in one request. This reduces LLM calls from N+1 to 2 per extraction.

### New endpoint: `POST /memory/supersede`

Targeted memory update with audit trail.

**Request:**
```json
{
  "old_id": 42,
  "new_text": "Uses Drizzle ORM (switched from Prisma)",
  "source": "claude-code/my-project"
}
```

Deletes old memory, creates new one with `supersedes: 42` in metadata. The old memory's text is preserved in the new memory's metadata as `previous_text` for audit.

### New endpoint: `GET /extract/status`

Returns extraction provider health and configuration:

```json
{
  "enabled": true,
  "provider": "anthropic",
  "model": "claude-haiku-4-5-20251001",
  "status": "healthy"
}
```

Returns `{"enabled": false}` when `EXTRACT_PROVIDER` is not set.

### LLM provider abstraction

New module `llm_provider.py` with a simple interface:

```python
class LLMProvider:
    def complete(self, system: str, user: str) -> str: ...

class AnthropicProvider(LLMProvider): ...   # uses anthropic SDK
class OpenAIProvider(LLMProvider): ...      # uses openai SDK
class OllamaProvider(LLMProvider): ...      # uses HTTP to ollama
```

Configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRACT_PROVIDER` | (none) | `anthropic`, `openai`, or `ollama`. Empty = extraction disabled. |
| `EXTRACT_MODEL` | (per provider) | Model to use. Defaults: `claude-haiku-4-5-20251001`, `gpt-4.1-nano`, `gemma3:4b` |
| `ANTHROPIC_API_KEY` | (none) | Required if provider is `anthropic` |
| `OPENAI_API_KEY` | (none) | Required if provider is `openai` |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server URL |

Dependencies are optional — `anthropic` and `openai` SDKs installed only when the provider is selected. Similar to the existing `requirements-cloud.txt` pattern:

```
# requirements-extract.txt
anthropic>=0.40.0
openai>=1.50.0
```

Dockerfile uses a build arg:
```dockerfile
ARG ENABLE_EXTRACT=false
RUN if [ "$ENABLE_EXTRACT" = "true" ]; then pip install -r requirements-extract.txt; fi
```

### Extraction prompts

Two prompts, stored as constants in `llm_extract.py`:

**FACT_EXTRACTION_PROMPT:**
```
Extract atomic facts worth remembering from this conversation.
Focus on: decisions made, preferences expressed, bugs found + root causes + fixes,
architectural choices, tool/library selections, project conventions.
Output a JSON array of strings, one fact per element.
Each fact should be self-contained and understandable without the conversation.
If nothing worth storing, output [].
```

**AUDN_PROMPT:**
```
You are a memory manager. For each new fact, decide what to do given
the existing similar memories.

Actions:
- ADD: No similar memory exists. Store as new.
- UPDATE <id>: An existing memory covers the same topic but the information
  has changed. Provide the updated text that replaces it.
- DELETE <id>: An existing memory is now contradicted or obsolete.
- NOOP: The fact is already captured by an existing memory.

Output JSON array of decisions.
```

### Ollama-specific considerations

When using Ollama (local, free), AUDN is simplified:
- Extraction works normally (gemma3:4b handles this well in ~5s)
- AUDN is downgraded to novelty-check-only: instead of the full AUDN prompt, we use the existing `/memory/is-novel` endpoint with cosine similarity. No UPDATE/DELETE, only ADD/NOOP.
- This is documented clearly: "Local models support extraction but not intelligent updates. Use Anthropic or OpenAI for full AUDN."

## Part 2: Client-Side Integrations

### Claude Code / Codex hooks

Five shell scripts in `integrations/claude-code/hooks/`:

#### memory-recall.sh (SessionStart, sync, timeout: 3s)

```bash
# Reads: cwd from stdin JSON
# Searches FAISS for project-specific memories
# Returns additionalContext with relevant memories
PROJECT=$(basename "$CWD")
RESULTS=$(curl -s POST /search with "project $PROJECT" query)
# Output JSON with hookSpecificOutput.additionalContext
```

Token cost: ~500-1500 tokens, once per session.

#### memory-query.sh (UserPromptSubmit, sync, timeout: 2s)

```bash
# Reads: prompt from stdin JSON
# Skips prompts shorter than 20 chars
# Searches FAISS for memories relevant to this prompt
# Returns additionalContext
```

Token cost: ~200-800 tokens per turn. High similarity threshold (0.4) keeps noise low.

#### memory-extract.sh (Stop, async, timeout: 30s)

```bash
# Reads: transcript_path, stop_hook_active from stdin JSON
# Skips if stop_hook_active is true (prevents loops)
# Reads last exchange from transcript
# POSTs to /memory/extract
```

Token cost: Zero locally (the server pays). ~$0.001/turn if using API provider.

#### memory-flush.sh (PreCompact, async, timeout: 30s)

Same as extract but with `"context": "pre_compact"` — more aggressive extraction since context is about to be lost.

#### memory-commit.sh (SessionEnd, async, timeout: 30s)

Same as extract with `"context": "session_end"`.

#### hooks.json

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup|resume",
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-recall.sh",
        "timeout": 3
      }]
    }],
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-query.sh",
        "timeout": 2
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-extract.sh",
        "timeout": 30,
        "async": true
      }]
    }],
    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-flush.sh",
        "timeout": 30,
        "async": true
      }]
    }],
    "SessionEnd": [{
      "hooks": [{
        "type": "command",
        "command": "${FAISS_HOOKS_DIR:-~/.claude/hooks/memory}/memory-commit.sh",
        "timeout": 30,
        "async": true
      }]
    }]
  }
}
```

#### install.sh

Interactive installer:

```
$ ./integrations/claude-code/install.sh

FAISS Memory — Automatic Memory Layer Setup
============================================

[1/4] Checking FAISS service... ✓ (120 memories, healthy)

[2/4] Extraction provider (for automatic learning):
  1. Anthropic (recommended, ~$0.001/turn)
  2. OpenAI (~$0.001/turn)
  3. Ollama (free, local, extraction only — no AUDN)
  4. Skip (retrieval only, no automatic extraction)
  > 1

  Anthropic API key: sk-ant-...
  Testing... ✓ (claude-haiku-4-5-20251001)

[3/4] Installing hooks to ~/.claude/hooks/memory/... ✓
      Merging config into ~/.claude/settings.json... ✓

[4/4] Done!

  ✓ Session start: loads project memories
  ✓ Every prompt: retrieves relevant context
  ✓ After responses: extracts and stores new facts (Anthropic)

  Env vars added to ~/.zshrc:
    FAISS_URL=http://localhost:8900
    FAISS_API_KEY=your-api-key-here
    EXTRACT_PROVIDER=anthropic
    ANTHROPIC_API_KEY=sk-ant-...
```

### Codex

Codex supports Claude Code hooks format. The install is:

```bash
# Option A: Symlink (if memories repo is cloned)
ln -s /path/to/memories/integrations/claude-code/hooks ~/.codex/hooks/memory

# Option B: Use the installer
./integrations/claude-code/install.sh --codex
```

The `--codex` flag writes to Codex's config location instead of Claude Code's.

### OpenClaw

The existing `integrations/openclaw-skill.md` gets updated with:

1. A `memory_extract_faiss` function that POSTs conversation text to `/memory/extract`
2. Instructions in the skill to call this after completing significant tasks
3. A `memory_recall_faiss` function called at task start to load project context

OpenClaw doesn't have hooks, so extraction is agent-initiated (the skill instructs the agent when to call it). This is less automatic than hooks but better than nothing.

## Part 3: What This Does NOT Include

Per the Mem0/Supermemory research:

| Feature | Included? | Why |
|---------|-----------|-----|
| Automatic retrieval | Yes | Hooks inject on every prompt |
| Automatic extraction | Yes | Hooks extract after every turn |
| AUDN (smart updates) | Yes (API providers) | LLM decides add/update/delete/noop |
| Knowledge graph | No | Flat memories are fine for single-user dev |
| Temporal decay | No | Not needed at <1000 memories |
| Multi-user scoping | No | Single-user system |
| Relationship tracking | No | Supersede links provide basic lineage |

## Token Budget

| Hook | Frequency | Tokens injected | LLM cost |
|------|-----------|----------------|----------|
| SessionStart recall | 1x/session | ~1000 | Free (curl) |
| UserPromptSubmit query | Every turn | ~500 | Free (curl) |
| Stop extract | Every turn | 0 (async) | ~$0.001 (API) or free (Ollama) |
| PreCompact flush | Rare | 0 (async) | ~$0.002 (API) or free (Ollama) |
| SessionEnd commit | 1x/session | 0 (async) | ~$0.001 (API) or free (Ollama) |

Net effect: ~1500 extra tokens/turn in context (retrieval), ~$0.002/turn for extraction. Pays for itself by preventing re-discovery of decisions and context.

## Implementation Order

1. `llm_provider.py` — LLM abstraction (Anthropic/OpenAI/Ollama)
2. `llm_extract.py` — Extraction + AUDN pipeline
3. `POST /memory/extract` endpoint in `app.py`
4. `POST /memory/supersede` endpoint in `app.py`
5. `GET /extract/status` endpoint in `app.py`
6. Hook scripts (5 shell scripts)
7. `install.sh` installer
8. Codex install path
9. OpenClaw skill update
10. Tests
11. Documentation (README update)
12. Docker build arg for extraction deps
