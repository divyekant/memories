---
id: ts-003
type: troubleshooting
audience: internal
topic: Export/Import Issues
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Troubleshooting: Export/Import Issues

## Symptoms
- "Export returns empty file" (only header, count: 0)
- "Import reports all errors, nothing imported"
- "Import skips everything with smart strategy"
- "Source prefix not authorized" errors during import
- "File not found" when running import CLI command
- "Invalid strategy" error from API

## Quick Check
1. Check that the server is running: `curl http://localhost:8900/health`
   - If not responding, the export/import endpoints are unavailable. Start the server first.
2. If importing, verify the file starts with a valid header: `head -1 file.jsonl | python -m json.tool`
   - Must contain `"_header": true` -- without this, import rejects the entire file.

## Diagnostic Steps

### Step 1: Check export filtering
- **Run:** `memories export --source "YOUR_PREFIX"` (test with a known prefix)
- **If count: 0:** The prefix doesn't match. Run `memories folders` to list existing source prefixes.
- **If count > 0 but missing records:** Date filters (`--since`, `--until`) may be too narrow. Try without date filters first.

### Step 2: Check import header
- **Run:** `head -1 your-file.jsonl`
- **If no `_header` field:** The file is not a valid export. Re-export from the source instance.
- **If `_header` present but import fails:** Check for encoding issues -- file must be UTF-8.

### Step 3: Check import strategy behavior
- **Run:** `memories import file.jsonl --strategy add` (force no-dedup)
- **If add works but smart doesn't:** Smart is correctly deduplicating. The records already exist.
- **If add also fails:** Check individual record format -- each must have `text` and `source` fields.

### Step 4: Check auth permissions
- **Run:** `memories auth whoami` (if using CLI auth)
- **If scoped key:** Verify the key's prefixes include the target source prefixes in the import file.
- **If admin key:** Auth should not be the issue -- investigate further.

## Resolutions

### Empty export
- **Fix:** Verify source prefix with `memories folders`. Use exact prefix including trailing slash. Remove date filters to export all.
- **Verify:** `memories export | head -1` should show count > 0.

### Invalid header on import
- **Fix:** Ensure file was created by `memories export`, not manually constructed. First line must be: `{"_header": true, "count": N, "version": "2.0.0", ...}`
- **Verify:** Re-export and try import again.

### Smart strategy skips everything
- **Fix:** This is expected if all records already exist. Use `--strategy add` to force import regardless of duplicates.
- **Verify:** `memories count` should show the expected total after import.

### Auth errors on import lines
- **Fix:** Use an admin key for unrestricted import, or remap sources to writable prefixes: `--source-remap "remote/=local/writable/"`
- **Verify:** Re-run import -- errors array should be empty.

## Escalation
- **Escalate to:** Engineering team
- **Include:** The NDJSON file (or first 10 lines), the import response JSON, server logs from the import attempt
- **SLA:** Same-day response for data migration issues

## Related
- [Feature Handoff: Export/Import (fh-003)](../feature-handoffs/fh-003-export-import.md)
- [Feature Handoff: Multi-Auth (fh-001)](../feature-handoffs/fh-001-multi-auth.md)
- [Use Case: Instance Migration (uc-006)](../use-cases/uc-006-instance-migration.md)
