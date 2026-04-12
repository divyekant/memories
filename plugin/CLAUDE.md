# Memories

You have access to a persistent, semantically searchable memory system via Memories MCP.
Hooks automatically recall and capture memories at lifecycle boundaries.
Your job is to USE the recalled context and STORE decisions at natural breakpoints.

## Behavioral Rules (always active)

### Before responding
- **IMPORTANT: ALWAYS search memories BEFORE responding** to questions about prior decisions, architecture, conventions, deferred work, past bugs, project history, or resuming a topic. Do NOT rely solely on hook-injected context — it is keyword-matched and incomplete. Load the tool first if needed: ToolSearch("select:mcp__memories__memory_search")
- **Search memories BEFORE asking a clarifying question.** The answer may already be stored. Check first.
- **You MUST NOT skip memory search.** Do not rationalize:
  - "The Retrieved Memories section has what I need" — it may not
  - "I can figure it out from the code" — prior decisions aren't in code
  - "This is a simple question" — simple questions about past work need recall
- When hooks inject `## Retrieved Memories` or `## Relevant Memories`, read them carefully. They are curated context, not noise.

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
