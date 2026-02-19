"""Tests for llm_provider module."""
import os
import pytest
import json
import time
from unittest.mock import patch, MagicMock, PropertyMock


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
            assert provider.supports_audn is True


class TestAnthropicOAuth:
    """Test Anthropic OAuth subscription token support."""

    def test_standard_key_no_oauth(self):
        """Standard API key should not create OAuth state."""
        env = {"EXTRACT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-api03-fake"}
        with patch.dict(os.environ, env):
            mock_anthropic = MagicMock()
            with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
                from llm_provider import get_provider
                provider = get_provider()
                assert provider._oauth is None
                mock_anthropic.Anthropic.assert_called_once_with(api_key="sk-ant-api03-fake")

    def test_oauth_token_creates_oauth_state(self):
        """OAuth token (sk-ant-oat01-) should use custom transport and create OAuth state."""
        env = {"EXTRACT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-oat01-faketoken"}
        with patch.dict(os.environ, env):
            mock_anthropic = MagicMock()
            with patch.dict("sys.modules", {"anthropic": mock_anthropic, "httpx": MagicMock()}):
                from llm_provider import get_provider
                provider = get_provider()
                assert provider._oauth is not None
                assert provider._oauth.access_token == "sk-ant-oat01-faketoken"
                call_kwargs = mock_anthropic.Anthropic.call_args
                assert call_kwargs.kwargs.get("api_key") == "placeholder"
                assert "http_client" in call_kwargs.kwargs

    def test_oauth_token_detection(self):
        """Verify the OAuth token detection helper."""
        from llm_provider import _is_oauth_token
        assert _is_oauth_token("sk-ant-oat01-abc123") is True
        assert _is_oauth_token("sk-ant-api03-abc123") is False
        assert _is_oauth_token("") is False

    def test_oauth_state_not_expired_initially(self):
        """Fresh OAuth state with no expiry should not be expired."""
        from llm_provider import _OAuthState
        state = _OAuthState(access_token="test-token")
        assert state.is_expired() is False

    def test_oauth_state_expired_when_past_deadline(self):
        """OAuth state should report expired when past expires_at."""
        from llm_provider import _OAuthState
        state = _OAuthState(access_token="test-token")
        state.expires_at = time.time() - 10  # 10 seconds ago
        assert state.is_expired() is True

    def test_oauth_refresh_updates_tokens(self):
        """Successful refresh should update access_token, refresh_token, and expires_at."""
        from llm_provider import _OAuthState
        state = _OAuthState(access_token="old-token")
        state.refresh_token = "old-refresh"
        state.expires_at = time.time() - 10  # expired

        refresh_response = json.dumps({
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = refresh_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("llm_provider.urllib.request.urlopen", return_value=mock_resp):
            result = state.refresh()

        assert result is True
        assert state.access_token == "new-access"
        assert state.refresh_token == "new-refresh"
        assert state.expires_at > time.time()

    def test_oauth_refresh_fails_without_refresh_token(self):
        """Refresh should fail gracefully when no refresh_token is set."""
        from llm_provider import _OAuthState
        state = _OAuthState(access_token="test-token")
        assert state.refresh() is False

    def test_oauth_refresh_handles_network_error(self):
        """Refresh should return False on network error."""
        from llm_provider import _OAuthState
        state = _OAuthState(access_token="test-token")
        state.refresh_token = "some-refresh"

        with patch("llm_provider.urllib.request.urlopen", side_effect=Exception("network error")):
            result = state.refresh()

        assert result is False

    def test_oauth_state_refresh_updates_access_token_used_by_transport(self):
        """When OAuth token expires, refresh should update access_token in state."""
        from llm_provider import _OAuthState
        state = _OAuthState(access_token="old-token")
        state.refresh_token = "refresh-token"
        state.expires_at = time.time() - 10  # expired

        refresh_response = json.dumps({
            "access_token": "refreshed-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = refresh_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("llm_provider.urllib.request.urlopen", return_value=mock_resp):
            assert state.is_expired() is True
            result = state.refresh()

        assert result is True
        assert state.access_token == "refreshed-token"
        # Transport reads state.access_token directly, so it will use the refreshed token


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

            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"response": "test output"}).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)

            with patch("llm_provider.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
                result = provider.complete("system prompt", "user prompt")
                assert result == "test output"
                mock_urlopen.assert_called_once()

    def test_health_check(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()

            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)

            with patch("llm_provider.urllib.request.urlopen", return_value=mock_resp):
                assert provider.health_check() is True

    def test_health_check_failure(self):
        env = {"EXTRACT_PROVIDER": "ollama"}
        with patch.dict(os.environ, env):
            from llm_provider import get_provider
            provider = get_provider()

            with patch("llm_provider.urllib.request.urlopen", side_effect=Exception("conn refused")):
                assert provider.health_check() is False

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
