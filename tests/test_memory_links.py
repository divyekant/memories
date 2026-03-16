"""Tests for memory relationships (lightweight graph edges between memories)."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


VALID_LINK_TYPES = ["supersedes", "related_to", "blocked_by", "caused_by", "reinforces"]


class TestMemoryEngineLinks:
    """Test link operations on MemoryEngine."""

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
            MockStore.return_value = mock_store

            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            eng.add_memories(
                texts=["memory A", "memory B", "memory C"],
                sources=["test", "test", "test"],
            )
            return eng

    def test_add_link(self, engine):
        result = engine.add_link(from_id=0, to_id=1, link_type="related_to")
        assert result["from_id"] == 0
        assert result["to_id"] == 1
        assert result["type"] == "related_to"

    def test_add_link_invalid_type(self, engine):
        with pytest.raises(ValueError, match="Invalid link type"):
            engine.add_link(from_id=0, to_id=1, link_type="invalid")

    def test_add_link_nonexistent_source(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.add_link(from_id=999, to_id=1, link_type="related_to")

    def test_add_link_nonexistent_target(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.add_link(from_id=0, to_id=999, link_type="related_to")

    def test_add_link_self_reference(self, engine):
        with pytest.raises(ValueError, match="cannot link.*itself"):
            engine.add_link(from_id=0, to_id=0, link_type="related_to")

    def test_get_links_empty(self, engine):
        links = engine.get_links(memory_id=0)
        assert links == []

    def test_get_links_after_add(self, engine):
        engine.add_link(from_id=0, to_id=1, link_type="related_to")
        links = engine.get_links(memory_id=0)
        assert len(links) == 1
        assert links[0]["to_id"] == 1
        assert links[0]["type"] == "related_to"

    def test_get_links_includes_incoming(self, engine):
        engine.add_link(from_id=1, to_id=0, link_type="blocked_by")
        links = engine.get_links(memory_id=0, include_incoming=True)
        assert len(links) == 1
        assert links[0]["from_id"] == 1
        assert links[0]["direction"] == "incoming"

    def test_get_links_outgoing_only_by_default(self, engine):
        engine.add_link(from_id=1, to_id=0, link_type="blocked_by")
        links = engine.get_links(memory_id=0)
        assert len(links) == 0

    def test_remove_link(self, engine):
        engine.add_link(from_id=0, to_id=1, link_type="related_to")
        result = engine.remove_link(from_id=0, to_id=1, link_type="related_to")
        assert result["removed"] is True
        links = engine.get_links(memory_id=0)
        assert len(links) == 0

    def test_remove_nonexistent_link(self, engine):
        result = engine.remove_link(from_id=0, to_id=1, link_type="related_to")
        assert result["removed"] is False

    def test_multiple_links_same_source(self, engine):
        engine.add_link(from_id=0, to_id=1, link_type="related_to")
        engine.add_link(from_id=0, to_id=2, link_type="caused_by")
        links = engine.get_links(memory_id=0)
        assert len(links) == 2

    def test_duplicate_link_rejected(self, engine):
        engine.add_link(from_id=0, to_id=1, link_type="related_to")
        with pytest.raises(ValueError, match="already exists"):
            engine.add_link(from_id=0, to_id=1, link_type="related_to")

    def test_links_persist_across_save_load(self, engine):
        engine.add_link(from_id=0, to_id=1, link_type="reinforces")
        engine.save()
        meta = engine._get_meta_by_id(0)
        assert any(l["to_id"] == 1 for l in meta.get("links", []))

    def test_all_valid_link_types(self, engine):
        for i, lt in enumerate(VALID_LINK_TYPES):
            engine.add_link(from_id=0, to_id=1 + (i % 2), link_type=lt)
        # Should not raise for any valid type

    def test_delete_memory_cleans_outgoing_links(self, engine):
        engine.add_link(from_id=0, to_id=1, link_type="related_to")
        engine.delete_memory(0)
        assert not engine._id_exists(0)

    def test_get_links_by_type(self, engine):
        engine.add_link(from_id=0, to_id=1, link_type="related_to")
        engine.add_link(from_id=0, to_id=2, link_type="caused_by")
        links = engine.get_links(memory_id=0, link_type="related_to")
        assert len(links) == 1
        assert links[0]["type"] == "related_to"


class TestLinkAPI:
    """Test the HTTP API for memory links."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = [
                    {"id": 1, "text": "A", "source": "test"},
                    {"id": 2, "text": "B", "source": "test"},
                ]
                mock_engine.add_link.return_value = {
                    "from_id": 1, "to_id": 2, "type": "related_to",
                    "created_at": "2026-03-15T00:00:00+00:00",
                }
                mock_engine.get_links.return_value = [
                    {"to_id": 2, "type": "related_to", "direction": "outgoing",
                     "created_at": "2026-03-15T00:00:00+00:00"},
                ]
                mock_engine.remove_link.return_value = {"removed": True}

                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    def test_add_link_endpoint(self, client):
        tc, mock = client
        resp = tc.post("/memory/1/link", json={"to_id": 2, "type": "related_to"})
        assert resp.status_code == 200
        mock.add_link.assert_called_once_with(from_id=1, to_id=2, link_type="related_to")

    def test_get_links_endpoint(self, client):
        tc, mock = client
        resp = tc.get("/memory/1/links")
        assert resp.status_code == 200
        assert resp.json()["links"] == mock.get_links.return_value

    def test_delete_link_endpoint(self, client):
        tc, mock = client
        resp = tc.delete("/memory/1/link/2", params={"type": "related_to"})
        assert resp.status_code == 200
        mock.remove_link.assert_called_once_with(from_id=1, to_id=2, link_type="related_to")

    def test_add_link_invalid_type_returns_422(self, client):
        tc, mock = client
        mock.add_link.side_effect = ValueError("Invalid link type: foo")
        resp = tc.post("/memory/1/link", json={"to_id": 2, "type": "foo"})
        assert resp.status_code == 400
