"""Tests for shadow_runner module."""
import pytest


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
