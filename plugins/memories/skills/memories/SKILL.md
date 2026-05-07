---
name: memories
description: >-
  Memory discipline for capturing and recalling important context across sessions
  using Memories MCP. Use this skill whenever you make an architectural decision,
  defer work for later, discover a non-obvious pattern or fix, or when the user
  explicitly asks to remember something. Also use it when you're about to ask the
  user a clarifying question or when entering a project domain where prior context
  might exist — search memories first, the answer might already be stored. Triggers
  on: "remember this", "store this", "save this for later", phase transitions,
  post-decision moments, deferred work, implicit tech mentions, entering a new domain,
  and before clarifying questions. Even if the user doesn't say "do you remember",
  search proactively when the topic might have prior context.
---

# Memories

You have access to a persistent, semantically searchable memory system via Memories MCP.
This skill teaches you *when* and *how* to use it effectively. It complements — but does
not replace — the assistant's built-in auto-memory (which handles project conventions
passively) and lifecycle hooks (which provide baseline context at startup).

Your job is the judgment layer: deciding what's worth storing, when to store it, when
to actively search for context, and when to trigger lifecycle operations.

## Quick Operating Rules

- Search before asking architecture, resumption, or preference questions.
- If recalled context already answers the question, answer in sentence one.
- Do not narrate the memory process. Avoid phrases like `stored decision`, `memory confirms`, or `I found`.
- For short follow-ups, restate the current choice and the trigger together.
- If work is deferred or blocked, say `not yet`, `deferred`, or `blocked on` directly.
- Preserve boundary words like `until`, `unless`, and `because`.

Examples:
- `Does that still apply?` -> `Yes — SQLite is still the local cache until shared invalidation is required.`
- `Should we switch now?` -> `Not yet — stay on the current approach until the trigger changes.`
- `Is file-based storage okay for now?` -> `Yes — keep the simple local format until cross-device sync is required.`

## Three Responsibilities

### 1. Read — Proactive Recall

This is where the skill makes the biggest difference. Without it, you only search when
the user explicitly says "do you remember" — but most valuable recall opportunities
don't come with that cue.

**When to search (do this BEFORE asking questions or proposing solutions):**
- When the user asks about a feature or system that might have prior context — even if
  they don't reference a past session. "Add webhook support to the notifications service"
  should trigger a search for any stored context about notifications, webhooks, or
  related deferred work.
- When the user's message is a short follow-up that depends on recent context ("what
  about retries?", "does that still apply?", "should we keep it?"). Passive hook recall
  is often too generic here — run an explicit search using the recent conversation topic.
- Before asking a clarifying question about project architecture or preferences
- When picking up work in a domain where deferred tasks might exist
- Before attempting something that feels like it might have failed before
- When the user references a past decision or discussion you don't have context for
- When passive startup recall feels insufficient for the current task

**How to search:**
- Use `memory_search` with natural language queries
- Search project-scoped prefixes first when you know the project. Prefer
  `claude-code/{project}`, `learning/{project}`, and `wip/{project}` before broad global
  searches so unrelated projects do not drown out local signal.
- Try 2-3 query angles if the first search doesn't find what you need
- Search for deferred work: `"deferred TODO revisit later wip {project}"`
- Search for past failures: `"failure fix gotcha {technology}"`
- Search for decisions: `"decision chose selected approach {topic}"`
- Search by domain: `"{project} {feature-area} architecture design"`
- For short follow-ups, include recent conversation context in the query instead of
  searching with only the latest user message.

**What to do with results:**
- Surface relevant findings to the user before proposing solutions. If you find that
  a feature was previously deferred pending a dependency, tell the user before diving in.
- If no results found, proceed normally — but mention you checked if it's relevant context.

### 2. Write — Capture at Natural Breakpoints

Store memories when you encounter these moments during work:

**Hard triggers (always store):**
- User explicitly says "remember this", "store this", "save for later", or similar
- These bypass all judgment gates — capture immediately

**Soft triggers (use judgment):**
- An architectural decision was just made or confirmed — including *implicit* ones
  where the user casually mentions a technology choice ("I've been using Postgres for
  the user data") without formally deciding. If a future session would benefit from
  knowing this, store it.
- Work is being deferred ("we'll do this later", "parking this for now", "come back
  to it next week"). Always capture *what* was deferred and *why* (what's blocking it).
- You discovered a non-obvious fix, gotcha, or pattern. Store only the fix, not the
  debugging journey that led to it.
- A phase transition just happened (design -> implementation, debug -> fix)
- A major commit just landed that changes how the project works
- You're about to end a session with open threads
- Multiple decisions in one message — store each decision as a separate memory so
  they can be found independently. Don't combine three tech choices into one blob.

**What NOT to store:**
- Session-specific status ("currently working on X")
- Generic knowledge Claude already knows
- Anything that duplicates CLAUDE.md instructions
- Intermediate debugging steps — the env var that didn't work, the logs you checked,
  the error you saw along the way. Store only the resolution.
- Information that will be stale by next session
- Current task requests ("help me add a retry mechanism") — that's what you're doing
  right now, not something to remember

#### Choosing the Right Tool

You have two write paths — use the right one for the situation:

| Situation | Tool | Cost | Why |
|-----------|------|------|-----|
| Single clear new fact, no prior context | `memory_add` | Free | Simple, fast, sufficient |
| User says "remember X" (clear, novel) | `memory_add` | Free | Direct intent, no AUDN needed |
| Decision changed or reversed | `memory_extract` | ~$0.001 | AUDN finds and updates the old memory |
| Deferred work completed or updated | `memory_extract` | ~$0.001 | AUDN updates or deletes the wip/ entry |
| Rich conversation, multiple facts | `memory_extract` | ~$0.001 | Server splits facts and handles each |
| Contradiction found during recall | `memory_extract` | ~$0.001 | AUDN resolves old vs new |
| Learning superseded by better fix | `memory_extract` | ~$0.001 | AUDN updates the old learning |

**When using `memory_add`:** always check `memory_is_novel` first to prevent duplicates.

**When using `memory_extract`:** skip the novelty check — AUDN handles dedup internally.
Pass the relevant conversation chunk as `messages` and the appropriate source prefix.
The server extracts facts, searches for similar existing memories, and decides per-fact
whether to add, update, delete, or skip.

### 3. Maintain — Lifecycle Management

Memories aren't permanent records — they need maintenance as context evolves. Most
maintenance happens automatically through `memory_extract`'s AUDN cycle, but some
situations need direct action.

**When to maintain directly:**
- User says "forget this" or "don't remember that" → search for it with `memory_search`,
  then delete by ID with `memory_delete`
- Project sunset or namespace cleanup → `memory_delete_by_source` with the project prefix
- Bulk cleanup of stale wip/ items → `memory_delete_by_source` with `wip/{project}`
- For scoped API keys, ensure any bulk delete source prefix is inside authorized prefixes

**When AUDN handles it for you:**
- Decision reversed → `memory_extract` with the new context. AUDN sees the old decision
  memory and the new contradicting fact, and issues an UPDATE (delete old + add new).
- Deferred work done → `memory_extract` with the completion context. AUDN sees the
  wip/ memory and the "completed" signal, and issues a DELETE.
- Learning updated → `memory_extract` with the better fix. AUDN sees the old learning
  and the improved version, and issues an UPDATE with `supersedes` metadata.

Direct delete tools are for explicit user requests and bulk cleanup. For everything
else, let `memory_extract` make the classification decision.

## How to Store

### Source Prefix Convention

Use consistent prefixes so memories can be found and cleaned up by scope:

| Prefix | Use for |
|--------|---------|
| `claude-code/{project}` or `codex/{project}` | Code decisions, architecture choices, project-specific context (choose the client prefix you run under) |
| `learning/{project}` | Discovered patterns, gotchas, fixes, non-obvious behaviors |
| `wip/{project}` | Deferred work, open threads, "revisit later" items |

Never invent ad-hoc prefixes like `myapp/decisions` or `myapp/deployment`. Stick to
these three categories — they enable reliable search and cleanup by scope.
If your key is prefix-scoped, `source` must be non-empty and inside those authorized
prefixes.

### Format

Write memories that will be useful to a future Claude with zero context about the
current session:

- **Concise** — one to three sentences, focused on the actionable insight
- **Self-contained** — include enough context to be useful without the original conversation
- **Forward-looking** — what does a future session need to know?
- **Include the why** — not just what was chosen, but why (especially rejection rationale)

**Good:**
```
Selected Redis over Memcached for session caching because we need pub/sub
for real-time invalidation. Config in services/cache/redis.yml.
```

**Good (implicit decision captured):**
```
myapp uses PostgreSQL for user data storage. Users table has an email
column that needs indexing for search performance.
```

**Good (deferred work):**
```
Deferred: notification system design is parked until rate limiting is
figured out. Rate limiting is a dependency — notifications need throughput
constraints before they can be designed.
```

**Bad:**
```
We talked about caching options and decided to go with Redis.
```

### Before Storing (with `memory_add`)

Always check novelty first:

1. Call `memory_is_novel` with your candidate text
2. If similar memory exists, either skip or use `memory_extract` to update it
3. If novel, store with appropriate source prefix

This prevents duplicate memories from accumulating across sessions.

When using `memory_extract`, skip this step — AUDN handles dedup internally.

### Multiple Decisions

When the user makes several decisions in one message ("Next.js for frontend, tRPC for
API, Drizzle for ORM"), use `memory_extract` with the full conversation chunk. The
server will extract each fact separately and handle AUDN per-fact — more efficient
than multiple `memory_add` calls and handles any superseded decisions automatically.

For simple cases (2 clear, novel facts), individual `memory_add` calls are fine.

## Integration with Other Systems

- **Backend provisioning**: Run `/memories:setup` to check service health, configure extraction providers, and write env files. Use this when entering a project where the Memories backend is not yet reachable.
- **Auto-memory** handles project conventions and patterns in local files — let it do its job
- **Session hooks and plugins** already provide baseline context where supported — Claude Code / Cursor use the full 12-hook lifecycle, Codex uses 5 native hooks plus MCP + developer instructions, and OpenCode uses MCP plus plugin prompt recall and memory-tool telemetry. Don't duplicate that work.
- **Hook-injected recall is a starting point, not a substitute for active recall.** If the
  retrieved memories look noisy, low-confidence, or obviously cross-project, run explicit
  `memory_search` calls yourself before answering.
- **Extraction hooks** already trigger `memory_extract` via HTTP at lifecycle boundaries for clients that support them.
  Claude Code / Cursor use `Stop`, `PreCompact`, and `SessionEnd`; Codex uses a
  beefier `Stop` hook because it has no compaction/session-end lifecycle events. OpenCode does not auto-extract by default yet; OpenCode extraction is gated until reliable end-of-turn transcript access is proven.
  The skill triggers extraction *within* a session at natural breakpoints that hooks
  miss — like mid-conversation decision changes or deferred work completions.
- **Learning skill** captures failure-fix patterns — if both skills apply, the learning skill
  owns the detailed capture; you store a concise cross-reference
- **Conductor** orchestrates skill sequencing — don't interfere with its flow

This skill activates at natural breakpoints *within* whatever workflow is running,
not as a standalone phase.

## Hook Lifecycle

### Claude Code / Cursor

These hooks provide seamless memory recall and extraction without manual intervention:

| Hook | Event | What It Does |
|------|-------|-------------|
| `memory-recall.sh` | SessionStart | Loads project memories, hydrates MEMORY.md via sync marker, checks service health |
| `memory-subagent-recall.sh` | SubagentStart | Injects project-scoped memories into subagents (Plan, Explore, code-reviewer, etc.) at spawn time for memory-aware subagents |
| `memory-query.sh` | UserPromptSubmit | Searches for memories relevant to the current prompt using transcript context; uses assertive injection framing to ensure recalled memories influence the response |
| `memory-extract.sh` | Stop | Extracts facts from the last user+assistant pair (context: `stop`, ~4K chars); fires unconditionally with no keyword filter |
| `memory-tool-observe.sh` | PostToolUse (Write\|Edit\|Bash) | Logs tool observations (files changed, commands run) to a session-scoped JSONL file for richer extraction context |
| `memory-flush.sh` | PreCompact | Aggressive extraction before context loss (context: `pre_compact`, ~12K chars) |
| `memory-commit.sh` | SessionEnd | Final extraction pass (context: `session_end`, ~8K chars) |
| `memory-rehydrate.sh` | PostCompact | Re-injects memories using the compact summary as a targeted search query |
| `memory-subagent-capture.sh` | SubagentStop | Captures architectural decisions from subagents |
| `memory-observe.sh` | PostToolUse (mcp__memories__) | Logs when memory MCP tools are called (observability) |
| `memory-guard.sh` | PreToolUse | Blocks direct writes to MEMORY.md (managed by sync) |
| `memory-config-guard.sh` | ConfigChange | Warns if memory hooks are removed from settings |

The Stop hook fires unconditionally on every turn — there is no signal keyword filter. This ensures all decisions and context are captured regardless of phrasing.

The extraction `context` parameter controls aggressiveness:
- `stop`: Standard — skips task completion details, commit hashes, metrics
- `pre_compact`: Aggressive — includes file paths, config patterns, naming conventions
- `session_end`: Same as pre_compact (context about to be lost permanently)
- `subagent_stop`: Standard — captures subagent decisions

### Codex

| Hook | Event | What It Does |
|------|-------|-------------|
| `memory-recall.sh` | SessionStart | Loads project memory pointers and emits recall guidance for the session |
| `memory-query.sh` | UserPromptSubmit | Searches for memories relevant to the current prompt using transcript context |
| `memory-extract.sh` | Stop | Extracts facts from a larger transcript sample (~8K chars, 500 tail lines, 10 message pairs) to compensate for missing `PreCompact` / `SessionEnd` hooks |
| `memory-observe.sh` | PostToolUse (`mcp__memories__`) | Logs when memory MCP tools are called (observability) |
| `memory-guard.sh` | PreToolUse (`Write\|Edit`) | Blocks direct writes to `MEMORY.md` if attempted |

Codex stores those hooks in `~/.codex/hooks.json`, uses `~/.codex/settings.json` for tool permissions, and relies on `~/.codex/config.toml` for MCP registration plus developer instructions that bias `memory_search` usage.
Active-search monitoring writes privacy-safe local telemetry to `~/.config/memories/active-search.jsonl`; summarize it with `.venv/bin/python scripts/active_search_metrics.py --log ~/.config/memories/active-search.jsonl`.

## Auto-Memory Hydration (Claude Code / Cursor)

On every session start, `memory-recall.sh` syncs top memory pointers into Claude Code's auto-memory file at `~/.claude/projects/{encoded-cwd}/memory/MEMORY.md`.

The sync uses a marker comment: `<!-- SYNCED-FROM-MEMORIES-MCP -->`

- Everything **above** the marker is preserved (your manual/pinned content)
- Everything **below** the marker is replaced with fresh memory pointers from MCP
- Claude Code loads the first 200 lines of MEMORY.md at session start — synced content counts against this limit
- To pin important context, write it above the marker manually

This gives Claude scoped memory starting points without putting full memory text into passive context; call `memory_search` with the listed source prefix before using remembered details.

Codex does not have a `MEMORY.md` auto-memory file. It relies on `memory-recall.sh`, `memory-query.sh`, the Memories MCP server, and developer instructions instead.

## Manual vs Automatic Extraction

**Automatic** (hooks handle): Lifecycle boundaries — session stop, pre-compaction, session end, subagent completion. These capture facts from recent conversation without intervention.

**Manual** (you trigger): Mid-session moments hooks miss:
- A decision just reversed or updated — call `memory_extract` with the conversation chunk so AUDN can UPDATE the old memory
- Deferred work identified — call `memory_add` with the deferred item
- User explicitly says "remember this" — call `memory_add` immediately

AUDN deduplicates across both paths. If a hook extracts a fact that `memory_add` already stored, AUDN issues a NOOP. If you manually extract something a hook will also extract, the duplicate is harmless.

## Multi-Backend Routing

This session may have multiple Memories backends configured via `~/.config/memories/backends.yaml`.
Memory operations are routed automatically:

- **Search** fans out to all search-enabled backends and merges results
- **Extract** writes to dev/personal backends only (configurable)
- **Add** writes to all writable backends
- **Feedback** routes to dev/personal backends

When search results include `_backend` tags, that shows which instance each memory came from.
You don't need to route manually — the hooks and MCP server handle it transparently.

### Source prefix conventions for multi-backend
- `decision/{project}` — shared/team decisions (written to all backends)
- `personal/{project}` — personal notes (stays on personal/dev backend only)
- `claude-code/{project}`, `learning/{project}`, `wip/{project}` — standard prefixes, routed per config
