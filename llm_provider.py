"""LLM provider abstraction for memory extraction.

Supports Anthropic, OpenAI, and Ollama. Configured via environment variables:
  EXTRACT_PROVIDER: "anthropic", "openai", or "ollama" (empty = disabled)
  EXTRACT_MODEL: model name (defaults per provider)
  ANTHROPIC_API_KEY: required for anthropic
  OPENAI_API_KEY: required for openai
  OLLAMA_URL: ollama server URL (default: http://host.docker.internal:11434)
"""
import os
import json
import logging
import urllib.request
import urllib.error
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

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
    def complete(self, system: str, user: str) -> str:
        """Send a completion request. Returns the response text."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the provider is reachable and working."""
        ...


class AnthropicProvider(LLMProvider):
    """Anthropic API provider (Claude models)."""

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
        self.client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

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

    def complete(self, system: str, user: str) -> str:
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
            self.complete("Reply with OK", "health check")
            return True
        except Exception as e:
            logger.warning("OpenAI health check failed: %s", e)
            return False


class OllamaProvider(LLMProvider):
    """Ollama local provider. Extraction only — no AUDN support."""

    provider_name = "ollama"
    supports_audn = False

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or "http://host.docker.internal:11434").rstrip("/")
        self.model = model or DEFAULT_MODELS["ollama"]

    def complete(self, system: str, user: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["response"]

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

    elif provider_name == "ollama":
        base_url = os.environ.get("OLLAMA_URL", "").strip() or None
        return OllamaProvider(base_url=base_url, model=model)

    else:
        raise ValueError(f"Unknown EXTRACT_PROVIDER: '{provider_name}'. Use: anthropic, openai, or ollama")
