"""Tests for QdrantStore snapshot create/list/restore methods."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

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
