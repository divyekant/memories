# Rust server: the migrate and verify commands

Date: 2026-11-16
Status: adopted (task P1-4 of the Rust rewrite plan)

## Decision

`memories-server migrate` copies the records of the Python server into the
collection `<QDRANT_COLLECTION>_v2`. `memories-server verify` compares the
two. Both commands only read the old data: `metadata.json`, `config.json` and
the old collection. A test checks the SHA-256 of `metadata.json` and the
count of the old collection before and after each run.

## Sources

| Item | Source |
|---|---|
| Records | `$DATA_DIR/metadata.json`, the canonical store of v5. A repeated ID keeps the last entry, as the Python `_id_map` does. The report names each repeated ID. |
| Old collection | `qdrant_collection` of `config.json`. If the file has no value, `QDRANT_COLLECTION`. |
| Dense vector | The unnamed vector of the old point, if `embedding_signature` of `config.json` is equal to the signature of the Rust embedder and the vector has the right dimension. If `config.json` has no signature, up to 8 old vectors must have a cosine of 0.9999 or more with new embeddings. If not, the server embeds each text again (`EMBED_DOC_PREFIX`). The old and new collection names must be different. |
| Keyword vector | `doc_vector(text, avgdl)` of decision P0-2, with the exact average token count of all records. `state.db` gets the sum of the token counts and the record count. |

An install with the embedded Qdrant mode of Python has no old collection on
the Qdrant server. The migration then embeds each text. The test on the
Python seed corpus gave a lowest cosine of 0.99999999 between these vectors
and the Python vectors.

## Rules

| Rule | Behavior |
|---|---|
| Second run | Out of shadow mode, the command writes only the IDs that are not in `memories_v2`. After a finished migration it reports `up_to_date`. |
| Stopped run | The next run writes the remaining IDs. Each batch of 256 points is one upsert. |
| End of a run | `verify` runs. Only if it passes, `state.db` gets the ID floor (highest ID plus 1), the token count sums and the SHA-256 of `metadata.json` (`migration_metadata_sha256`). |
| Shadow mode (`--shadow`) | `metadata.json` stays the canonical store: the run writes each record again and deletes the IDs that `metadata.json` does not have. After a migration that finished out of shadow mode, `--shadow` is refused, because `memories_v2` is then the canonical store. |
| File changed during an unfinished run | A run records the SHA-256 of `metadata.json` when it starts. If the file is different at the next run or start, the server refuses and names `memories-server migrate --shadow`, which writes each record again. |
| Out of shadow mode | The run does not replace or delete a record in `memories_v2`. A different record makes `verify` fail. |
| Free disk space | Before the run: free bytes on the file system of `DATA_DIR` must be 3 times `metadata.json` plus 2 KiB for each record. If Qdrant keeps its files on another disk, this check does not see that disk. |
| First start of `serve` | If `metadata.json` exists and no migration finished, the server listens, answers only `GET /health`, gives HTTP 503 on the other routes, and runs the migration. If it fails or panics, the process stops with exit code 1 and a message. |
| Changed file | If the SHA-256 of `metadata.json` differs from the recorded value, `serve` and `migrate` refuse to start and name `memories-server migrate --merge` (task P7-1). |

## Report

Each command writes one JSON object to standard output. `migrate` gives
`status`, the counts (`metadata_entries`, `records`, `written`,
`already_present`, `copied_vectors`, `embedded_vectors`, `deleted`),
`duplicate_ids`, `avgdl`, `next_id`, `seconds` and the `verify` report.
`verify` gives `ok`, `collection_count`, the counts and the first 20 IDs of
`missing`, `extra`, `field_mismatch` and `vector_mismatch`, `vectors_checked`
and `lowest_cosine`. `verify` exits with code 1 when `ok` is false.

`verify` compares each field of each record (`Record::to_json`). It samples
`--sample` records (default 1000) evenly by ID. For each sample, the dense
vector must have a cosine of 0.9999 or more with the old vector (or a new
embedding), and the keyword vector must be equal to a new `doc_vector`.
Records that the Rust server wrote after the migration count as `extra`.

## Test

- `server/crates/memories-server/tests/migrate.rs` runs against Qdrant 1.15.4
  in CI. Data: the 246 seed records, the edge records and one repeated ID.
  The old collection has a vector for 4 of 5 records; a fake embedder gives
  the rest.
- By hand on 2026-11-16: the Python server v5.19.0
  (`MEMORIES_PERSIST_DELAY_SEC=0`, Qdrant server mode) wrote the seed corpus.
  `migrate` copied 246 vectors in 0.4 s and `verify` passed with each vector
  checked. The first start of `serve` gave HTTP 503 on `/health/ready` until
  the migration was done. After a write to `metadata.json`, `serve` refused
  to start.

## Open items

- If `state.db` is lost after the migration, the records that the Rust
  server wrote count as `extra`, and the first start fails `verify`. Keep
  `state.db` in the backups.

- Shadow mode of `serve` (task P6-2): the start check refuses a changed
  `metadata.json`. A shadow container must skip this check.
- `migrate --merge` is task P7-1.
- With `EMBED_QUERY_PREFIX` or `EMBED_DOC_PREFIX` set, the Rust signature
  does not have the Python prefix fingerprint (SHA-1), so the migration embeds
  each text again.
