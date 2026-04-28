"""Tests for shadow_runner module."""
import json
import os
import threading
import pytest
from pathlib import Path
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


class TestWriteShadowLog:
    def test_writes_single_jsonl_line(self, tmp_path):
        from shadow_runner import write_shadow_log
        write_shadow_log(str(tmp_path), "qwen-2.5-3b", {"k": "v", "n": 1})
        log_file = tmp_path / "memories-shadow-qwen-2.5-3b.log"
        assert log_file.exists()
        content = log_file.read_text().strip()
        assert json.loads(content) == {"k": "v", "n": 1}

    def test_appends_multiple_lines(self, tmp_path):
        from shadow_runner import write_shadow_log
        write_shadow_log(str(tmp_path), "m", {"i": 1})
        write_shadow_log(str(tmp_path), "m", {"i": 2})
        write_shadow_log(str(tmp_path), "m", {"i": 3})
        lines = (tmp_path / "memories-shadow-m.log").read_text().strip().split("\n")
        assert [json.loads(l)["i"] for l in lines] == [1, 2, 3]

    def test_concurrent_writes_produce_intact_lines(self, tmp_path):
        """Each write must result in a complete, parseable JSONL line."""
        from shadow_runner import write_shadow_log

        big_value = "x" * 8000

        def writer(idx):
            for i in range(20):
                write_shadow_log(str(tmp_path), "m", {"writer": idx, "i": i, "pad": big_value})

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        lines = (tmp_path / "memories-shadow-m.log").read_text().strip().split("\n")
        assert len(lines) == 8 * 20
        for line in lines:
            parsed = json.loads(line)
            assert parsed["pad"] == big_value
            assert "writer" in parsed and "i" in parsed

    def test_sanitizes_model_name_for_filename(self, tmp_path):
        """Model names with slashes must not create subdirs."""
        from shadow_runner import write_shadow_log
        write_shadow_log(str(tmp_path), "mlx-community/qwen-2.5-3b", {"k": "v"})
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].is_file()
        assert "/" not in files[0].name
