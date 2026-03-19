---
id: feat-005
type: feature-doc
title: Extraction Debug Trace
audience: external
generated: 2026-03-18
---

# Extraction Debug Trace

When you submit a conversation for extraction, you normally get back action counts — how many facts were added, updated, or deleted. The debug trace opens the black box: it shows you every fact the LLM extracted, every AUDN decision it made, and which existing memories influenced those decisions.

## How to enable it

Pass `"debug": true` in your extraction request:

```bash
curl -s -X POST http://localhost:8900/memory/extract \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "messages": "user: We decided to use PostgreSQL instead of MySQL for the analytics service.\nassistant: Got it, I will use PostgreSQL for the analytics service going forward.",
    "source": "claude-code/my-project",
    "debug": true
  }' | jq .
```

This returns a job ID. Poll the job to get the trace:

```bash
curl -s http://localhost:8900/memory/extract/JOB_ID \
  -H "X-API-Key: $API_KEY" | jq .result.debug_trace
```

## Trace structure

The `debug_trace` object contains three sections:

### `extracted_facts`

Every fact the LLM pulled from the conversation text:

```json
{
  "extracted_facts": [
    {
      "text": "The analytics service uses PostgreSQL instead of MySQL",
      "category": "decision"
    }
  ]
}
```

Each fact has:
- **`text`** — the extracted statement
- **`category`** — classification (e.g., `decision`, `preference`, `detail`, `deferred`)

### `audn_decisions`

The Add/Update/Delete/Noop decision for each extracted fact, along with the similar memories that influenced the decision:

```json
{
  "audn_decisions": [
    {
      "fact_index": 0,
      "action": "UPDATE",
      "similar_memories": [
        {
          "id": 17,
          "text": "Analytics service database is MySQL",
          "similarity": 0.87
        }
      ],
      "old_id": 17,
      "new_id": 42
    }
  ]
}
```

Fields per decision:
- **`fact_index`** — index into the `extracted_facts` array
- **`action`** — one of `ADD`, `UPDATE`, `DELETE`, `NOOP`
- **`similar_memories`** — existing memories the LLM compared against, with similarity scores
- **`old_id`** — (UPDATE/DELETE) the memory being replaced or removed
- **`new_id`** — (ADD/UPDATE) the newly created memory ID
- **`existing_id`** — (NOOP) the memory that already captures this fact
- **`conflicts_with`** — (CONFLICT) the memory that contradicts the new fact

### `execution_summary`

A compact summary of what was actually executed:

```json
{
  "execution_summary": {
    "added": [42, 43],
    "updated": [{"old": 17, "new": 42}],
    "deleted": [8],
    "noops": 1,
    "conflicts": 0
  }
}
```

## When to use debug mode

**Diagnosing incorrect updates** — If extraction is updating the wrong memory, the `similar_memories` field shows you which candidates the LLM considered and their similarity scores. This reveals whether the issue is retrieval (wrong candidates surfaced) or LLM judgment (wrong decision given correct candidates).

**Understanding NOOPs** — A high NOOP rate might mean extraction is running too frequently on redundant content. The debug trace confirms which existing memories are absorbing incoming facts.

**Conflict investigation** — When a fact contradicts an existing memory but the LLM is unsure which is correct, it creates a conflict. The trace shows both the new fact and the conflicting memory for manual review.

## Notes

- Debug mode adds overhead. The `similar_memories` lookup happens regardless, but packaging and returning the trace increases response size. Use it for investigation, not in production hooks.
- When `debug` is `false` (the default), no `debug_trace` key appears in the job result.
- The debug flag is passed through to `run_extraction` and only affects the response payload — it does not change which actions are executed.
