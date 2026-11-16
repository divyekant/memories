# Keyword vector pass (task P3-4)

Result on 2026-11-16: **the exit check passes.** The rules of the pass are in
`docs/decisions/2026-11-16-rust-keyword-index.md`, section "The full pass".

## Exit check

| Condition | Result | Limit |
|---|---|---|
| Grow dataset: keyword overlap at 10 with rank_bm25, each checkpoint | 0.947 or more | 0.94 |
| Add p95 during passes | 46 ms (96 ms with the grow run on the same 4 CPUs) | 250 ms |
| Forced stop in the middle of a pass | The next start continues from the cursor. Each keyword vector is then equal to the vector that `verify` expects. | Pass |
| A pass changes only the keyword vectors | Only `scroll` and `update_keyword_vectors` calls. The payloads and the dense vectors do not change. | Pass |

## Grow dataset through the Rust server

`parity/keyword_index/run.py --dataset grow-server` adds the 20,000 records
of the `grow` dataset (2,000 short synthetic records, then LongMemEval
chunks) to the Rust server in batches of 100, through `POST /memory/add-batch`.
The server ran with `MEMORIES_KEYWORD_REFRESH_CHECK_SEC=1` and
`MEMORIES_KEYWORD_REFRESH_MIN_GAP_SEC=0`, because the run takes 8 minutes,
not hours. The run of task P2-4 also did not simulate the 1 hour limit. At
each checkpoint, the script compares the keyword vectors that the server
wrote with rank_bm25. "Now" is directly after the add. "Settled" is after the
pass that was due.

| Records | avgdl | avgdl of the last pass | Overlap, now | Overlap, settled | Top 1, settled |
|---|---|---|---|---|---|
| 2,000 | 14.8 | 14.8 | 1.000 | 1.000 | 1.000 |
| 4,000 | 179.4 | 179.4 | 0.968 | 0.974 | 0.978 |
| 6,000 | 232.3 | 232.3 | 0.975 | 0.975 | 0.955 |
| 8,000 | 259.4 | 232.3 | 0.964 | 0.964 | 0.967 |
| 10,000 | 276.3 | 232.3 | 0.951 | 0.951 | 0.939 |
| 12,000 | 286.6 | 232.3 | 0.947 | 0.947 | 0.911 |
| 14,000 | 295.7 | 295.7 | 0.960 | 0.960 | 0.950 |
| 16,000 | 302.1 | 295.7 | 0.954 | 0.954 | 0.961 |
| 18,000 | 306.8 | 295.7 | 0.954 | 0.954 | 0.955 |
| 20,000 | 310.5 | 295.7 | 0.951 | 0.951 | 0.961 |

The server made 4 passes (after 2,100, 4,100, 6,100 and 14,100 records). The
lowest value, 0.947 at 12,000 records, is just before a pass: the average is
23% from the value of the last pass. The values with the exact average (run of
task P2-4) are 0.953 to 0.975 at the same checkpoints.

Raw numbers: `parity/keyword_index/results-grow-server-2026-11-16.json`.

An earlier run with the same code gave 0.951 at 4,000 records. A collection
with the same texts and the exact average, made directly in Qdrant, gives
0.974. The two runs differ only in the time of the Qdrant writes, so the cause
is probably the Qdrant segment state. The test did not find the cause.

## Qdrant test

`cargo test -p memories-engine --test keyword_refresh -- --ignored` (CI step
"Keyword vector pass") runs against Qdrant 1.15.4 and the real embedder:

1. 1,000 short records, then 300 long records. A pass writes 1,300 records
   in 13 batches. The test compares each payload and dense vector before and
   after the pass, and each keyword vector with the vector of its text.
2. 300 more long records, then a pass with batches of 20 that the test stops
   at an await point (as at a kill). A new writer continues the pass from the
   cursor, and each keyword vector is then exact.
3. 200 single adds while passes run without a break: add p95 is 250 ms or less.

## How to run the grow check again

```bash
API_KEY=k DATA_DIR=/tmp/grow QDRANT_URL=http://127.0.0.1:6333 QDRANT_COLLECTION=p34grow \
  MEMORIES_KEYWORD_REFRESH_CHECK_SEC=1 MEMORIES_KEYWORD_REFRESH_MIN_GAP_SEC=0 \
  server/target/release/memories-server serve --port 8934 &
.venv/bin/python -m parity.keyword_index.run --qdrant local=http://127.0.0.1:6333 \
  --dataset grow-server --server http://127.0.0.1:8934 --key k \
  --collection p34grow_v2 --data-dir /tmp/grow --out grow-server.json
```

Use an empty `DATA_DIR` and a collection name that no install uses.
