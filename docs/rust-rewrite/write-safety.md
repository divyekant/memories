# Write safety: test and benchmark report (task P3-3)

Date: 2026-11-16. Design: `docs/decisions/2026-11-16-rust-write-safety.md`.

## Result

| Exit check | Limit | Result |
|---|---|---|
| Add p95 at 100,000 records | 250 ms or less | 9.6 ms |
| Add p95 at 300,000 records | 250 ms or less | 10.9 ms |
| Records in a state between two steps after 100 forced stops | 0 | 0 |

## Test machine

| Item | Value |
|---|---|
| Machine | Claude cloud session (Linux VM), kernel 6.18.44 |
| CPU | Intel Xeon Processor @ 2.10GHz, 4 vCPUs |
| Memory | 16 GB |
| Qdrant | 1.15.4, release binary, on the same VM, local disk |
| Embedder | all-MiniLM-L6-v2, ONNX Runtime 1.31.0, CPU |
| Build | `cargo bench` (release profile), Rust 1.100.0 |

The benchmark and Qdrant share the 4 vCPUs. The production droplet also has 4
vCPUs, but other services use them too.

## Add benchmark and search under write load

Command: `cargo bench -p memories-engine --bench write_load` (file
`server/benches/write_load.rs`). The benchmark calls the engine directly, with
no HTTP layer, audit row or usage row.

Corpus: synthetic records. Each record has a real embedding of one of 2,000
texts plus a small random change, so the vectors spread as real records do.
The benchmark waits until Qdrant has built the index. Each measure has 200
samples, after 10 adds to warm up.

The two load measures run at the same time: one client adds records with no
pause, and one client sends 200 searches.

### 100,000 records

Seed and index: 42 s. Start of the writer (catalog load and length count):
2.5 s.

| Measure | Samples | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|
| Add | 200 | 6.8 | 9.6 | 15.7 | 18.5 |
| Add with `deduplicate` | 200 | 15.3 | 25.4 | 27.6 | 42.6 |
| Search, no write load | 200 | 5.5 | 8.1 | 9.8 | 12.4 |
| Search, constant write load | 200 | 9.8 | 16.7 | 21.4 | 24.5 |
| Add, during the searches | 174 | 11.3 | 18.6 | 25.8 | 32.8 |

### 300,000 records

Seed and index: 130 s. Start of the writer: 7.3 s.

| Measure | Samples | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|
| Add | 200 | 6.9 | 10.9 | 16.0 | 20.0 |
| Add with `deduplicate` | 200 | 12.0 | 18.2 | 26.9 | 27.9 |
| Search, no write load | 200 | 5.7 | 8.1 | 9.7 | 14.0 |
| Search, constant write load | 200 | 9.6 | 16.2 | 23.8 | 25.2 |
| Add, during the searches | 166 | 11.4 | 19.7 | 26.9 | 30.0 |

Notes:

- The add latency does not grow from 100,000 to 300,000 records. The
  embedding and the Qdrant upsert of one point do not depend on the corpus
  size.
- A constant write load doubles the search p95 (8 ms to 16 ms). The cause is
  the CPU: the embedder of the writes and of the searches shares the 4 vCPUs
  with Qdrant. A search does not wait for the write lock.
- The writer start time grows with the corpus (one scroll for the catalog and
  one for the length sums). At 300,000 records it is 7.3 s.
- A first run on the same machine had a parallel `cargo build` during the
  300,000 run. Its add p95 was 16.0 ms (with `deduplicate`: 69.1 ms). The
  tables above come from a second run with no other load.

## Crash test

Command: `cargo test -p memories-server --test write_crash -- --ignored
--nocapture` (file `server/crates/memories-server/tests/write_crash.rs`).

The test runs the server binary (debug build) on one data folder
and one Qdrant collection. Four clients send add, batch add (150 items),
supersede and merge with no pause. At a random time from 200 to 1,500 ms, the
test stops the server with SIGKILL, then starts it again and checks the store
(see the decision record for the rules).

Result of the last run:

| Item | Value |
|---|---|
| Forced stops | 100 |
| Writes that succeeded (add, batch add, supersede, merge) | 251, 164, 244, 159 |
| Records at the end | 26,359 |
| Journal entries that a start completed or dropped | 8 (supersede 3, merge 5) |
| Records in a state between two steps | 0 |
| Time | 260 s |

The test fails if a kind of write never succeeds, or if no stop falls
inside a journal entry. Thus a pass proves that the repair ran.

The engine test `crates/memories-engine/tests/write_safety.rs` makes each
state between two steps by hand (supersede, merge and delete), then starts a
new engine and writer, and checks the repair.
