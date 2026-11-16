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
as in Python. `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL` set the base URL, as
in the Python SDKs. In OAuth mode, `?beta=true` is added only when the base
URL has no path, as in Python.

## Differences from Python

- `chatgpt-subscription` is not ported, because production does not use it
  (P0-3 baseline). The server refuses the setting with a clear message.
- Each attempt has a time limit of 120 s. The Python SDKs wait 600 s. The
  time limit to connect is 5 s, as in the SDKs.
- Retries follow the SDK rules, with a maximum of 2:
  - `x-should-retry: true|false` decides first. Else HTTP 408, 409, 429 and
    5xx get a retry. Each transport error gets a retry.
  - The delay is `retry-after-ms` or `retry-after` (seconds) when the value is
    more than 0 and at most 60 s. Else the delay is 0.5 s, then 1 s.
  - The SDKs also read an HTTP date in `retry-after` and add jitter. Rust does
    not.
  - Ollama now retries too, but not after a time-out. Python Ollama calls had
    no retry.
- The OAuth token refresh of `_OAuthState` is not ported. Python never sets a
  refresh token, so the refresh never runs.
- An empty `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` gives the default URL.
  Rust also checks the scheme of these two variables at start, and strips all
  trailing slashes. Python did not.
- If an OpenAI reply has no `content`, the text is empty. Python gave `None`.
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
capture replaces each secret with its variable name, for example
`Bearer <OPENAI_API_KEY>`, so the test sees which secret was sent. The test
fails if a replay file holds a test secret.

To record again after a change to `llm_provider.py`:

```bash
uv sync --frozen --extra extract
.venv/bin/python server/crates/memories-extract/tests/record_python.py
```

`ReplayServer` answers HTTP 418 after its last recorded reply, so a provider
does not retry. It is in the main build, because P4-3 tests in other crates
use it and `axum` is in the server binary anyway. Task P4-3 can use `ReplayServer` with recorded LLM replies: point the base
URL variable of the provider at it.
