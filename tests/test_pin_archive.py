"""Tests for pin/protect — PatchMemoryRequest pinned/archived fields and bulk-delete exclusion."""

from __future__ import annotations

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestPinProtect:
    """Pin/archive fields on PATCH /memory/{id} and bulk-delete exclusion."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.update_memory.return_value = {"id": 1, "updated_fields": ["pinned"]}
                mock_engine.get_memory.return_value = {"id": 1, "text": "test", "source": "test/src"}

                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    def test_pin_memory(self, client):
        """PATCH with pinned=True calls update_memory with pinned=True."""
        tc, mock = client
        mock.update_memory.return_value = {"id": 1, "updated_fields": ["pinned"]}

        resp = tc.patch("/memory/1", json={"pinned": True})

        assert resp.status_code == 200
        mock.update_memory.assert_called_once()
        call_kwargs = mock.update_memory.call_args[1]
        assert call_kwargs["pinned"] is True

    def test_unpin_memory(self, client):
        """PATCH with pinned=False calls update_memory with pinned=False."""
        tc, mock = client
        mock.update_memory.return_value = {"id": 1, "updated_fields": ["pinned"]}

        resp = tc.patch("/memory/1", json={"pinned": False})

        assert resp.status_code == 200
        call_kwargs = mock.update_memory.call_args[1]
        assert call_kwargs["pinned"] is False

    def test_archive_memory(self, client):
        """PATCH with archived=True calls update_memory with archived=True."""
        tc, mock = client
        mock.update_memory.return_value = {"id": 1, "updated_fields": ["archived"]}

        resp = tc.patch("/memory/1", json={"archived": True})

        assert resp.status_code == 200
        call_kwargs = mock.update_memory.call_args[1]
        assert call_kwargs["archived"] is True

    def test_unarchive_memory(self, client):
        """PATCH with archived=False calls update_memory with archived=False."""
        tc, mock = client
        mock.update_memory.return_value = {"id": 1, "updated_fields": ["archived"]}

        resp = tc.patch("/memory/1", json={"archived": False})

        assert resp.status_code == 200
        call_kwargs = mock.update_memory.call_args[1]
        assert call_kwargs["archived"] is False

    def test_patch_rejects_empty_body(self, client):
        """PATCH with {} (no fields provided) returns 400."""
        tc, mock = client

        resp = tc.patch("/memory/1", json={})

        assert resp.status_code == 400

    def test_patch_pin_and_text_together(self, client):
        """PATCH can combine pinned with text in one call."""
        tc, mock = client
        mock.update_memory.return_value = {"id": 1, "updated_fields": ["text", "pinned"]}

        resp = tc.patch("/memory/1", json={"pinned": True, "text": "updated text"})

        assert resp.status_code == 200
        call_kwargs = mock.update_memory.call_args[1]
        assert call_kwargs["pinned"] is True
        assert call_kwargs["text"] == "updated text"

    def test_list_memories_filter_pinned_true(self, client):
        """GET /memories?pinned=true returns only pinned memories."""
        tc, mock = client
        mock.list_memories.return_value = {
            "memories": [
                {"id": 1, "text": "pinned mem", "source": "s", "pinned": True},
                {"id": 2, "text": "normal mem", "source": "s", "pinned": False},
                {"id": 3, "text": "unpinned", "source": "s"},
            ],
            "total": 3,
            "offset": 0,
            "limit": 20,
        }

        resp = tc.get("/memories?pinned=true")

        assert resp.status_code == 200
        memories = resp.json()["memories"]
        assert len(memories) == 1
        assert memories[0]["id"] == 1

    def test_list_memories_filter_pinned_false(self, client):
        """GET /memories?pinned=false returns only non-pinned memories."""
        tc, mock = client
        mock.list_memories.return_value = {
            "memories": [
                {"id": 1, "text": "pinned mem", "source": "s", "pinned": True},
                {"id": 2, "text": "normal mem", "source": "s", "pinned": False},
                {"id": 3, "text": "unset", "source": "s"},
            ],
            "total": 3,
            "offset": 0,
            "limit": 20,
        }

        resp = tc.get("/memories?pinned=false")

        assert resp.status_code == 200
        memories = resp.json()["memories"]
        # id=2 has pinned=False; id=3 has no pinned key (not equal to False)
        assert len(memories) == 1
        assert memories[0]["id"] == 2

    def test_list_memories_no_filter(self, client):
        """GET /memories without pinned param returns all memories."""
        tc, mock = client
        mock.list_memories.return_value = {
            "memories": [
                {"id": 1, "text": "pinned", "source": "s", "pinned": True},
                {"id": 2, "text": "normal", "source": "s"},
            ],
            "total": 2,
            "offset": 0,
            "limit": 20,
        }

        resp = tc.get("/memories")

        assert resp.status_code == 200
        assert len(resp.json()["memories"]) == 2


def _make_engine_stub(metadata):
    """Build a MemoryEngine stub with controlled metadata, no real init."""
    import contextlib
    import threading
    from memory_engine import MemoryEngine

    engine = MemoryEngine.__new__(MemoryEngine)
    engine.metadata = metadata
    engine.config = {"last_updated": ""}
    engine.qdrant_store = MagicMock()
    engine._write_lock = threading.Lock()
    engine._entity_locks = MagicMock()
    engine._entity_locks.acquire_many.return_value = contextlib.nullcontext()
    engine._backup = MagicMock()
    engine._snapshot_before_delete = MagicMock(return_value="snap-001")
    engine._rebuild_bm25 = MagicMock()
    engine.save = MagicMock()
    engine._delete_ids_targeted = MagicMock()
    return engine


class TestPinnedBulkDeleteExclusion:
    """Pinned memories are excluded from delete_by_source and delete_by_prefix."""

    def test_delete_by_source_skips_pinned(self):
        """delete_by_source excludes pinned memories from deletion."""
        engine = _make_engine_stub([
            {"id": 1, "source": "test/src", "text": "pinned", "pinned": True},
            {"id": 2, "source": "test/src", "text": "normal", "pinned": False},
            {"id": 3, "source": "test/src", "text": "default"},
        ])

        engine.delete_by_source("test/src", skip_snapshot=True)

        deleted_ids = engine._delete_ids_targeted.call_args[0][0]
        assert 1 not in deleted_ids, "Pinned memory should be excluded"
        assert 2 in deleted_ids
        assert 3 in deleted_ids

    def test_delete_by_prefix_skips_pinned(self):
        """delete_by_prefix excludes pinned memories from deletion."""
        engine = _make_engine_stub([
            {"id": 10, "source": "folder/x", "text": "pinned", "pinned": True},
            {"id": 11, "source": "folder/y", "text": "normal"},
            {"id": 12, "source": "other/z", "text": "other source"},
        ])

        engine.delete_by_prefix("folder/", skip_snapshot=True)

        deleted_ids = engine._delete_ids_targeted.call_args[0][0]
        assert 10 not in deleted_ids, "Pinned memory should be excluded"
        assert 11 in deleted_ids
        assert 12 not in deleted_ids  # different prefix

    def test_delete_by_source_dry_run_excludes_pinned(self):
        """delete_by_source dry_run also excludes pinned memories from would_delete list."""
        engine = _make_engine_stub([
            {"id": 1, "source": "test/src", "text": "pinned", "pinned": True},
            {"id": 2, "source": "test/src", "text": "normal"},
        ])

        result = engine.delete_by_source("test/src", dry_run=True)

        assert result["count"] == 1
        assert 1 not in result["would_delete"]
        assert 2 in result["would_delete"]
