# Rust server: durable extract queue and job API

Date: 2026-11-16
Status: adopted (task P4-1 of the Rust rewrite plan)

## Routes

| Route | Behavior |
|---|---|
| `POST /memory/extract` | Checks the body as the Python `ExtractRequest`, then the access rules, then writes the job. Returns 202 with `job_id` (32 hexadecimal characters), `status`, `queue_depth` and `result_url`. |
| `GET /memory/extract/{job_id}` | Returns the job body with the Python fields. 404 for an unknown job. 403 by the rules of `_can_access_extract_job`. |
| `GET /extract/status` | The queue numbers, and the provider name, model and health. The server calls the provider health check at most one time in 60 s. |

The body of `GET /health` for a caller with the `API_KEY` value (or when
`API_KEY` is empty) adds `extract_queue_depth` and
`extract_oldest_job_age_sec`.

`POST /memory/extract` writes the audit row `extract` with the source, as the
Python `_audit` call does. Task P5-1 adds the usage event.

## Queue

The queue is the table `extract_jobs` in `state.db` (WAL, `synchronous=FULL`).
A job that gets 202 is on disk before the reply.

| Column | Content |
|---|---|
| `seq` | The order of the jobs |
| `job_id` | The ID |
| `status` | `queued`, `running`, `completed` or `failed` |
| `attempts` | The tries that started |
| `request` | The pipeline input as JSON: `messages`, `source`, `context`, `allowed_prefixes`, `debug`, `dry_run`, `document_at`, `trusted_authorship`, `promotion_context` |
| `job` | The body of `GET /memory/extract/{job_id}` as JSON |
| `enqueued_at`, `finished_at` | Unix seconds, for the job age and the trim |
| `not_before` | Unix seconds. No worker takes the job before this time (the delay after a failed try). |

Rules:

- The limit counts the `queued` jobs only, as the Python `qsize()`. At the
  limit, the reply is 429 with the Python detail and `Retry-After`.
- A job gets 3 tries. A try fails when the pipeline returns an error, panics,
  or the server stops during the try. A failed try puts the job back in its
  old place, with a delay of 10 s times the tries so far. After the third
  failed try, the job is `failed` with the error.
- The queue gives each job at least one run, and can give more than one. The
  writes of the pipeline (task P4-3) must give the same result when they run
  again. Python ran each job once, or lost it at a restart.
- Between tries, the job body shows `status: queued` with the `started_at`
  and `queue_depth` of the last try. The error of a try before the last one
  is only in the log.
- At start, each `running` job goes back to `queued`. If it already had 3
  tries, it is `failed` with the error "the server stopped during the last
  try". Thus a job that stops the server each time cannot stop the server
  forever.
- A server takes an exclusive lock on `DATA_DIR/memories-server.lock` at
  start. A second server on the same `DATA_DIR` does not start, so it cannot
  put the running jobs of the first server back in the queue.
- If the result write fails, the worker tries it 5 times. Then the job stays
  `running` until the next start.
- A finished job stays `EXTRACT_JOB_RETENTION_SEC` (300 s). Above
  `EXTRACT_JOBS_MAX` (200) finished jobs, the oldest finished jobs go first.
  Queued and running jobs never go. Python counted all jobs against the cap,
  but its queue held only 40 jobs. With 10,000 queued jobs, that rule would
  delete each result at once.
- A provider failure inside the pipeline is a `completed` job with an error
  result, as in Python (rule W7). It does not use a try.

## Settings

| Variable | Default | Note |
|---|---|---|
| `EXTRACT_MAX_INFLIGHT` | 2 | The worker count |
| `EXTRACT_QUEUE_MAX` | 10,000 | Python: 20 for each worker (40). The queue is on disk now. |
| `EXTRACT_JOB_RETENTION_SEC` | 300 | Minimum 60 |
| `EXTRACT_JOBS_MAX` | 200 | Minimum 10 |
| `MAX_EXTRACT_MESSAGE_CHARS` | 120,000 | The `messages` limit |

A provider that cannot start (for example, a missing key) logs a warning and
turns extraction off, as in Python.

## Until task P4-3

The `Pipeline` trait is the interface for P4-3. The binary uses
`ProviderOnly` until then:

- A provider that does not answer gives the Python result of a provider
  failure (`provider_runtime_failure`, stage `extract_facts`).
- A provider that answers fails the try, because the extraction is not
  ported yet.
- `EXTRACT_FALLBACK_ADD` with no provider still gives 501. P4-3 ports the
  fallback.

Notes for P4-3:

- Python resolves the extraction profile at the request. This route stores
  only `dry_run`. If P4-3 resolves the profile in the worker, a profile edit
  applies to the jobs that are already queued.
- `promotion_context` in the stored request is the declaration of the
  client, checked only for its shape. Python runs `build_promotion_context`
  (and `observe_declaration`) at the request. The pipeline must apply the
  same gates from `source`, `allowed_prefixes` and `trusted_authorship`.

## Checks

- `server/scripts/parity_extract.sh`: the golden cases `extract-status`,
  `extract-202`, `extract-job`, `extract-job-404` and `extract-ro-403`
  (CI job `rust-parity`).
- `tests/forced_stop.rs`: SIGKILL with 101 jobs (1 running, 100 queued), then
  a restart. Each job completes.
- `tests/extract.rs`: the 422 bodies of the Python model
  (`record_extract_validation.py`), the access rules, 429, 3 tries, the status
  and health bodies, and an enqueue p95 of 50 ms or less.
