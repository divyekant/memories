# Parity kit

The parity kit compares the HTTP replies of two Memories servers. The Python
server is the reference. The Rust server of the rewrite must give the same
replies.

The kit has three commands:

| Command | What it does |
|---|---|
| `capture` | Sends the scenario to a server and records each request and reply. |
| `replay` | Sends the requests of a capture file to a second server and records the replies. |
| `diff` | Compares two capture files. Exits with code 1 if a reply is different. |

## Files

| Path | Content |
|---|---|
| `parity/scenario.py` | The cases: the wire rules, the seed corpus, then each route. |
| `parity/seed.py` | The seed corpus generator. It uses a fixed random seed. |
| `parity/kit.py` | The runner, the normalizer and the diff. |
| `parity/golden/golden.ndjson` | The golden captures from the Python server (v5.19.0). |
| `parity/workspace/` | The files that `POST /index/build` reads (`WORKSPACE_DIR`). |
| `parity/run_python_server.sh` | Starts the Python server with the parity settings. |
| `parity/extract_golden.py` | Writes or checks `golden/extract/text.ndjson`: the text cases of the extraction pipeline. |
| `parity/extract_pipeline.py` | The live extraction check with recorded LLM replies (`golden/extract/pipeline.ndjson`). Run it with `server/scripts/parity_pipeline.sh`. |
| `parity/extract_corpus.py` | Gate G4 part a: the eval transcripts through the extraction pipeline, with LLM replies that are a function of the prompt (`golden/extract/corpus.ndjson`). Run it with `server/scripts/parity_pipeline.sh <target> --check extract_corpus`. |
| `parity/intended.json` | The intended differences: Python v5.19.0 defects that the Rust server fixes. Each entry names a case, the Python status and the Rust reply. |
| `parity/clients.py` | Gate G6: runs the real clients and the probes against two servers and compares them. Run it with `server/scripts/gate_g6.sh`. |
| `parity/sort_tests.py` | Gate G6: sorts the Python tests (`docs/rust-rewrite/gates/G6-tests.tsv`) and finds the check of each HTTP test. |
| `parity/g6_checks.json` | Gate G6: the check of each HTTP test status that no golden case or probe has. |
| `parity/extract_live.py` | Gate G4 part b: 24 dry-run probes with a live model on the two servers. Run it with `server/scripts/gate_g4_live.sh <rust binary>`. It needs a provider key. |
| `parity_capture.py` | The capture middleware of the Python server. |

## Scenario

The scenario has 225 cases in a fixed order. Each case runs on the state that
the cases before it made.

1. Cases for the wire rules of the clients (rules W1 to W9 in `scenario.py`).
2. The seed corpus: 240 synthetic records in 5 source prefixes, 30 links,
   8 pinned records, 8 archived records and 6 conflict markers.
3. Cases for the API keys, the reads, the writes and the metrics.
4. Destructive cases: extraction, backup, restore, deletes, the index build
   and the snapshots.

The seed goes into the server through the HTTP API. Thus a replay against a
different server makes the same corpus.

A test in `tests/test_parity_kit.py` makes sure that the scenario has a case
for each of the 93 routes of `app.py` and for each wire rule.

## Record the golden captures again

Do this procedure after each change to the Python server or to the scenario.

CAUTION: The scenario deletes records. Use only a new, empty server. The kit
refuses a server that holds records.

1. Start two new Python servers:

   ```bash
   parity/run_python_server.sh /tmp/parity-a 18001 &
   parity/run_python_server.sh /tmp/parity-b 18002 &
   ```

2. Record the golden captures from the first server:

   ```bash
   .venv/bin/python -m parity capture --base-url http://127.0.0.1:18001
   ```

3. Replay them against the second server:

   ```bash
   .venv/bin/python -m parity replay --base-url http://127.0.0.1:18002 --out /tmp/replay.ndjson
   ```

4. Make sure that the diff finds zero differences:

   ```bash
   .venv/bin/python -m parity diff parity/golden/golden.ndjson /tmp/replay.ndjson
   ```

5. Run the self-test: `.venv/bin/python -m pytest tests/test_parity_kit.py`.

## Settings of the server under test

The replay needs the same settings as `run_python_server.sh`:

- `API_KEY` is the admin key. The kit sends `parity-admin-key`, or the value
  of `--admin-key`.
- `USAGE_TRACKING=true` and `AUDIT_LOG=true`.
- `EXTRACT_PROVIDER=ollama` with `OLLAMA_URL` on a closed port. Each extract
  job then ends with a provider failure (rule W7).
- `WORKSPACE_DIR` points to `parity/workspace/`.
- `MEMORIES_PERSIST_DELAY_SEC=0`.

## Normalizer

The normalizer masks the values that change on each run. The diff keeps the
key and the type of each masked value.

- Each key that ends in `_at`, and the keys in `VOLATILE_KEYS` in `kit.py`.
  Each key in that set is there for a reason: a time, a random ID, a path in
  the data directory, a latency, or a memory size of the Python process.
  A configuration value is never masked.
- The `id` of each item in `events`, because the event ID comes from the
  clock.
- Timestamps, UUIDs and 32-character hex values inside strings and keys.
- The keys in the `volatile` field of a case:
  - The backup cases mask the backup count, because automatic backups are
    named by the second and two backups in one second have the same name.
  - The health cases mask `version`, because the Rust server has a new version.
  - The extract job case masks `error_message`, because the error number of
    a refused connection is different on Linux and macOS.

Two floats compare with a tolerance of 0.0001. An integer and a float are
different, because a client can see the difference.

If a case fails with an error (for example, the server closes the
connection), the kit records status 0 with the error and continues. The diff
then shows the case as a difference.

A case with `delay_s` waits that number of seconds before the request. The
snapshot case uses this field, because snapshot names have whole seconds and
`/index/build` makes a snapshot just before.

A case with `max_ms` fails if the reply of the server under test takes more
time. The extract case uses this check for rule W5 (202 in less than 2 s).

## Capture middleware

If `PARITY_CAPTURE_DIR` is set, the Python server writes each request and its
reply to `<dir>/capture-<UTC date>.ndjson`. The default is off.

WARNING: A capture file holds private memory text. Keep the file on the host
that made it. Do not put it in the repository.

- The file has mode 0600.
- The middleware writes `<redacted>` in place of the `X-API-Key` and
  `Authorization` values. A replay sends the admin key in place of
  `<redacted>`.
- The middleware also redacts the new raw key in the reply of `/api/keys`.
- A request never waits for the file. A background thread writes the
  records. If the queue (1000 records) is full, the record is lost.
- `PARITY_CAPTURE_MAX_MB` sets the size limit of each daily file. The
  default is 1024. After the limit, the records of that day are lost.
- The middleware does not record `/ui` paths.
- For an event stream, the middleware records only the first chunk with data.
- A request or reply body is recorded up to 4 MB. A record with a cut body
  has a `truncated` field.

To replay a capture file from the middleware against a server that holds
records, add `--allow-nonempty` to `replay`.

## Rank golden cases

The rank core of the Rust server (task P2-1) has its own golden cases in
`parity/golden/rank/`. `parity/rank_golden.py` calls the Python rank
functions with fixed inputs and writes one JSON file for each rule:

| File | Rule |
|---|---|
| `hybrid.json` | `hybrid_search`: the fusion, the weights, the threshold, the temporal filter and the graph step |
| `intent.json` | `classify_query` and `detect_temporal_intent` |
| `isoformat.json` | `datetime.fromisoformat` of Python 3.11 |
| `recency.json`, `confidence.json` | The recency score and the confidence |
| `temporal.json` | The temporal filter |
| `relative.json` | `relative_score` |
| `round.json` | Python `round()` to 4 and 6 decimals |
| `tokens.json` | The keyword tokens of a query and of a stored text |
| `pyset.json` | The iteration order of a Python set of ints |

The Rust test `server/crates/memories-engine/tests/rank_golden.rs` reads
these files. CI runs the script with `--check`, so a change to a Python rank
function fails CI until the files are written again.

To write the files again, run this command with Python 3.11:

```bash
.venv/bin/python -m parity.rank_golden
```

## Known limits

- The golden captures come from the embedded Qdrant mode (`QDRANT_URL`
  empty). In that mode, a snapshot has a `local-backup-` name, and a snapshot
  restore makes the local store read-only. The snapshot cases are thus the
  last cases. Production uses a Qdrant server.
- The scenario does not test the recency weight of search, because the score
  depends on the time between the add and the search.
- A replay of a middleware capture sends the admin key for each request,
  because the capture keeps no key. A request that a scoped key made can
  thus get a different reply.
- The text of some Python error messages depends on the order of a Python
  set. `run_python_server.sh` sets `PYTHONHASHSEED=0` to fix the order.

## Intended differences

`diff --intended` reads `parity/intended.json`. Use it only for a diff of a
Python capture (left) with a Rust capture (right). For each case in the file, the diff
compares the reply of the server under test with the `rust` reply of the
entry, not with the Python reply. If the Python reply no longer has the
status of the entry (or the `body`, if the entry gives one), the diff reports
the entry as stale.

To add an intended difference, do these steps:

1. Add a case for the defect to a live scenario (for example
   `parity/bulk_scenario.py`). Do not add it to the golden scenario.
2. Add an entry with `case`, `route`, `python`, `rust` and `reason`.
3. Write the fix in the decision record of the route.
