---
id: uc-006
type: use-case
audience: internal
topic: Instance Migration
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Use Case: Instance Migration

## Trigger
User wants to move all memories from one Memories instance to another (e.g., moving from a local dev instance to a production server, or migrating between machines).

## Preconditions
- Source instance is running and accessible
- Target instance is running and accessible
- User has API access to both instances (admin key or key with sufficient prefix scope)
- CLI is configured for source instance (or user specifies `--url`)

## Flow
1. **User does:** `memories export -o migration.jsonl`
   **System does:** Streams all memories as NDJSON, writes to file. Header shows total count.

2. **User does:** Reconfigures CLI to point at target instance (change URL in config or use `--url`)
   **System does:** Nothing -- this is client-side config change.

3. **User does:** `memories import migration.jsonl`
   **System does:** Creates auto-backup on target, validates header, imports all records via `add` strategy. Returns summary with imported count.

4. **User sees:** `Imported: 847` (or JSON equivalent)
   **System does:** All memories now exist on target with fresh IDs and recomputed embeddings.

## Variations
- **If target already has data:** Use `--strategy smart` to skip duplicates and resolve conflicts by timestamp.
- **If migrating a specific project:** Export with `--source "project/"` to only export that namespace.
- **If source prefixes differ between instances:** Use `--source-remap "old/=new/"` during import.

## Edge Cases
- Exporting 100K+ memories: Works but loads all metadata into memory. File may be hundreds of MB.
- Importing into an instance with different embedding model: Embeddings are recomputed on import, so this works correctly.
- Network interruption during export: Partial file -- re-run export to get a complete file.

## Data Impact
| Data | Action | Location |
|------|--------|----------|
| NDJSON file | created | Local filesystem |
| Pre-import backup | created | Target instance `backups/` directory |
| Memories | created | Target instance (new IDs, fresh embeddings) |

## CS Notes
- Migration preserves text, source, timestamps, and custom fields -- but NOT IDs or embeddings
- Customers migrating between cloud providers should export before decommissioning the old instance
- If migration fails, the auto-backup on the target can be restored: `memories backup restore pre-import_TIMESTAMP`
