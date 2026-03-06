---
id: feat-003
type: feature-doc
audience: external
topic: Export/Import
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Export/Import

## Overview
Move memories between Memories instances with full control over what gets transferred and how conflicts are resolved. Export writes a portable file; import reads it into any instance — with smart deduplication so you never accidentally create duplicates.

## How to Use It

### Exporting

Export all memories to a file:
```bash
memories export -o backup.jsonl
```

Or filter by source prefix and date range:
```bash
memories export --source "claude-code/myapp" --since 2026-01-01 -o recent.jsonl
```

The output is NDJSON — one JSON object per line, starting with a header:
```jsonl
{"_header": true, "count": 847, "version": "2.0.0", "exported_at": "2026-03-05T10:30:00Z", ...}
{"text": "Always use TypeScript strict mode", "source": "standards", "created_at": "2026-01-15T...", ...}
```

### Importing

Import into a new instance (fastest — no duplicate checking):
```bash
memories import backup.jsonl
```

Import with smart deduplication (recommended when the target has existing data):
```bash
memories import backup.jsonl --strategy smart
```

Import with source remapping (change namespaces):
```bash
memories import backup.jsonl --source-remap "old-project/=new-project/"
```

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `--strategy` | Import conflict resolution: `add`, `smart`, or `smart+extract` | `add` |
| `--source-remap` | Rewrite source prefixes during import (format: `old=new`) | None |
| `--no-backup` | Skip automatic pre-import backup | false (backup created) |
| `--source` | (Export only) Filter by source prefix | None (export all) |
| `--since` | (Export only) Only memories created after this ISO8601 date | None |
| `--until` | (Export only) Only memories created before this ISO8601 date | None |
| `-o` | (Export only) Output file path. Omit to print to stdout | stdout |

## Examples

### Example: Full Instance Migration
```bash
# On source machine
memories export -o all-memories.jsonl

# Transfer file to target machine, then:
memories import all-memories.jsonl
```

### Example: Merge Two Instances
```bash
# Export from instance A
memories export -o instance-a.jsonl

# Import into instance B with smart dedup
memories import instance-a.jsonl --strategy smart
# Output: Imported: 340, Skipped: 507, Updated: 3
```

### Example: Share Project Memories
```bash
# Export just one project's memories
memories export --source "claude-code/myapp" -o myapp-memories.jsonl

# Import into team instance with namespace change
memories import myapp-memories.jsonl --source-remap "claude-code/myapp=team/shared"
```

## Import Strategies

| Strategy | Speed | Dedup | Best For |
|----------|-------|-------|----------|
| `add` | Fastest | None | Clean migration to empty instance |
| `smart` | Moderate | Vector similarity + timestamps | Merging instances with overlapping data |
| `smart+extract` | Slowest | LLM for borderline cases | Active instances with contradictory facts |

## Limitations
- Exported files don't include memory IDs — all memories get new IDs on import
- Embeddings are not exported — they're recomputed on import
- The `smart+extract` LLM escalation for borderline cases uses tokens (Haiku costs ~$5-10 per 10K memories)
- Very large exports load all metadata into memory before streaming

## Related
- [API Reference](../api-reference.md) — `GET /export` and `POST /import` endpoints
- [CLI Quick Start](../tutorials/tut-003-cli-quickstart.md) — CLI installation and basic usage
