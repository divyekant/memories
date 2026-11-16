# Rust server: Python defects fixed, not kept for parity

Date: 2026-11-16. Decision by dk ("Fix them now in rust").

## Context

The rewrite tasks P3-1, P3-2, P5-2 and P2-2 kept some wrong replies of the
Python server v5.19.0, so that the parity runs stayed equal. dk decided to fix
them in the Rust server before the release of v6.0.0.

## Decision

The Rust server fixes each defect in the table. Each fix is an intended
difference in `parity/intended.json`. The parity diff compares the Rust reply
with the entry, not with the Python reply. A Python reply that no longer has
the defect makes the entry stale, and the diff fails.

| Route | Python v5.19.0 | Rust server | Parity case |
|---|---|---|---|
| `PATCH /memory/{memory_id}`, scoped key, record out of scope | JSON 500 | 403 | `patch-rw-out-of-scope` |
| `POST /memory/{memory_id}/link`, key that cannot write a record | JSON 500 | 403 | `link-rw` |
| `POST /memory/deduplicate`, `dry_run` false | Delete, then JSON 500, no audit row | 200 with the result, audit row `memory.deduplicated` | `dedup` |
| `POST /memory/merge`, missing ID | Plain-text 500 | 400 `Memory <id> not found` | `merge-missing-500` |
| `POST /memory/merge`, repeated ID | Adds the merged record, then 400 | 400 `Memory <id> is listed more than once`, no write | `merge-dup-400`, `merge-dup-cleanup` |
| `POST /memory/supersede`, scoped key, missing ID | Plain-text 500 | 404 `Memory <id> not found` | `supersede-legacy-rw-missing` |
| `DELETE /memory/{memory_id}/link/{target_id}`, missing record | Plain-text 500 | 404 `Memory ID <id> not found` | `unlink-missing-500` |
| `DELETE /memory/{memory_id}/link/{target_id}`, no match | Writes an empty `links` list | No write | none (the reply is the same) |
| `PATCH /api/keys/{key_id}`, missing key whose ID holds `principal_id` | 422 | 404 | `keys-update-principal-404` |
| `POST /search` and `/search/evidence`, date that the intent step cannot compute | Plain-text 500 | Search with no intent | `search-intent-overflow` |

The case `merge-dup-cleanup` deletes the extra record that Python adds. Python
then gives its ID again at the next add, so the two servers keep the same IDs.

## Consequences

- Each new status is one that the route already gives for other requests, so
  a client needs no change.
- These Python replies stay as they are: a record with a source or a date
  field that is not a string (bulk deletes, temporal filters), and
  `POST /snapshots/{name}/restore` of a missing snapshot (JSON 500). They need
  data that the API does not write, or they are not in a decision record as a
  kept defect. A later task can fix them.
