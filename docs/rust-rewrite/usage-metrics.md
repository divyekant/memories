# Usage, metrics and feedback routes (task P5-1)

The Rust server keeps `usage.db` with the same seven tables and the same
schema as the Python `UsageTracker` (v5.19.0). It answers these routes with
the same replies as the Python server:

- `GET /usage`
- `GET /metrics` (JSON)
- `GET /metrics/search-quality`, `/metrics/extraction-quality`,
  `/metrics/quality-summary`, `/metrics/failures`,
  `/metrics/problem-queries`, `/metrics/stale-memories`,
  `/metrics/graph-search` and `/metrics/temporal-search`
- `POST /search/feedback`
- `GET /search/feedback/history`
- `DELETE /search/feedback/{feedback_id}` (admin only, writes the audit entry
  `feedback.retracted`)

The route `GET /metrics/prometheus` is new. It is admin only and gives the
Prometheus text format, version 0.0.4.

`USAGE_TRACKING=true` turns the tracker on. If the tracker is off, the routes
give the replies of the Python `NullTracker`.

## Writer

`memories-server/src/usage.rs` holds the tracker.

- A route sends each row to one writer thread (`usage-writer`) through a
  channel. The route does not wait for the disk.
- The writer takes up to 512 rows from the channel and writes them in one
  transaction.
- The channel holds 50,000 rows. If the channel is full, the server drops the
  row and writes a warning to the log.
- A read first sends a flush to the writer and waits for it. Thus a read sees
  all the rows of the calls before it.
- At a stop, the server writes all the queued rows before it exits.
- `POST /search/feedback` waits for its row, so the next search reads the new
  feedback score.
- At start, the tracker adds the columns that Python added after v5.7.2, as
  `UsageTracker._migrate` does.

## Rows that the routes write

| Route | Rows |
|---|---|
| `POST /search`, `/search/batch` | `search` event, one retrieval row for each result, a graph row if `graph_weight` > 0, a temporal row if `since` or `until` is set |
| `POST /search/explain` | none (as Python) |
| `POST /search/evidence` | `search_evidence` event |
| `POST /memory/is-novel` | `is_novel` event |
| `POST /memory/add` | `add` event with the source |
| `POST /memory/add-batch` | `add` event with the item count |
| `DELETE /memory/{memory_id}` | `delete` event with the source |
| `POST /memory/delete-batch` | `delete` event with the ID count |
| `POST /memory/extract` | `extract` event |
| Extract worker | extraction outcome row, and a token row for each stage with tokens |
| `POST /search/feedback` | feedback row |

Each event row keeps the headers `X-Memories-Client`,
`X-Memories-Session-Id` and `X-Memories-Invocation`, cut to 100, 200 and 100
characters.

A hybrid search reads the feedback scores when `feedback_weight` > 0, as
Python does.

## Exit check

`server/scripts/parity_usage.sh` is the exit check. CI runs it in the job
`rust-parity`. The Python server and the Rust server use one `usage.db` in
turns:

1. Python runs the golden cases from `health-minimal` to `usage-headers-add`.
   Then `memories-server migrate` copies the records into Qdrant.
2. Rust runs `usage-session` and `usage-7d`. These cases read the rows that
   Python wrote.
3. Python runs the cases from `stats` to `link-remove`.
4. Rust runs the four feedback cases.
5. Python runs the cases from `missed` to `extract-status`.
6. Rust runs the nine metrics and usage cases after `metrics`. The script also
   compares the key set and the value types of `GET /metrics` with the golden
   case `metrics`.
7. Rust runs a search, an add, an add-batch, a delete and a delete-batch with
   usage headers. Then Python reads the rows with `GET /usage`.

Result on 2026-11-16: 0 cases differ in stints 2, 4 and 6. The `GET /metrics`
shape is the same. Python reads the rows of the Rust calls.

`tests/usage_db.rs` gives a second check in `cargo test`.
`tests/fixtures/usage/make_fixture.py` writes `usage.db` with the Python
tracker and records the Python reads in `expected.json`.

- The Rust reads of the Python `usage.db` give `expected.json`.
- The Rust writes of the same rows give the same reads.

## Differences from Python

- SQL ties: some Python queries have an `ORDER BY` that does not give one
  order for equal values. The order then comes from the SQLite query plan. The
  Rust queries add a second sort key that gives the order of SQLite 3.45 (the
  Python version). The key is `id` for failures and feedback history, `query`
  for problem queries and `memory_id` for stale memories.
- `GET /metrics` has no `promotion` part. Task P5-6 adds it.
- `embedder_reload` in `GET /metrics` always shows `enabled: false` and zero
  counters. The Rust embedder has no reload. Task P5-3 decides on the reload
  routes.
- The task row names `period=daily` for `GET /usage`. Python v5.19.0 refuses
  `daily` with a 422 reply (pattern `^(today|7d|30d|all)$`). The Rust server
  gives the same 422 reply.
- A full channel drops a row. Python writes each row in the request and does
  not drop rows.
