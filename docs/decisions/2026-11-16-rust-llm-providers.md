# Rust server: LLM providers

Date: 2026-11-16
Status: adopted (task P4-2 of the Rust rewrite plan)

## Decision

The module `provider` of crate `memories-extract` ports `llm_provider.py`
(v5.19.0). One type, `Provider`, serves four providers:

| Provider | Endpoint | Note |
|---|---|---|
| `anthropic` | `POST /v1/messages` | API key mode (`x-api-key`, optional `anthropic-workspace-id`) or OAuth token mode (`Authorization: Bearer`, `anthropic-beta`, `?beta=true`). Models before Opus 4.7 get `temperature: 0.0`. Other models get `output_config.effort: low`. |
| `openai` | `POST /chat/completions` | Shared OpenAI-compatible client. |
| `omlx` | `POST /chat/completions` | The same client, plus `chat_template_kwargs.enable_thinking: false`. An empty `content` falls back to `reasoning`. |
| `ollama` | `POST /api/generate` | Health check is `GET /api/tags`. |

The environment variables, the defaults and the error messages are the same
as in Python. `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL` work as in the
Python SDKs.

## Differences from Python

- `chatgpt-subscription` is not ported, because production does not use it
  (P0-3 baseline). The server refuses the setting with a clear message.
- Each attempt has a time limit of 120 s. The Python SDKs wait 600 s.
- Each provider retries at most 2 times after HTTP 408, 409, 429, 5xx, a
  time-out or a failed connection. The delay is 0.5 s, then 1 s, or the
  `retry-after` value up to 60 s. The Python SDKs do the same for Anthropic
  and OpenAI. Python Ollama calls had no retry.
- The OAuth token refresh of `_OAuthState` is not ported. Python never sets a
  refresh token, so the refresh never runs.
- The SDK variables `ANTHROPIC_AUTH_TOKEN`, `OPENAI_ORG_ID` and
  `OPENAI_PROJECT_ID` are not read.

## Parity check

`server/crates/memories-extract/tests/record_python.py` runs each Python
provider against a local capture server and writes one replay file for each
case to `tests/replay/`. The test `provider_replay.rs` sends the same call
from Rust to `memories_extract::replay::ReplayServer` and compares the method,
the path, the body and the contract headers with the Python request. The
compare ignores transport headers (`host`, `user-agent`, `accept`,
`accept-encoding`, `connection`, `content-length`, `x-stainless-*`). The
capture hides each secret, and the test fails if a replay file holds a test
secret.

To record again after a change to `llm_provider.py`:

```bash
uv sync --frozen --extra extract
.venv/bin/python server/crates/memories-extract/tests/record_python.py
```

Task P4-3 can use `ReplayServer` with recorded LLM replies: point the base
URL variable of the provider at it.
