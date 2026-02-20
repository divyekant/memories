"""LLM provider abstraction for memory extraction.

Supports five provider sources via environment variables:
  EXTRACT_PROVIDER: "anthropic", "openai", "chatgpt-subscription", or "ollama" (empty = disabled)
  EXTRACT_MODEL: model name (defaults per provider)

  anthropic:              ANTHROPIC_API_KEY (standard sk-ant-api03- or OAuth sk-ant-oat01- token)
  openai:                 OPENAI_API_KEY
  chatgpt-subscription:   CHATGPT_REFRESH_TOKEN + CHATGPT_CLIENT_ID (OAuth token exchange)
  ollama:                 OLLAMA_URL (default: http://host.docker.internal:11434)
"""
import os
import json
import time
import logging
import urllib.parse
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass
from chatgpt_oauth import refresh_tokens, exchange_id_token_for_api_key

logger = logging.getLogger(__name__)


@dataclass
class CompletionResult:
    """Result from an LLM completion, including token usage."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4.1-nano",
    "ollama": "gemma3:4b",
}


class LLMProvider(ABC):
    """Base class for LLM providers."""

    provider_name: str
    model: str
    supports_audn: bool

    @abstractmethod
    def complete(self, system: str, user: str) -> CompletionResult:
        """Send a completion request. Returns text and token usage."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the provider is reachable and working."""
        ...


def _is_oauth_token(key: str) -> bool:
    """Check if an Anthropic key is an OAuth subscription token."""
    return key.startswith("sk-ant-oat01-")


OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"


class _OAuthState:
    """Tracks OAuth access/refresh tokens and expiry for Anthropic subscription tokens."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.refresh_token: str | None = None
        self.expires_at: float | None = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at

    def refresh(self) -> bool:
        """Refresh the OAuth token. Returns True on success."""
        if not self.refresh_token:
            return False
        try:
            payload = json.dumps({
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            }).encode()
            req = urllib.request.Request(
                OAUTH_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]
            self.expires_at = time.time() + data["expires_in"]
            logger.info("OAuth token refreshed successfully")
            return True
        except Exception as e:
            logger.warning("OAuth token refresh failed: %s", e)
            return False


REQUIRED_BETAS = ["oauth-2025-04-20", "interleaved-thinking-2025-05-14"]


def _make_oauth_httpx_client(oauth_state: "_OAuthState"):
    """Build an httpx.Client that intercepts requests to inject OAuth auth.

    Matches the pattern from WebChat/llm.js:
    - Sets Authorization: Bearer <token>
    - Removes x-api-key header
    - Adds anthropic-beta: oauth-2025-04-20 header
    - Appends ?beta=true to /v1/messages URL
    """
    import httpx

    class _OAuthTransport(httpx.BaseTransport):
        def __init__(self, transport: httpx.BaseTransport, state: "_OAuthState"):
            self._transport = transport
            self._state = state

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            # Refresh if expired
            if self._state.is_expired():
                self._state.refresh()

            # Set Bearer auth, remove x-api-key
            request.headers["authorization"] = f"Bearer {self._state.access_token}"
            if "x-api-key" in request.headers:
                del request.headers["x-api-key"]

            # Merge beta headers
            existing = request.headers.get("anthropic-beta", "")
            existing_list = [b.strip() for b in existing.split(",") if b.strip()]
            all_betas = list(dict.fromkeys(existing_list + REQUIRED_BETAS))
            request.headers["anthropic-beta"] = ",".join(all_betas)

            # Add ?beta=true for /v1/messages
            if request.url.path == "/v1/messages":
                request.url = request.url.copy_merge_params({"beta": "true"})

            return self._transport.handle_request(request)

    base_transport = httpx.HTTPTransport()
    return httpx.Client(transport=_OAuthTransport(base_transport, oauth_state))


class AnthropicProvider(LLMProvider):
    """Anthropic API provider (Claude models).

    Supports both standard API keys and OAuth subscription tokens (sk-ant-oat01-).
    OAuth tokens use a custom HTTP transport that injects Bearer auth, removes
    x-api-key, adds required beta headers, and appends ?beta=true to the URL.
    """

    provider_name = "anthropic"
    supports_audn = True

    def __init__(self, api_key: str, model: str | None = None):
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package required. Install with: pip install anthropic>=0.40.0"
            )
        self.model = model or DEFAULT_MODELS["anthropic"]
        self._oauth: _OAuthState | None = None

        if _is_oauth_token(api_key):
            self._oauth = _OAuthState(access_token=api_key)
            http_client = _make_oauth_httpx_client(self._oauth)
            # Use placeholder api_key to satisfy SDK constructor;
            # the custom transport replaces it with Bearer auth.
            self.client = anthropic.Anthropic(
                api_key="placeholder",
                http_client=http_client,
            )
            logger.info("Using OAuth subscription token (sk-ant-oat01-) with custom transport")
        else:
            self.client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str) -> CompletionResult:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return CompletionResult(
            text=response.content[0].text,
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
        )

    def health_check(self) -> bool:
        try:
            self.complete("Reply with OK", "health check")
            return True
        except Exception as e:
            logger.warning("Anthropic health check failed: %s", e)
            return False


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    provider_name = "openai"
    supports_audn = True

    def __init__(self, api_key: str, model: str | None = None):
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package required. Install with: pip install openai>=1.50.0"
            )
        self.model = model or DEFAULT_MODELS["openai"]
        self.client = openai.OpenAI(api_key=api_key)

    def complete(self, system: str, user: str) -> CompletionResult:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=1024,
        )
        usage = response.usage
        return CompletionResult(
            text=response.choices[0].message.content,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )

    def health_check(self) -> bool:
        try:
            self.complete("Reply with OK", "health check")
            return True
        except Exception as e:
            logger.warning("OpenAI health check failed: %s", e)
            return False


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
        if tokens.get("refresh_token"):
            self._refresh_token = tokens["refresh_token"]
        self._api_key = exchange_id_token_for_api_key(id_token, self._client_id)
        expires_in = tokens.get("expires_in", 3600)
        self._expires_at = time.time() + max(60, expires_in - 300)
        self.client = self._openai_module.OpenAI(api_key=self._api_key)
        logger.info("ChatGPT subscription: API key refreshed (expires_in=%ds)", expires_in)

    def _ensure_fresh(self) -> None:
        if time.time() >= self._expires_at:
            logger.info("ChatGPT subscription: API key expired, refreshing...")
            self._refresh_api_key()

    def complete(self, system: str, user: str) -> CompletionResult:
        self._ensure_fresh()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=1024,
        )
        usage = response.usage
        return CompletionResult(
            text=response.choices[0].message.content,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )

    def health_check(self) -> bool:
        try:
            self._ensure_fresh()
            self.complete("Reply with OK", "health check")
            return True
        except Exception as e:
            logger.warning("ChatGPT subscription health check failed: %s", e)
            return False


class OllamaProvider(LLMProvider):
    """Ollama local provider with AUDN support."""

    provider_name = "ollama"
    supports_audn = True

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or "http://host.docker.internal:11434").rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid OLLAMA_URL scheme: {parsed.scheme!r} (must be http or https)")
        self.model = model or DEFAULT_MODELS["ollama"]

    def complete(self, system: str, user: str) -> CompletionResult:
        payload = json.dumps({
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "format": "json",
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return CompletionResult(
            text=data["response"],
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    def health_check(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning("Ollama health check failed: %s", e)
            return False


def get_provider() -> LLMProvider | None:
    """Factory: create an LLM provider from environment variables.

    Returns None if EXTRACT_PROVIDER is not set (extraction disabled).
    Raises ValueError for invalid configuration.
    """
    provider_name = os.environ.get("EXTRACT_PROVIDER", "").strip().lower()
    if not provider_name:
        return None

    model = os.environ.get("EXTRACT_MODEL", "").strip() or None

    if provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY required when EXTRACT_PROVIDER=anthropic")
        return AnthropicProvider(api_key=api_key, model=model)

    elif provider_name == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY required when EXTRACT_PROVIDER=openai")
        return OpenAIProvider(api_key=api_key, model=model)

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

    elif provider_name == "ollama":
        base_url = os.environ.get("OLLAMA_URL", "").strip() or None
        return OllamaProvider(base_url=base_url, model=model)

    else:
        raise ValueError(
            f"Unknown EXTRACT_PROVIDER: '{provider_name}'. "
            "Use: anthropic, openai, chatgpt-subscription, or ollama"
        )
