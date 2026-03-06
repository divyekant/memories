---
id: fh-003
type: feature-handoff
audience: internal
topic: Export/Import
status: draft
generated: 2026-03-05
source-tier: direct
context-files: [docs/plans/2026-03-05-export-import-design.md]
hermes-version: 1.0.0
---

# Feature Handoff: Export/Import

## What It Does
Export/import enables selective, portable data exchange between Memories instances. Users can export all or filtered memories to a standard NDJSON file (.jsonl), then import that file into another instance -- or back into the same instance after a reset. This supports instance migration, project-scoped sharing, and cross-instance synchronization.

Unlike backup/restore (which copies the entire database for disaster recovery), export/import is selective: users choose which memories to export by source prefix or date range, and can control how conflicts are resolved during import.

## How It Works
**Export** reads from the in-memory metadata store, filters by optional source prefix and date range, strips instance-specific fields (IDs, embeddings), and emits one NDJSON line per memory with a header line containing the total count and version. The API endpoint wraps this in a `StreamingResponse` with `application/x-ndjson` content type, auth-filtering each record through `AuthContext.can_read()`.

**Import** reads NDJSON from the request body, validates the header line, optionally creates a pre-import backup, and dispatches to one of three strategies:
- **add**: Passes all records directly to `add_memories(deduplicate=False)`. Fastest, no checks.
- **smart**: For each record, runs a vector similarity search against existing memories. High similarity (>=0.95) -> skip. Low similarity (<0.80) -> add. Borderline (0.80-0.95) -> compare timestamps, newer wins. Deleted old record if import wins.
- **smart+extract**: Same as smart, but borderline cases are escalated to the LLM extraction pipeline (AUDN: Add/Update/Delete/Noop). Most thorough, costs LLM tokens on ~5-10% of memories.

Source remapping happens before strategy dispatch -- the `source_remap` tuple rewrites matching prefixes on each record.

## User-Facing Behavior
- **API**: `GET /export` with optional `source`, `since`, `until` query params. `POST /import` with `strategy`, `source_remap`, `no_backup` query params and NDJSON body.
- **CLI**: `memories export [-o file] [--source prefix] [--since date] [--until date]` and `memories import <file|-> [--strategy add|smart|smart+extract] [--source-remap old=new] [--no-backup]`
- **Response**: Import returns `{"imported": N, "skipped": N, "updated": N, "errors": [...], "backup": "name"}`

## Configuration
| Option | Type | Default | Effect |
|--------|------|---------|--------|
| No new env vars | -- | -- | Export/import uses existing engine and auth configuration |

## Edge Cases & Limitations
- `smart+extract` strategy is defined in the design but the LLM escalation path is not yet implemented -- borderline cases currently fall through to timestamp comparison (same as `smart`)
- Exports from very large instances (>100K memories) load all metadata into memory before streaming -- no pagination
- Date filtering uses string comparison on ISO8601 timestamps -- timezone-naive strings may sort incorrectly
- The NDJSON header `count` reflects records before auth filtering on export -- downstream count may differ for scoped keys
- Import does not preserve original IDs -- all memories get new IDs on import

## Common Questions
**Q: How is this different from backup/restore?**
A: Backup copies the entire metadata.json and rebuilds vectors on restore -- it's a full database snapshot for disaster recovery. Export/import is selective (filter by source or date), portable (NDJSON format, no IDs or embeddings), and supports conflict resolution. Use backup for data safety, export/import for migration and sharing.

**Q: Will import create duplicate memories?**
A: With `--strategy add`, yes -- every record creates a new memory. Use `--strategy smart` to skip duplicates (>=0.95 vector similarity) and resolve near-duplicates by timestamp.

**Q: What happens if import fails midway?**
A: Per-line errors are collected but don't abort the import. The response includes an `errors` array with line numbers and error messages. If the server crashes mid-import, the auto-backup (created before processing starts) can be restored via `memories backup restore <name>`.

**Q: Can I import from a different Memories version?**
A: The header includes a `version` field for compatibility checking. Currently the system doesn't enforce version restrictions, but future versions may reject incompatible formats.

**Q: How does source remapping work with auth?**
A: Remapping happens in the API layer before auth checking. The remapped source must be writable by the caller's key. If remapping produces a source outside the key's prefix scope, that line is skipped with an auth error.

## Troubleshooting
| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| Export returns empty (count: 0) | Source prefix doesn't match any memories, or date range excludes all | Check `source` param matches existing prefixes. Use `memories list --source X` to verify. |
| Import returns all errors | Header missing `_header: true` field | Ensure first line is a valid header with `_header`, `count`, and `version` fields |
| Import skips all records (smart) | All records are near-duplicates of existing data | Expected behavior -- smart strategy deduplicates. Use `add` strategy to force import. |
| Auth error on import lines | Scoped key can't write to the target source prefix | Use an admin key, or remap sources to a writable prefix with `--source-remap` |
| "File not found" CLI error | Path to JSONL file is incorrect | Use absolute path or verify relative path. Use `-` for stdin. |

## Related
- [Feature Handoff: Multi-Auth (fh-001)](fh-001-multi-auth.md) -- auth prefix enforcement applies to export/import
- [Feature Handoff: CLI (fh-002)](fh-002-cli.md) -- export/import CLI commands
- [Design Doc](../../plans/2026-03-05-export-import-design.md) -- approved design specification
