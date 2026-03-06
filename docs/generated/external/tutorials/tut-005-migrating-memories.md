---
id: tut-005
type: tutorial
audience: external
topic: Migrating Memories Between Instances
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Tutorial: Migrating Memories Between Instances

## What You'll Build
By the end of this tutorial, you'll have migrated memories from one Memories instance to another, verified the transfer, and cleaned up. This covers the most common migration scenario.

## Prerequisites
- Two running Memories instances (source and target)
- CLI installed and configured (`pip install -e .`)
- API key with access to both instances
- Estimated time: 5 minutes

## Steps

### 1. Export from the source instance

Point your CLI at the source instance and export:

```bash
memories --url http://source:8900 export -o migration.jsonl
```

Check the export:
```bash
head -1 migration.jsonl
```

Expected output:
```json
{"_header":true,"exported_at":"2026-03-05T10:30:00Z","count":847,"version":"2.0.0",...}
```

### 2. Import into the target instance

Point your CLI at the target and import:

```bash
memories --url http://target:8900 import migration.jsonl
```

Expected output:
```
Imported: 847
Backup:   pre-import_20260305_103000
```

### 3. Verify the migration

Search for a known memory on the target:

```bash
memories --url http://target:8900 search "your known memory text"
```

Expected output:
```json
{"results": [{"text": "your known memory text", "similarity": 1.0, ...}]}
```

### 4. (Optional) Verify counts match

```bash
memories --url http://target:8900 admin stats
```

The total memory count should match the exported count.

## Verify It Works

Run a count on the target:
```bash
memories --url http://target:8900 count
```

Expected: The count should equal or exceed the exported count (equal if target was empty, greater if it had existing data).

## What's Next
- [Export/Import Feature Doc](../features/feat-003-export-import.md) — learn about smart deduplication and source remapping
- [Backup Management](../tutorials/tut-003-cli-quickstart.md) — managing backups created during import

## Troubleshooting
- **"File not found" error:** Use the full path to the JSONL file, or `cd` to its directory first.
- **Import shows 0 imported:** Check that the file starts with a valid header line (`_header: true`). Re-export if needed.
- **Auth errors on some lines:** Your API key may not have write access to all source prefixes. Use an admin key or add `--source-remap` to map to an authorized prefix.
