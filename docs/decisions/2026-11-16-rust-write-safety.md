# Rust server: write safety

Date: 2026-11-16
Status: adopted (task P3-3 of the Rust rewrite plan)

## Scope

- The order of writes to the same key (the entity locks of
  `entity_locks.py`).
- A repair step at start for a write of two steps that stopped after its
  first step.
- The crash test, the add benchmark and the search test under a constant
  write load. The results are in `docs/rust-rewrite/write-safety.md`.
- `last_updated` of `/stats` (a gap of task P2-3).

The code is `memories_engine::writes` (the write lock),
`memories_engine::writes::journal` (the journal and the repair) and the table
`pending_ops` of `state.db`.

## The write lock

The Rust server keeps the one write lock of task P3-1 (`Writer::lock`, a
`tokio::sync::Mutex`). It does not add a lock for each key.

| Item | Python v5.19.0 | Rust server |
|---|---|---|
| Lock for each source (`EntityLockManager`) | Held for the full write, also during the embedding. | None. |
| Global write lock (`_write_lock`) | Held for the change of `metadata.json` only. | Held for the full write: the check, the embedding, the Qdrant calls, the catalog and `state.db`. |
| Two writes to the same key | One after the other. | One after the other. |
| Two writes to different keys | The embeddings can run at the same time. | One after the other. |
| Reinforcement flush | Under `_write_lock`. | Under the write lock. |

Reasons:

1. The one lock gives the guarantee of the task (two writes to the same key run
   one after the other) for each key: source, `entity_key` and record ID. A
   lock for each key must also find the keys of a record before the lock (for
   example, the old source of a supersede), and Python needs a retry loop for
   that.
2. A write reads a record and then writes its full payload. The reinforcement
   flush must not run between the two steps. The one lock gives this.
3. The benchmark shows that the lock does not make add slow: the add p95 is
   far below 250 ms at 300,000 records (see the report).

Upgrade, if a benchmark shows that writes wait for the lock: embed the texts
before the lock. The embedding is the longest step, and it does not read the
store.

### Rules for task P3-4 (keyword vector refresh)

- Take `Writer::lock` for each batch of the pass, and release it between two
  batches. Do not hold it for the full pass.
- Read the records of a batch and write their keyword vectors under the same
  lock hold. A write that changes a text between two batches writes its own
  keyword vector, so the pass must not write a vector from a text that it read
  before the lock.
- The pass changes only the keyword vector. It does not need a journal entry,
  because each batch leaves each record correct. Keep the progress of the pass
  in the `meta` table of `state.db`.

## Journal of writes of two steps

Each write of two steps puts an entry in the table `pending_ops` of
`state.db` before its first step. It removes the entry after its last step.
At start, `Writer::open` completes each entry that stays, then removes it. The
repair runs before the server accepts requests.

| Write | First step | Second step | Repair at start |
|---|---|---|---|
| Supersede | Store the new record with `supersedes` and its link. | Set `archived` and `superseded_by` on the old record. | If the new record is stored and the old record is stored, do the second step. Else nothing. If the old record has a `superseded_by` with a higher ID, a later supersede wins, and the repair does nothing. |
| Merge | Store the new record with a `supersedes` link to each record. | Set `archived` on the merged records. | If the new record is stored, do the second step on the merged records that are stored. Else nothing. |
| Delete of one record | Remove the links to the record from the records that link to it. | Delete the record. | Do the two steps again, unless the record has a pin now. |

Rules:

- The new record ID comes from the ID counter before the journal entry, so the
  entry names the new record.
- Each repair step is safe to do two times. A stop during the repair leaves the
  entry, and the next start does the repair again.
- If a step fails (for example, Qdrant is not available), the write completes
  or drops its entry at once with the repair rules, then replies with the
  error. Only if that also fails does the entry stay for the next start. Thus
  an entry does not stay for days while users change the records.
- A merge with a repeated ID has no second step (Python archives nothing), so
  it has no journal entry.
- The repair reads the catalog, which loads from Qdrant at start. Thus "the
  new record is stored" is the state of Qdrant, not of the old process.
- One entry costs two SQLite writes. A plain add, a batch add, an upsert and a
  PATCH have one Qdrant write for each record, so they have no entry.

### Batch add

A batch add stores its records in chunks of 100 points (task P3-1). Each point
write is complete: the payload and the two vectors are in one Qdrant call. A
stop between two chunks keeps the chunks before the stop. No record is in a
state between two steps, so a batch add has no journal entry.

This is a difference from Python v5.19.0: Python writes `metadata.json` once
at the end, so a stop keeps no record of the batch. A client that sends the
batch again after a stop can get a second copy of the records of the first
chunks. No client sends a batch again after an error today.

### Why a journal and not a scan at start

A scan at start could look for a record with `supersedes` and an old record
with no `superseded_by`. But a user can set `archived` to false on an old
record or a merged record, and a scan would archive it again at each start.
Data from the Python migration can also have such records. The journal
completes only the writes that the Rust server started.

## `last_updated`

The writer sets `last_updated` in the `meta` table of `state.db` where Python
v5.19.0 sets `config["last_updated"]`: after an add (also the add of a
supersede, a merge and an upsert that creates), after each delete, and after
each PATCH (also an upsert that updates). `/stats` reads the value (decision
of task P2-3).

## Known gap

`POST /memory/conflicts/resolve` writes each changed record with its own
Qdrant call (task P3-2). A stop between two calls can leave the loser archived
with no link from the winner, or the reverse. Python has the same order of
calls. The task row of P3-3 names add, batch add, supersede and merge, so this
change does not add a journal entry for it. The upgrade is one journal entry
for each resolved pair.

## Checks

| Check | Command |
|---|---|
| Journal in `state.db` | `cargo test -p memories-store` (`pending_ops_stay_until_their_end`) |
| Repair of each write, same-key writes, `last_updated` | `cargo test -p memories-engine --test write_safety -- --ignored` |
| 100 forced stops (exit check) | `cargo test -p memories-server --test write_crash -- --ignored --nocapture` |
| Add p95 at 100,000 and 300,000 records (exit check) | `cargo bench -p memories-engine --bench write_load` |

The crash test runs the real binary. Four clients send add, batch add (150
items), supersede and merge with no pause. The test stops the server with
SIGKILL at a random time from 200 to 1,500 ms, then starts it again. After
each start, it checks:

- `pending_ops` is empty.
- Each kind of write succeeded at least once, and at least one start completed
  a journal entry (else no stop fell between two steps, and the test fails).
- Each point has the dense vector and the keyword vector, and a `text`,
  `source`, `created_at` and `updated_at`.
- For each record with `supersedes` X: X has `superseded_by` equal to the
  newest record that supersedes X.
- For each `supersedes` link to a stored record: that record is archived.
