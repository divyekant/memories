# Backup, export and import (task P5-4)

This page gives the step plan, the design and the contract differences of task
P5-4. The Python reference is v5.19.0 with `MEMORIES_PERSIST_DELAY_SEC=0`.

## Step plan (test first)

1. Write the store test: a snapshot of a collection downloads to a file, and
   an upload of the file gives back the points (`qdrant_store.rs`, ignored
   test against Qdrant 1.15.4). Then add `download_snapshot`,
   `upload_snapshot` and `delete_snapshot` to `QdrantStore`.
2. Write the unit tests of the pure parts: the export lines (header, record,
   `ensure_ascii`), the import parse (header, line errors, remap), the backup
   name and the retention rule. Then write the code.
3. Write the route tests (`tests/backup.rs`, ignored, Qdrant and the
   embedder): export and import across the filters and the key scopes,
   backup and restore with counts and links, snapshot create and restore,
   the error replies. Then write the eight routes.
4. Write the exit check script `server/scripts/parity_backup.sh`: Python
   export into Rust and back, backup and restore, and the golden cases of
   these routes. Add the script and the route tests to CI.
5. Add the scheduled backup with a test of the retention rule.

## Design

A backup is a folder `DATA_DIR/backups/<prefix>_<YYYYMMDD>_<HHMMSS>`, as in
Python. The folder holds these files:

| File | Content |
|---|---|
| `memories_v2.snapshot` | The Qdrant snapshot of the collection, downloaded from Qdrant. |
| `export.ndjson` | The `GET /export` lines of all records. This copy does not need Qdrant. |
| `<name>.db` | A copy of each SQLite file of `DATA_DIR` (`VACUUM INTO`). |

- The server downloads the snapshot through the Qdrant REST API, then deletes
  the snapshot in Qdrant. Thus a backup does not depend on the Qdrant storage.
- `POST /restore` holds the write lock. It makes a `pre_restore` backup,
  uploads the snapshot file with `priority=snapshot`, and loads the catalog,
  the document lengths and the ID floor again. The ID counter never goes
  down, so the server does not use an ID again after a restore.
- A restore does not replace the SQLite files. The copies in the backup are
  for a manual recovery of keys, the audit log and the queue.
- `POST /snapshots/{name}/restore` downloads the named snapshot from Qdrant
  and restores it in the same way, also after a `pre_restore` backup.
- Retention: the `MAX_BACKUPS` newest folders, the 2 newest folders of each
  prefix, and the 7 newest `scheduled` folders stay. The server deletes the
  other folders.
- Scheduled backup: one time each day, the server makes a backup with the
  prefix `scheduled`. The first one comes 24 hours after the newest
  `scheduled` folder, or 1 hour after the start if there is none.
- `GET /export` and `POST /import` take the same query parameters, lines and
  replies as Python. The import strategies `add`, `smart` and
  `smart+extract` use the write flow of task P3-1.

## Contract differences

| Python v5.19.0 | Rust |
|---|---|
| A backup copies `metadata.json`, `config.json` and `index.faiss`. | A backup holds the Qdrant snapshot, an NDJSON export and the SQLite files. |
| `POST /restore` copies `metadata.json` back and makes the vectors again. | `POST /restore` uploads the snapshot. A backup folder of the Python server has no snapshot, so its restore answers 404 "Backup '<name>' is incomplete". |
| A snapshot name in embedded mode is `local-backup-<ts>`. | The Qdrant server gives the name. |
| A snapshot restore in server mode passes only the name to Qdrant and fails. | The snapshot restore works, and makes a `pre_restore` backup first. |
| A JSON line that is not an object, or a `text` or `source` that is not a string, gives HTTP 500 with the Python exception text. | The same lines give HTTP 500 with "Internal server error". |
| No scheduled backup. | One scheduled backup each day. |
