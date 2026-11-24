# Rust server: rollback tool, merge and rollback drill

Date: 2026-11-16
Status: adopted (task P7-1 of the Rust rewrite plan)

## Scope

Task P7-1 adds the parts of a return from the Rust server to the Python
server. It adds no deploy, no release and no change to a client or to the
Python server.

| Part | Path |
|---|---|
| `memories-server export-legacy` | `server/crates/memories-server/src/rollback.rs` |
| `memories-server migrate --merge` | `server/crates/memories-server/src/rollback.rs` |
| Rollback image and its entrypoint | `server/rollback/` |
| Send the exported extract jobs to the Python server | `server/scripts/resend_extract_jobs.py` |
| Rollback drill | `server/scripts/rollback_drill.sh` and `rollback_drill.py` |

The drill on a copy of the production data is not in this task. It needs
access to the droplet, and it must pass before task P7-2 starts.

## The base of the two sides

`state.db` has a new table `legacy_base_records`: the JSON text of each
record of `metadata.json` at the last migration, export or merge. `DONE_HASH`
(the SHA-256 of the full file) changes in the same transaction. Thus the merge
knows which side changed which field of a record after that point.

## export-legacy

| Item | Rule |
|---|---|
| When it exports | Only when `memories_v2` is the canonical store: `metadata.json` does not exist, its SHA-256 is equal to `DONE_HASH`, or its SHA-256 is equal to `export_pending_sha256` (an export that stopped after the rename). |
| When it skips | No `state.db`, an empty `QDRANT_URL`, a migration in shadow mode, no finished migration, or a `metadata.json` that a Python server changed. A skip is a JSON report with `status: skipped` and exit code 0. The export never replaces the writes of a Python server. |
| Size guard | The export refuses if `memories_v2` is empty and `metadata.json` has records, if `memories_v2` has less than half of the records of the base, or if there is no `metadata.json` but the old collection has points. This guards against a lost Qdrant volume. `--force` skips the guard. |
| Order of the writes | 1. The old collection: upsert of each record with its vector from `memories_v2`, delete of each other point, then a count check. 2. The extract jobs. 3. A copy of the old file to `metadata.json.pre-export-<sha>`. 4. `export_pending_sha256` in `state.db`. 5. The rename of `metadata.json`. 6. The base, `DONE_HASH` and an empty `export_pending_sha256`, in one transaction. |
| A stopped export | Before step 5, `metadata.json` does not change. The old collection can be partly written, so the Python server can refuse to start on its count check. After step 5, the Rust server and `migrate --merge` refuse to start, and the next export finishes the run. |
| `metadata.json` | The Python format: a JSON list of records with `id`. The records keep the order of the old file, then each new ID in ID order (`GET /memories` of Python lists records in file order). The write uses a temporary file, fsync, a copy of the old file to `metadata.json.bak`, and a rename, as Python does. |
| Old collection | One unnamed cosine vector, as Python makes it. If the collection does not exist, the command makes it. The payload is the record without `id`. |
| Extract jobs | Each `queued` or `running` job goes to `rollback-extract-jobs.ndjson` (appended, one `{job_id, status, request}` for each line). Then the job ends in the Rust queue with status `failed`, so that a later return to the Rust server does not run it two times. |
| After the export | `DONE_HASH` is the SHA-256 of the new file. If no Python server writes, the Rust server starts again with no merge. |
| Lock | The command takes `memories-server.lock`, so it does not run beside a Rust server. Stop the Python server before the command. |

## migrate --merge

The merge compares each record of `metadata.json` and of `memories_v2` with
the base. A search on either side writes only `last_reinforced_at`. That field
takes the later value, and it is not a change.

| `metadata.json` | `memories_v2` | Result |
|---|---|---|
| Changed | Deleted | The Python record comes back (`restored`). |
| Deleted | No change | Deleted. |
| Deleted | Changed | The Rust record stays (`kept`). |
| Present | Present | A field merge (see below). |
| New, on an ID that the Rust server gave out | Absent | The Python record gets a new ID, because links of `memories_v2` can point to the old ID. |

The field merge, for each field:

| Field changed by | Result |
|---|---|
| No side, or the two sides to the same value | The value of `memories_v2`. |
| One side | The value of that side. |
| The two sides, to different values | `text`: the Python record gets a new ID (`renumbered`) and `memories_v2` keeps its record. A field that ends in `_at`: the later value. Other fields: the value of `memories_v2`. The record is in `resolved`. |

- A Python record that points (`links`, `supersedes`, `superseded_by`,
  `conflicts_with`, `source_memory_ids`) to a renumbered record points to the
  new ID.
- The merge stores its new IDs in `state.db` (`merge_pending`) before the
  first write. A merge that stops can run again: it uses the same new IDs, so
  no record comes in two times.
- The vector of a Python record comes from the old collection if the
  embedding signature agrees, else from the embedder.
- The merge does not change `metadata.json` or the old collection. After the
  merge, the base is the current `metadata.json`, and the server starts.
- With no base (a migration before this task), each ID counts as new on the
  two sides, so no delete of a Python server comes through.

## Rollback image

`server/rollback/Dockerfile` adds the `memories-server` binary to a Python
server image (`BASE_IMAGE`). The entrypoint runs `export-legacy`, then the
Python server. If the export fails, the Python server does not start. The
image build runs `memories-server --version`, so a binary that cannot run on
the C library of the Python image stops the build. The image does not need
ONNX Runtime, because the export copies the vectors.

## Rollback drill

`server/scripts/rollback_drill.sh` runs in CI (job `rust-parity`) on the seed
corpus of the parity kit:

1. Case 1: the Python server writes the seed corpus. The Rust server migrates
   it and gets 1,000 changes and 3 extract jobs. A provider that never
   answers keeps the jobs waiting. The rollback entrypoint
   exports and starts the Python server. The Python server must hold each
   record of `memories_v2` and no other record, and answer 14 read checks.
   The vector searches of the two servers must be equal. The 3 jobs go to the
   Python server. After a Python write, a new start of the rollback
   entrypoint must skip the export and keep the write.
2. Case 2: the Rust server migrates, gets 200 changes and runs searches. A
   plain Python server runs searches, adds, changes and deletes records, and
   pins a record that the Rust server changed. The Rust server must refuse to
   start, and `migrate --merge` runs. `memories_v2` must hold the records of
   the two sides, each Python record one time, the pin with the Rust text,
   and the correct count.

## Limits

- A change of the text on each side gives two records, not one record with
  the two changes. A conflict on another field keeps one value only.
- The resend script sends each job with the admin key. For the job of a
  scoped key, the source must be in the allowed prefixes of that key, and the
  script drops `promotion_context`. `trusted_authorship` still comes from the
  admin key.
- The drill checks read routes on the Python server only. Most read routes
  of the Rust server are not ported yet, so the drill reads `memories_v2`
  from Qdrant.
