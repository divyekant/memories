"""Tests for folder listing and rename API endpoints."""

import importlib
import os
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
        mock_engine.is_ready.return_value = {"ready": True, "status": "ready"}
        mock_engine.metadata = [
            {"id": 0, "text": "a", "source": "project-x/decisions"},
            {"id": 1, "text": "b", "source": "project-x/bugs"},
            {"id": 2, "text": "c", "source": "project-y/notes"},
            {"id": 3, "text": "d", "source": "standalone"},
            {"id": 4, "text": "e", "source": ""},
        ]
        mock_engine.update_memory.return_value = {"id": 0, "updated_fields": ["source"]}
        mock_engine.delete_by_prefix.return_value = {"deleted_count": 2}
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


HEADERS = {"X-API-Key": "test-key"}


# -- GET /folders -------------------------------------------------------------


def test_list_folders_returns_grouped_counts(client):
    tc, _ = client
    resp = tc.get("/folders", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    folders = {f["name"]: f["count"] for f in data["folders"]}
    assert folders["project-x"] == 2
    assert folders["project-y"] == 1
    assert folders["standalone"] == 1
    assert folders["(ungrouped)"] == 1
    assert data["total"] == 5


def test_list_folders_empty_metadata(client):
    tc, mock_engine = client
    mock_engine.metadata = []
    resp = tc.get("/folders", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["folders"] == []
    assert data["total"] == 0


def test_list_folders_requires_auth(client):
    tc, _ = client
    resp = tc.get("/folders")
    assert resp.status_code == 401


# -- POST /folders/rename -----------------------------------------------------


def test_rename_folder_updates_matching_sources(client):
    tc, mock_engine = client
    resp = tc.post(
        "/folders/rename",
        json={"old_name": "project-x", "new_name": "renamed"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated"] == 2
    assert data["errors"] == 0
    assert data["old_name"] == "project-x"
    assert data["new_name"] == "renamed"
    # update_memory called once per matching entry
    assert mock_engine.update_memory.call_count == 2
    calls = mock_engine.update_memory.call_args_list
    sources = sorted(c.kwargs.get("source", c[1].get("source", "")) for c in calls)
    # Should have renamed project-x/decisions -> renamed/decisions, project-x/bugs -> renamed/bugs
    assert "renamed/bugs" in sources
    assert "renamed/decisions" in sources


def test_rename_folder_404_when_no_match(client):
    tc, _ = client
    resp = tc.post(
        "/folders/rename",
        json={"old_name": "nonexistent", "new_name": "whatever"},
        headers=HEADERS,
    )
    assert resp.status_code == 404


def test_rename_folder_requires_auth(client):
    tc, _ = client
    resp = tc.post("/folders/rename", json={"old_name": "a", "new_name": "b"})
    assert resp.status_code == 401


def test_rename_folder_validates_empty_names(client):
    tc, _ = client
    resp = tc.post(
        "/folders/rename",
        json={"old_name": "", "new_name": "b"},
        headers=HEADERS,
    )
    assert resp.status_code == 422


def test_rename_folder_tracks_errors(client):
    tc, mock_engine = client
    # Make update_memory fail for the second call
    mock_engine.update_memory.side_effect = [
        {"id": 0, "updated_fields": ["source"]},
        ValueError("ID not found"),
    ]
    resp = tc.post(
        "/folders/rename",
        json={"old_name": "project-x", "new_name": "fixed"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated"] == 1
    assert data["errors"] == 1


def test_rename_exact_match_no_slash(client):
    """Renaming 'standalone' (no sub-path) works correctly."""
    tc, mock_engine = client
    resp = tc.post(
        "/folders/rename",
        json={"old_name": "standalone", "new_name": "moved"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated"] == 1
    mock_engine.update_memory.assert_called_once_with(memory_id=3, source="moved")
