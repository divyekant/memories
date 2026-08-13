# Task 10 report: Codex non-persistent API keys

## RED

- Base commit: `712aed05adb7d8532136256b6c0cb2746209a2f0`
- Command: `node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs`
- Result: exit 1; 50 tests ran, 48 passed, 2 failed.
- Expected failures: the adapter and end-to-end CLI regressions both found `MEMORIES_API_KEY = "super-secret"` in the local TOML env block despite `persistApiKey = false` / `--no-persist-api-key`.

## GREEN and verification

- Focused adapter/CLI/pack command: `node --test mcp-server/test/adapter-codex.test.mjs mcp-server/test/cli.test.mjs mcp-server/test/pack.test.mjs` — exit 0; 52 passed, 0 failed.
- npm package suite: `cd mcp-server && npm test` — exit 0; 214 passed, 0 failed.
- Targeted Python contracts: `uv run pytest -q tests/test_codex_plugin.py tests/test_installer.py` — exit 0; 22 passed.
- Shell syntax and whitespace checks: `bash -n mcp-server/assets/codex/hooks/*.sh mcp-server/assets/codex/memory-codex-notify.sh` and `git diff --check` — both passed.

## Residual risk

The new switch is scoped to the local stdio TOML block. Remote OAuth remains key-free by construction, and unmanaged TOML sections are still preserved byte-for-byte; no known residual risk remains for this contract.

Commit message: `fix(codex): honor non-persistent API keys`.

The final commit hash is intentionally recorded by branch history rather than embedded here, because this report is part of that commit and amending it changes the hash.
