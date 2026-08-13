# Task 11 report: portable jq syntax for Codex query output

## Root-cause investigation

- Current base: `0c9494e51da309bc931c80ae62ce28ea5f062f2e`.
- `jq --version` on the host is `jq-1.8.1`; both shipped query hooks are byte-identical and contain the same object-field expression at line 410.
- Ubuntu 24.04's package candidate is jq 1.7.1 (the container reports `jq-1.7`).
- The failing expression is the unparenthesized binary `+` after an `additionalContext:` value made from two `if ... end` expressions. jq 1.7 rejects it while jq 1.8 accepts it; the hook exits before emitting JSON.

## RED

Command (Ubuntu 24.04 container, jq 1.7):

```text
docker run --rm -v "$PWD":/repo -w /repo ubuntu:24.04 bash -lc 'set -e; apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends jq python3-pytest; jq --version; python3 -m pytest -q tests/test_claude_memory_hooks.py -k "codex_memory_query_minimal_reminder_omits_toolsearch or codex_memory_query_named_backend_401_guidance_uses_backend_config or codex_memory_query_search_reachability_is_independent_of_extract_routing or codex_memory_query_configured_default_401_guidance_uses_custom_env"'
jq-1.7
4 failed, 102 deselected in 0.88s
```

Failures: minimal reminder, named backend 401, search reachability, and configured-default 401. Each reports `jq: error: syntax error, unexpected '+', expecting '}'` at the `additionalContext` expression.

## GREEN and verification

- Host focused Codex query tests (jq 1.8.1): `uv run pytest -q tests/test_claude_memory_hooks.py -k 'codex_memory_query'` — 9 passed, 97 deselected.
- Ubuntu 24.04 container focused CI failures (jq 1.7): same four-test `-k` expression as RED — 4 passed, 102 deselected.
- Shell/render/synchronization checks: `bash -n mcp-server/assets/codex/hooks/memory-query.sh integrations/codex/hooks/memory-query.sh`, `cmp -s ...`, `uv run python scripts/render_project_hooks.py --check`, and `git diff --check` — all passed. `integrations/codex/hooks` is a tracked symlink to `mcp-server/assets/codex/hooks`, so the two shipped paths share one source and remain byte-identical.
- Full Python suite: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q` — 1807 passed, 1 warning in 158.35s. The warning is the existing local-Qdrant payload-index warning and is unrelated.

## Residual risk

The fix only adds grouping parentheses around the minimal-reminder `additionalContext` concatenation. It does not alter any emitted wording or branch behavior. jq 1.7 and jq 1.8 both pass the affected cases; no known residual risk remains for this contract.

Commit message: `fix(codex): keep query output portable across jq versions`.

The final commit hash is intentionally recorded by branch history rather than embedded here, because this report is part of that commit and amending it changes the hash.
