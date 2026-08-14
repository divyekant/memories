"""Tests for MemoryEngine core functionality"""

import json
import tempfile
import threading
import pytest
from pathlib import Path

from memory_engine import MemoryEngine
from project_memory import ProjectMemoryPolicyError, TrustedAuthorship
from project_promotion import (
    PromotionMode,
    PromotionProposal,
    PromotionState,
    PromotionStatus,
)


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
        assert engine.stats_light()["total_memories"] == 1

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
        assert len(results) <= populated_engine.stats_light()["total_memories"]

    def test_hybrid_search_ranks_one_allowed_prefix_union(self, engine):
        engine.add_memories(
            texts=[
                "deployment decision uses port 9000",
                "private note confirms deployment port 9000",
                "unrelated namespace also mentions deployment port 9000",
            ],
            sources=[
                "codex/shared/decisions",
                "person/alice/shared/knowledge",
                "codex/other/knowledge",
            ],
        )

        results = engine.hybrid_search(
            "deployment port 9000",
            k=10,
            allowed_prefixes=[
                "codex/shared/decisions",
                "person/alice/shared",
            ],
            graph_weight=0.1,
        )

        assert {result["source"] for result in results} == {
            "codex/shared/decisions",
            "person/alice/shared/knowledge",
        }

    def test_hybrid_search_explain_honors_allowed_prefix_union(self, engine):
        engine.add_memories(
            texts=[
                "shared deployment decision",
                "private deployment detail",
                "unrelated deployment detail",
            ],
            sources=[
                "codex/shared/decisions",
                "person/alice/shared/knowledge",
                "codex/other/knowledge",
            ],
        )

        explained = engine.hybrid_search_explain(
            "deployment",
            k=10,
            allowed_prefixes=[
                "codex/shared",
                "person/alice/shared",
            ],
            graph_weight=0.1,
        )

        assert {result["source"] for result in explained["results"]} == {
            "codex/shared/decisions",
            "person/alice/shared/knowledge",
        }

    def test_add_sets_created_at_and_updated_at(self, engine):
        ids = engine.add_memories(["timestamp test"], ["test/ts"])
        meta = engine.metadata[ids[0]]
        assert "created_at" in meta
        assert "updated_at" in meta
        assert meta["created_at"] == meta["updated_at"]
        # Backward compat alias
        assert "timestamp" in meta
        assert meta["timestamp"] == meta["created_at"]

    def test_project_source_requires_trusted_authorship(self, engine):
        with pytest.raises(ProjectMemoryPolicyError):
            engine.add_memories(
                texts=["shared fact"],
                sources=["project/fplguru/knowledge"],
            )

        assert engine.metadata == []
        assert engine.qdrant_store.count() == 0

    def test_typed_trusted_promotion_is_persisted_but_caller_metadata_is_ignored(self, engine):
        proposal = PromotionProposal(
            project_relevance=0.95,
            visibility="project",
            assertion_status="confirmed",
            project_kind="knowledge",
            confidence=0.9,
            reason="durable project fact",
            classifier_version="classifier-v1",
        )
        state = PromotionState(
            status=PromotionStatus.CANDIDATE,
            owner="alice",
            project_id="demo",
            declaration_fingerprint="a" * 64,
            classifier_provider="anthropic",
            classifier_model="claude-haiku",
            reviewer_provider="anthropic",
            reviewer_model="claude-haiku",
            capture_mode=PromotionMode.AUTO,
            route="ordinary",
            proposal=proposal,
            review=None,
            evidence_fingerprint="b" * 64,
            captured_at="2026-08-14T12:00:00+00:00",
        )
        ids = engine.add_memories(
            texts=["The project uses Qdrant."],
            sources=["person/alice/demo/knowledge"],
            metadata_list=[{"promotion": {"status": "promoted"}}],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            trusted_promotion=state,
        )

        stored = engine.get_memory(ids[0])
        assert stored["promotion"]["status"] == "candidate"
        engine.update_memory(
            ids[0],
            metadata_patch={"promotion": {"status": "promoted"}},
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            trusted_promotion=state,
        )
        assert engine.get_memory(ids[0])["promotion"]["status"] == "candidate"

    def test_client_cannot_override_trusted_authorship_metadata(self, engine):
        ids = engine.add_memories(
            texts=["shared fact"],
            sources=["project/fplguru/knowledge"],
            metadata_list=[
                {
                    "author": "mallory",
                    "contributors": ["mallory"],
                    "origin_client": "  spoofed-client ",
                    "source_memory_ids": [999],
                    "custom": "kept",
                }
            ],
            trusted_authorship=TrustedAuthorship.principal("alice", " Codex "),
        )

        meta = engine.metadata[ids[0]]
        assert meta["author"] == "alice"
        assert meta["origin_client"] == "codex"
        assert "contributors" not in meta
        assert "source_memory_ids" not in meta
        assert meta["custom"] == "kept"

    def test_system_authorship_stamps_contributors_and_source_memory_ids(self, engine):
        ids = engine.add_memories(
            texts=["derived shared fact"],
            sources=["project/fplguru/knowledge"],
            metadata_list=[{"author": "mallory", "origin_client": "bad"}],
            trusted_authorship=TrustedAuthorship.system(
                contributors=["alice", "bob"],
                source_memory_ids=[11, 12],
                origin_client="hook",
            ),
        )

        meta = engine.metadata[ids[0]]
        assert meta["author"] == "system"
        assert meta["contributors"] == ["alice", "bob"]
        assert meta["source_memory_ids"] == [11, 12]
        assert meta["origin_client"] == "hook"

    def test_malformed_reserved_project_source_is_rejected(self, engine):
        with pytest.raises(ProjectMemoryPolicyError, match="project sources must be"):
            engine.add_memories(
                texts=["legacy-looking fact"],
                sources=["project/fplguru/custom"],
                metadata_list=[{"origin_client": "  unknown-client "}],
            )


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
        count_before = populated_engine.stats_light()["total_memories"]
        result = populated_engine.delete_memory(0)
        assert populated_engine.stats_light()["total_memories"] == count_before - 1
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

    def test_delete_memories_batch(self, populated_engine):
        count_before = populated_engine.stats_light()["total_memories"]
        result = populated_engine.delete_memories([0, 2, 999])
        assert result["deleted_count"] == 2
        assert result["deleted_ids"] == [0, 2]
        assert result["missing_ids"] == [999]
        assert populated_engine.stats_light()["total_memories"] == count_before - 2

    @pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
    def test_delete_retries_with_stable_id_and_current_source_locks(
        self, populated_engine, monkeypatch, bulk
    ):
        """A source move before lock acquisition must invalidate the lock snapshot."""
        from contextlib import contextmanager

        memory_id = 0
        old_key = populated_engine._entity_key(
            populated_engine._get_meta_by_id(memory_id).get("source", "")
        )
        new_source = "moved/project"
        new_key = populated_engine._entity_key(new_source)
        memory_key = populated_engine._memory_key(memory_id)
        original_acquire = populated_engine._entity_locks.acquire_many
        acquired = []
        raced = False

        @contextmanager
        def racing_acquire(keys):
            nonlocal raced
            normalized = set(keys)
            acquired.append(normalized)
            if not raced and old_key in normalized:
                raced = True
                populated_engine._get_meta_by_id(memory_id)["source"] = new_source
            with original_acquire(keys):
                yield

        monkeypatch.setattr(
            populated_engine._entity_locks, "acquire_many", racing_acquire
        )

        if bulk:
            result = populated_engine.delete_memories([memory_id])
            assert result["deleted_ids"] == [memory_id]
        else:
            result = populated_engine.delete_memory(memory_id)
            assert result["deleted_id"] == memory_id

        assert raced
        assert any({memory_key, new_key}.issubset(keys) for keys in acquired)
        assert not populated_engine._id_exists(memory_id)

    def test_delete_by_prefix(self, populated_engine):
        result = populated_engine.delete_by_prefix("lang")
        assert result["deleted_count"] == 2


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

    def test_exact_source_filter_scopes_search_and_novelty(self, populated_engine):
        query = "Python is a great programming language"
        scoped = populated_engine.search(query, k=5, source_exact="lang.md")
        assert scoped
        assert all(result["source"] == "lang.md" for result in scoped)

        is_new, match = populated_engine.is_novel(
            query,
            threshold=0.5,
            source_exact="other/source",
        )
        assert is_new is True
        assert match is None

    def test_trusted_dedup_does_not_cross_exact_sources(self, engine):
        alice = TrustedAuthorship.principal("alice")
        bob = TrustedAuthorship.principal("bob")
        engine.add_memories(
            ["The shared deployment decision"],
            ["project/acme/decisions"],
            trusted_authorship=alice,
        )

        added = engine.add_memories(
            ["The shared deployment decision"],
            ["project/other/decisions"],
            deduplicate=True,
            trusted_authorship=bob,
        )

        assert added
        assert engine._get_meta_by_id(added[0])["source"] == "project/other/decisions"


class TestFetchAndUpsert:
    def test_get_memory(self, populated_engine):
        mem = populated_engine.get_memory(0)
        assert mem["id"] == 0

    def test_get_memories(self, populated_engine):
        result = populated_engine.get_memories([0, 999])
        assert len(result["memories"]) == 1
        assert result["missing_ids"] == [999]

    def test_update_memory(self, populated_engine):
        result = populated_engine.update_memory(0, text="Updated memory text")
        assert result["id"] == 0
        assert "text" in result["updated_fields"]
        assert populated_engine.get_memory(0)["text"] == "Updated memory text"

    def test_update_memory_source_only_fast_path(self, populated_engine):
        """Source-only update should skip backup and re-embedding."""
        old_text = populated_engine.get_memory(0)["text"]
        backup_count_before = len(list(Path(populated_engine.data_dir).glob("backups/*")))
        result = populated_engine.update_memory(0, source="new-source/path")
        assert result["updated_fields"] == ["source"]
        mem = populated_engine.get_memory(0)
        assert mem["source"] == "new-source/path"
        assert mem["text"] == old_text  # text unchanged
        # Source-only fast path should NOT create a backup
        backup_count_after = len(list(Path(populated_engine.data_dir).glob("backups/*")))
        assert backup_count_after == backup_count_before

    def test_update_preserves_created_at(self, populated_engine):
        ids = populated_engine.add_memories(["will be updated"], ["test/ts"])
        original_created = populated_engine.metadata[ids[0]]["created_at"]
        import time; time.sleep(0.01)
        populated_engine.update_memory(ids[0], text="updated text")
        meta = populated_engine.metadata[ids[0]]
        assert meta["created_at"] == original_created
        assert meta["updated_at"] > original_created
        assert meta["timestamp"] == meta["created_at"]  # alias stays at creation

    def test_upsert_memory_create_then_update(self, populated_engine):
        created = populated_engine.upsert_memory(
            text="entity value",
            source="carto/poet-pads/db",
            key="entity-1",
            metadata={"owner": "carto"},
        )
        assert created["action"] == "created"

        updated = populated_engine.upsert_memory(
            text="entity value v2",
            source="carto/poet-pads/db",
            key="entity-1",
            metadata={"owner": "carto"},
        )
        assert updated["action"] == "updated"


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
        assert any(backup_path.iterdir())

    def test_restore_from_backup(self, populated_engine):
        backup_path = populated_engine._backup(prefix="test")
        # Add more data
        populated_engine.add_memories(texts=["extra"], sources=["extra.md"])
        count_after_add = populated_engine.stats_light()["total_memories"]

        # Restore
        populated_engine.restore_from_backup(backup_path.name)
        assert populated_engine.stats_light()["total_memories"] < count_after_add

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

    def test_list_and_count_with_source_boundary_exclude_sibling_prefix(self, engine):
        engine.add_memories(
            texts=["project", "sibling"],
            sources=["codex/shared/knowledge", "codex/shared-extra/knowledge"],
        )

        listed = engine.list_memories(source_filter="codex/shared", source_boundary=True)

        assert [memory["text"] for memory in listed["memories"]] == ["project"]
        assert engine.count_memories(source_prefix="codex/shared", source_boundary=True) == 1

    def test_filtered_count_can_include_archived_browse_rows(self, engine):
        ids = engine.add_memories(
            texts=["active", "archived"],
            sources=["codex/shared/knowledge", "codex/shared/knowledge"],
        )
        engine.update_memory(ids[1], archived=True)

        assert engine.count_by_filter(
            source_prefix="codex/shared",
            source_boundary=True,
            include_archived=True,
        ) == 2


class TestPersistence:
    def test_save_and_load(self, engine, tmp_path):
        engine.add_memories(texts=["persisted data"], sources=["test.md"])
        engine.save()

        # Create a new engine pointing to the same directory
        engine2 = MemoryEngine(data_dir=str(tmp_path))
        assert engine2.stats_light()["total_memories"] == 1
        listed = engine2.list_memories()
        assert listed["memories"][0]["text"] == "persisted data"

    def test_concurrent_saves_do_not_collide(self, engine):
        """A fixed tmp filename let parallel save() calls rename each other's
        tmp away mid-cycle: the loser crashed with FileNotFoundError (seen in
        production from the extraction worker) and the winner could install a
        half-written file."""
        engine.add_memories(texts=["seed"], sources=["test.md"])
        engine.config["_pad"] = "x" * (2 * 1024 * 1024)  # widen the write+fsync window

        errors = []

        def hammer():
            for _ in range(15):
                try:
                    engine.save()
                except OSError as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"concurrent save() raised: {errors[:3]}"
        with open(engine.metadata_path, encoding="utf-8") as f:
            json.load(f)
        with open(engine.config_path, encoding="utf-8") as f:
            json.load(f)

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

    def test_load_reindexes_when_embedding_dimension_changes(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        class DummyEmbedder:
            def get_sentence_embedding_dimension(self):
                return 1536

        class FakeQdrantStore:
            def __init__(self, *args, **kwargs):
                pass

            def ensure_collection(self, dim):
                return None

            def ensure_payload_indexes(self):
                return None

            def count(self, exact=True):
                return 1

            def get_collection_dimension(self):
                return 384

        called = {"reindexed": False}

        def fake_reindex(self):
            called["reindexed"] = True

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "metadata.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "text": "persisted data",
                        "source": "test.md",
                        "timestamp": "2026-02-18T00:00:00+00:00",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (data_dir / "config.json").write_text(
            json.dumps({"dimension": 384, "model": "all-MiniLM-L6-v2"}),
            encoding="utf-8",
        )

        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.setenv("EMBED_MODEL", "text-embedding-3-small")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(memory_engine_module, "QdrantStore", FakeQdrantStore)
        monkeypatch.setattr(
            memory_engine_module.MemoryEngine,
            "_make_embedder",
            lambda self: DummyEmbedder(),
        )
        monkeypatch.setattr(
            memory_engine_module.MemoryEngine,
            "_reindex_store_from_metadata",
            fake_reindex,
        )

        memory_engine_module.MemoryEngine(data_dir=str(data_dir))
        assert called["reindexed"] is True


class TestModelCache:
    def test_model_cache_dir_env_is_forwarded_to_embedder(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        captured = {}

        class DummyEmbedder:
            def get_sentence_embedding_dimension(self):
                return 384

        def fake_make_embedder(self):
            captured["model_name"] = self._active_embed_model()
            captured["cache_dir"] = self._embedder_cache_dir
            return DummyEmbedder()

        cache_dir = tmp_path / "model-cache"
        monkeypatch.setenv("EMBED_PROVIDER", "onnx")
        monkeypatch.setattr(memory_engine_module.MemoryEngine, "_make_embedder", fake_make_embedder)
        monkeypatch.setenv("MODEL_CACHE_DIR", str(cache_dir))
        monkeypatch.delenv("PRELOADED_MODEL_CACHE_DIR", raising=False)

        memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))

        assert captured["cache_dir"] == str(cache_dir)
        assert cache_dir.exists()

    def test_preloaded_cache_seeds_empty_model_cache(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        captured = {}

        class DummyEmbedder:
            def get_sentence_embedding_dimension(self):
                return 384

        def fake_make_embedder(self):
            captured["cache_dir"] = self._embedder_cache_dir
            return DummyEmbedder()

        model_cache = tmp_path / "model-cache"
        preload_cache = tmp_path / "preloaded-cache"
        preload_file = preload_cache / "models--seed" / "blob.bin"
        preload_file.parent.mkdir(parents=True, exist_ok=True)
        preload_file.write_text("seeded", encoding="utf-8")

        monkeypatch.setenv("EMBED_PROVIDER", "onnx")
        monkeypatch.setattr(memory_engine_module.MemoryEngine, "_make_embedder", fake_make_embedder)
        monkeypatch.setenv("MODEL_CACHE_DIR", str(model_cache))
        monkeypatch.setenv("PRELOADED_MODEL_CACHE_DIR", str(preload_cache))

        memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))

        assert captured["cache_dir"] == str(model_cache)
        assert (model_cache / "models--seed" / "blob.bin").read_text(encoding="utf-8") == "seeded"

    def test_embed_provider_defaults_to_onnx(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        class DummyEmbedder:
            def get_sentence_embedding_dimension(self):
                return 384

        monkeypatch.delenv("EMBED_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(memory_engine_module.MemoryEngine, "_make_embedder", lambda self: DummyEmbedder())

        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        assert engine._embed_provider == "onnx"

    def test_openai_provider_uses_embed_model_in_stats(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        class DummyEmbedder:
            def get_sentence_embedding_dimension(self):
                return 1536

        monkeypatch.setenv("EMBED_PROVIDER", "openai")
        monkeypatch.setenv("EMBED_MODEL", "text-embedding-3-small")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(memory_engine_module.MemoryEngine, "_make_embedder", lambda self: DummyEmbedder())

        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        assert engine.stats_light()["model"] == "text-embedding-3-small"


class TestEmbedderReload:
    def test_reload_embedder_swaps_instance_and_closes_old(self, tmp_path, monkeypatch):
        import memory_engine as memory_engine_module

        created = []

        class DummyEmbedder:
            def __init__(self):
                self.model_name = "all-MiniLM-L6-v2"
                self.cache_dir = None
                self.closed = False
                created.append(self)

            def get_sentence_embedding_dimension(self):
                return 384

            def close(self):
                self.closed = True

        monkeypatch.setenv("EMBED_PROVIDER", "onnx")
        monkeypatch.setattr(
            memory_engine_module.MemoryEngine,
            "_make_embedder",
            lambda self: DummyEmbedder(),
        )

        engine = memory_engine_module.MemoryEngine(data_dir=str(tmp_path / "data"))
        old_embedder = engine.model

        result = engine.reload_embedder()

        assert result["reloaded"] is True
        assert result["model"] == "all-MiniLM-L6-v2"
        assert result["dimension"] == 384
        assert old_embedder.closed is True
        assert engine.model is not old_embedder
        assert len(created) == 2


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
        """Supersede archives the old memory (never deletes) and adds the new one with a link."""
        old_count = populated_engine.stats_light()["total_memories"]
        old_id = 0  # first memory in populated_engine

        result = populated_engine.supersede(
            old_id=old_id,
            new_text="Updated: switched from Prisma to Drizzle",
            source="test/supersede"
        )

        assert result["old_id"] == old_id
        assert result["new_id"] is not None
        assert result["archived_old"] is True
        # archive + add: the original is preserved as history, so count grows
        assert populated_engine.stats_light()["total_memories"] == old_count + 1
        old_meta = populated_engine._get_meta_by_id(old_id)
        assert old_meta["archived"] is True
        assert old_meta["superseded_by"] == result["new_id"]

    def test_supersede_nonexistent_id_raises(self, populated_engine):
        """Superseding a nonexistent memory raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            populated_engine.supersede(
                old_id=9999,
                new_text="does not matter",
                source="test"
            )


class TestLegacyFaissCutover:
    def test_archives_legacy_faiss_and_writes_marker(self, engine):
        engine.add_memories(texts=["legacy migration"], sources=["legacy.md"])
        engine.index_path.write_text("legacy-faiss-bytes", encoding="utf-8")

        migrated = engine._finalize_legacy_faiss_cutover()

        assert migrated is True
        assert engine.faiss_migration_marker.exists()
        assert not engine.index_path.exists()
