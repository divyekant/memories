# Rust server: bulk writes and relation writes

Date: 2026-11-16
Status: adopted (task P3-2 of the Rust rewrite plan)

## Scope

The Rust server has these 13 routes of `app.py` v5.19.0:

- `POST /memory/delete-by-source`, `POST /memory/delete-by-prefix` and
  `DELETE /memories`.
- `POST /memory/supersede` and `POST /memory/{memory_id}/supersede`.
- `POST /memory/merge` and `POST /memory/archive-batch`.
- `POST /memory/{memory_id}/link` and
  `DELETE /memory/{memory_id}/link/{target_id}`.
- `POST /folders/rename`.
- `POST /memory/conflicts/resolve` and `POST /memory/deduplicate`.
- `POST /memory/missed`.

The code is `memories_engine::writes::bulk` (the engine part) and
`memories_server::bulk` (the routes).

## Rules

| Item | Rule |
|---|---|
| `delete-by-source` match | The pattern is a substring of the source (`pattern in source`). |
| `delete-by-prefix` and `DELETE /memories` match | The source starts with the prefix. There is no "/" boundary: `team/a` also matches `team/alpha`. |
| Pinned records | A bulk delete does not delete a pinned record. `deduplicate` deletes pinned records too, as Python does. |
| Source that is not a string | If a record has a source that is not a string, `delete-by-source`, `delete-by-prefix` and `DELETE /memories` reply with the JSON 500, and `POST /folders/rename` replies with the plain-text 500, as Python does. |
| Snapshot | `delete-by-source` and `delete-by-prefix` make a Qdrant snapshot first, unless `skip_snapshot`. `DELETE /memories` always makes one. A scoped key on `delete-by-source` makes one only for more than 10 records. |
| Links to deleted records | The bulk deletes and `deduplicate` keep the links that point to the deleted records, as Python does. A single delete removes them (task P3-1). |
| Merge | The new record has a `supersedes` link to each merged record. The merged records get `archived` and no `superseded_by`. |
| Conflict resolution | `newest_wins` on `document_at`, then `created_at`, then `timestamp`. A tie goes to the flagging record. The loser gets `archived` and `superseded_by` in the Qdrant payload. If a link step fails, the server writes the changes before that step, then replies 500, as Python does. |
| Deduplicate | The similarity comes from the stored dense vectors. Python embeds each text again; the two vectors are equal to 0.9999997 cosine (gate G2). Only records of the same source compare, and only the 5 most similar records of each record. If two similarities are equal, the order can be different from Python. Before the delete, the server makes the Qdrant snapshot `pre_dedup`. |
| Missed counts | `missed_count` counts the calls for each source since the server started, as in Python. |
| Audit | The same actions as `_audit` in `app.py`. |
| Errors | Where Python raises an error that no handler catches, the reply is the plain-text 500 of Starlette. Where Python raises an `HTTPException` inside `except Exception`, the reply is the JSON 500. |

## Kept Python defects

The live parity run compares each reply with Python, so the Rust server keeps
these replies of v5.19.0:

| Route | Python v5.19.0 reply |
|---|---|
| `POST /memory/deduplicate` with `dry_run` false | The records are deleted, then HTTP 500: the audit call does `len()` of an int. No audit row. |
| `POST /memory/{memory_id}/link` with a key that cannot write a record | HTTP 500, not 403. |
| `POST /memory/merge` with a missing ID | Plain-text HTTP 500, not 400. |
| `POST /memory/merge` with a repeated ID | HTTP 400 after the merged record is added. No record is archived. |
| `POST /memory/supersede` with a scoped key and a missing ID | Plain-text HTTP 500. |
| `DELETE /memory/{memory_id}/link/{target_id}` with a missing record | Plain-text HTTP 500. |
| `DELETE /memory/{memory_id}/link/{target_id}` with no match | A record with no links gets an empty `links` list. |

## Differences from the Python server

| Item | Python v5.19.0 | Rust server | Reason |
|---|---|---|---|
| Backup before a bulk delete (`_backup`) | A copy of `metadata.json`. | No copy. | The Rust server has no `metadata.json`. |
| Deduplicate scan | All pairs of all records (one matrix of N x N). | All pairs inside each source. | Same result with less memory. A source with very many records is slow; a filtered Qdrant search for each record is the upgrade. |
| Usage events (`_log_usage_event`) | Written. | Not written. | Task P5-1 adds them. |

## Check

`server/scripts/parity_bulk.sh` is the exit check:

1. It replays the golden cases of the write routes of P3-1 and P3-2 (95
   cases, 0 differ). It leaves out the golden case `delete-by-prefix`: its
   reply counts 2 records from `POST /import`, which this server does not
   have yet.
2. It runs the bulk scenario (`parity/bulk_scenario.py`, 122 cases) on a new
   Python server and on a new Rust server, and compares each reply (0
   differ). After each delete, a case lists the records that stay. Thus the
   sets of deleted IDs of each delete route are equal on the seed corpus.

`crates/memories-engine/tests/bulk.rs` tests the engine part against Qdrant
and the embedder.
