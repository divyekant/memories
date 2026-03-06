"""Tests for export/import API endpoints."""

import importlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_engine():
    eng = MagicMock()
    eng.export_memories.return_value = [
        json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                     "exported_at": "2026-01-01T00:00:00Z",
                     "source_filter": None, "since": None, "until": None}),
        json.dumps({"text": "hello", "source": "s/",
                     "created_at": "2026-01-01T00:00:00Z",
                     "updated_at": "2026-01-01T00:00:00Z"}),
    ]
    eng.import_memories.return_value = {
        "imported": 2, "skipped": 0, "updated": 0,
        "errors": [], "backup": "pre-import_20260305",
    }
    eng.stats_light.return_value = {"total_memories": 5, "dimension": 384, "model": "all-MiniLM-L6-v2"}
    eng.is_ready.return_value = {"ready": True, "status": "ready"}
    return eng


@pytest.fixture
def client(mock_engine):
    with patch.dict(os.environ, {"API_KEY": "", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


class TestExportEndpoint:
    def test_export_returns_ndjson(self, client):
        test_client, mock_engine = client
        resp = test_client.get("/export")
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers.get("content-type", "")
        lines = [l for l in resp.text.strip().split("\n") if l.strip()]
        assert len(lines) == 2
        header = json.loads(lines[0])
        assert header["_header"] is True

    def test_export_passes_source_filter(self, client):
        test_client, mock_engine = client
        test_client.get("/export?source=proj/")
        mock_engine.export_memories.assert_called_once()
        kwargs = mock_engine.export_memories.call_args
        assert "proj/" in str(kwargs)

    def test_export_passes_since_and_until(self, client):
        test_client, mock_engine = client
        test_client.get("/export?since=2026-01-01&until=2026-02-01")
        mock_engine.export_memories.assert_called_once()
        kwargs = mock_engine.export_memories.call_args
        assert "2026-01-01" in str(kwargs)
        assert "2026-02-01" in str(kwargs)


class TestImportEndpoint:
    def test_import_add(self, client):
        test_client, mock_engine = client
        body = "\n".join([
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "one", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ])
        resp = test_client.post(
            "/import?strategy=add",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2

    def test_import_invalid_strategy(self, client):
        test_client, mock_engine = client
        body = json.dumps({"_header": True, "count": 0, "version": "2.0.0",
                           "exported_at": "2026-01-01T00:00:00Z",
                           "source_filter": None, "since": None, "until": None})
        resp = test_client.post(
            "/import?strategy=invalid",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 400

    def test_import_with_source_remap(self, client):
        test_client, mock_engine = client
        body = "\n".join([
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "one", "source": "old/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ])
        resp = test_client.post(
            "/import?strategy=add&source_remap=old%2F%3Dnew%2F",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200
        mock_engine.import_memories.assert_called_once()
        call_kwargs = mock_engine.import_memories.call_args
        # source_remap should be passed as a tuple ("old/", "new/")
        assert call_kwargs[1]["source_remap"] == ("old/", "new/")

    def test_import_no_backup(self, client):
        test_client, mock_engine = client
        body = "\n".join([
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "one", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ])
        resp = test_client.post(
            "/import?strategy=add&no_backup=true",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200
        call_kwargs = mock_engine.import_memories.call_args
        # create_backup should be False
        assert "create_backup=False" in str(call_kwargs) or call_kwargs[1].get("create_backup") is False
