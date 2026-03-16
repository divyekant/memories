"""Tests for embedding model migration (re-embed) endpoint."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestReembedEndpoint:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "admin-key", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = [
                    {"id": 0, "text": "fact one", "source": "test"},
                    {"id": 1, "text": "fact two", "source": "test"},
                ]
                mock_engine.config = {"model": "all-MiniLM-L6-v2", "dimension": 384}
                mock_engine.reembed.return_value = {
                    "status": "completed",
                    "old_model": "all-MiniLM-L6-v2",
                    "new_model": "all-MiniLM-L12-v2",
                    "memories_processed": 2,
                }
                app_module.memory = mock_engine

                yield TestClient(app_module.app), mock_engine

    def test_reembed_requires_admin(self, client):
        tc, mock = client
        resp = tc.post("/maintenance/reembed",
                       json={"model": "all-MiniLM-L12-v2"},
                       headers={"X-API-Key": "wrong"})
        assert resp.status_code in (401, 403)

    def test_reembed_calls_engine(self, client):
        tc, mock = client
        resp = tc.post("/maintenance/reembed",
                       json={"model": "all-MiniLM-L12-v2"},
                       headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        mock.reembed.assert_called_once_with(model_name="all-MiniLM-L12-v2")

    def test_reembed_returns_result(self, client):
        tc, mock = client
        resp = tc.post("/maintenance/reembed",
                       json={"model": "all-MiniLM-L12-v2"},
                       headers={"X-API-Key": "admin-key"})
        body = resp.json()
        assert body["status"] == "completed"
        assert body["old_model"] == "all-MiniLM-L6-v2"

    def test_reembed_without_model_uses_current(self, client):
        tc, mock = client
        mock.reembed.return_value = {
            "status": "completed",
            "old_model": "all-MiniLM-L6-v2",
            "new_model": "all-MiniLM-L6-v2",
            "memories_processed": 2,
        }
        resp = tc.post("/maintenance/reembed",
                       json={},
                       headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200


class TestReembedEngine:
    """Test the engine-level reembed method."""

    @pytest.fixture
    def engine(self, tmp_path):
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.ensure_payload_indexes.return_value = None
            mock_store.count.return_value = 0
            mock_store.search.return_value = []
            mock_store.upsert_points.return_value = None
            mock_store.recreate_collection.return_value = None
            MockStore.return_value = mock_store

            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            eng.add_memories(texts=["fact A", "fact B"], sources=["test", "test"])
            return eng

    def test_reembed_with_same_model(self, engine):
        result = engine.reembed()
        assert result["status"] == "completed"
        assert result["memories_processed"] == 2

    def test_reembed_creates_backup(self, engine):
        engine.reembed()
        # Should have called _backup internally
        backup_dir = engine.backup_dir
        assert backup_dir.exists()

    def test_reembed_updates_config(self, engine):
        engine.reembed()
        assert engine.config["last_updated"] is not None
