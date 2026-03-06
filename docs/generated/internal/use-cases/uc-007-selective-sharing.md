---
id: uc-007
type: use-case
audience: internal
topic: Selective Sharing Between Instances
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Use Case: Selective Sharing Between Instances

## Trigger
User wants to share a subset of memories from one instance with another (e.g., sharing project-specific learnings with a team instance, syncing recent discoveries).

## Preconditions
- Both instances running and accessible
- User has read access on source, write access on target
- Specific source prefix or date range identified for sharing

## Flow
1. **User does:** `memories export --source "claude-code/myapp" --since 2026-01-01 -o share.jsonl`
   **System does:** Exports only memories matching the source prefix created after the specified date.

2. **User does:** Transfers file to target environment (or pipes via network).

3. **User does:** `memories import share.jsonl --strategy smart`
   **System does:** Creates backup, checks each memory for novelty. Skips duplicates (>=0.95 similarity). Adds novel memories. For borderline cases (0.80-0.95), keeps the newer version.

4. **User sees:** `Imported: 12, Skipped: 35, Updated: 3`

## Variations
- **If namespaces differ between instances:** Use `--source-remap "claude-code/myapp=team/shared"` to remap during import.
- **If contradictions exist (e.g., weight change):** Smart strategy picks newer timestamp. For LLM-grade resolution, use `--strategy smart+extract`.

## Edge Cases
- Sharing the same export multiple times: Smart strategy skips already-imported records -- safe to re-run.
- Sharing into an instance with scoped keys: Import filters out records the key can't write -- those lines appear in the errors array.

## Data Impact
| Data | Action | Location |
|------|--------|----------|
| NDJSON file | created | Source environment |
| Pre-import backup | created | Target instance |
| Novel memories | created | Target instance |
| Outdated memories | deleted+replaced | Target instance (if smart strategy updates) |

## CS Notes
- This is the recommended flow for teams sharing learnings between personal and shared instances
- Repeated sharing is safe -- smart strategy handles dedup automatically
- The `errors` array in the response helps diagnose permission issues
