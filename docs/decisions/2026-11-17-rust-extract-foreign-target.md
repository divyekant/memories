# Rust extract: a target of another source adds the fact (2026-11-17)

## Problem

In an extract job, the AUDN step (add, update, delete, noop) shows the model
similar memories from all sources. The model can then decide to update a
memory of another source, or to mark a conflict with one. Python v5.19.0 and
Rust 6.0.0 refuse the write ("old_id not authorized"), and the fact is lost.
On production, this happened 45 times from 2026-11-17 00:31Z to 04:05Z.

## Decision

dk approved the fix on 2026-11-17. In the job pipeline, an UPDATE or CONFLICT
whose target has another source (or a source outside the key prefixes) runs
as FALLBACK_ADD:

- The new memory has the job source and the fact text. It never uses
  `new_text`, because `new_text` can hold words of the other source.
- The novelty gate applies, as for each add.
- The result action is `fallback_add` with `foreign_old_id`. It counts in
  `stored_count`, not in `fallback_count`. `fallback_count` stays the count
  of AUDN failures, which the usage tables report as a quality signal.
- The policy pre-check of the job reads the fact text for such a decision,
  not `new_text`. A credential of the other source in `new_text` does not
  stop the job.
- The other memory does not change.

Not changed:

- `POST /memory/extract/commit`. The caller approved each action, so it keeps
  the Python error.
- DELETE of a memory of another source. It keeps the error, because there is
  no fact to keep.

`EXTRACT_FOREIGN_TARGET_ADD=false` gives the Python behavior. The parity
check `server/scripts/parity_pipeline.sh` sets it, so that it compares with
the Python goldens.

## Test

`server/crates/memories-server/tests/extract_foreign.rs` (Qdrant and the
embedder; a CI step runs it).
