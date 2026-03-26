# Temporal Reasoning Engine

**Date:** 2026-03-26
**Status:** Draft
**Branch:** feat/temporal-reasoning (targets develop)
**Release:** v5.0.0

## Problem

Temporal reasoning scores 8.7% (tool) / 42.2% (system) vs supermemory's ~75%. The engine has no temporal substrate: no stable document dates, no version preservation, no date-range filtering. LongMemEval temporal questions ask "how many days between X and Y?" and "which happened first?" but memories lack the dates needed to answer.

## Solution

Four changes: temporal metadata fields, version preservation on UPDATE, temporal search filters, and Qdrant date indexes.

## Design

### Change 1: Temporal metadata fields

**New fields on memory metadata:**

| Field | Type | Set by | Purpose |
|---|---|---|---|
| `document_at` | ISO 8601 string | Caller (optional) | When the source content was created |
| `last_reinforced_at` | ISO 8601 string | `reinforce()` | Last retrieval reinforcement time |
| `is_latest` | bool | Version system | True for current version, false for superseded |

**`document_at`:**
- Optional field. Not in `_reserved_add` — callers can set it via metadata.
- Must be ISO 8601 at write time. If caller provides non-ISO string, attempt to parse and normalize.
- Used by recency scoring: read `document_at` → `created_at` → `timestamp` (in that order).
- Used by temporal filters (`since`/`until`).

**`last_reinforced_at`:**
- Set by `reinforce()` instead of mutating `updated_at`.
- `updated_at` now only changes on actual content/metadata updates.
- Confidence scoring reads `last_reinforced_at` → `updated_at` → `created_at` (preserving reinforcement-decay semantics).

**`is_latest`:**
- Defaults to `True` on creation.
- Set to `False` when superseded by a newer version.
- Separate from `archived` — `archived` is visibility, `is_latest` is version semantics.
- Searchable: `is_latest=true` returns only current versions. Default: not filtered (backward compat).

### Change 2: Version preservation on UPDATE

**In `execute_actions()` UPDATE path (llm_extract.py):**

Current: `engine.delete_memory(old_id)` → create new memory with `supersedes` metadata.

New:
```python
# 1. Archive old memory, mark as not-latest
engine.update_memory(old_id, archived=True, metadata_patch={"is_latest": False})
# 2. Create new memory with supersedes link + is_latest
fact_meta["is_latest"] = True
added_ids = engine.add_memories(...)
# 3. Create supersedes link from new → old
engine.add_link(new_id, old_id, "supersedes")
```

**Other mutation paths:**
- `replace_memory()`: same pattern — archive old, create new, link.
- `update_memory()` (in-place mutation): does NOT create versions. This is for metadata patches, not content replacement. `updated_at` changes, `is_latest` stays true.
- `merge_memories()`: already archives originals + adds supersedes links. No change needed.

**Timeline queries:** "what was the previous X?" requires `include_archived=True` to see superseded versions. This is already a search parameter.

### Change 3: Temporal search filters

Add to `hybrid_search()`, `search()`, `_search_no_reinforce()`, and `hybrid_search_explain()`:

```python
since: Optional[str] = None,  # ISO 8601 — filter document_at >= since
until: Optional[str] = None,  # ISO 8601 — filter document_at <= until
```

**Filtering logic:**
- Applied during candidate retrieval (like `source_prefix` and `include_archived`).
- Reads `document_at` first, falls back to `created_at`.
- If memory has neither `document_at` nor parseable `created_at`, it passes through (no filter applied).

**No `temporal_sort` parameter.** The agent can request `recency_weight=1.0` to sort by recency within relevance-filtered candidates. Adding a separate sort-by-date would replace retrieval with pure chronological ordering, which returns "most recent thing" not "most recent relevant thing."

**API surface:**
- HTTP `SearchRequest`: add `since` and `until` fields (Optional[str]).
- MCP `memory_search`: add `since` and `until` params.
- Pass through to all search methods.

### Change 4: Qdrant payload indexes

Add payload index on `document_at` for efficient temporal filtering:

```python
self.client.create_payload_index(
    collection_name=self.collection,
    field_name="document_at",
    field_schema=models.PayloadSchemaType.KEYWORD,  # ISO strings sort lexicographically
)
```

Also add `is_latest` index:
```python
self.client.create_payload_index(
    collection_name=self.collection,
    field_name="is_latest",
    field_schema=models.PayloadSchemaType.BOOL,
)
```

### Change 5: LongMemEval eval harness

- `seed_question()` already stores `document_at` in metadata (from `haystack_dates`).
- Normalize the raw date string to ISO 8601 before storing.
- System eval prompt already includes `question_date`.
- Add `--category` filter to `run_longmemeval.py` (already done).

## What doesn't change

- `created_at`, `updated_at`, `timestamp` fields (backward compat)
- `add_memories()` reserved fields
- Graph expansion (`_graph_expand`, `_merge_graph_results`)
- MCP tool signatures (new params are additive)
- Existing recency_weight / confidence_weight behavior at `graph_weight=0`

## Testing Strategy

**Unit tests:**
- `reinforce()` updates `last_reinforced_at`, not `updated_at`
- `update_memory()` updates `updated_at` on content change
- UPDATE in extraction archives old memory instead of deleting
- UPDATE creates supersedes link from new → old
- `is_latest=False` on superseded, `True` on new
- `since` filter excludes memories before date
- `until` filter excludes memories after date
- `since` + `until` combined range filter
- Missing `document_at` passes through temporal filter
- ISO normalization of date strings
- Qdrant indexes created for `document_at` and `is_latest`

**Integration tests:**
- Full extraction with UPDATE → old memory archived, new memory linked
- Search with `since`/`until` → only date-range results returned
- MCP memory_search accepts `since`/`until`
- Confidence scoring uses `last_reinforced_at`

**Eval:**
- LongMemEval temporal-reasoning category: 133 questions
- Compare tool eval score before/after (baseline: 9.9%)
- Compare system eval score before/after (baseline: 42.2%)

## Risks

1. **Backward compat**: `reinforce()` no longer updates `updated_at`. Confidence behavior changes. Mitigated by pointing confidence at `last_reinforced_at`.
2. **Archive bloat**: UPDATE no longer deletes → memory count grows. Mitigated by existing pruning scheduler (weekly).
3. **ISO normalization**: Non-ISO date strings from callers need parsing. Mitigated by best-effort parse with fallback to raw string.
