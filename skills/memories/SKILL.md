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
- Before asking a clarifying question about project architecture or preferences
- When picking up work in a domain where deferred tasks might exist
- Before attempting something that feels like it might have failed before
- When the user references a past decision or discussion you don't have context for
- When passive startup recall feels insufficient for the current task

**How to search:**
- Use `memory_search` with natural language queries
- Try 2-3 query angles if the first search doesn't find what you need
- Search for deferred work: `"deferred TODO revisit later wip {project}"`
- Search for past failures: `"failure fix gotcha {technology}"`
- Search for decisions: `"decision chose selected approach {topic}"`
- Search by domain: `"{project} {feature-area} architecture design"`

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

- **Auto-memory** handles project conventions and patterns in local files — let it do its job
- **Session hooks** provide baseline context at startup — don't duplicate that work
- **Extraction hooks** (Stop, PreCompact, SessionEnd) already trigger `memory_extract`
  via HTTP at lifecycle boundaries. The skill triggers extraction *within* a session at
  natural breakpoints that hooks miss — like mid-conversation decision changes or
  deferred work completions.
- **Learning skill** captures failure-fix patterns — if both skills apply, the learning skill
  owns the detailed capture; you store a concise cross-reference
- **Conductor** orchestrates skill sequencing — don't interfere with its flow

This skill activates at natural breakpoints *within* whatever workflow is running,
not as a standalone phase.
