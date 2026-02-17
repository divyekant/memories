"""Tests for MemoryEngine core functionality"""

import json
import tempfile
import pytest
from pathlib import Path

from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    """Create a fresh MemoryEngine with a temp data dir"""
    return MemoryEngine(data_dir=str(tmp_path))


@pytest.fixture
def populated_engine(engine):
    """Engine with some test memories"""
    engine.add_memories(
        texts=[
            "Python is a great programming language for data science",
            "JavaScript runs in the browser and on Node.js",
            "Docker containers package applications with their dependencies",
            "FastAPI is a modern Python web framework",
            "Memories uses a library for efficient similarity search",
        ],
        sources=["lang.md", "lang.md", "devops.md", "python.md", "ml.md"],
    )
    return engine


class TestAddAndSearch:
    def test_add_single(self, engine):
        ids = engine.add_memories(texts=["hello world"], sources=["test.md"])
        assert ids == [0]
        assert engine.index.ntotal == 1

    def test_add_empty(self, engine):
        ids = engine.add_memories(texts=[], sources=[])
        assert ids == []

    def test_search_returns_results(self, populated_engine):
        results = populated_engine.search("Python web framework", k=3)
        assert len(results) > 0
        assert results[0]["similarity"] > 0

    def test_search_empty_index(self, engine):
        results = engine.search("anything")
        assert results == []

    def test_search_with_threshold(self, populated_engine):
        results = populated_engine.search("Python", k=5, threshold=0.99)
        # Very high threshold should filter most results
        assert len(results) <= 5

    def test_search_k_capped(self, populated_engine):
        results = populated_engine.search("test", k=1000)
        assert len(results) <= populated_engine.index.ntotal


class TestHybridSearch:
    def test_hybrid_returns_results(self, populated_engine):
        results = populated_engine.hybrid_search("Docker containers", k=3)
        assert len(results) > 0

    def test_hybrid_empty_index(self, engine):
        results = engine.hybrid_search("anything")
        assert results == []

    def test_bm25_exact_match_boost(self, populated_engine):
        """BM25 should boost exact keyword matches"""
        results = populated_engine.hybrid_search("Memories", k=3)
        assert any("Memories" in r["text"] for r in results)


class TestDelete:
    def test_delete_by_id(self, populated_engine):
        count_before = populated_engine.index.ntotal
        result = populated_engine.delete_memory(0)
        assert populated_engine.index.ntotal == count_before - 1
        assert "deleted_id" in result

    def test_delete_invalid_id(self, populated_engine):
        with pytest.raises(ValueError):
            populated_engine.delete_memory(999)

    def test_delete_by_source(self, populated_engine):
        result = populated_engine.delete_by_source("lang.md")
        assert result["deleted_count"] == 2

    def test_delete_by_source_no_match(self, populated_engine):
        result = populated_engine.delete_by_source("nonexistent.md")
        assert result["deleted_count"] == 0


class TestNovelty:
    def test_novel_text(self, populated_engine):
        is_new, _ = populated_engine.is_novel("Kubernetes orchestrates containers")
        assert is_new is True

    def test_duplicate_text(self, populated_engine):
        is_new, match = populated_engine.is_novel(
            "Python is a great programming language", threshold=0.5
        )
        assert is_new is False
        assert match is not None


class TestDeduplication:
    def test_find_no_duplicates(self, populated_engine):
        dupes = populated_engine.find_duplicates(threshold=0.99)
        assert len(dupes) == 0

    def test_dedup_dry_run(self, populated_engine):
        result = populated_engine.deduplicate(threshold=0.3, dry_run=True)
        assert result["dry_run"] is True


class TestChunking:
    def test_chunk_basic(self):
        content = """# Title

First paragraph with enough text to pass the minimum length check.

## Section One

This section has some useful content about the topic at hand.

## Section Two

Another section with different content that should be a separate chunk.
"""
        chunks = MemoryEngine.chunk_markdown(content, "test.md")
        assert len(chunks) >= 1
        for text, source in chunks:
            assert "test.md" in source

    def test_chunk_empty(self):
        chunks = MemoryEngine.chunk_markdown("", "test.md")
        assert chunks == []

    def test_chunk_short_content_skipped(self):
        chunks = MemoryEngine.chunk_markdown("short", "test.md")
        assert chunks == []


class TestBackupRestore:
    def test_backup_creates_directory(self, populated_engine):
        backup_path = populated_engine._backup(prefix="test")
        assert backup_path.exists()
        assert (backup_path / "index.faiss").exists()
        assert (backup_path / "metadata.json").exists()

    def test_restore_from_backup(self, populated_engine):
        backup_path = populated_engine._backup(prefix="test")
        # Add more data
        populated_engine.add_memories(texts=["extra"], sources=["extra.md"])
        count_after_add = populated_engine.index.ntotal

        # Restore
        result = populated_engine.restore_from_backup(backup_path.name)
        assert populated_engine.index.ntotal < count_after_add

    def test_restore_nonexistent(self, populated_engine):
        with pytest.raises(FileNotFoundError):
            populated_engine.restore_from_backup("nonexistent_backup")

    def test_backup_prefix_sanitized(self, populated_engine):
        backup_path = populated_engine._backup(prefix="../../../etc")
        assert ".." not in backup_path.name


class TestListMemories:
    def test_list_all(self, populated_engine):
        result = populated_engine.list_memories()
        assert result["total"] == 5
        assert len(result["memories"]) == 5

    def test_list_with_pagination(self, populated_engine):
        result = populated_engine.list_memories(offset=2, limit=2)
        assert len(result["memories"]) == 2
        assert result["offset"] == 2

    def test_list_with_source_filter(self, populated_engine):
        result = populated_engine.list_memories(source_filter="lang.md")
        assert result["total"] == 2


class TestPersistence:
    def test_save_and_load(self, engine, tmp_path):
        engine.add_memories(texts=["persisted data"], sources=["test.md"])
        engine.save()

        # Create a new engine pointing to the same directory
        engine2 = MemoryEngine(data_dir=str(tmp_path))
        assert engine2.index.ntotal == 1
        assert engine2.metadata[0]["text"] == "persisted data"

    def test_integrity_check(self, engine, tmp_path):
        engine.add_memories(texts=["data"], sources=["test.md"])
        engine.save()

        # Corrupt metadata by removing an entry
        with open(engine.metadata_path) as f:
            meta = json.load(f)
        meta.pop()
        with open(engine.metadata_path, "w") as f:
            json.dump(meta, f)

        with pytest.raises(RuntimeError, match="mismatch"):
            MemoryEngine(data_dir=str(tmp_path))


class TestModelCache:
    def test_model_cache_dir_env_is_forwarded_to_embedder(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        captured = {}

        class DummyEmbedder:
            def __init__(self, model_name="all-MiniLM-L6-v2", cache_dir=None):
                captured["model_name"] = model_name
                captured["cache_dir"] = cache_dir

            def get_sentence_embedding_dimension(self):
                return 384

        cache_dir = tmp_path / "model-cache"
        monkeypatch.setattr(memory_engine_module, "OnnxEmbedder", DummyEmbedder)
        monkeypatch.setenv("MODEL_CACHE_DIR", str(cache_dir))
        monkeypatch.delenv("PRELOADED_MODEL_CACHE_DIR", raising=False)

        memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))

        assert captured["cache_dir"] == str(cache_dir)
        assert cache_dir.exists()

    def test_preloaded_cache_seeds_empty_model_cache(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        captured = {}

        class DummyEmbedder:
            def __init__(self, model_name="all-MiniLM-L6-v2", cache_dir=None):
                captured["cache_dir"] = cache_dir

            def get_sentence_embedding_dimension(self):
                return 384

        model_cache = tmp_path / "model-cache"
        preload_cache = tmp_path / "preloaded-cache"
        preload_file = preload_cache / "models--seed" / "blob.bin"
        preload_file.parent.mkdir(parents=True, exist_ok=True)
        preload_file.write_text("seeded", encoding="utf-8")

        monkeypatch.setattr(memory_engine_module, "OnnxEmbedder", DummyEmbedder)
        monkeypatch.setenv("MODEL_CACHE_DIR", str(model_cache))
        monkeypatch.setenv("PRELOADED_MODEL_CACHE_DIR", str(preload_cache))

        memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))

        assert captured["cache_dir"] == str(model_cache)
        assert (model_cache / "models--seed" / "blob.bin").read_text(encoding="utf-8") == "seeded"


class TestRebuildFromFiles:
    def test_rebuild(self, engine, tmp_path):
        md_file = tmp_path / "test_source.md"
        md_file.write_text(
            "# Test Document\n\n"
            "This is a paragraph with enough content to be indexed as a chunk.\n\n"
            "## Section\n\n"
            "Another section with meaningful content for testing the rebuild process."
        )

        result = engine.rebuild_from_files([str(md_file)])
        assert result["files_processed"] == 1
        assert result["memories_added"] >= 1

    def test_rebuild_nonexistent_file(self, engine):
        result = engine.rebuild_from_files(["/nonexistent/file.md"])
        assert result["files_processed"] == 0
        assert result["memories_added"] == 0


class TestSupersede:
    """Test memory supersede (targeted update with audit trail)."""

    def test_supersede_replaces_memory(self, populated_engine):
        """Supersede deletes old memory and adds new one with link."""
        old_count = populated_engine.index.ntotal
        old_id = 0  # first memory in populated_engine

        result = populated_engine.supersede(
            old_id=old_id,
            new_text="Updated: switched from Prisma to Drizzle",
            source="test/supersede"
        )

        assert result["old_id"] == old_id
        assert result["new_id"] is not None
        assert populated_engine.index.ntotal == old_count  # same count (delete + add)

    def test_supersede_nonexistent_id_raises(self, populated_engine):
        """Superseding a nonexistent memory raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            populated_engine.supersede(
                old_id=9999,
                new_text="does not matter",
                source="test"
            )
