"""Tests for shadow_runner module."""
import os
import pytest
from unittest.mock import patch, MagicMock


class TestParseShadowProviders:
    def test_empty_string_returns_empty_list(self):
        from shadow_runner import parse_shadow_providers
        assert parse_shadow_providers("") == []

    def test_single_entry_with_model(self):
        from shadow_runner import parse_shadow_providers, ShadowProviderConfig
        result = parse_shadow_providers("ollama:qwen-2.5-3b")
        assert result == [ShadowProviderConfig(provider_name="ollama", model="qwen-2.5-3b")]

    def test_multiple_entries_comma_separated(self):
        from shadow_runner import parse_shadow_providers, ShadowProviderConfig
        result = parse_shadow_providers("ollama:qwen-2.5-3b,ollama:gemma-3-4b")
        assert result == [
            ShadowProviderConfig(provider_name="ollama", model="qwen-2.5-3b"),
            ShadowProviderConfig(provider_name="ollama", model="gemma-3-4b"),
        ]

    def test_whitespace_around_entries_is_stripped(self):
        from shadow_runner import parse_shadow_providers, ShadowProviderConfig
        result = parse_shadow_providers("  ollama:qwen-2.5-3b  ,  ollama:gemma-3-4b  ")
        assert result == [
            ShadowProviderConfig(provider_name="ollama", model="qwen-2.5-3b"),
            ShadowProviderConfig(provider_name="ollama", model="gemma-3-4b"),
        ]

    def test_entry_without_model_has_none_model(self):
        from shadow_runner import parse_shadow_providers, ShadowProviderConfig
        result = parse_shadow_providers("ollama")
        assert result == [ShadowProviderConfig(provider_name="ollama", model=None)]

    def test_blank_entries_are_skipped(self):
        from shadow_runner import parse_shadow_providers, ShadowProviderConfig
        result = parse_shadow_providers("ollama:a,,ollama:b,")
        assert result == [
            ShadowProviderConfig(provider_name="ollama", model="a"),
            ShadowProviderConfig(provider_name="ollama", model="b"),
        ]


class TestBuildShadowProviders:
    def test_unset_env_returns_empty_list(self):
        from shadow_runner import build_shadow_providers
        with patch.dict(os.environ, {}, clear=True):
            assert build_shadow_providers() == []

    def test_empty_env_returns_empty_list(self):
        from shadow_runner import build_shadow_providers
        with patch.dict(os.environ, {"SHADOW_PROVIDERS": ""}):
            assert build_shadow_providers() == []

    def test_single_omlx_shadow_returns_provider(self):
        from shadow_runner import build_shadow_providers
        from llm_provider import OMLXProvider
        env = {"SHADOW_PROVIDERS": "omlx:fplv2"}
        with patch.dict(os.environ, env, clear=True):
            with patch.dict("sys.modules", {"openai": MagicMock()}):
                providers = build_shadow_providers()
        assert len(providers) == 1
        assert isinstance(providers[0], OMLXProvider)
        assert providers[0].model == "fplv2"
        assert providers[0].base_url == "http://localhost:11434/v1"

    def test_omlx_uses_shadow_url_override(self):
        from shadow_runner import build_shadow_providers
        env = {
            "SHADOW_PROVIDERS": "omlx:fplv2",
            "SHADOW_OMLX_URL": "http://10.0.0.5:11434/v1",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch.dict("sys.modules", {"openai": MagicMock()}):
                providers = build_shadow_providers()
        assert providers[0].base_url == "http://10.0.0.5:11434/v1"

    def test_ollama_shadow_still_works(self):
        from shadow_runner import build_shadow_providers
        from llm_provider import OllamaProvider
        env = {"SHADOW_PROVIDERS": "ollama:qwen2.5:3b"}
        with patch.dict(os.environ, env, clear=True):
            providers = build_shadow_providers()
        assert len(providers) == 1
        assert isinstance(providers[0], OllamaProvider)
        assert providers[0].model == "qwen2.5:3b"

    def test_multiple_mixed_providers(self):
        from shadow_runner import build_shadow_providers
        from llm_provider import OMLXProvider, OllamaProvider
        env = {"SHADOW_PROVIDERS": "omlx:fplv2,omlx:gemma26b-3bit,ollama:qwen2.5:3b"}
        with patch.dict(os.environ, env, clear=True):
            with patch.dict("sys.modules", {"openai": MagicMock()}):
                providers = build_shadow_providers()
        assert len(providers) == 3
        assert isinstance(providers[0], OMLXProvider) and providers[0].model == "fplv2"
        assert isinstance(providers[1], OMLXProvider) and providers[1].model == "gemma26b-3bit"
        assert isinstance(providers[2], OllamaProvider) and providers[2].model == "qwen2.5:3b"

    def test_unsupported_provider_is_skipped_not_raised(self):
        from shadow_runner import build_shadow_providers
        from llm_provider import OMLXProvider
        env = {"SHADOW_PROVIDERS": "anthropic:claude-haiku,omlx:fplv2"}
        with patch.dict(os.environ, env, clear=True):
            with patch.dict("sys.modules", {"openai": MagicMock()}):
                providers = build_shadow_providers()
        assert len(providers) == 1
        assert isinstance(providers[0], OMLXProvider)
