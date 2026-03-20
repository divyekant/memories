"""Tests for QdrantStore snapshot create/list/restore methods."""

from __future__ import annotations

import importlib
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient

from qdrant_config import QdrantSettings
from qdrant_store import QdrantStore


def _remote_settings() -> QdrantSettings:
    return QdrantSettings(
        url="http://localhost:6333",
        collection="test_col",
        api_key="",
        wait=True,
        write_ordering="strong",
        read_consistency="majority",
        replication_factor=1,
        write_consistency_factor=1,
    )


def _local_settings() -> QdrantSettings:
    return QdrantSettings(
        url="",
        collection="test_col",
        api_key="",
        wait=True,
        write_ordering="strong",
        read_consistency="majority",
        replication_factor=1,
        write_consistency_factor=1,
    )


class TestQdrantStoreSnapshots:
    def test_create_snapshot_remote(self):
        """create_snapshot() on remote mode calls client.create_snapshot and returns snapshot name."""
        mock_client = MagicMock()
        snapshot = SimpleNamespace(name="snapshot-2026-01-01.snapshot")
        mock_client.create_snapshot.return_value = snapshot

        store = QdrantStore(settings=_remote_settings(), client=mock_client)
        result = store.create_snapshot()

        mock_client.create_snapshot.assert_called_once_with(collection_name="test_col")
        assert result == "snapshot-2026-01-01.snapshot"

    def test_list_snapshots_remote(self):
        """list_snapshots() on remote mode returns list of dicts with name/creation_time/size."""
        mock_client = MagicMock()
        snap1 = SimpleNamespace(name="snap-a.snapshot", creation_time="2026-01-01T00:00:00", size=1024)
        snap2 = SimpleNamespace(name="snap-b.snapshot", creation_time="2026-01-02T00:00:00", size=2048)
        mock_client.list_snapshots.return_value = [snap1, snap2]

        store = QdrantStore(settings=_remote_settings(), client=mock_client)
        result = store.list_snapshots()

        mock_client.list_snapshots.assert_called_once_with(collection_name="test_col")
        assert len(result) == 2
        assert result[0] == {"name": "snap-a.snapshot", "creation_time": "2026-01-01T00:00:00", "size": 1024}
        assert result[1] == {"name": "snap-b.snapshot", "creation_time": "2026-01-02T00:00:00", "size": 2048}

    def test_create_snapshot_local_mode(self, tmp_path):
        """create_snapshot() in local mode copies the data dir via shutil.copytree and returns backup name."""
        local_path = str(tmp_path / "qdrant_data")
        os.makedirs(local_path, exist_ok=True)

        mock_client = MagicMock()

        # Patch QdrantClient so local path construction doesn't fail
        with patch("qdrant_store.QdrantClient", return_value=mock_client), \
             patch("qdrant_store._LOCAL_CLIENTS", {}), \
             patch("qdrant_store.shutil") as mock_shutil:

            store = QdrantStore(settings=_local_settings(), local_path=local_path)
            result = store.create_snapshot()

        # Result should be a backup dir name starting with "local-backup-"
        assert result.startswith("local-backup-")
        # copytree should have been called once
        mock_shutil.copytree.assert_called_once()
        src_arg, dst_arg = mock_shutil.copytree.call_args[0]
        assert src_arg == local_path
        expected_snapshots_dir = os.path.join(local_path, ".snapshots")
        assert dst_arg.startswith(expected_snapshots_dir)

    def test_list_snapshots_local_mode(self, tmp_path):
        """list_snapshots() in local mode lists backup dirs in .snapshots/."""
        local_path = str(tmp_path / "qdrant_data")
        snapshots_dir = os.path.join(local_path, ".snapshots")
        os.makedirs(snapshots_dir, exist_ok=True)
        os.makedirs(os.path.join(snapshots_dir, "local-backup-20260101-120000"), exist_ok=True)
        os.makedirs(os.path.join(snapshots_dir, "local-backup-20260102-120000"), exist_ok=True)

        mock_client = MagicMock()

        with patch("qdrant_store.QdrantClient", return_value=mock_client), \
             patch("qdrant_store._LOCAL_CLIENTS", {}):

            store = QdrantStore(settings=_local_settings(), local_path=local_path)
            result = store.list_snapshots()

        names = [r["name"] for r in result]
        assert "local-backup-20260101-120000" in names
        assert "local-backup-20260102-120000" in names

    def test_restore_snapshot_remote(self):
        """restore_snapshot() on remote mode calls client.recover_snapshot."""
        mock_client = MagicMock()

        store = QdrantStore(settings=_remote_settings(), client=mock_client)
        store.restore_snapshot("snapshot-2026-01-01.snapshot")

        mock_client.recover_snapshot.assert_called_once_with(
            collection_name="test_col",
            location="snapshot-2026-01-01.snapshot",
        )

    def test_restore_snapshot_local_mode(self, tmp_path):
        """restore_snapshot() in local mode removes current data and copies backup dir back."""
        local_path = str(tmp_path / "qdrant_data")
        snapshots_dir = os.path.join(local_path, ".snapshots")
        backup_name = "local-backup-20260101-120000"
        backup_path = os.path.join(snapshots_dir, backup_name)

        os.makedirs(local_path, exist_ok=True)
        os.makedirs(backup_path, exist_ok=True)
        # Create a subdirectory representing existing collection data (triggers shutil.rmtree)
        os.makedirs(os.path.join(local_path, "collection"), exist_ok=True)

        mock_client = MagicMock()

        with patch("qdrant_store.QdrantClient", return_value=mock_client), \
             patch("qdrant_store._LOCAL_CLIENTS", {}), \
             patch("qdrant_store.shutil") as mock_shutil:

            store = QdrantStore(settings=_local_settings(), local_path=local_path)
            store.restore_snapshot(backup_name)

        # rmtree should be called to clear the existing collection subdir
        mock_shutil.rmtree.assert_called()
        # copytree should restore the backup
        mock_shutil.copytree.assert_called_once()
        src_arg, dst_arg = mock_shutil.copytree.call_args[0]
        assert src_arg == backup_path
        assert dst_arg == local_path


class TestEngineSnapshotBeforeDelete:
    """Tests for MemoryEngine snapshot-before-delete, dry_run, and snapshot API endpoints."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.list_snapshots.return_value = []
                mock_engine._snapshot_before_delete.return_value = "snap-001"
                mock_engine.delete_by_source.return_value = {"deleted_count": 2}
                mock_engine.delete_by_prefix.return_value = {"deleted_count": 3}
                mock_engine.qdrant_store = MagicMock()
                mock_engine.qdrant_store.create_snapshot.return_value = "snap-001"

                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    # --- /snapshots API ---

    def test_list_snapshots(self, client):
        tc, mock = client
        mock.list_snapshots.return_value = [
            {"name": "snap-001", "reason": "manual", "timestamp": "2026-03-19T00:00:00Z", "point_count": 10}
        ]
        resp = tc.get("/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert "snapshots" in data
        assert len(data["snapshots"]) == 1
        assert data["snapshots"][0]["name"] == "snap-001"

    def test_list_snapshots_empty(self, client):
        tc, mock = client
        mock.list_snapshots.return_value = []
        resp = tc.get("/snapshots")
        assert resp.status_code == 200
        assert resp.json()["snapshots"] == []

    def test_create_snapshot_manual(self, client):
        tc, mock = client
        mock._snapshot_before_delete.return_value = "snap-manual-001"
        resp = tc.post("/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot"] == "snap-manual-001"
        mock._snapshot_before_delete.assert_called_once_with("manual")

    def test_restore_snapshot_admin_required(self, client):
        tc, mock = client
        # Non-admin auth context should get 403
        from auth_context import AuthContext
        with patch("app._get_auth", return_value=AuthContext(role="reader", prefixes=["test/"], key_type="scoped")):
            resp = tc.post("/snapshots/snap-001/restore")
        assert resp.status_code == 403

    def test_restore_snapshot_calls_qdrant(self, client):
        tc, mock = client
        # Patch auth to be admin
        with patch("app._require_admin"):
            resp = tc.post("/snapshots/snap-001/restore")
        assert resp.status_code == 200
        mock.qdrant_store.restore_snapshot.assert_called_once_with("snap-001")

    # --- delete-by-source dry_run / skip_snapshot ---

    def test_delete_by_source_dry_run(self, client):
        tc, mock = client
        mock.delete_by_source.return_value = {"count": 2, "would_delete": [1, 2]}
        resp = tc.post(
            "/memory/delete-by-source",
            json={"source_pattern": "test/", "dry_run": True},
        )
        assert resp.status_code == 200
        mock.delete_by_source.assert_called_once()
        call_kwargs = mock.delete_by_source.call_args
        assert call_kwargs.kwargs.get("dry_run") is True or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1]
        )

    def test_delete_by_source_skip_snapshot(self, client):
        tc, mock = client
        resp = tc.post(
            "/memory/delete-by-source",
            json={"source_pattern": "test/", "skip_snapshot": True},
        )
        assert resp.status_code == 200
        call_kwargs = mock.delete_by_source.call_args
        assert call_kwargs.kwargs.get("skip_snapshot") is True or (
            len(call_kwargs.args) > 2 and call_kwargs.args[2]
        )

    def test_delete_by_prefix_dry_run(self, client):
        tc, mock = client
        mock.delete_by_prefix.return_value = {"count": 3, "would_delete": [1, 2, 3]}
        resp = tc.post(
            "/memory/delete-by-prefix",
            json={"source_prefix": "test/", "dry_run": True},
        )
        assert resp.status_code == 200
        call_kwargs = mock.delete_by_prefix.call_args
        assert call_kwargs.kwargs.get("dry_run") is True or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1]
        )

    # --- Engine _snapshot_before_delete and list_snapshots ---

    def test_engine_snapshot_before_delete_writes_manifest(self, tmp_path):
        """_snapshot_before_delete writes to manifest.json and returns name."""
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 5
            mock_store.create_snapshot.return_value = "snap-engine-001"
            MockStore.return_value = mock_store

            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            name = eng._snapshot_before_delete("pre_delete_test")

        assert name == "snap-engine-001"
        manifest_path = tmp_path / "data" / "snapshots" / "manifest.json"
        assert manifest_path.exists()
        import json
        entries = json.loads(manifest_path.read_text())
        assert len(entries) == 1
        assert entries[0]["name"] == "snap-engine-001"
        assert entries[0]["reason"] == "pre_delete_test"
        assert "timestamp" in entries[0]
        assert "point_count" in entries[0]

    def test_engine_list_snapshots_empty(self, tmp_path):
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 0
            MockStore.return_value = mock_store
            MockSettings.from_env.return_value = MagicMock(read_consistency="majority")

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            result = eng.list_snapshots()

        assert result == []

    def test_engine_list_snapshots_returns_manifest(self, tmp_path):
        import json
        from memory_engine import MemoryEngine

        data_dir = tmp_path / "data"
        snapshots_dir = data_dir / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        manifest = [
            {"name": "snap-001", "reason": "manual", "timestamp": "2026-03-19T00:00:00Z", "point_count": 10}
        ]
        (snapshots_dir / "manifest.json").write_text(json.dumps(manifest))

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 0
            MockStore.return_value = mock_store
            MockSettings.from_env.return_value = MagicMock(read_consistency="majority")

            eng = MemoryEngine(data_dir=str(data_dir))
            result = eng.list_snapshots()

        assert len(result) == 1
        assert result[0]["name"] == "snap-001"

    def test_engine_delete_by_source_dry_run(self, tmp_path):
        """delete_by_source with dry_run=True returns count without deleting."""
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
            MockSettings.from_env.return_value = MagicMock(read_consistency="majority")

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            eng.metadata = [
                {"id": 1, "text": "hello", "source": "test/proj"},
                {"id": 2, "text": "world", "source": "test/proj"},
                {"id": 3, "text": "other", "source": "other/proj"},
            ]

            result = eng.delete_by_source("test/proj", dry_run=True)

        assert result["count"] == 2
        assert set(result["would_delete"]) == {1, 2}
        # No deletion occurred
        mock_store.delete_points.assert_not_called()

    def test_engine_delete_by_source_auto_snapshots(self, tmp_path):
        """delete_by_source calls _snapshot_before_delete by default."""
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 2
            mock_store.create_snapshot.return_value = "auto-snap-001"
            mock_store.search.return_value = []
            mock_store.upsert_points.return_value = None
            mock_store.delete_points.return_value = None
            MockStore.return_value = mock_store
            MockSettings.from_env.return_value = MagicMock(read_consistency="majority")

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            eng.metadata = [
                {"id": 1, "text": "hello", "source": "test/proj"},
            ]

            eng.delete_by_source("test/proj")

        mock_store.create_snapshot.assert_called_once()

    def test_engine_delete_by_source_skip_snapshot(self, tmp_path):
        """delete_by_source with skip_snapshot=True skips the auto-snapshot."""
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 2
            mock_store.search.return_value = []
            mock_store.upsert_points.return_value = None
            mock_store.delete_points.return_value = None
            MockStore.return_value = mock_store
            MockSettings.from_env.return_value = MagicMock(read_consistency="majority")

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            eng.metadata = [
                {"id": 1, "text": "hello", "source": "test/proj"},
            ]

            eng.delete_by_source("test/proj", skip_snapshot=True)

        mock_store.create_snapshot.assert_not_called()

    def test_engine_delete_memories_snapshots_above_threshold(self, tmp_path):
        """delete_memories with >10 IDs triggers auto-snapshot."""
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 15
            mock_store.create_snapshot.return_value = "auto-snap-002"
            mock_store.search.return_value = []
            mock_store.upsert_points.return_value = None
            mock_store.delete_points.return_value = None
            MockStore.return_value = mock_store
            MockSettings.from_env.return_value = MagicMock(read_consistency="majority")

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            ids = list(range(11))
            eng.metadata = [{"id": i, "text": f"mem {i}", "source": "src"} for i in ids]
            # Sync _id_map so _id_exists returns True
            eng._id_map = {i: i for i in ids}

            eng.delete_memories(ids)

        mock_store.create_snapshot.assert_called_once()

    def test_engine_delete_memories_no_snapshot_below_threshold(self, tmp_path):
        """delete_memories with <=10 IDs does NOT trigger auto-snapshot."""
        from memory_engine import MemoryEngine

        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.count.return_value = 5
            mock_store.search.return_value = []
            mock_store.upsert_points.return_value = None
            mock_store.delete_points.return_value = None
            MockStore.return_value = mock_store
            MockSettings.from_env.return_value = MagicMock(read_consistency="majority")

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            ids = list(range(5))
            eng.metadata = [{"id": i, "text": f"mem {i}", "source": "src"} for i in ids]
            # Sync _id_map so _id_exists returns True
            eng._id_map = {i: i for i in ids}

            eng.delete_memories(ids)

        mock_store.create_snapshot.assert_not_called()
