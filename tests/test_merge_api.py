"""Tests for merge memories API — engine method and POST /memory/merge endpoint."""

from __future__ import annotations

import importlib
import os
import tempfile
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Engine-level tests (real MemoryEngine with mocked Qdrant)
# ---------------------------------------------------------------------------


class TestMergeMemoriesEngine:
    """Direct engine tests for merge_memories method."""

    @pytest.fixture
    def engine(self, tmp_path):
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 0
            mock_store.search.return_value = []
            mock_store.upsert_points.return_value = None
            mock_store.delete_points.return_value = None
            mock_store.set_payload.return_value = None
            MockStore.return_value = mock_store

            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            eng.add_memories(
                texts=["memory A", "memory B", "memory C"],
                sources=["test/src", "test/src", "test/src"],
            )
            return eng

    def test_merge_two_memories(self, engine):
        """merge_memories returns {id, archived} with correct shape."""
        result = engine.merge_memories(
            ids=[0, 1],
            merged_text="merged A and B",
            source="test/src",
        )
        assert "id" in result
        assert result["archived"] == [0, 1]
        assert isinstance(result["id"], int)

    def test_merge_creates_supersedes_links(self, engine):
        """New memory has supersedes links to each original."""
        result = engine.merge_memories(
            ids=[0, 1],
            merged_text="merged A and B",
            source="test/src",
        )
        new_id = result["id"]
        links = engine.get_links(new_id, link_type="supersedes")
        linked_to_ids = {lnk["to_id"] for lnk in links}
        assert 0 in linked_to_ids
        assert 1 in linked_to_ids

    def test_merge_archives_originals(self, engine):
        """Original memories are archived after merge."""
        engine.merge_memories(
            ids=[0, 1],
            merged_text="merged A and B",
            source="test/src",
        )
        meta_a = engine._get_meta_by_id(0)
        meta_b = engine._get_meta_by_id(1)
        assert meta_a.get("archived") is True
        assert meta_b.get("archived") is True

    def test_merge_three_memories(self, engine):
        """Merge of three memories archives all three."""
        result = engine.merge_memories(
            ids=[0, 1, 2],
            merged_text="merged A, B, and C",
            source="test/src",
        )
        assert sorted(result["archived"]) == [0, 1, 2]
        assert len(engine.get_links(result["id"], link_type="supersedes")) == 3

    def test_merge_requires_at_least_two(self, engine):
        """Single ID raises ValueError."""
        with pytest.raises(ValueError, match="at least 2"):
            engine.merge_memories(ids=[0], merged_text="alone", source="test/src")

    def test_merge_rejects_nonexistent_id(self, engine):
        """Non-existent ID raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            engine.merge_memories(ids=[0, 999], merged_text="bad merge", source="test/src")

    def test_merge_with_pinned_original(self, engine):
        """Pinned originals can be archived during merge (bypass pin protection)."""
        engine.update_memory(0, pinned=True)
        # Should NOT raise; pinned protection is bypassed for explicit merge
        result = engine.merge_memories(
            ids=[0, 1],
            merged_text="merged with pinned",
            source="test/src",
        )
        meta_a = engine._get_meta_by_id(0)
        assert meta_a.get("archived") is True
        assert result["archived"] == [0, 1]


# ---------------------------------------------------------------------------
# API-level tests (mock engine via TestClient)
# ---------------------------------------------------------------------------


class TestMergeAPI:
    """HTTP API tests for POST /memory/merge."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = [
                    {"id": 1, "text": "A", "source": "test/src"},
                    {"id": 2, "text": "B", "source": "test/src"},
                ]
                mock_engine.get_memory.side_effect = lambda mid: {
                    1: {"id": 1, "text": "A", "source": "test/src"},
                    2: {"id": 2, "text": "B", "source": "test/src"},
                }[mid]
                mock_engine.merge_memories.return_value = {"id": 3, "archived": [1, 2]}

                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    def test_merge_two_memories(self, client):
        """POST /memory/merge returns {id, archived}."""
        tc, mock = client
        resp = tc.post(
            "/memory/merge",
            json={"ids": [1, 2], "merged_text": "combined", "source": "test/src"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 3
        assert data["archived"] == [1, 2]

    def test_merge_calls_engine_with_correct_args(self, client):
        """Engine merge_memories is called with correct kwargs."""
        tc, mock = client
        tc.post(
            "/memory/merge",
            json={"ids": [1, 2], "merged_text": "combined", "source": "test/src"},
        )
        mock.merge_memories.assert_called_once_with(
            ids=[1, 2],
            merged_text="combined",
            source="test/src",
        )

    def test_merge_creates_supersedes_links_via_engine(self, client):
        """Engine merge_memories is called (which internally adds supersedes links)."""
        tc, mock = client
        resp = tc.post(
            "/memory/merge",
            json={"ids": [1, 2], "merged_text": "combined", "source": "test/src"},
        )
        assert resp.status_code == 200
        # Engine method is responsible for link creation; verify it was called
        mock.merge_memories.assert_called_once()

    def test_merge_requires_at_least_two_ids(self, client):
        """Single ID returns 422 (Pydantic validation)."""
        tc, mock = client
        resp = tc.post(
            "/memory/merge",
            json={"ids": [1], "merged_text": "alone", "source": "test/src"},
        )
        assert resp.status_code == 422

    def test_merge_engine_value_error_returns_400(self, client):
        """ValueError from engine returns 400."""
        tc, mock = client
        mock.merge_memories.side_effect = ValueError("Memory 999 not found")
        resp = tc.post(
            "/memory/merge",
            json={"ids": [1, 2], "merged_text": "combined", "source": "test/src"},
        )
        assert resp.status_code == 400
        assert "999" in resp.json()["detail"]

    def test_merge_empty_ids_returns_422(self, client):
        """Empty ids list returns 422."""
        tc, mock = client
        resp = tc.post(
            "/memory/merge",
            json={"ids": [], "merged_text": "empty", "source": "test/src"},
        )
        assert resp.status_code == 422

    def test_merge_with_pinned_original(self, client):
        """Pinned originals can be merged (no 403/400 at API level)."""
        tc, mock = client
        # get_memory returns a pinned memory
        mock.get_memory.side_effect = lambda mid: {
            1: {"id": 1, "text": "A", "source": "test/src", "pinned": True},
            2: {"id": 2, "text": "B", "source": "test/src"},
        }[mid]
        resp = tc.post(
            "/memory/merge",
            json={"ids": [1, 2], "merged_text": "merged with pinned", "source": "test/src"},
        )
        assert resp.status_code == 200
