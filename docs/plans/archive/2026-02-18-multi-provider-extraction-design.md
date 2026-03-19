# Multi-Provider Extraction Design

**Date:** 2026-02-18
**Status:** Approved

## Goal

Enable Memories extraction pipeline to work with 5 provider sources:

1. Anthropic API Key
2. Anthropic Claude Subscription (OAuth token)
3. OpenAI API Key
4. OpenAI ChatGPT Subscription (OAuth token exchange)
5. Ollama (local, now with full AUDN)

## Approach

**Thin Provider Extension** — extend the existing `LLMProvider` abstraction in
`llm_provider.py`. Add one new provider class (`ChatGPTSubscriptionProvider`),
upgrade Ollama to support AUDN, and add a Python CLI tool for one-time OAuth
setup. No new runtime services, no new pip dependencies, no gateway servers.

Reference implementations:
- [SignInWithClaudeCode](https://github.com/divyekant/SignInWithClaudeCode) — token-to-session gateway (patterns borrowed for OAuth refresh)
- [SignInWithChatGPT](https://github.com/divyekant/SignInWithChatGPT) — OAuth2+PKCE gateway (patterns borrowed for token exchange)

## Provider Matrix

| `EXTRACT_PROVIDER` | Auth Config | AUDN | Notes |
|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` (sk-ant-api03-*) | Yes | Unchanged — direct Anthropic SDK |
| `anthropic` | `ANTHROPIC_API_KEY` (sk-ant-oat01-*) | Yes | Unchanged — auto-detected OAuth path with custom httpx transport |
| `openai` | `OPENAI_API_KEY` (sk-*) | Yes | Unchanged — direct OpenAI SDK |
| `chatgpt-subscription` | `CHATGPT_REFRESH_TOKEN` + `CHATGPT_CLIENT_ID` | Yes | **New** — OAuth token exchange for ephemeral API key |
| `ollama` | `OLLAMA_URL` | Yes | **Upgraded** — AUDN enabled, JSON format hint added |

Note: `anthropic-subscription` is not a separate provider value. The existing
`anthropic` provider auto-detects `sk-ant-oat01-*` tokens and applies OAuth
transport. No change needed.

## New: ChatGPTSubscriptionProvider

### Token Lifecycle

1. **One-time setup:** User runs `python -m memories auth chatgpt`
2. CLI opens browser to `auth.openai.com/oauth/authorize` with PKCE
3. User logs in at OpenAI, grants permissions (scope: `openid profile email offline_access`)
4. CLI captures callback, exchanges auth code for `id_token` + `refresh_token`
5. CLI does token exchange: `id_token` → real OpenAI API key
   (`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`)
6. CLI writes `CHATGPT_REFRESH_TOKEN` and `CHATGPT_CLIENT_ID` to `~/.config/memories/env`

### Runtime Behavior

```
ChatGPTSubscriptionProvider(LLMProvider)
  ├── __init__(refresh_token, client_id, model)
  │     └── Exchanges refresh_token → id_token → API key
  ├── complete(system, user) → str
  │     ├── Check if API key expired → refresh if needed
  │     └── Call OpenAI SDK (same as OpenAIProvider)
  ├── health_check() → bool
  └── _refresh_api_key()
        ├── POST auth.openai.com/oauth/token (refresh_token → new id_token)
        └── POST auth.openai.com/oauth/token (token exchange → new API key)
```

The provider internally wraps the OpenAI SDK. Once it has a valid API key, it
behaves identically to `OpenAIProvider`. The only difference is automatic key
refresh via the stored `refresh_token`.

### Configuration

```bash
EXTRACT_PROVIDER=chatgpt-subscription
CHATGPT_REFRESH_TOKEN=<from CLI setup>
CHATGPT_CLIENT_ID=<OpenAI OAuth client ID>
EXTRACT_MODEL=gpt-4.1-nano  # optional, defaults to gpt-4.1-nano
```

## Ollama AUDN Upgrade

Two changes:
1. Set `supports_audn = True` on `OllamaProvider`
2. Add `"format": "json"` to the `/api/generate` request payload

The `"format": "json"` parameter is natively supported by Ollama and constrains
model output to valid JSON, dramatically reducing parse failures. The AUDN prompt
and decision pipeline are already provider-agnostic — no changes needed there.

## CLI Auth Tool

### Entry Point

```bash
python -m memories auth chatgpt   # Interactive OAuth setup for ChatGPT
python -m memories auth status    # Show current provider config + health
```

### `auth chatgpt` Flow

1. Generate PKCE `code_verifier` (43-char base64url from 256 random bits)
2. Compute `code_challenge` = base64url(SHA256(code_verifier))
3. Generate random `state` parameter
4. Start ephemeral `http.server` on `localhost:9876`
5. Open browser to authorize URL:
   ```
   https://auth.openai.com/oauth/authorize?
     client_id={CHATGPT_CLIENT_ID}&
     response_type=code&
     redirect_uri=http://localhost:9876/callback&
     scope=openid+profile+email+offline_access&
     code_challenge={code_challenge}&
     code_challenge_method=S256&
     state={state}
   ```
6. Wait for callback: `GET /callback?code=...&state=...`
7. Validate state matches
8. Exchange code for tokens:
   ```
   POST https://auth.openai.com/oauth/token
   {grant_type: authorization_code, code, redirect_uri, code_verifier, client_id}
   → {id_token, access_token, refresh_token}
   ```
9. Exchange id_token for API key:
   ```
   POST https://auth.openai.com/oauth/token
   {grant_type: urn:ietf:params:oauth:grant-type:token-exchange,
    subject_token: id_token, requested_token: openai-api-key, client_id}
   → {access_token: "sk-..."}
   ```
10. Write to `~/.config/memories/env`:
    ```
    EXTRACT_PROVIDER=chatgpt-subscription
    CHATGPT_REFRESH_TOKEN=<refresh_token>
    CHATGPT_CLIENT_ID=<client_id>
    ```
11. Print success message, shut down server

### `auth status` Flow

1. Read env config (EXTRACT_PROVIDER, keys, etc.)
2. Show configured provider and model
3. Instantiate provider, call `health_check()`
4. Show token expiry info if applicable
5. Print summary table

### Dependencies

Zero new pip dependencies. All stdlib:
- `http.server` — ephemeral callback listener
- `urllib.request` — HTTP calls for token exchange
- `hashlib` + `base64` — PKCE S256 challenge
- `secrets` — random state/verifier generation
- `webbrowser` — open browser for OAuth
- `json` — token parsing
- `argparse` — CLI argument handling

## File Changes

| File | Change |
|---|---|
| `llm_provider.py` | Add `ChatGPTSubscriptionProvider` (~80 lines), add to `get_provider()` factory, set `OllamaProvider.supports_audn = True`, add `"format": "json"` to Ollama payload |
| `memories_auth.py` **(new)** | CLI auth tool — PKCE generation, OAuth flow, token exchange, env file writing (~200 lines) |
| `__main__.py` **(new)** | Entry point for `python -m memories auth` dispatching to `memories_auth.py` |
| `docker-compose.yml` | Add `CHATGPT_REFRESH_TOKEN`, `CHATGPT_CLIENT_ID` env pass-through |
| `docker-compose.snippet.yml` | Same env var additions |
| `requirements.txt` | No changes (all stdlib) |
| `tests/test_llm_provider.py` | Tests for ChatGPT subscription provider, Ollama AUDN flag, JSON format |
| `tests/test_memories_auth.py` | Tests for PKCE generation, token exchange, env file writing |

## Non-Goals

- No runtime provider switching (restart to change)
- No browser-based auth UI in Memories web UI
- No separate gateway services needed
- No Claude subscription CLI flow (auto-detected from token prefix today)

## Security Notes

- `CHATGPT_REFRESH_TOKEN` is a long-lived secret — stored in `~/.config/memories/env` (same as `API_KEY` today)
- Ephemeral callback server binds to `localhost` only, runs for <60 seconds
- PKCE prevents auth code interception (no client_secret needed)
- API keys obtained via token exchange have the same permissions as the user's ChatGPT subscription
