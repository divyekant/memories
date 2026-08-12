# Task 3 — Trusted authorship integration report

## Status

Complete. Managed-key requests now construct request-bound `TrustedAuthorship`
from `AuthContext.principal_id` and normalized `X-Memories-Client`; the value
reaches every creation/replacement boundary while env/unconfigured admin writes
remain legacy-compatible and exact project writes fail closed.

## Commits

- `b4e1288` — `feat: thread trusted authorship through memory writes`
- The report is added as a follow-up documentation commit.

## Files changed

- `app.py`: request helper, optional-authorship propagation for add/batch,
  doctrine, upsert, supersede, merge, import, missed capture, patch/source
  moves, extraction fallback/queue/commit, and stable project-policy 422/job
  results. Legacy call shapes omit the optional keyword when no managed
  principal is available.
- `memory_engine.py`: shared project write gate using
  `transcript_hygiene.redact_secrets`, preflight for replacement/import/batch
  paths, reserved-field filtering for updates, and trusted authorship through
  upsert/import helpers.
- `llm_extract.py`: trusted value through `execute_actions`/`run_extraction`
  ADD, UPDATE, and CONFLICT paths; policy errors propagate to synchronous
  callers and queued workers record a policy result.
- `consolidator.py`: system authorship with normalized contributors and source
  memory IDs.
- Listed Task 3 tests: endpoint identity/anti-spoofing/policy responses,
  queued identity preservation, real-engine metadata/source/upsert/secret
  behavior, extraction, import, merge, supersede, missed capture, and
  consolidation coverage.

## Red evidence

Before production wiring, the new focused tests were intentionally run red:
12 failures showed missing `trusted_authorship` propagation at endpoint and
engine calls, `execute_actions` rejecting the new argument, and consolidation
not supplying system authorship. No unrelated baseline failures were used as
the implementation signal.

## Final verification

- Required focused command:
  `uv run pytest -q tests/test_project_memory.py tests/test_memory_api.py tests/test_extract_api.py tests/test_llm_extract.py tests/test_supersede_endpoint.py tests/test_export_import_api.py tests/test_merge_api.py tests/test_missed_memory.py tests/test_consolidator.py tests/test_secret_redaction.py`
  → **216 passed, 1 warning in 7.27s** (the warning is the existing local
  Qdrant payload-index warning).
- Broader regression/auth/extraction-fake command (254 tests) → **254 passed,
  1 warning in 29.25s**.
- Full repository command `uv run pytest -q` → **1822 passed, 1 warning in
  135.86s**.
- `uv run python -m py_compile` over all changed production/test modules →
  passed.
- `git diff --check` → passed.

## Creation/replacement call-site audit

- `app.py`: fallback `add_memories`; `/memory/add` doctrine/add; batch add;
  both supersede endpoints; patch/archive/source-move updates; both upserts;
  import; extraction worker/fallback/commit; merge; missed capture.
- `memory_engine.py`: `add_memories` boundary; `supersede`; `add_with_doctrine`;
  `merge_memories`; `update_memory`; both upserts; import dispatch/add/smart
  helpers.
- `llm_extract.py`: `execute_actions` ADD/UPDATE/CONFLICT and archive update;
  both `run_extraction` execution branches.
- `consolidator.py`: consolidation output add boundary with system trust.
- No other production `add_memories`, replacement, import, or extraction
  call-sites were found by the final `rg` audit.

## Self-review

- Reserved trusted metadata (`author`, `contributors`, `origin_client`,
  `source_memory_ids`) is filtered on every add and metadata update; ordinary
  patches preserve the existing author, while upsert replacement applies the
  current trusted author.
- Exact project writes validate authorship and credential-shaped text before
  embedding/storage. Import and batch upsert preflight all records before
  mutation, and source transitions into exact project namespaces require and
  stamp trusted authorship.
- Managed principal trust is applied on legacy/person sources too, without
  changing ACL checks. Env/unconfigured admin contexts pass no trust, keeping
  legacy behavior while exact project writes fail closed.
- Optional keyword plumbing is omitted when trust is absent, preserving fake
  engine/runner signatures and the single shared engine boundary.
- Queued extraction stores the request-bound trust object; the worker passes it
  through rather than re-deriving identity.

## Concerns

- One pre-existing warning remains from local Qdrant payload-index setup; it is
  unrelated to this change.
- Async extraction policy failures intentionally return an accepted job whose
  terminal result records `error: project_policy`; synchronous policy failures
  return stable HTTP 422 responses.
