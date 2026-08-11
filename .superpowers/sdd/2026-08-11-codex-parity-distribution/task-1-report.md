# Task 1 report: Codex hook runtime reliability parity

## Commits

- Base (merge-base with `origin/develop`): `f50b05d2c461b244be10c68c13f4980eb9e4ab57`.
- Branch head before Task 1: `8f1b2de` (`docs: plan Codex parity implementation`).
- Resulting task commit: this report is included in the commit carrying
  `fix(codex): match hook runtime reliability guarantees` (inspect `git log -1`
  for its hash).

## Files changed

- `mcp-server/assets/codex/hooks/_lib.sh`
- `mcp-server/assets/codex/hooks/memory-recall.sh`
- `mcp-server/assets/codex/hooks/memory-query.sh`
- `tests/test_claude_memory_hooks.py`
- `.superpowers/sdd/2026-08-11-codex-parity-distribution/task-1-report.md`

## TDD evidence

Added Codex parity cases before production edits. RED command:

```text
uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (unconfigured or enabled_false or backends_file or routed or breaker or budget or 401 or timeout)'
```

RED result: `7 failed, 2 passed, 69 deselected`; failures covered activation
gating, routed health, per-backend breaker isolation, tiny-budget output, 401
diagnostics, and the missing fair-timeout decision helper.

## GREEN verification

- The same parity selector: `9 passed, 69 deselected`.
- `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex or memory_hooks'`:
  `78 passed`.
- `uv run pytest -q tests/test_claude_memory_hooks.py tests/test_codex_notify_hook.py`:
  `84 passed`.
- `bash -n mcp-server/assets/codex/hooks/_lib.sh
  mcp-server/assets/codex/hooks/memory-recall.sh
  mcp-server/assets/codex/hooks/memory-query.sh` and `git diff --check`: passed.

## Notes and risks

- Codex-specific source-prefix ordering, request `source` payload, usage
  headers, session metadata, and hook JSON output were retained.
- Activation resolves explicit/project/global backend files before stdin is
  available. A file that exists only at a payload cwd different from both
  `$PWD` and `CODEX_PROJECT_DIR` still requires `MEMORIES_ENABLED=true` to
  force activation; once active, the loader resolves that cwd file.

## Follow-up diagnostics correction

- Follow-up base: `7f666db303643b4dcc5c8f9ba60b590f4eeffd4a`.
- RED command: `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (identity or extraction or collision)'`.
- RED result: `3 failed, 78 deselected` (missing multi-backend auth identity,
  extraction overclaim, and colliding fan-out temp paths).
- GREEN selector: `3 passed, 78 deselected`.
- GREEN broader suite: `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex or memory_hooks'` -> `81 passed`.
- GREEN full suite: `uv run pytest -q tests/test_claude_memory_hooks.py tests/test_codex_notify_hook.py` -> `87 passed`.
- `bash -n` on all three Codex hook scripts and `git diff --check`: passed.
- Resulting follow-up commit: this report is included in the commit carrying
  `fix(codex): correct multi-backend hook diagnostics` (inspect `git log -1`
  for its hash).

## Follow-up breaker and auth guidance correction

- Follow-up base: `d67129928b72b45601797ee2db4645b61308e0c9`.
- RED command: `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex and (named_backend or punctuation or multi_backend_401)'`.
- RED result: `3 failed, 80 deselected` (punctuation-heavy breaker names
  collided, named 401 guidance prescribed `MEMORIES_API_KEY`, and mixed
  backend recall retained that default-only wording).
- GREEN selector: `3 passed, 80 deselected`.
- GREEN broader suite: `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex or memory_hooks'` -> `83 passed in 57.00s`.
- GREEN full suite: `uv run pytest -q tests/test_claude_memory_hooks.py tests/test_codex_notify_hook.py` -> `89 passed in 57.78s`.
- `bash -n` on all three Codex hook scripts and `git diff --check`: passed.
- Resulting follow-up commit: this report is included in the commit carrying
  `fix(codex): isolate backend breaker and auth guidance` (inspect `git log -1`
  for its hash).
