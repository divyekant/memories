# Rust server: read routes

Date: 2026-11-16
Status: adopted (task P2-3 of the Rust rewrite plan)

## Scope

The Rust server answers these routes with the replies of the Python server
v5.19.0:

| Route | Read |
|---|---|
| `GET /memory/{memory_id}` | One Qdrant get |
| `POST /memory/get-batch` | One Qdrant get for all IDs |
| `GET /memories` | One Qdrant page (query with offset and limit, ID order) |
| `GET /memories/count` | The source map; with no source, the record count |
| `GET /folders` | The source map |
| `GET /stats` | The record count, `state.db` and the backup folder |
| `GET /memory/{memory_id}/links` | One get of the targets; a scroll on `links[].to_id` for incoming links |
| `GET /memory/conflicts` | A scroll on `conflicts_with` |
| `GET /health` (with the `API_KEY` value, or no `API_KEY`) | The record count of the engine |

The engine of task P2-2 gives the store, the source map (each source and its
record count) and the reinforcement overlay. Each read record shows a pending
reinforcement, as the Python server shows its in-memory value.

## Rules that the routes keep

- A list page is the page of the records in the source scope. The records
  that the key cannot read, and the records with another `pinned` value, are
  removed from the page after the read. Thus a page of a scoped key can have
  fewer items than `limit`, as in Python.
- `total` of a list for a scoped key, and the count of a scoped key, are the
  records of the readable sources in the scope. Archived records count.
- `GET /memory/conflicts` gives the stored record without `confidence`, and
  the conflicting record with `confidence`, as in Python. The route keeps only
  the records of the requested page in memory.
- An outgoing link with a deleted target is not in the links reply. A link to
  or from a record that the key cannot read is not in the reply.
- `created_at` and `last_updated` of `/stats` come from the `meta` table of
  `state.db`. If the table has no value, they come from the `config.json` of
  the Python install in `DATA_DIR`, else null. A write task sets
  `last_updated`.

## Contract differences

| Item | Python v5.19.0 | Rust |
|---|---|---|
| `index_size_bytes` of `/stats` | Size of the embedded Qdrant files (0 with a Qdrant server) | Always 0 (a Qdrant server keeps no files in `DATA_DIR`) |
| `confidence` of a record whose source has an extraction profile with `confidence_half_life_days` | Half-life of the profile | Half-life 90 days until the profile routes are ported |
| `/usage` events `get` and `get_batch` | Written when `USAGE_TRACKING` is on | Not written until the usage task |
| A get-batch ID above 2^63-1 in `missing_ids` | The ID as sent | 9223372036854775807 |
| No `QDRANT_URL` | Embedded Qdrant | The store routes answer 503 (task P2-2) |

## Tests

- `server/scripts/parity_reads.sh`: the Python server runs the golden cases
  up to `stats`, a loader copies `metadata.json` into the Rust collection,
  and the Rust server replays the 21 read cases. Then the Python server
  starts again on the same data, and `server/scripts/parity_reads_ab.py`
  sends 257 more read requests to both servers (links in each direction,
  three keys, pages, filters, the 422 replies). Zero differences is the pass
  condition. A changed link type rule in the Rust code makes the check fail.
- `server/scripts/list_memory_check.sh`: 300,000 synthetic records, 200
  warm-up list requests, then 1,000 list requests with random offsets, limits
  up to 5,000 and source filters. The check fails if the resident memory of
  the process increases by more than 16 MB.
- Unit tests for the pydantic parse rules of the query values and the batch
  IDs, the folders, the `pinned` compare and the backup count. A Qdrant test
  for the page of the store.
- `server/scripts/load_v2.py` is a parity helper until `memories-server
  migrate` (task P1-4) is on `rust-rewrite`.
