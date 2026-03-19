# Export/Import Design

**Goal:** Enable selective, portable data exchange between Memories instances for migration and multi-instance workflows.

**Status:** Approved

**Context:** This is distinct from the existing backup system. Backup copies full `metadata.json` and rebuilds vectors on restore — it's for data safety. Export/import is for selective, portable data exchange between instances.

---

## Export

### Format

Streaming NDJSON (`.jsonl`) with a metadata header line:

```jsonl
{"_header": true, "exported_at": "2026-03-05T10:30:00Z", "source_filter": "claude-code/", "since": null, "until": null, "count": 847, "version": "2.0.0"}
{"text": "Selected Redis over...", "source": "claude-code/myapp", "created_at": "2026-01-15T...", "updated_at": "2026-01-15T...", "custom_fields": {}}
{"text": "Another memory...", "source": "claude-code/myapp", "created_at": "2026-02-01T...", "updated_at": "2026-02-01T...", "custom_fields": {}}
```

- One memory per line after the header
- Includes: `text`, `source`, `created_at`, `updated_at`, `custom_fields`
- Excludes: IDs (instance-specific), embeddings (recomputed on import)
- Header includes `count` for progress tracking and `version` for compatibility

### Endpoint

`GET /export` with query params:
- `source` — source prefix filter (optional)
- `since` — ISO8601 datetime, only memories created after (optional)
- `until` — ISO8601 datetime, only memories created before (optional)
- Returns `StreamingResponse` with `application/x-ndjson` content type
- Auth: respects API key source prefix restrictions — scoped keys only export accessible memories

### CLI

```bash
memories export -o file.jsonl                                # export all
memories export --source "claude-code/" -o project.jsonl     # by prefix
memories export --source "claude-code/" --since 2026-01-01 -o recent.jsonl
memories export                                              # stdout if no -o
```

---

## Import

### Input

Same `.jsonl` format that export produces. First line is `_header`, rest are memories.

### Strategies

| Strategy | Dedup | Conflict Resolution | LLM Cost | Use Case |
|----------|-------|---------------------|----------|----------|
| `add` | None | None | Free | Clean migration to empty instance |
| `smart` | Novelty check (vector similarity) | Newer timestamp wins | Free | Merging instances, selective sync |
| `smart+extract` | Novelty first pass, LLM for borderline | AUDN classification | ~5-10% of memories hit LLM | Merging active instances with contradictions |

#### Strategy: `add`
Every line creates a new memory. No checks. Fast. Use when importing into an empty instance.

#### Strategy: `smart`
1. Run novelty check per memory (local vector similarity, free)
2. If clearly novel (low similarity) — add
3. If clearly duplicate (very high similarity) — skip
4. If near-duplicate found — keep the one with the newer `created_at`, skip the older one

Handles the "70kg vs 69kg" scenario: the Feb entry (newer) wins over the Jan entry.

#### Strategy: `smart+extract`
1. Same as `smart` for first pass
2. Borderline cases (similar but not identical) get sent to `memory_extract` AUDN cycle
3. LLM classifies each borderline case as Add, Update, Delete, or No-op
4. Most thorough but costs LLM tokens on ~5-10% of memories

### Auto-Backup

Server automatically creates a `pre-import_{timestamp}` backup before processing. Restorable via `memories backup restore <name>`. Skippable with `--no-backup` flag.

### Source Remapping

Optional `--source-remap "old/=new/"` rewrites source prefixes during import. Useful when importing from one project namespace into another.

### Endpoint

`POST /import` with query params:
- `strategy` — `add` | `smart` | `smart+extract` (default: `add`)
- `source_remap` — optional prefix remapping (format: `old=new`)
- `no_backup` — skip auto-backup (default: false)
- Accepts streaming NDJSON request body
- Validates header line first (version compatibility)
- Processes memories in chunks (reuses existing add-batch/upsert-batch internals)
- Per-line errors don't abort the import — they're collected and returned
- Auth: respects API key source prefix restrictions for target prefixes

### Response

```json
{
  "imported": 840,
  "skipped": 5,
  "updated": 2,
  "errors": [
    {"line": 42, "error": "source prefix not authorized"}
  ],
  "backup": "pre-import_20260305_103000"
}
```

### CLI

```bash
memories import file.jsonl                                    # default: add
memories import file.jsonl --strategy smart                   # novelty + timestamp
memories import file.jsonl --strategy smart+extract           # LLM for borderline
memories import file.jsonl --source-remap "old/=new/"         # remap prefix
memories import file.jsonl --no-backup                        # skip auto-backup
cat file.jsonl | memories import -                            # stdin
```

---

## Architecture

### Files to Create/Modify

- **`app.py`** — Add `GET /export` and `POST /import` endpoints
- **`memory_engine.py`** — Add `export_memories()` generator and `import_memories()` method with strategy dispatch
- **`cli/client.py`** — Add `export_stream()` and `import_stream()` methods
- **`cli/commands/export_import.py`** — New CLI commands for export and import
- **`tests/test_cli_export_import.py`** — CLI tests with MockTransport
- **`tests/test_export_import.py`** — Unit tests for engine-level export/import logic

### Data Flow

**Export:**
```
CLI/API → GET /export → memory_engine.export_memories(filters) → yields NDJSON lines → StreamingResponse
```

**Import:**
```
CLI/API → POST /import → auto-backup → validate header → strategy dispatch:
  add:           → add_batch (chunks of 500)
  smart:         → per-memory novelty check + timestamp compare → add/skip/update
  smart+extract: → smart first pass → borderline cases → AUDN extract → add/update/delete/skip
→ return summary
```

### Error Handling

- Invalid header → 400 with version mismatch details
- Auth failure on source prefix → skip that line, add to errors
- Malformed JSON line → skip, add to errors
- Server error mid-import → return partial results + error count
- Backup already exists → append unique suffix
