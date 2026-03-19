# Multi-Provider Extraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable Memories extraction from 5 sources: Anthropic API key, Anthropic Claude subscription, OpenAI API key, ChatGPT subscription (OAuth token exchange), and Ollama (with full AUDN).

**Architecture:** Extend the existing `LLMProvider` abstraction with one new provider class (`ChatGPTSubscriptionProvider`), upgrade Ollama to AUDN, add a CLI auth tool for ChatGPT OAuth setup. Pure Python stdlib — zero new dependencies.

**Tech Stack:** Python stdlib (`urllib.request`, `http.server`, `hashlib`, `secrets`, `webbrowser`, `argparse`), existing OpenAI SDK (already in deps), pytest for tests.

**Design doc:** `docs/plans/2026-02-18-multi-provider-extraction-design.md`

---

### Task 1: Upgrade Ollama — AUDN flag + JSON format

**Files:**
- Modify: `llm_provider.py:229-263` (OllamaProvider class)
- Test: `tests/test_llm_provider.py`

**Step 1: Write failing tests for Ollama AUDN support**

Add to `tests/test_llm_provider.py` in the `TestOllamaProvider` class:

```python
def test_ollama_supports_audn(self):
    env = {"EXTRACT_PROVIDER": "ollama"}
    with patch.dict(os.environ, env):
        from llm_provider import get_provider
        provider = get_provider()
        assert provider.supports_audn is True

def test_ollama_complete_sends_json_format(self):
    env = {"EXTRACT_PROVIDER": "ollama"}
    with patch.dict(os.environ, env):
        from llm_provider import get_provider
        provider = get_provider()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": "test"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("llm_provider.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            provider.complete("system", "user")
            call_args = mock_urlopen.call_args
            request_obj = call_args[0][0]
            body = json.loads(request_obj.data)
            assert body.get("format") == "json"
```

Also update the existing test `test_ollama_provider_no_key_needed` to expect `supports_audn is True` (line 49).

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm_provider.py -v -k "ollama" 2>&1`
Expected: 2 new tests FAIL (supports_audn still False, no format key)

**Step 3: Implement Ollama changes**

In `llm_provider.py`, OllamaProvider class (line 229-263):

1. Change `supports_audn = False` → `supports_audn = True` (line 233)
2. Add `"format": "json"` to the payload dict in `complete()` (line 240-245):

```python
def complete(self, system: str, user: str) -> str:
    payload = json.dumps({
        "model": self.model,
        "system": system,
        "prompt": user,
        "stream": False,
        "format": "json",
    }).encode()
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm_provider.py -v -k "ollama" 2>&1`
Expected: ALL ollama tests PASS

**Step 5: Commit**

```bash
git add llm_provider.py tests/test_llm_provider.py
git commit -m "feat: enable AUDN for Ollama with JSON format constraint"
```

---

### Task 2: ChatGPT OAuth token exchange helpers

**Files:**
- Create: `chatgpt_oauth.py`
- Test: `tests/test_chatgpt_oauth.py`

This module contains the pure-function OAuth helpers (no server, no browser). Extracted so both the CLI and the provider can use them.

**Step 1: Write failing tests for token exchange helpers**

Create `tests/test_chatgpt_oauth.py`:

```python
"""Tests for chatgpt_oauth module — PKCE and token exchange helpers."""
import json
import hashlib
import base64
import pytest
from unittest.mock import patch, MagicMock


class TestPKCE:
    """Test PKCE code_verifier and code_challenge generation."""

    def test_generate_code_verifier_length(self):
        from chatgpt_oauth import generate_code_verifier
        verifier = generate_code_verifier()
        # base64url of 64 random bytes = 86 chars (no padding)
        assert 43 <= len(verifier) <= 128

    def test_generate_code_verifier_is_base64url(self):
        from chatgpt_oauth import generate_code_verifier
        verifier = generate_code_verifier()
        # Must only contain base64url chars (no +, /, =)
        assert "+" not in verifier
        assert "/" not in verifier
        assert "=" not in verifier

    def test_generate_code_verifier_unique(self):
        from chatgpt_oauth import generate_code_verifier
        v1 = generate_code_verifier()
        v2 = generate_code_verifier()
        assert v1 != v2

    def test_compute_code_challenge(self):
        from chatgpt_oauth import generate_code_verifier, compute_code_challenge
        verifier = generate_code_verifier()
        challenge = compute_code_challenge(verifier)
        # Verify manually: SHA256 → base64url
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert challenge == expected

    def test_generate_state(self):
        from chatgpt_oauth import generate_state
        state = generate_state()
        assert len(state) >= 32
        assert "+" not in state
        assert "/" not in state


class TestTokenExchange:
    """Test OAuth token exchange functions (mocked HTTP)."""

    def _mock_urlopen(self, response_data: dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_exchange_code_for_tokens(self):
        from chatgpt_oauth import exchange_code_for_tokens
        tokens_response = {
            "id_token": "fake-id-token",
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": 3600,
        }
        with patch("chatgpt_oauth.urllib.request.urlopen",
                    return_value=self._mock_urlopen(tokens_response)) as mock_url:
            result = exchange_code_for_tokens(
                code="auth-code-123",
                code_verifier="verifier-abc",
                redirect_uri="http://localhost:9876/callback",
                client_id="test-client-id",
            )
            assert result["id_token"] == "fake-id-token"
            assert result["refresh_token"] == "fake-refresh-token"
            # Verify the request was made to the right URL
            call_args = mock_url.call_args[0][0]
            assert "auth.openai.com/oauth/token" in call_args.full_url

    def test_exchange_code_sends_correct_params(self):
        from chatgpt_oauth import exchange_code_for_tokens
        with patch("chatgpt_oauth.urllib.request.urlopen",
                    return_value=self._mock_urlopen({"id_token": "t", "refresh_token": "r"})) as mock_url:
            exchange_code_for_tokens(
                code="code-1",
                code_verifier="verifier-1",
                redirect_uri="http://localhost:9876/callback",
                client_id="cid-1",
            )
            request_obj = mock_url.call_args[0][0]
            body = request_obj.data.decode()
            assert "grant_type=authorization_code" in body
            assert "code=code-1" in body
            assert "code_verifier=verifier-1" in body
            assert "client_id=cid-1" in body

    def test_exchange_id_token_for_api_key(self):
        from chatgpt_oauth import exchange_id_token_for_api_key
        with patch("chatgpt_oauth.urllib.request.urlopen",
                    return_value=self._mock_urlopen({"access_token": "sk-fake-key"})):
            api_key = exchange_id_token_for_api_key(
                id_token="fake-id-token",
                client_id="test-client-id",
            )
            assert api_key == "sk-fake-key"

    def test_exchange_api_key_sends_token_exchange_grant(self):
        from chatgpt_oauth import exchange_id_token_for_api_key
        with patch("chatgpt_oauth.urllib.request.urlopen",
                    return_value=self._mock_urlopen({"access_token": "sk-key"})) as mock_url:
            exchange_id_token_for_api_key(id_token="idt", client_id="cid")
            request_obj = mock_url.call_args[0][0]
            body = request_obj.data.decode()
            assert "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Atoken-exchange" in body
            assert "requested_token=openai-api-key" in body
            assert "subject_token_type=urn" in body

    def test_refresh_tokens(self):
        from chatgpt_oauth import refresh_tokens
        with patch("chatgpt_oauth.urllib.request.urlopen",
                    return_value=self._mock_urlopen({
                        "id_token": "new-id",
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 7200,
                    })):
            result = refresh_tokens(
                refresh_token="old-refresh",
                client_id="cid",
            )
            assert result["id_token"] == "new-id"
            assert result["refresh_token"] == "new-refresh"

    def test_network_error_raises(self):
        from chatgpt_oauth import exchange_code_for_tokens
        with patch("chatgpt_oauth.urllib.request.urlopen",
                    side_effect=Exception("Connection refused")):
            with pytest.raises(Exception, match="Connection refused"):
                exchange_code_for_tokens(
                    code="c", code_verifier="v",
                    redirect_uri="http://localhost:9876/callback",
                    client_id="cid",
                )
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chatgpt_oauth.py -v 2>&1`
Expected: FAIL with ModuleNotFoundError (chatgpt_oauth doesn't exist yet)

**Step 3: Implement chatgpt_oauth.py**

Create `chatgpt_oauth.py`:

```python
"""ChatGPT OAuth2+PKCE helpers for token exchange.

Pure functions for:
- PKCE code_verifier / code_challenge generation
- Authorization code → tokens exchange
- Token exchange: id_token → OpenAI API key
- Refresh token → new tokens

All HTTP via urllib.request (stdlib). Zero external dependencies.
"""
import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request

OPENAI_ISSUER = "https://auth.openai.com"
TOKEN_URL = f"{OPENAI_ISSUER}/oauth/token"


def _base64url(data: bytes) -> str:
    """Base64url-encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_code_verifier() -> str:
    """Generate a PKCE code_verifier (64 random bytes → base64url)."""
    return _base64url(secrets.token_bytes(64))


def compute_code_challenge(verifier: str) -> str:
    """Compute S256 code_challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _base64url(digest)


def generate_state() -> str:
    """Generate a random OAuth state parameter."""
    return _base64url(secrets.token_bytes(32))


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
) -> str:
    """Build the OpenAI OAuth authorization URL."""
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "originator": "memories",
    })
    return f"{OPENAI_ISSUER}/oauth/authorize?{params}"


def _post_token(params: dict) -> dict:
    """POST to the OpenAI token endpoint with url-encoded form body."""
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def exchange_code_for_tokens(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """Exchange authorization code for id_token + refresh_token."""
    return _post_token({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": client_id,
    })


def exchange_id_token_for_api_key(id_token: str, client_id: str) -> str:
    """Exchange id_token for an OpenAI API key via token exchange grant."""
    result = _post_token({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "client_id": client_id,
        "subject_token": id_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        "requested_token": "openai-api-key",
    })
    return result["access_token"]


def refresh_tokens(refresh_token: str, client_id: str) -> dict:
    """Refresh tokens using a refresh_token."""
    return _post_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "scope": "openid profile email",
    })
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chatgpt_oauth.py -v 2>&1`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add chatgpt_oauth.py tests/test_chatgpt_oauth.py
git commit -m "feat: add ChatGPT OAuth PKCE and token exchange helpers"
```

---

### Task 3: ChatGPTSubscriptionProvider

**Files:**
- Modify: `llm_provider.py:193-295` (add new class + update factory)
- Test: `tests/test_llm_provider.py`

**Step 1: Write failing tests for ChatGPTSubscriptionProvider**

Add to `tests/test_llm_provider.py`:

```python
class TestChatGPTSubscriptionProvider:
    """Test ChatGPTSubscriptionProvider OAuth token exchange + OpenAI SDK."""

    def test_factory_creates_chatgpt_subscription_provider(self):
        env = {
            "EXTRACT_PROVIDER": "chatgpt-subscription",
            "CHATGPT_REFRESH_TOKEN": "fake-refresh-token",
            "CHATGPT_CLIENT_ID": "fake-client-id",
        }
        with patch.dict(os.environ, env, clear=True):
            # Mock the initial token refresh that happens in __init__
            mock_tokens = {
                "id_token": "fake-id",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            }
            with patch("llm_provider.refresh_tokens", return_value=mock_tokens), \
                 patch("llm_provider.exchange_id_token_for_api_key", return_value="sk-fake-key"), \
                 patch.dict("sys.modules", {"openai": MagicMock()}):
                from llm_provider import get_provider
                provider = get_provider()
                assert provider is not None
                assert provider.provider_name == "chatgpt-subscription"
                assert provider.supports_audn is True

    def test_requires_refresh_token(self):
        env = {"EXTRACT_PROVIDER": "chatgpt-subscription", "CHATGPT_CLIENT_ID": "cid"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="CHATGPT_REFRESH_TOKEN"):
                get_provider()

    def test_requires_client_id(self):
        env = {"EXTRACT_PROVIDER": "chatgpt-subscription", "CHATGPT_REFRESH_TOKEN": "rt"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="CHATGPT_CLIENT_ID"):
                get_provider()

    def test_complete_refreshes_expired_key(self):
        """When API key is expired, complete() should refresh before calling OpenAI."""
        from llm_provider import ChatGPTSubscriptionProvider
        mock_openai = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test response"
        mock_openai.OpenAI.return_value.chat.completions.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai}), \
             patch("llm_provider.refresh_tokens", return_value={
                 "id_token": "new-id", "refresh_token": "new-rt", "expires_in": 3600,
             }) as mock_refresh, \
             patch("llm_provider.exchange_id_token_for_api_key", return_value="sk-new"):
            provider = ChatGPTSubscriptionProvider(
                refresh_token="rt", client_id="cid",
            )
            # Force expiry
            provider._expires_at = time.time() - 10
            result = provider.complete("sys", "usr")
            assert result == "test response"
            # Should have refreshed (init + expiry refresh = 2 calls)
            assert mock_refresh.call_count == 2

    def test_default_model(self):
        from llm_provider import ChatGPTSubscriptionProvider
        mock_openai = MagicMock()
        with patch.dict("sys.modules", {"openai": mock_openai}), \
             patch("llm_provider.refresh_tokens", return_value={
                 "id_token": "id", "refresh_token": "rt", "expires_in": 3600,
             }), \
             patch("llm_provider.exchange_id_token_for_api_key", return_value="sk-k"):
            provider = ChatGPTSubscriptionProvider(refresh_token="rt", client_id="cid")
            assert provider.model == "gpt-4.1-nano"

    def test_custom_model(self):
        from llm_provider import ChatGPTSubscriptionProvider
        mock_openai = MagicMock()
        with patch.dict("sys.modules", {"openai": mock_openai}), \
             patch("llm_provider.refresh_tokens", return_value={
                 "id_token": "id", "refresh_token": "rt", "expires_in": 3600,
             }), \
             patch("llm_provider.exchange_id_token_for_api_key", return_value="sk-k"):
            provider = ChatGPTSubscriptionProvider(
                refresh_token="rt", client_id="cid", model="gpt-4o"
            )
            assert provider.model == "gpt-4o"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm_provider.py::TestChatGPTSubscriptionProvider -v 2>&1`
Expected: FAIL (class doesn't exist)

**Step 3: Implement ChatGPTSubscriptionProvider**

Add to `llm_provider.py` after `OpenAIProvider` (after line 227), before `OllamaProvider`:

```python
from chatgpt_oauth import refresh_tokens, exchange_id_token_for_api_key


class ChatGPTSubscriptionProvider(LLMProvider):
    """ChatGPT subscription provider — OAuth token exchange for ephemeral API key.

    Uses a refresh_token to periodically obtain a fresh OpenAI API key via:
      1. refresh_token → id_token (standard OAuth refresh)
      2. id_token → API key (token exchange grant)

    Once the API key is obtained, delegates to the OpenAI SDK (same as OpenAIProvider).
    """

    provider_name = "chatgpt-subscription"
    supports_audn = True

    def __init__(
        self,
        refresh_token: str,
        client_id: str,
        model: str | None = None,
    ):
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package required. Install with: pip install openai>=1.50.0"
            )
        self.model = model or DEFAULT_MODELS["openai"]
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._api_key: str | None = None
        self._expires_at: float = 0.0
        self._openai_module = openai
        self.client = None  # type: ignore
        self._refresh_api_key()

    def _refresh_api_key(self) -> None:
        """Refresh tokens and obtain a new OpenAI API key."""
        tokens = refresh_tokens(self._refresh_token, self._client_id)
        id_token = tokens.get("id_token", "")
        # Update refresh_token if rotated
        if tokens.get("refresh_token"):
            self._refresh_token = tokens["refresh_token"]
        # Exchange id_token → API key
        self._api_key = exchange_id_token_for_api_key(id_token, self._client_id)
        expires_in = tokens.get("expires_in", 3600)
        # Refresh 5 minutes early to avoid edge-case expiry
        self._expires_at = time.time() + max(60, expires_in - 300)
        self.client = self._openai_module.OpenAI(api_key=self._api_key)
        logger.info("ChatGPT subscription: API key refreshed (expires_in=%ds)", expires_in)

    def _ensure_fresh(self) -> None:
        if time.time() >= self._expires_at:
            logger.info("ChatGPT subscription: API key expired, refreshing...")
            self._refresh_api_key()

    def complete(self, system: str, user: str) -> str:
        self._ensure_fresh()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=1024,
        )
        return response.choices[0].message.content

    def health_check(self) -> bool:
        try:
            self._ensure_fresh()
            self.complete("Reply with OK", "health check")
            return True
        except Exception as e:
            logger.warning("ChatGPT subscription health check failed: %s", e)
            return False
```

Then update `get_provider()` factory (add after the `openai` elif block, before `ollama`):

```python
    elif provider_name == "chatgpt-subscription":
        refresh_token = os.environ.get("CHATGPT_REFRESH_TOKEN", "").strip()
        client_id = os.environ.get("CHATGPT_CLIENT_ID", "").strip()
        if not refresh_token:
            raise ValueError("CHATGPT_REFRESH_TOKEN required when EXTRACT_PROVIDER=chatgpt-subscription")
        if not client_id:
            raise ValueError("CHATGPT_CLIENT_ID required when EXTRACT_PROVIDER=chatgpt-subscription")
        return ChatGPTSubscriptionProvider(
            refresh_token=refresh_token, client_id=client_id, model=model,
        )
```

Update the error message to include the new provider:

```python
    else:
        raise ValueError(
            f"Unknown EXTRACT_PROVIDER: '{provider_name}'. "
            "Use: anthropic, openai, chatgpt-subscription, or ollama"
        )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm_provider.py -v 2>&1`
Expected: ALL PASS (including existing tests)

**Step 5: Commit**

```bash
git add llm_provider.py tests/test_llm_provider.py
git commit -m "feat: add ChatGPTSubscriptionProvider with OAuth token exchange"
```

---

### Task 4: CLI auth tool — `memories_auth.py` + `__main__.py`

**Files:**
- Create: `memories_auth.py`
- Create: `__main__.py`
- Test: `tests/test_memories_auth.py`

**Step 1: Write failing tests for CLI auth helpers**

Create `tests/test_memories_auth.py`:

```python
"""Tests for memories_auth CLI tool — env file writing and status display."""
import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestEnvFileWriter:
    """Test writing OAuth tokens to ~/.config/memories/env."""

    def test_writes_env_file(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "env"
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt-123",
                client_id="cid-456",
            )
            content = env_path.read_text()
            assert 'EXTRACT_PROVIDER="chatgpt-subscription"' in content
            assert 'CHATGPT_REFRESH_TOKEN="rt-123"' in content
            assert 'CHATGPT_CLIENT_ID="cid-456"' in content

    def test_preserves_existing_non_conflicting_vars(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "env"
            env_path.write_text('MEMORIES_URL="http://localhost:8900"\nMEMORIES_API_KEY="my-key"\n')
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt",
                client_id="cid",
            )
            content = env_path.read_text()
            assert 'MEMORIES_URL="http://localhost:8900"' in content
            assert 'MEMORIES_API_KEY="my-key"' in content
            assert 'CHATGPT_REFRESH_TOKEN="rt"' in content

    def test_overwrites_existing_provider_vars(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "env"
            env_path.write_text('EXTRACT_PROVIDER="openai"\nOPENAI_API_KEY="old"\n')
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt",
                client_id="cid",
            )
            content = env_path.read_text()
            assert 'EXTRACT_PROVIDER="chatgpt-subscription"' in content
            assert 'CHATGPT_REFRESH_TOKEN="rt"' in content
            # Old provider key should be removed
            assert "OPENAI_API_KEY" not in content

    def test_creates_parent_directories(self):
        from memories_auth import write_env_file
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "subdir" / "nested" / "env"
            write_env_file(
                env_path=env_path,
                provider="chatgpt-subscription",
                refresh_token="rt",
                client_id="cid",
            )
            assert env_path.exists()


class TestAuthStatus:
    """Test the auth status display function."""

    def test_status_when_no_provider(self):
        from memories_auth import get_auth_status
        with patch.dict(os.environ, {}, clear=True):
            status = get_auth_status()
            assert status["provider"] is None
            assert status["configured"] is False

    def test_status_with_anthropic(self):
        from memories_auth import get_auth_status
        env = {"EXTRACT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-api03-test"}
        with patch.dict(os.environ, env):
            status = get_auth_status()
            assert status["provider"] == "anthropic"
            assert status["configured"] is True
            assert "sk-ant-api03-****" in status["key_preview"]

    def test_status_with_chatgpt_subscription(self):
        from memories_auth import get_auth_status
        env = {
            "EXTRACT_PROVIDER": "chatgpt-subscription",
            "CHATGPT_REFRESH_TOKEN": "long-refresh-token-value",
            "CHATGPT_CLIENT_ID": "my-client-id",
        }
        with patch.dict(os.environ, env):
            status = get_auth_status()
            assert status["provider"] == "chatgpt-subscription"
            assert status["configured"] is True
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_memories_auth.py -v 2>&1`
Expected: FAIL (module doesn't exist)

**Step 3: Implement memories_auth.py**

Create `memories_auth.py`:

```python
"""Memories CLI auth tool — one-time OAuth setup for extraction providers.

Usage:
    python -m memories auth chatgpt   # Interactive ChatGPT OAuth flow
    python -m memories auth status    # Show current provider config
"""
import os
import sys
import json
import logging
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

DEFAULT_ENV_PATH = Path.home() / ".config" / "memories" / "env"
DEFAULT_CALLBACK_PORT = 9876

# Keys managed by auth setup — removed when switching providers
_PROVIDER_KEYS = {
    "EXTRACT_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "CHATGPT_REFRESH_TOKEN", "CHATGPT_CLIENT_ID", "OLLAMA_URL",
    "EXTRACT_MODEL",
}


def write_env_file(
    env_path: Path,
    provider: str,
    refresh_token: str | None = None,
    client_id: str | None = None,
    api_key: str | None = None,
) -> None:
    """Write or update the env file, preserving non-provider vars."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                existing_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key not in _PROVIDER_KEYS:
                existing_lines.append(line)

    new_lines = [f'EXTRACT_PROVIDER="{provider}"']
    if refresh_token:
        new_lines.append(f'CHATGPT_REFRESH_TOKEN="{refresh_token}"')
    if client_id:
        new_lines.append(f'CHATGPT_CLIENT_ID="{client_id}"')
    if api_key:
        if provider == "anthropic":
            new_lines.append(f'ANTHROPIC_API_KEY="{api_key}"')
        elif provider == "openai":
            new_lines.append(f'OPENAI_API_KEY="{api_key}"')

    all_lines = existing_lines + new_lines
    env_path.write_text("\n".join(all_lines) + "\n")


def get_auth_status() -> dict:
    """Read current provider config from environment and return status dict."""
    provider = os.environ.get("EXTRACT_PROVIDER", "").strip() or None
    if not provider:
        return {"provider": None, "configured": False}

    status = {"provider": provider, "configured": True}

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            status["key_preview"] = key[:12] + "****" if len(key) > 12 else "****"
        else:
            status["configured"] = False

    elif provider == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            status["key_preview"] = key[:7] + "****" if len(key) > 7 else "****"
        else:
            status["configured"] = False

    elif provider == "chatgpt-subscription":
        rt = os.environ.get("CHATGPT_REFRESH_TOKEN", "")
        cid = os.environ.get("CHATGPT_CLIENT_ID", "")
        if rt and cid:
            status["key_preview"] = rt[:8] + "****"
            status["client_id"] = cid
        else:
            status["configured"] = False

    elif provider == "ollama":
        url = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
        status["ollama_url"] = url

    model = os.environ.get("EXTRACT_MODEL", "")
    if model:
        status["model"] = model

    return status


def run_chatgpt_auth(client_id: str, port: int = DEFAULT_CALLBACK_PORT) -> dict:
    """Run the interactive ChatGPT OAuth flow. Returns tokens dict.

    Opens a browser for user login, captures the callback, and exchanges
    the authorization code for tokens + API key.
    """
    from chatgpt_oauth import (
        generate_code_verifier,
        compute_code_challenge,
        generate_state,
        build_authorize_url,
        exchange_code_for_tokens,
        exchange_id_token_for_api_key,
    )

    code_verifier = generate_code_verifier()
    code_challenge = compute_code_challenge(code_verifier)
    state = generate_state()
    redirect_uri = f"http://localhost:{port}/callback"

    auth_url = build_authorize_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
    )

    result = {"code": None, "error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            if params.get("error"):
                result["error"] = params["error"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Auth failed. You can close this tab.</h2>")
                threading.Thread(target=self.server.shutdown).start()
                return

            received_state = params.get("state", [""])[0]
            if received_state != state:
                result["error"] = "state_mismatch"
                self.send_response(400)
                self.end_headers()
                return

            result["code"] = params.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Success! You can close this tab.</h2>")
            threading.Thread(target=self.server.shutdown).start()

        def log_message(self, format, *args):
            pass  # Suppress HTTP logs

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)

    print(f"\nOpening browser for ChatGPT login...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for callback...")
    server.serve_forever()
    server.server_close()

    if result["error"]:
        raise RuntimeError(f"OAuth failed: {result['error']}")
    if not result["code"]:
        raise RuntimeError("No authorization code received")

    print("Exchanging authorization code for tokens...")
    tokens = exchange_code_for_tokens(
        code=result["code"],
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        client_id=client_id,
    )

    print("Exchanging id_token for OpenAI API key...")
    api_key = exchange_id_token_for_api_key(
        id_token=tokens["id_token"],
        client_id=client_id,
    )

    return {
        "refresh_token": tokens.get("refresh_token", ""),
        "id_token": tokens.get("id_token", ""),
        "api_key": api_key,
        "expires_in": tokens.get("expires_in"),
    }


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="memories auth",
        description="Memories extraction provider auth setup",
    )
    sub = parser.add_subparsers(dest="command")

    chatgpt_parser = sub.add_parser("chatgpt", help="Set up ChatGPT subscription auth")
    chatgpt_parser.add_argument("--client-id", required=True, help="OpenAI OAuth client ID")
    chatgpt_parser.add_argument("--port", type=int, default=DEFAULT_CALLBACK_PORT, help="Callback port")
    chatgpt_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH, help="Env file path")

    sub.add_parser("status", help="Show current provider configuration")

    args = parser.parse_args(argv)

    if args.command == "chatgpt":
        try:
            result = run_chatgpt_auth(client_id=args.client_id, port=args.port)
            write_env_file(
                env_path=args.env_file,
                provider="chatgpt-subscription",
                refresh_token=result["refresh_token"],
                client_id=args.client_id,
            )
            print(f"\n✓ Auth complete! Config written to {args.env_file}")
            print(f"  Provider: chatgpt-subscription")
            print(f"  API key:  {result['api_key'][:12]}...")
            print(f"\nRestart Memories to use the new provider.")
        except Exception as e:
            print(f"\n✗ Auth failed: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "status":
        status = get_auth_status()
        if not status["configured"]:
            print("No extraction provider configured.")
            print("Set EXTRACT_PROVIDER env var or run: python -m memories auth chatgpt")
        else:
            print(f"Provider:  {status['provider']}")
            if "key_preview" in status:
                print(f"Key:       {status['key_preview']}")
            if "model" in status:
                print(f"Model:     {status['model']}")
            if "ollama_url" in status:
                print(f"URL:       {status['ollama_url']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

Create `__main__.py` (project root):

```python
"""Entry point for `python -m memories`."""
import sys

if len(sys.argv) >= 2 and sys.argv[1] == "auth":
    from memories_auth import main
    main(sys.argv[2:])
else:
    print("Usage: python -m memories auth [chatgpt|status]")
    sys.exit(1)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_memories_auth.py -v 2>&1`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add memories_auth.py __main__.py tests/test_memories_auth.py
git commit -m "feat: add CLI auth tool for ChatGPT OAuth setup"
```

---

### Task 5: Docker compose env pass-through

**Files:**
- Modify: `docker-compose.yml:47-51`
- Modify: `docker-compose.snippet.yml:50-54`

**Step 1: No test needed — declarative config**

**Step 2: Add env vars to docker-compose.yml**

After the existing `OLLAMA_URL` line (line 51), add:

```yaml
      - CHATGPT_REFRESH_TOKEN=${CHATGPT_REFRESH_TOKEN:-}
      - CHATGPT_CLIENT_ID=${CHATGPT_CLIENT_ID:-}
```

**Step 3: Add env vars to docker-compose.snippet.yml**

After the existing `OLLAMA_URL` line (line 54), add:

```yaml
      - CHATGPT_REFRESH_TOKEN=${CHATGPT_REFRESH_TOKEN:-}
      - CHATGPT_CLIENT_ID=${CHATGPT_CLIENT_ID:-}
```

**Step 4: Verify compose config is valid**

Run: `docker compose -f docker-compose.yml config --quiet 2>&1 && echo "OK"`
Expected: OK (no errors)

**Step 5: Commit**

```bash
git add docker-compose.yml docker-compose.snippet.yml
git commit -m "chore: add ChatGPT subscription env vars to compose files"
```

---

### Task 6: Copy chatgpt_oauth.py into Docker image

**Files:**
- Modify: `Dockerfile`

**Step 1: Check current Dockerfile COPY lines**

Read `Dockerfile` and find the COPY section for Python files in the runtime-base stage.

**Step 2: Add COPY for chatgpt_oauth.py**

Add `COPY chatgpt_oauth.py .` in the same block where `llm_provider.py` and `llm_extract.py` are copied.

**Step 3: Verify build succeeds**

Run: `docker compose build --no-cache memories 2>&1 | tail -5`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add Dockerfile
git commit -m "chore: include chatgpt_oauth.py in Docker image"
```

---

### Task 7: Full integration test run + update error message

**Files:**
- Modify: `tests/test_llm_provider.py` (update error message test if needed)

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v 2>&1`
Expected: ALL PASS (157 existing + ~20 new = ~177 total)

**Step 2: Check the unknown provider error message test**

The `test_raises_for_unknown_provider` test (line 22-26) checks the error message format. Since we updated the error message to include `chatgpt-subscription`, verify the test still passes. If the error message match pattern changed, update the test.

**Step 3: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: multi-provider extraction — 5 sources supported

Adds ChatGPT subscription provider (OAuth token exchange),
upgrades Ollama to full AUDN with JSON format constraint,
and adds CLI auth tool for one-time OAuth setup.

Providers: anthropic, anthropic (OAuth), openai,
chatgpt-subscription, ollama (now with AUDN)."
```

---

## Task Dependency Graph

```
Task 1 (Ollama AUDN)  ←──── independent
Task 2 (OAuth helpers) ←──── independent
        │
        v
Task 3 (ChatGPT Provider) ←── depends on Task 2
        │
        v
Task 4 (CLI auth tool) ←── depends on Task 2
        │
        v
Task 5 (Compose env vars) ←── independent
Task 6 (Dockerfile COPY) ←── depends on Task 2
        │
        v
Task 7 (Integration test) ←── depends on all above
```

Tasks 1, 2, and 5 can run in parallel. Task 3 and 4 depend on Task 2. Task 7 is final validation.
