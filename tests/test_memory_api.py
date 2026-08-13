"""Tests for memory CRUD/search API endpoints."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth_context import AuthContext
from project_memory import ProjectMemoryPolicyError


@pytest.fixture
def client():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5, "dimension": 384, "model": "all-MiniLM-L6-v2"}
        mock_engine.search.return_value = []
        mock_engine.hybrid_search.return_value = []
        mock_engine.delete_memories.return_value = {"deleted_count": 2, "deleted_ids": [1, 3], "missing_ids": []}
        mock_engine.get_memory.return_value = {"id": 1, "text": "hello", "source": "carto/poet-pads/db"}
        mock_engine.get_memories.return_value = {"memories": [{"id": 1}], "missing_ids": [2]}
        mock_engine.upsert_memory.return_value = {"id": 7, "action": "created"}
        mock_engine.upsert_memories.return_value = {
            "created": 1,
            "updated": 1,
            "errors": 0,
            "results": [{"id": 7, "action": "created"}, {"id": 8, "action": "updated"}],
        }
        mock_engine.delete_by_prefix.return_value = {"deleted_count": 4}
        mock_engine.update_memory.return_value = {"id": 4, "updated_fields": ["text"]}
        mock_engine.is_ready.return_value = {"ready": True, "status": "ready"}
        mock_engine.reload_embedder.return_value = {
            "reloaded": True,
            "model": "all-MiniLM-L6-v2",
            "dimension": 384,
        }
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


def test_search_accepts_source_prefix_and_passes_to_engine(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/search",
        json={"query": "python", "k": 3, "hybrid": False, "source_prefix": "carto/poet-pads/"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    mock_engine.search.assert_called_once_with(
        query="python",
        k=3,
        threshold=None,
        source_prefix="carto/poet-pads/",
        include_archived=False,
        since=None,
        until=None,
    )


def test_search_passes_opt_in_source_boundary_to_engine(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/search",
        json={
            "query": "shared",
            "k": 3,
            "hybrid": False,
            "source_prefix": "project/acme",
            "source_boundary": True,
        },
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    assert mock_engine.search.call_args.kwargs["source_boundary"] is True


def test_delete_batch_endpoint_deletes_multiple_ids(client):
    test_client, _ = client
    response = test_client.post(
        "/memory/delete-batch",
        json={"ids": [1, 3]},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["deleted_count"] == 2
    assert body["deleted_ids"] == [1, 3]


def test_get_memory_by_id(client):
    test_client, mock_engine = client
    response = test_client.get("/memory/1", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["id"] == 1
    mock_engine.get_memory.assert_called_once_with(1)


def test_get_memory_by_id_logs_attributed_usage(client):
    test_client, _ = client
    import app as app_module

    tracker = MagicMock()
    app_module.usage_tracker = tracker

    response = test_client.get(
        "/memory/1",
        headers={
            "X-API-Key": "test-key",
            "X-Memories-Client": "codex",
            "X-Memories-Session-Id": "session-get",
            "X-Memories-Invocation": "mcp",
        },
    )

    assert response.status_code == 200
    tracker.log_api_event.assert_called_once_with(
        "get",
        "",
        1,
        client="codex",
        session_id="session-get",
        invocation="mcp",
    )


def test_patch_substantive_project_edit_restamps_current_trusted_authorship(client):
    test_client, mock_engine = client
    import app as app_module
    from project_memory import TrustedAuthorship

    mock_engine.get_memory.return_value = {
        "id": 4,
        "text": "Alice's project fact",
        "source": "project/acme/knowledge",
        "author": "alice",
    }
    mock_engine.update_memory.return_value = {
        "id": 4,
        "updated_fields": ["text", "metadata"],
        "author": "bob",
    }
    bob = TrustedAuthorship.principal("bob", "codex")
    with patch.object(app_module, "_trusted_authorship", return_value=bob):
        response = test_client.patch(
            "/memory/4",
            json={
                "text": "Bob's replacement",
                "metadata_patch": {
                    "author": "mallory",
                    "contributors": ["mallory"],
                    "kept": True,
                },
            },
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    assert response.json()["author"] == "bob"
    kwargs = mock_engine.update_memory.call_args.kwargs
    assert kwargs["trusted_authorship"] == bob
    assert kwargs["apply_trusted_authorship"] is True


def test_patch_metadata_only_does_not_request_authorship_restamp(client):
    test_client, mock_engine = client
    import app as app_module
    from project_memory import TrustedAuthorship

    mock_engine.get_memory.return_value = {
        "id": 4,
        "text": "Alice's project fact",
        "source": "project/acme/knowledge",
        "author": "system",
        "contributors": ["alice"],
        "source_memory_ids": [17],
    }
    mock_engine.update_memory.return_value = {
        "id": 4,
        "updated_fields": ["metadata"],
        "author": "system",
        "contributors": ["alice"],
        "source_memory_ids": [17],
    }
    bob = TrustedAuthorship.principal("bob", "codex")
    with patch.object(app_module, "_trusted_authorship", return_value=bob):
        response = test_client.patch(
            "/memory/4",
            json={"metadata_patch": {"kept": True}},
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    kwargs = mock_engine.update_memory.call_args.kwargs
    assert kwargs["trusted_authorship"] == bob
    assert "apply_trusted_authorship" not in kwargs


def test_env_admin_can_patch_project_lifecycle_without_authorship_stamp(client):
    test_client, mock_engine = client
    mock_engine.get_memory.return_value = {
        "id": 4,
        "text": "Alice's project fact",
        "source": "project/acme/knowledge",
        "author": "alice",
    }
    mock_engine.update_memory.return_value = {
        "id": 4,
        "updated_fields": ["pinned", "archived"],
    }

    response = test_client.patch(
        "/memory/4",
        json={"pinned": True, "archived": True},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    kwargs = mock_engine.update_memory.call_args.kwargs
    assert "trusted_authorship" not in kwargs
    assert "apply_trusted_authorship" not in kwargs


def test_managed_dedup_blocker_lookup_is_scoped_to_destination_source(client):
    test_client, mock_engine = client
    import app as app_module
    from project_memory import TrustedAuthorship

    mock_engine.add_memories.return_value = []
    mock_engine.is_novel.return_value = (
        False,
        {"id": 8, "source": "project/acme/knowledge", "similarity": 0.99},
    )
    bob = TrustedAuthorship.principal("bob", "codex")
    with patch.object(app_module, "_trusted_authorship", return_value=bob):
        response = test_client.post(
            "/memory/add",
            json={
                "text": "Project fact",
                "source": "project/acme/knowledge",
                "deduplicate": True,
            },
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    assert mock_engine.is_novel.call_args.kwargs["source_exact"] == "project/acme/knowledge"


def test_legacy_fallback_dedup_remains_global_for_env_admin(client, monkeypatch):
    _, mock_engine = client
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "_fallback_extract_facts",
        lambda _messages: ["A durable decision was recorded for this project"],
    )
    mock_engine.is_novel.return_value = (
        False,
        {"id": 8, "source": "other/source", "similarity": 0.99},
    )

    app_module._run_fallback_extraction(
        "ignored transcript",
        "legacy/source",
        "stop",
        None,
    )

    kwargs = mock_engine.is_novel.call_args.kwargs
    assert "source_exact" not in kwargs


def test_get_memory_batch(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/get-batch",
        json={"ids": [1, 2]},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["missing_ids"] == [2]
    mock_engine.get_memories.assert_called_once_with([1, 2])


def test_upsert_memory(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/upsert",
        json={"text": "new text", "source": "carto/poet-pads/db", "key": "entity-1", "metadata": {"team": "carto"}},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["action"] == "created"
    mock_engine.upsert_memory.assert_called_once_with(
        text="new text",
        source="carto/poet-pads/db",
        key="entity-1",
        metadata={"team": "carto"},
    )


def test_managed_principal_authorship_reaches_add_boundary(client, monkeypatch):
    test_client, mock_engine = client
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "_get_auth",
        lambda request: AuthContext(
            role="read-write",
            prefixes=["project/demo/"],
            key_type="managed",
            key_id="key-1",
            principal_id="alice",
        ),
    )
    response = test_client.post(
        "/memory/add",
        json={
            "text": "a project decision",
            "source": "project/demo/decisions",
            "metadata": {
                "author": "mallory",
                "contributors": ["mallory"],
                "origin_client": "spoofed",
                "source_memory_ids": [999],
            },
        },
        headers={"X-API-Key": "test-key", "X-Memories-Client": "  Claude-Code "},
    )

    assert response.status_code == 200
    trusted = mock_engine.add_memories.call_args.kwargs["trusted_authorship"]
    assert trusted.author == "alice"
    assert trusted.origin_client == "claude-code"


def test_managed_principal_authorship_reaches_batch_add_boundary(client, monkeypatch):
    test_client, mock_engine = client
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "_get_auth",
        lambda request: AuthContext(
            role="read-write",
            prefixes=["project/demo/"],
            key_type="managed",
            principal_id="alice",
        ),
    )
    response = test_client.post(
        "/memory/add-batch",
        json={
            "memories": [
                {
                    "text": "first",
                    "source": "project/demo/decisions",
                    "metadata": {"author": "mallory"},
                },
                {
                    "text": "second",
                    "source": "project/demo/knowledge",
                    "metadata": {"origin_client": "mallory"},
                },
            ]
        },
        headers={"X-API-Key": "test-key", "X-Memories-Client": "hook"},
    )

    assert response.status_code == 200
    trusted = mock_engine.add_memories.call_args.kwargs["trusted_authorship"]
    assert trusted.author == "alice"
    assert trusted.origin_client == "hook"


def test_project_policy_error_is_stable_422_for_add(client):
    test_client, mock_engine = client
    mock_engine.add_memories.side_effect = ProjectMemoryPolicyError("project policy")
    response = test_client.post(
        "/memory/add",
        json={"text": "shared", "source": "project/demo/decisions"},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "project policy"


def test_upsert_batch_memory(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/upsert-batch",
        json={
            "memories": [
                {"text": "t1", "source": "a", "key": "k1"},
                {"text": "t2", "source": "b", "key": "k2"},
            ]
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["created"] == 1
    assert body["updated"] == 1
    mock_engine.upsert_memories.assert_called_once()


def test_managed_principal_authorship_reaches_upsert_paths(client, monkeypatch):
    test_client, mock_engine = client
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "_get_auth",
        lambda request: AuthContext(
            role="read-write",
            prefixes=["project/demo/"],
            key_type="managed",
            principal_id="alice",
        ),
    )

    one = test_client.post(
        "/memory/upsert",
        json={
            "text": "replacement",
            "source": "project/demo/decisions",
            "key": "decision-1",
            "metadata": {"author": "mallory"},
        },
        headers={"X-API-Key": "test-key"},
    )
    many = test_client.post(
        "/memory/upsert-batch",
        json={
            "memories": [
                {
                    "text": "replacement 2",
                    "source": "project/demo/decisions",
                    "key": "decision-2",
                    "metadata": {"origin_client": "mallory"},
                }
            ]
        },
        headers={"X-API-Key": "test-key"},
    )

    assert one.status_code == 200
    assert many.status_code == 200
    assert mock_engine.upsert_memory.call_args.kwargs["trusted_authorship"].author == "alice"
    assert mock_engine.upsert_memories.call_args.kwargs["trusted_authorship"].author == "alice"


def test_search_batch(client):
    test_client, _ = client
    response = test_client.post(
        "/search/batch",
        json={
            "queries": [
                {"query": "python", "k": 2},
                {"query": "docker", "k": 2, "hybrid": True},
            ]
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["results"]) == 2


def test_delete_by_prefix(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/memory/delete-by-prefix",
        json={"source_prefix": "carto/poet-pads/"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 4
    mock_engine.delete_by_prefix.assert_called_once_with("carto/poet-pads/", skip_snapshot=False, dry_run=False)


def test_patch_memory(client):
    test_client, mock_engine = client
    response = test_client.patch(
        "/memory/4",
        json={"text": "updated"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == 4
    mock_engine.update_memory.assert_called_once_with(
        memory_id=4,
        text="updated",
        source=None,
        metadata_patch=None,
        pinned=None,
        archived=None,
    )


def test_health_ready(client):
    test_client, mock_engine = client
    response = test_client.get("/health/ready", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    mock_engine.is_ready.assert_called_once()


def test_reload_embedder_endpoint(client):
    test_client, mock_engine = client
    response = test_client.post(
        "/maintenance/embedder/reload",
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["reloaded"] is True
    mock_engine.reload_embedder.assert_called_once_with()
