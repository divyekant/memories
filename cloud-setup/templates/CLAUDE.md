# Global instructions (installed to ~/.claude/CLAUDE.md in cloud sessions)

This file is installed by the Memories cloud bootstrap. It applies to every
Claude Code cloud session in this environment, on top of any repo-level CLAUDE.md.

## Memory (Memories MCP + hooks)

You have a persistent, semantically searchable memory. Hooks automatically
recall context at session start / before prompts and capture decisions at stop.
USE recalled context; STORE decisions at natural breakpoints.

- **ALWAYS search memories BEFORE answering** questions about prior decisions,
  architecture, conventions, deferred work, past bugs, or "where we left off".
  Hook-injected context is keyword-matched and incomplete — search to confirm.
  Load the tool if needed: `ToolSearch("select:mcp__memories__memory_search")`.
- **Do not search for self-contained prompts** (arithmetic, translation,
  formatting, generic facts) that don't depend on project/prior context.
- **Search before asking a clarifying question** — the answer may be stored.
- **Use exact project-scoped source prefixes first** (e.g. `claude-code/<project>`)
  before broad/unscoped search.
- **Lead with the answer.** No "based on memory…" preamble, no meta phrases.
- **Preserve boundary conditions** (`until`, `unless`, `because`, `blocked on`)
  verbatim. Say `deferred` / `blocked on` directly; don't soften.
- **Capture** architectural decisions, deferred work, non-obvious fixes, and
  phase transitions with `memory_add` / `memory_extract` as they happen.
- If the backend is unreachable (health check fails), memory silently no-ops via
  a circuit breaker — proceed without it and mention it once if relevant.

## Cloud sessions

- The sandbox is **ephemeral** — only what's committed and pushed survives.
  Commit/push work before ending a session; never leave results only on disk.
- **Stateful services do not run here** (no nested Docker, no persistent volumes).
  The Memories backend is hosted externally; this session only runs thin clients
  that call it via `MEMORIES_URL` / `MEMORIES_API_KEY`.
- Reaching the backend requires its domain in the environment's **Custom network
  allowlist**. If memory recall is empty, suspect the allowlist or env vars first.

## GitHub connection

- Use the **GitHub MCP tools** (`mcp__github__*`) for all GitHub work — there is
  no `gh`/`hub` CLI and no direct API access in cloud sessions.
- **Do NOT open a pull request unless explicitly asked.**
- GitHub access is **scoped** to the session's configured repositories; calls to
  other repos are denied. Don't assume cross-repo access.
- Be frugal with PR/issue comments — only when genuinely necessary.

## Skills

- Prefer invoking a matching skill over ad-hoc work; check the available-skills
  list first. Reference `/<skill-name>` only if it's actually available.
