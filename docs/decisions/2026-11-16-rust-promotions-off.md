# Rust server: project promotion in the off mode

Date: 2026-11-16
Status: adopted (task P5-6 of the Rust rewrite plan)

## Decision

The Rust server has only the `off` mode of project promotion. The production
baseline (task P0-3, `docs/rust-rewrite/baseline.md`) shows that production
runs in the `off` mode and that no record has a `promotion` field. The full
port (the reviewer, the reconcile job at 03:45 UTC, the `shadow` and `auto`
modes) is a task after the cutover, if a host needs it.

## Scope

| Route | Reply |
|---|---|
| `GET /promotions` | The query checks of Python, then an empty list with `limit` and `offset` |
| `GET /promotions/{candidate_id}` | 404 `Promotion candidate not found` (422 for an ID that is not an integer) |
| `POST /promotions/{candidate_id}/approve` | The body checks of `PromotionApproveRequest`, 409 `reason is required` for a blank reason, else 404 |
| `POST /promotions/{candidate_id}/reject` | The same, with `PromotionRejectRequest` |
| `GET /metrics`, part `promotion` | The counters of `PromotionService.metrics_snapshot` with no candidate |

## Rules that the routes keep

- The query checks run in the Python order: `project_id`, `owner`, `since`,
  `until`, `limit`, `offset`. Then an unknown `state` gives 422 with the
  Python `ValueError` text (`'x' is not a valid PromotionStatus`).
- `since` and `until` use `speedate`, the datetime parser of pydantic-core. A
  value that is not a datetime and not a date gives the pydantic error
  `datetime_from_date_parsing` with the same text.
- A blank reason is the Python `str.strip()` rule (U+001C to U+001F count as
  space). The reason check runs before the candidate lookup, as in Python.
- No route writes an audit row or a usage row: Python audits only a decision
  that succeeds, and the off mode has no such decision.
- In the `promotion` part of `GET /metrics`, `project_count_by_kind` counts the
  records with a `project/<id>/<kind>` source that are not archived.
  `exact_reuse_count` counts those with more than one `source_memory_ids`
  item. `promotion_error_count` counts the `promotion.failed` audit rows. The
  other counters are zero, and the alerts are false.
- If `PROJECT_PROMOTION_MODE` is `shadow` or `auto`, the server does not start
  and logs `PROJECT_PROMOTION_MODE=<value> is not supported`.

## Differences from Python

- Python reads a record with a `promotion` field as a candidate. The Rust
  server does not. Production has no such record.
- Python with `PROJECT_PROMOTION_MODE=shadow` and no
  `PROJECT_PROMOTION_RELEVANCE_THRESHOLD` logs a warning and runs in the `off`
  mode. The Rust server refuses each `shadow` or `auto` value.
- Python gives the `extra_forbidden` items of a body in body order. The Rust
  server gives them in name order, because `serde_json` keeps no key order
  (the same as the other Rust routes).
- `speedate` 0.18.0 is newer than the copy in pydantic-core 2.23.2. The
  server truncates digits after the microseconds and refuses an exponent
  (`1.5e9`), as pydantic-core 2.23.2 does. One known text difference stays:
  a Unix time before the year 1600 gives `dates before 0000` in place of
  `dates before 1600` in the 422 text.
- An `offset` above 2^63-1 comes back as 9223372036854775807. Python gives
  the exact value.
- The `state` value in the 422 text escapes control characters only. Python
  `repr` also escapes other characters that it does not print (for example
  U+200B and U+0085).
- A JSON `null` body gives `model_attributes_type`. Python gives `missing`.
  The shared body code of all Rust routes has this difference.

## Exit check

`server/scripts/parity_promotions.sh <rust binary>`:

1. The Python server gets project records through a managed key with a
   principal, and one archived project record. Then it gets 36 promotion
   requests (`server/scripts/parity_promotions_ab.py`). The script also
   adds one `promotion.failed` audit row.
2. `memories-server migrate` copies `metadata.json` into the Rust collection.
3. The Rust server replays the four golden promotion cases (0 differ) and
   gets the same 36 requests (0 differ, `promotion` part of `GET /metrics`
   included).
4. The Rust server with `PROJECT_PROMOTION_MODE=shadow` does not start.
