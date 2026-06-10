"""Tests for embedding space config: settings, signatures, collection naming, registry."""

import json

import pytest

from embedding_space import (
    DEFAULT_ONNX_MODEL,
    EmbedderSettings,
    EmbeddingSpaceMismatchError,
    EmbeddingSpaceRegistry,
    embedding_signature,
    model_slug,
)


class TestModelSlug:
    def test_simple_model(self):
        assert model_slug("all-MiniLM-L6-v2") == "all_minilm_l6_v2"

    def test_hf_repo_id(self):
        assert model_slug("nomic-ai/nomic-embed-text-v1.5") == "nomic_ai_nomic_embed_text_v1_5"

    def test_collapses_runs_and_strips(self):
        assert model_slug("__Qwen3--Embedding..0.6B__") == "qwen3_embedding_0_6b"


class TestSignature:
    def test_basic_signature(self):
        assert embedding_signature("onnx", "all-MiniLM-L6-v2", 384) == "onnx:all-MiniLM-L6-v2:384d"

    def test_prefixes_change_signature(self):
        plain = embedding_signature("onnx", "m", 768)
        prefixed = embedding_signature("onnx", "m", 768, document_prefix="search_document: ")
        assert plain != prefixed
        assert plain in prefixed.split("+")[0]

    def test_same_prefixes_same_signature(self):
        a = embedding_signature("openai", "m", 768, query_prefix="q: ", document_prefix="d: ")
        b = embedding_signature("openai", "m", 768, query_prefix="q: ", document_prefix="d: ")
        assert a == b


class TestEmbedderSettingsFromEnv:
    def test_defaults(self, monkeypatch):
        for var in (
            "EMBED_PROVIDER", "EMBED_MODEL", "EMBED_BASE_URL", "EMBED_API_KEY",
            "EMBED_DIMENSION", "EMBED_COLLECTION", "EMBED_QUERY_PREFIX",
            "EMBED_DOC_PREFIX", "EMBED_ALLOW_SPACE_REBIND",
        ):
            monkeypatch.delenv(var, raising=False)
        settings = EmbedderSettings.from_env()
        assert settings.provider == "onnx"
        assert settings.model == DEFAULT_ONNX_MODEL
        assert settings.base_url == ""
        assert settings.declared_dim is None
        assert settings.allow_space_rebind is False

    def test_openai_default_model(self, monkeypatch):
        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.delenv("EMBED_MODEL", raising=False)
        settings = EmbedderSettings.from_env()
        assert settings.provider == "openai"
        assert settings.model == "text-embedding-3-small"

    def test_omlx_style_env(self, monkeypatch):
        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.setenv("EMBED_MODEL", "Qwen3-Embedding-0.6B")
        monkeypatch.setenv("EMBED_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("EMBED_DIMENSION", "1024")
        settings = EmbedderSettings.from_env()
        assert settings.base_url == "http://localhost:11434/v1"
        assert settings.declared_dim == 1024

    def test_invalid_dimension_ignored(self, monkeypatch):
        monkeypatch.setenv("EMBED_DIMENSION", "not-a-number")
        settings = EmbedderSettings.from_env()
        assert settings.declared_dim is None

    def test_custom_default_onnx_model(self, monkeypatch):
        monkeypatch.delenv("EMBED_PROVIDER", raising=False)
        monkeypatch.delenv("EMBED_MODEL", raising=False)
        settings = EmbedderSettings.from_env(default_onnx_model="bge-small-en-v1.5")
        assert settings.model == "bge-small-en-v1.5"


class TestResolveCollection:
    def _settings(self, **kw):
        defaults = dict(provider="onnx", model=DEFAULT_ONNX_MODEL)
        defaults.update(kw)
        return EmbedderSettings(**defaults)

    def test_legacy_default_keeps_base_name(self):
        settings = self._settings()
        assert settings.resolve_collection("memories", 384) == "memories"

    def test_non_default_model_gets_explicit_name(self):
        settings = self._settings(provider="openai", model="Qwen3-Embedding-0.6B")
        assert settings.resolve_collection("memories", 1024) == "memories__qwen3_embedding_0_6b_1024d"

    def test_non_default_onnx_model_gets_explicit_name(self):
        settings = self._settings(model="nomic-ai/nomic-embed-text-v1.5")
        assert (
            settings.resolve_collection("memories", 768)
            == "memories__nomic_ai_nomic_embed_text_v1_5_768d"
        )

    def test_prefixes_on_legacy_model_break_legacy_passthrough(self):
        settings = self._settings(document_prefix="search_document: ")
        name = settings.resolve_collection("memories", 384)
        assert name != "memories"
        assert name.startswith("memories__")

    def test_collection_override_wins(self):
        settings = self._settings(
            provider="openai", model="anything", collection_override="pinned"
        )
        assert settings.resolve_collection("memories", 768) == "pinned"


class TestEmbeddingSpaceRegistry:
    def test_adopts_then_matches(self, tmp_path):
        registry = EmbeddingSpaceRegistry(tmp_path / "spaces.json")
        assert registry.check_and_record("memories", "onnx:m:384d") == "adopted"
        assert registry.check_and_record("memories", "onnx:m:384d") == "match"

    def test_mismatch_refuses(self, tmp_path):
        registry = EmbeddingSpaceRegistry(tmp_path / "spaces.json")
        registry.check_and_record("memories", "onnx:m:384d")
        with pytest.raises(EmbeddingSpaceMismatchError):
            registry.check_and_record("memories", "openai:other:384d")

    def test_mismatch_persists_across_instances(self, tmp_path):
        path = tmp_path / "spaces.json"
        EmbeddingSpaceRegistry(path).check_and_record("memories", "onnx:m:384d")
        with pytest.raises(EmbeddingSpaceMismatchError):
            EmbeddingSpaceRegistry(path).check_and_record("memories", "onnx:m2:384d")

    def test_rebind_allowed_updates_signature(self, tmp_path):
        path = tmp_path / "spaces.json"
        registry = EmbeddingSpaceRegistry(path)
        registry.check_and_record("memories", "onnx:m:384d")
        status = registry.check_and_record(
            "memories", "openai:new:384d", allow_rebind=True
        )
        assert status == "rebound"
        assert registry.check_and_record("memories", "openai:new:384d") == "match"
        data = json.loads(path.read_text())
        assert data["collections"]["memories"]["signature"] == "openai:new:384d"

    def test_independent_collections(self, tmp_path):
        registry = EmbeddingSpaceRegistry(tmp_path / "spaces.json")
        registry.check_and_record("memories", "onnx:m:384d")
        assert registry.check_and_record("memories__x_768d", "onnx:x:768d") == "adopted"

    def test_corrupt_registry_is_quarantined(self, tmp_path):
        path = tmp_path / "spaces.json"
        path.write_text("{not json", encoding="utf-8")
        registry = EmbeddingSpaceRegistry(path)
        assert registry.check_and_record("memories", "onnx:m:384d") == "adopted"
        quarantined = list(tmp_path.glob("spaces.json.corrupt-*"))
        assert len(quarantined) == 1

    def test_signature_of(self, tmp_path):
        registry = EmbeddingSpaceRegistry(tmp_path / "spaces.json")
        assert registry.signature_of("memories") is None
        registry.check_and_record("memories", "onnx:m:384d")
        assert registry.signature_of("memories") == "onnx:m:384d"
