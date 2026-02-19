"""Security tests for path traversal, error handling, and metadata preservation.

Covers reviewer findings P1-P3 and ensures hardening holds.
"""

import importlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {
            "total_memories": 5,
            "dimension": 384,
            "model": "all-MiniLM-L6-v2",
        }
        mock_engine.add_memories.return_value = [42]
        mock_engine.get_backup_dir.return_value = Path("/data/backups")

        # Cloud sync mock
        mock_cloud = MagicMock()
        mock_engine.get_cloud_sync.return_value = mock_cloud

        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine, mock_cloud


# -- Path traversal rejection -----------------------------------------------


def test_index_build_rejects_path_traversal(client):
    """P1: /index/build must block ../ in user-provided sources."""
    test_client, mock_engine, _ = client
    mock_engine.rebuild_from_files.return_value = {
        "files_processed": 0,
        "memories_added": 0,
        "backup_location": "/data/backups/pre_rebuild",
    }
    response = test_client.post(
        "/index/build",
        json={"sources": ["../../etc/passwd", "normal.md"]},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    # The traversal path should have been filtered out
    args = mock_engine.rebuild_from_files.call_args
    sources = args[1].get("sources", args[0][0] if args[0] else [])
    for s in sources:
        assert ".." not in s


def test_restore_rejects_path_traversal(client):
    """P1: /restore must reject backup names with traversal characters as 400."""
    test_client, mock_engine, _ = client
    mock_engine.restore_from_backup.side_effect = ValueError("Invalid backup name: ../etc")
    response = test_client.post(
        "/restore",
        json={"backup_name": "../etc"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 400
    assert "Invalid backup name" in response.json()["detail"]


def test_sync_download_rejects_traversal_backup_name(client):
    """P1: /sync/download must reject backup names with traversal characters."""
    test_client, _, mock_cloud = client
    mock_cloud.download_backup.side_effect = ValueError("Invalid backup name: ../../etc")
    response = test_client.post(
        "/sync/download?backup_name=../../etc&confirm=true",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 400
    assert "Invalid" in response.json()["detail"]


def test_sync_restore_rejects_traversal_backup_name(client):
    """P1: /sync/restore/{name} must reject traversal backup names."""
    test_client, _, mock_cloud = client
    mock_cloud.download_backup.side_effect = ValueError("Invalid backup name: ../../../etc")
    response = test_client.post(
        "/sync/restore/../../../etc?confirm=true",
        headers={"X-API-Key": "test-key"},
    )
    # Should be 400 (ValueError) not 500
    assert response.status_code in (400, 404, 422)


# -- /sync/download 404 contract --------------------------------------------


def test_sync_download_returns_404_when_no_remote_backup(client):
    """P2: /sync/download must return 404 — not 500 — when no backup exists."""
    test_client, _, mock_cloud = client
    mock_cloud.get_latest_snapshot.return_value = None
    response = test_client.post(
        "/sync/download?confirm=true",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower() or "No backups" in response.json()["detail"]


def test_sync_download_returns_404_when_specific_backup_missing(client):
    """P2: /sync/download returns 404 when named backup is not in cloud."""
    test_client, _, mock_cloud = client
    mock_cloud.download_backup.side_effect = FileNotFoundError("No backup found: nonexistent")
    response = test_client.post(
        "/sync/download?backup_name=nonexistent&confirm=true",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404


# -- Batch add metadata preservation ----------------------------------------


def test_batch_add_preserves_partial_metadata(client):
    """P2: /memory/add-batch must not drop metadata when some rows omit it."""
    test_client, mock_engine, _ = client
    mock_engine.add_memories.return_value = [1, 2, 3]
    response = test_client.post(
        "/memory/add-batch",
        json={
            "memories": [
                {"text": "memory one", "source": "src/a", "metadata": {"tag": "important"}},
                {"text": "memory two", "source": "src/b"},
                {"text": "memory three", "source": "src/c", "metadata": {"tag": "other"}},
            ]
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    # Verify metadata_list was passed (not None)
    call_kwargs = mock_engine.add_memories.call_args[1]
    metadata_list = call_kwargs.get("metadata_list")
    assert metadata_list is not None, "metadata_list should not be None when some rows have metadata"
    assert len(metadata_list) == 3
    assert metadata_list[0] == {"tag": "important"}
    assert metadata_list[1] is None
    assert metadata_list[2] == {"tag": "other"}


def test_batch_add_no_metadata_passes_none(client):
    """When NO rows have metadata, metadata_list should be None (optimization)."""
    test_client, mock_engine, _ = client
    mock_engine.add_memories.return_value = [1, 2]
    response = test_client.post(
        "/memory/add-batch",
        json={
            "memories": [
                {"text": "memory one", "source": "src/a"},
                {"text": "memory two", "source": "src/b"},
            ]
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    call_kwargs = mock_engine.add_memories.call_args[1]
    assert call_kwargs.get("metadata_list") is None


# -- Cloud sync path traversal (unit level) ---------------------------------


def test_cloud_sync_download_backup_rejects_traversal():
    """P1: CloudSync.download_backup must reject backup names with traversal."""
    from cloud_sync import CloudSync

    sync = CloudSync.__new__(CloudSync)
    sync.bucket = "test-bucket"
    sync.prefix = "memories/"
    sync.s3 = MagicMock()

    with pytest.raises(ValueError, match="Invalid backup name"):
        sync.download_backup("../../etc", Path("/tmp/backups"))

    with pytest.raises(ValueError, match="Invalid backup name"):
        sync.download_backup("foo/bar", Path("/tmp/backups"))

    with pytest.raises(ValueError, match="Invalid backup name"):
        sync.download_backup("foo\\bar", Path("/tmp/backups"))
