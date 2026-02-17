"""Tests for llm_provider module."""
import os
import pytest
import json
from unittest.mock import patch, MagicMock


class TestProviderFactory:
    """Test get_provider() factory function."""

    def test_returns_none_when_no_provider_set(self):
        with patch.dict(os.environ, {}, clear=True):
            from llm_provider import get_provider
            assert get_provider() is None

    def test_returns_none_for_empty_provider(self):
        with patch.dict(os.environ, {"EXTRACT_PROVIDER": ""}):
            from llm_provider import get_provider
            assert get_provider() is None

    def test_raises_for_unknown_provider(self):
        with patch.dict(os.environ, {"EXTRACT_PROVIDER": "unknown"}):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="Unknown.*unknown"):
                get_provider()

    def test_anthropic_provider_requires_key(self):
        env = {"EXTRACT_PROVIDER": "anthropic"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                get_provider()

    def test_openai_provider_requires_key(self):
        env = {"EXTRACT_PROVIDER": "openai"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                get_provider()

    def test_ollama_provider_no_key_needed(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env, clear=True):
            from llm_provider import get_provider
            provider = get_provider()
            assert provider is not None
            assert provider.provider_name == "ollama"
            assert provider.supports_audn is False


class TestOllamaProvider:
    """Test OllamaProvider without network calls."""

    def test_default_model(self):
        with patch.dict(os.environ, {"EXTRACT_PROVIDER": "ollama"}):
            from llm_provider import get_provider
            provider = get_provider()
            assert provider.model == "gemma3:4b"

    def test_custom_model(self):
        env = {"EXTRACT_PROVIDER": "ollama", "EXTRACT_MODEL": "llama3:8b"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()
            assert provider.model == "llama3:8b"

    def test_custom_url(self):
        env = {"EXTRACT_PROVIDER": "ollama", "OLLAMA_URL": "http://myhost:11434"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()
            assert "myhost" in provider.base_url

    def test_complete_calls_ollama_api(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"response": "test output"}

            with patch("llm_provider.requests.post", return_value=mock_response) as mock_post:
                result = provider.complete("system prompt", "user prompt")
                assert result == "test output"
                mock_post.assert_called_once()

    def test_health_check(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()

            mock_response = MagicMock()
            mock_response.status_code = 200

            with patch("llm_provider.requests.get", return_value=mock_response):
                assert provider.health_check() is True

    def test_health_check_failure(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()

            with patch("llm_provider.requests.get", side_effect=Exception("conn refused")):
                assert provider.health_check() is False


class TestProviderInterface:
    """Test that all providers expose the same interface."""

    def test_ollama_has_required_attrs(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()
            assert hasattr(provider, "complete")
            assert hasattr(provider, "health_check")
            assert hasattr(provider, "provider_name")
            assert hasattr(provider, "model")
            assert hasattr(provider, "supports_audn")
