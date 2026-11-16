# Rust server: core writes

Date: 2026-11-16
Status: adopted (task P3-1 of the Rust rewrite plan)

## Scope

The Rust server has these routes of `app.py` v5.19.0:

- `POST /memory/add`, with `deduplicate` and the `on_duplicate` modes
  (`add`, `skip`, `supersede`).
- `POST /memory/add-batch`.
- `POST /memory/upsert` and `POST /memory/upsert-batch`, keyed by `source`
  and `entity_key`.
- `PATCH /memory/{memory_id}` and `DELETE /memory/{memory_id}`.
- `POST /memory/delete-batch`.

The code is `memories_engine::writes` (the write flow) and
`memories_server::writes` (the routes).

## Rules

| Item | Rule |
|---|---|
| Duplicate threshold | 0.90 for `deduplicate` and for the doctrine check. |
| Identical threshold | `DOCTRINE_IDENTICAL_THRESHOLD`, default 0.97. The server reads it at start. A value that is not a number stops the start. |
| Order of writes | One lock puts all writes and the reinforcement flush in sequence. Thus a flush cannot run between the read and the payload write of a write. |
| Embedding | An add embeds the new texts. A PATCH embeds again only when the text changes. Other changes write the payload only. |
| Keyword vector | Each new or changed text gets a keyword vector with the current `avgdl` (decision P0-2). The sums are in `state.db`, table `meta`. |
| Catalog | Each write updates the source map and the link map. |
| Events | `memory.added`, `memory.updated`, `memory.deleted` and `memory.linked`, with the Python data. The bus keeps the last 100 events. Task P5-5 adds the stream route. |
| Audit | The same actions as `_audit` in `app.py`. |
| Policy | The project namespace policy, the reserved fields and the trusted authorship of `project_memory.py`. A policy error gives HTTP 422. |
| ID counter | At start, the counter moves above the highest stored ID. |
| Length sums at start | The server counts the sums again from the store at each start. |
| Failed batch add | Each stored chunk of 100 points goes into the catalog and the sums before the next chunk. A failed chunk leaves no stored point that the catalog does not show. |

## Qdrant calls of each write

The test `crates/memories-engine/tests/writes.rs` counts the Qdrant calls of
each write with 20 and with 200 records. The counts are the same. The test
also compares each point (payload and vectors) before and after each write.
Only the points of the affected records change.

| Write | Qdrant calls |
|---|---|
| Add | 1 upsert (each 100 points) |
| Add that `deduplicate` skips | 1 dense search |
| PATCH of the text | 1 read, 1 upsert of the point |
| PATCH of other fields | 1 read, 1 payload replace |
| Supersede | 1 read, 1 upsert, 1 payload set on the old record |
| Upsert (new) | 1 filtered scroll, 1 upsert |
| Upsert (update) | 1 filtered scroll, 1 read, 1 upsert |
| Delete | 1 read, 1 read of the records that link to it, 1 payload replace for each of them, 1 delete |
| Delete batch | 1 read, 1 delete (more than 10 IDs: 1 snapshot and 1 count first) |

## Differences from the Python server

| Item | Python v5.19.0 | Rust server | Reason |
|---|---|---|---|
| Add batch with items with and without metadata | HTTP 500 (`None.items()`). | Each item keeps its own metadata. | The Python reply is a defect. No client depends on a 500. |
| Backup before a write (`_backup`) | A copy of `metadata.json`. | No copy. | The Rust server has no `metadata.json`. |
| Supersede of the old record | `superseded_by` only in `metadata.json`. | `archived` and `superseded_by` in the Qdrant payload. | Qdrant is the only store. |
| Usage events (`_log_usage_event`) | Written. | Not written. | Task P5-1 adds them. |

Fixed Python defect (dk, 2026-11-16): a scoped key that patches a record out
of its scope gets HTTP 403. Python v5.19.0 gives HTTP 500, because the route
raises the 403 inside its `except Exception` block. The fix is an intended
difference in `parity/intended.json`.
