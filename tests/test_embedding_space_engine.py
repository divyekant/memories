"""Engine integration tests for explicit embedding spaces.

Uses a fake embedder (no model download) and the engine's embedded local
Qdrant (temp dir), following the patterns in test_memory_engine.py.
"""

import json

import numpy as np
import pytest

import memory_engine as memory_engine_module
from embedding_space import EmbeddingSpaceMismatchError


class DummyEmbedder:
    def __init__(self, dim=8):
        self._dim = dim

    def get_sentence_embedding_dimension(self):
        return self._dim

    def encode(self, sentences, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]
        out = np.zeros((len(sentences), self._dim), dtype=np.float32)
        for i, text in enumerate(sentences):
            seed = abs(hash(text)) % (2**32)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(self._dim).astype(np.float32)
            out[i] = vec / max(np.linalg.norm(vec), 1e-9)
        return out

    def close(self):
        pass


def _patch_embedder(monkeypatch, dim=8):
    monkeypatch.setattr(
        memory_engine_module.MemoryEngine,
        "_make_embedder",
        lambda self: DummyEmbedder(dim=dim),
    )


def _clear_embed_env(monkeypatch):
    for var in (
        "EMBED_PROVIDER", "EMBED_MODEL", "EMBED_BASE_URL", "EMBED_API_KEY",
        "EMBED_DIMENSION", "EMBED_COLLECTION", "EMBED_QUERY_PREFIX",
        "EMBED_DOC_PREFIX", "EMBED_ALLOW_SPACE_REBIND", "QDRANT_COLLECTION",
        "MODEL_NAME", "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


class TestCollectionNaming:
    def test_legacy_default_uses_base_collection(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        _patch_embedder(monkeypatch, dim=384)
        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        assert engine.qdrant_settings.collection == "memories"

    def test_non_default_model_gets_explicit_collection(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.setenv("EMBED_MODEL", "test-model")
        _patch_embedder(monkeypatch, dim=8)
        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        assert engine.qdrant_settings.collection == "memories__test_model_8d"
        assert engine.config["embedding_signature"] == "openai:test-model:8d"

    def test_embed_collection_pin_wins(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.setenv("EMBED_MODEL", "test-model")
        monkeypatch.setenv("EMBED_COLLECTION", "pinned_space")
        _patch_embedder(monkeypatch, dim=8)
        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        assert engine.qdrant_settings.collection == "pinned_space"


class TestSpaceRegistryGuard:
    def test_registry_written_on_init(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        _patch_embedder(monkeypatch, dim=384)
        data_dir = tmp_path / "data"
        memory_engine_module.MemoryEngine(data_dir=str(data_dir))
        registry = json.loads((data_dir / "embedding_spaces.json").read_text())
        assert registry["collections"]["memories"]["signature"] == "onnx:all-MiniLM-L6-v2:384d"

    def test_same_dim_model_swap_on_pinned_collection_refused(self, tmp_path, monkeypatch):
        """Same dimension, different model: the silent-mixing case the guard exists for."""
        _clear_embed_env(monkeypatch)
        data_dir = tmp_path / "data"
        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.setenv("EMBED_COLLECTION", "pinned_space")
        monkeypatch.setenv("EMBED_MODEL", "model-a")
        _patch_embedder(monkeypatch, dim=8)
        memory_engine_module.MemoryEngine(data_dir=str(data_dir))

        monkeypatch.setenv("EMBED_MODEL", "model-b")
        with pytest.raises(EmbeddingSpaceMismatchError):
            memory_engine_module.MemoryEngine(data_dir=str(data_dir))

    def test_rebind_env_allows_swap(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        data_dir = tmp_path / "data"
        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.setenv("EMBED_COLLECTION", "pinned_space")
        monkeypatch.setenv("EMBED_MODEL", "model-a")
        _patch_embedder(monkeypatch, dim=8)
        memory_engine_module.MemoryEngine(data_dir=str(data_dir))

        monkeypatch.setenv("EMBED_MODEL", "model-b")
        monkeypatch.setenv("EMBED_ALLOW_SPACE_REBIND", "1")
        engine = memory_engine_module.MemoryEngine(data_dir=str(data_dir))
        assert engine.config["embedding_signature"] == "openai:model-b:8d"

    def test_declared_dimension_mismatch_fails_fast(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        monkeypatch.setenv("EMBED_DIMENSION", "16")
        _patch_embedder(monkeypatch, dim=8)
        with pytest.raises(RuntimeError, match="EMBED_DIMENSION"):
            memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))


class TestEncodePrefixes:
    def test_document_and_query_prefixes_applied(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        monkeypatch.setenv("EMBED_QUERY_PREFIX", "search_query: ")
        monkeypatch.setenv("EMBED_DOC_PREFIX", "search_document: ")

        seen = []

        class RecordingEmbedder(DummyEmbedder):
            def encode(self, sentences, **kwargs):
                seen.extend(sentences if isinstance(sentences, list) else [sentences])
                return super().encode(sentences, **kwargs)

        monkeypatch.setattr(
            memory_engine_module.MemoryEngine,
            "_make_embedder",
            lambda self: RecordingEmbedder(dim=8),
        )
        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        engine.add_memories(texts=["hello world"], sources=["t.md"])
        engine.search("hello", k=1)

        assert any(s.startswith("search_document: ") for s in seen)
        assert any(s.startswith("search_query: ") for s in seen)

    def test_no_prefixes_by_default(self, tmp_path, monkeypatch):
        _clear_embed_env(monkeypatch)
        seen = []

        class RecordingEmbedder(DummyEmbedder):
            def encode(self, sentences, **kwargs):
                seen.extend(sentences if isinstance(sentences, list) else [sentences])
                return super().encode(sentences, **kwargs)

        monkeypatch.setattr(
            memory_engine_module.MemoryEngine,
            "_make_embedder",
            lambda self: RecordingEmbedder(dim=8),
        )
        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        engine.add_memories(texts=["plain text"], sources=["t.md"])
        assert "plain text" in seen
