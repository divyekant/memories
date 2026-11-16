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

- Keys that end in `_at`, `_ms`, `_sec`, `_path` and similar suffixes, and
  keys such as `job_id`, `key_prefix` and `timestamp`. See `VOLATILE_KEY` in
  `kit.py`.
- The `id` of each item in `events`, because the event ID comes from the
  clock.
- Timestamps, UUIDs and 32-character hex values inside strings and keys.
- The keys in the `volatile` field of a case. The backup cases use this
  field, because automatic backups are named by the second and two backups in
  one second have the same name.

Numbers compare with a tolerance of 0.0001. An integer and a float with the
same value are equal.

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
- For an event stream, the middleware records only the first chunk.
- A request or reply body is recorded up to 4 MB.

To replay a capture file from the middleware against a server that holds
records, add `--allow-nonempty` to `replay`.

## Known limits

- The golden captures come from the embedded Qdrant mode (`QDRANT_URL`
  empty). In that mode, a snapshot has a `local-backup-` name, and a snapshot
  restore makes the local store read-only. The snapshot cases are thus the
  last cases. Production uses a Qdrant server.
- The scenario does not test the recency weight of search, because the score
  depends on the time between the add and the search.
- The text of some Python error messages depends on the order of a Python
  set. `run_python_server.sh` sets `PYTHONHASHSEED=0` to fix the order.
