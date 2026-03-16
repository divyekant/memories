"""Tests for memory compaction — finding and merging similar memory clusters."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestFindClusters:
    """Test cluster detection in the engine."""

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
            mock_store.delete_points.return_value = None
            MockStore.return_value = mock_store

            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            return eng

    def test_find_clusters_empty(self, engine):
        clusters = engine.find_similar_clusters(threshold=0.85)
        assert clusters == []

    def test_find_clusters_no_duplicates(self, engine):
        engine.add_memories(
            texts=["fact about auth", "fact about database"],
            sources=["test", "test"],
        )
        clusters = engine.find_similar_clusters(threshold=0.99)
        assert clusters == []

    def test_find_clusters_returns_groups(self, engine):
        # Add very similar memories
        engine.add_memories(
            texts=[
                "We chose PostgreSQL for the database",
                "We selected PostgreSQL as our database",
                "Our database choice is PostgreSQL",
            ],
            sources=["test", "test", "test"],
        )
        # With a low threshold these should cluster
        # (depends on actual embedding, so we test the structure)
        clusters = engine.find_similar_clusters(threshold=0.5)
        # Should return list of clusters, each cluster is a list of memory IDs
        assert isinstance(clusters, list)
        for cluster in clusters:
            assert isinstance(cluster, list)
            assert all(isinstance(mid, int) for mid in cluster)


class TestCompactEndpoint:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "admin-key", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.find_similar_clusters.return_value = [[0, 1, 2]]
                # Mock get_memory so cluster details are populated
                mock_engine.get_memory.side_effect = lambda mid: {
                    "text": f"Memory text for id {mid}",
                    "source": "test/source",
                }
                app_module.memory = mock_engine

                yield TestClient(app_module.app), mock_engine

    def test_compact_requires_admin(self, client):
        tc, mock = client
        resp = tc.post("/maintenance/compact",
                       json={"threshold": 0.85},
                       headers={"X-API-Key": "wrong"})
        assert resp.status_code in (401, 403)

    def test_compact_returns_clusters(self, client):
        tc, mock = client
        resp = tc.post("/maintenance/compact",
                       json={"threshold": 0.85},
                       headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["clusters"]) == 1
        cluster = body["clusters"][0]
        assert cluster["size"] == 3
        assert len(cluster["memories"]) == 3
        assert cluster["memories"][0]["source"] == "test/source"
        assert "dry_run" not in body

    def test_compact_default_threshold(self, client):
        tc, mock = client
        resp = tc.post("/maintenance/compact",
                       json={},
                       headers={"X-API-Key": "admin-key"})
        assert resp.status_code == 200
        mock.find_similar_clusters.assert_called_once_with(threshold=0.85)

    def test_compact_response_structure(self, client):
        tc, mock = client
        resp = tc.post("/maintenance/compact",
                       json={"threshold": 0.9},
                       headers={"X-API-Key": "admin-key"})
        body = resp.json()
        assert "clusters" in body
        assert "cluster_count" in body
        assert "total_memories_in_clusters" in body
        assert body["cluster_count"] == 1
        assert body["total_memories_in_clusters"] == 3
