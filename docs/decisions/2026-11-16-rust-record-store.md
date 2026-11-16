# Rust server: record model, Qdrant store and ID counter

Date: 2026-11-16
Status: adopted (task P1-3 of the Rust rewrite plan)

## Record model

`memories_types::Record` has typed fields for the known fields and a map
(`extra`) for all other fields.

| Typed field | Type |
|---|---|
| `id` | `u64` (0 to 2^63-1, the Qdrant point ID) |
| `text`, `source`, `created_at`, `updated_at`, `timestamp`, `last_reinforced_at`, `document_at`, `entity_key` | `Option<String>` |
| `archived`, `pinned`, `is_latest` | `Option<bool>` |
| `conflicts_with`, `supersedes`, `superseded_by` | `Option<i64>` |
| `links` | `Option<Vec<Link>>` (`to_id`, `type`, optional `created_at`) |

Rule: a known field goes into its typed field only if the typed value writes
back the same JSON. If not, the field stays in `extra` as it is. Examples: a
null value, `"pinned": "yes"`, `"conflicts_with": "17"`, and a link with an
extra key. Thus a write and a read give back each field of each record.

The server calculates `confidence` at each read
(`memories_engine::records::with_confidence`). It uses the anchor rule of
`_enrich_with_confidence`: `last_reinforced_at`, then `updated_at`, then
`created_at`, then `timestamp`. A truthy anchor that is not a string gives
0.0, as in Python.

## Qdrant collection

| Item | Value |
|---|---|
| Name | `<QDRANT_COLLECTION>_v2`. The default is `memories_v2`. |
| Dense vector | `dense`, cosine, dimension of the embedder |
| Keyword vector | `keyword`, sparse, modifier `idf` (decision P0-2) |
| Payload | The full record except `id` |
| Payload indexes | `source`, `document_at`, `entity_key` (keyword); `archived`, `pinned`, `is_latest` (bool); `conflicts_with`, `links[].to_id` (integer) |

An existing collection must have the same vectors, or `ensure_collection`
fails. The store refuses an `id` key in a payload, because the point ID is the
record ID.

The store operations are `get`, `get_many`, `upsert`, `set_payload`,
`replace_payload`, `delete`, `scroll`, `count`, `facet` and `max_id`.

## Settings

The store reads the `QDRANT_*` settings of `qdrant_config.py` with the same
defaults. The Rust client uses gRPC. If `QDRANT_URL` has port 6333 or no
port, the client uses port 6334. `QDRANT_GRPC_PORT` sets another port. A URL
with a different port keeps that port.

## ID counter

`state.db` (SQLite, WAL mode) holds the counter in the table `counters`.

- `reserve_ids(n)` reads and writes the counter in one `IMMEDIATE`
  transaction. The new value is on disk before the IDs go to the caller.
- `raise_id_floor(max_id + 1)` moves the counter up at start. It never moves
  the counter down.
- An ID that the counter gave out is not given again: not after a restart,
  not after the delete of the highest ID, not after a crash between the
  reserve and the write, and not with two processes on one file.

The Python server took `max(id) + 1` at each load. Thus a delete of the
highest ID gave that ID again. The cause of the duplicate ID in production
(108742 two times in `metadata.json`, 2 November) is not known yet. The Rust
counter does not depend on the stored IDs, so a stale in-memory value cannot
give an ID two times. This guarantee needs `state.db` to stay: keep it on the
data volume and in the backups. If `state.db` is lost, the counter starts
again above the highest stored ID, so a deleted highest ID can come again.

## Contract differences

| Item | Python v5.19.0 | Rust |
|---|---|---|
| Empty `QDRANT_URL` | Embedded Qdrant in `DATA_DIR` | Refused at start: a Qdrant server is necessary |
| `QDRANT_GRPC_PORT` | Not used | New: the gRPC port |
| Collection | `QDRANT_COLLECTION` | `QDRANT_COLLECTION` plus `_v2` |
| Bad `QDRANT_WRITE_ORDERING`, `QDRANT_READ_CONSISTENCY` | Not checked at start; given to the Qdrant client as is | Refused when the settings load |
| Integer above 2^63-1 in a field | Stored | Refused with an error, because Qdrant would return a float |
| New ID after the delete of the highest ID | Same ID again | Next ID of the counter |

## Test

`server/crates/memories-store/tests/qdrant_store.rs` runs against Qdrant
1.15.4 in CI. The fixture `seed_records.json` holds the 246 stored records of
the Python server after the parity scenario up to its destructive cases.
`make_seed_records.py` makes the file again. `edge_records.json` adds records
with null values, values of other types, nested maps, floats, large integers
and Unicode text.
