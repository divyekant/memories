# Task P5-3: maintenance jobs and the scheduler

This file gives the step plan of task P5-3 and the decisions of the task. The
reference is the Python server v5.19.0 (`app.py`, `consolidator.py`,
`memory_engine.py`).

## Step plan (test first)

Each step writes the test first. Then the step writes the code that makes the
test pass.

1. Pure rules in the engine (unit tests): the prune candidates, the policy
   resolution of `extraction_profiles.json`, the promotion protection, the
   JSON array parse of an LLM reply, and the due time of each scheduled job.
2. Writer jobs against Qdrant and the embedder (`tests/maintenance.rs`):
   `enforce_policies`, `similar_clusters`, `consolidation_clusters`,
   `replace_consolidation_cluster` and `reembed`.
3. Routes (server tests with no store): request validation, the admin check,
   the 503 reply with no provider, and the two routes that do no work.
4. The scheduler (unit tests with a fixed clock): a job runs at its time, a
   missed job runs after the start, and a new install does not run a job at
   the first start.
5. Exit check (`server/scripts/parity_maintenance.sh`): the Python server and
   the Rust server run the same maintenance scenario with the same LLM replies.
   The diff compares each reply and the list of records after each job. A
   search load runs during the jobs on the Rust server, and its p95 must be
   500 ms or less.

## Routes

| Route | Rust server |
|---|---|
| `POST /maintenance/consolidate` | Same as Python. |
| `POST /maintenance/prune` | Same as Python. |
| `POST /maintenance/enforce-policies` | Same as Python. |
| `POST /maintenance/compact` | Same as Python. |
| `POST /maintenance/reembed` | Same as Python for the current model. A different model gets HTTP 400. |
| `POST /maintenance/embedder/reload` | HTTP 200 with the Python body. No work. |
| `POST /index/build` | HTTP 200 with the Python body and zero counts. No work. |

## Scheduler

The scheduler starts if `MAINTENANCE_ENABLED` is true and an extract provider
is set, as in Python. The times are in UTC:

| Job | Time | Setting |
|---|---|---|
| Consolidation | Each day at 03:00 | |
| Conflict queue | Each day at 03:30 | `MAINTENANCE_CONFLICT_DRAIN` (true), `MAINTENANCE_CONFLICT_MAX` (200) |
| Prune | Each Sunday at 04:00 | |

The scheduler keeps the time of the last run of each job in `state.db`. If
the server did not run at a scheduled time, the job runs after the next start.
At the first start of an install, the scheduler records the start time and runs
no job. The promotion repair at 03:45 is part of task P5-6.

One job runs at a time. A route and a scheduled job wait for the job that runs.

## Decisions

- `enforce-policies` reads `extraction_profiles.json` in `DATA_DIR` at each
  call, as the Python server does at its start. The profile routes are not
  ported yet.
- The promotion protection checks the shape of the `promotion` envelope and
  its status. It does not parse the proposal and the review. A record with a
  bad proposal is thus protected, where Python does not protect it. The
  difference keeps more records, never fewer.
- `reembed` computes the dense vector of each record again, one page of 100
  records at a time, and keeps the keyword vector. Before the job, it makes a
  Qdrant snapshot (Python writes a file backup).
- With more than 500 candidates, consolidation takes a random sample of 500,
  as Python does. The sample is not the same as in Python.
