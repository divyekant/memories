"""Tests for auth hardening — missing auth checks and audit trails."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_keys():
    """App fixture with env key and managed key store for auth testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "keys.db")
        env = {
            "API_KEY": "admin-key",
            "EXTRACT_PROVIDER": "",
            "DATA_DIR": tmpdir,
            "AUDIT_LOG": "true",
        }
        with patch.dict(os.environ, env):
            import app as app_module
            importlib.reload(app_module)

            mock_engine = MagicMock()
            mock_engine.metadata = [
                {"id": 1, "text": "existing fact", "source": "claude-code/test"},
            ]
            mock_engine.search.return_value = []
            mock_engine.hybrid_search.return_value = []
            mock_engine.is_novel.return_value = (True, [])
            mock_engine.get_memory.return_value = {
                "id": 1, "text": "existing fact", "source": "claude-code/test",
            }
            mock_engine.delete_memory.return_value = {
                "deleted_id": 1, "deleted_text": "existing fact",
            }
            app_module.memory = mock_engine

            from key_store import KeyStore
            ks = KeyStore(db_path)
            app_module.key_store = ks

            from audit_log import AuditLog
            app_module.audit_log = AuditLog(os.path.join(tmpdir, "audit.db"))

            yield TestClient(app_module.app), mock_engine, ks, app_module


class TestIsNovelRequiresAuth:
    """POST /memory/is-novel should require authentication."""

    def test_is_novel_without_key_returns_401(self, app_with_keys):
        client, _, _, _ = app_with_keys
        resp = client.post(
            "/memory/is-novel",
            json={"text": "some fact", "threshold": 0.88},
        )
        assert resp.status_code == 401

    def test_is_novel_with_valid_key_succeeds(self, app_with_keys):
        client, _, _, _ = app_with_keys
        resp = client.post(
            "/memory/is-novel",
            json={"text": "some fact", "threshold": 0.88},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "is_novel" in body

    def test_is_novel_with_scoped_key_succeeds(self, app_with_keys):
        client, _, key_store, _ = app_with_keys
        created = key_store.create_key(
            name="reader", role="read-only", prefixes=["claude-code/*"]
        )
        resp = client.post(
            "/memory/is-novel",
            json={"text": "some fact", "threshold": 0.88},
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 200


class TestDeleteFailFastForReadOnly:
    """DELETE /memory/{id} should reject read-only keys before fetching memory."""

    def test_read_only_key_rejected_immediately(self, app_with_keys):
        client, mock_engine, key_store, _ = app_with_keys
        created = key_store.create_key(
            name="reader", role="read-only", prefixes=["claude-code/*"]
        )
        resp = client.delete(
            "/memory/1",
            headers={"X-API-Key": created["key"]},
        )
        assert resp.status_code == 403
        # Key check: get_memory should NOT have been called (fail-fast)
        mock_engine.get_memory.assert_not_called()

    def test_admin_key_can_still_delete(self, app_with_keys):
        client, _, _, _ = app_with_keys
        resp = client.delete(
            "/memory/1",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200


class TestDeleteAuditAction:
    """DELETE /memory/{id} should use 'memory.deleted' action in audit."""

    def test_delete_audit_uses_namespaced_action(self, app_with_keys):
        client, _, _, mod = app_with_keys
        resp = client.delete(
            "/memory/1",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.deleted")
        assert len(entries) >= 1
        assert entries[0]["resource_id"] == "1"

    def test_old_delete_action_name_gone(self, app_with_keys):
        client, _, _, mod = app_with_keys
        client.delete("/memory/1", headers={"X-API-Key": "admin-key"})
        entries = mod.audit_log.query(action="delete")
        assert len(entries) == 0


class TestConsolidateAudit:
    """POST /maintenance/consolidate should audit on non-dry-run."""

    def test_consolidate_dry_run_no_audit(self, app_with_keys):
        client, _, _, mod = app_with_keys
        with patch("app.extract_provider", MagicMock()):
            with patch("consolidator.find_clusters", return_value=[]):
                resp = client.post(
                    "/maintenance/consolidate?dry_run=true",
                    headers={"X-API-Key": "admin-key"},
                )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.consolidated")
        assert len(entries) == 0

    def test_consolidate_live_audits(self, app_with_keys):
        client, _, _, mod = app_with_keys
        with patch("app.extract_provider", MagicMock()):
            with patch("consolidator.find_clusters", return_value=[[0, 1]]):
                with patch("consolidator.consolidate_cluster", return_value={"merged": True}):
                    resp = client.post(
                        "/maintenance/consolidate?dry_run=false",
                        headers={"X-API-Key": "admin-key"},
                    )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.consolidated")
        assert len(entries) == 1


class TestPruneAudit:
    """POST /maintenance/prune should audit on non-dry-run."""

    def test_prune_dry_run_no_audit(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        with patch("consolidator.find_prune_candidates", return_value=[{"id": 1}]):
            resp = client.post(
                "/maintenance/prune?dry_run=true",
                headers={"X-API-Key": "admin-key"},
            )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.pruned")
        assert len(entries) == 0

    def test_prune_live_audits(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        with patch("consolidator.find_prune_candidates", return_value=[{"id": 1}]):
            resp = client.post(
                "/maintenance/prune?dry_run=false",
                headers={"X-API-Key": "admin-key"},
            )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.pruned")
        assert len(entries) == 1
        assert entries[0]["resource_id"] == ""
        assert "count=1" in entries[0]["source_prefix"]


class TestIndexBuildAudit:
    """POST /index/build should audit after build."""

    def test_index_build_audits(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        mock_engine.rebuild_from_files.return_value = {
            "files_processed": 2, "memories_added": 10,
        }
        resp = client.post(
            "/index/build",
            json={},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="index.rebuilt")
        assert len(entries) == 1
        assert entries[0]["resource_id"] == ""
        assert "count=10" in entries[0]["source_prefix"]


class TestDeduplicateAudit:
    """POST /memory/deduplicate should audit only on non-dry-run."""

    def test_deduplicate_dry_run_no_audit(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        mock_engine.deduplicate.return_value = {
            "duplicates": [{"ids": [1, 2]}], "removed": [],
        }
        resp = client.post(
            "/memory/deduplicate",
            json={"threshold": 0.9, "dry_run": True},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.deduplicated")
        assert len(entries) == 0

    def test_deduplicate_live_audits(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        mock_engine.deduplicate.return_value = {
            "duplicates": [{"ids": [1, 2]}],
            "removed": [2, 3],
        }
        resp = client.post(
            "/memory/deduplicate",
            json={"threshold": 0.9, "dry_run": False},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.deduplicated")
        assert len(entries) == 1
        assert entries[0]["resource_id"] == ""
        assert "count=2" in entries[0]["source_prefix"]


class TestExtractionOriginMetadata:
    """Extracted memories should carry extraction_job_id and extract_source."""

    def test_extraction_sets_origin_metadata(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "User prefers dark mode", "category": "preference"}]

        result = execute_actions(
            mock_engine, actions, facts,
            source="claude-code/myproject",
            job_id="abc123",
        )
        assert result["stored_count"] == 1
        call_kwargs = mock_engine.add_memories.call_args
        meta = call_kwargs.kwargs.get("metadata_list", [{}])[0]
        assert meta["extraction_job_id"] == "abc123"
        assert meta["extract_source"] == "claude-code/myproject"
        assert meta["category"] == "preference"

    def test_extraction_update_sets_origin_metadata(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [101]
        mock_engine.get_memory.return_value = {"id": 42, "source": "test", "text": "old"}

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42, "new_text": "updated"}]
        facts = [{"text": "original", "category": "decision"}]

        result = execute_actions(
            mock_engine, actions, facts,
            source="claude-code/proj",
            job_id="def456",
        )
        assert result["updated_count"] == 1
        call_kwargs = mock_engine.add_memories.call_args
        meta = call_kwargs.kwargs.get("metadata_list", [{}])[0]
        assert meta["extraction_job_id"] == "def456"
        assert meta["extract_source"] == "claude-code/proj"

    def test_extraction_conflict_sets_origin_metadata(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [102]

        actions = [{"action": "CONFLICT", "fact_index": 0, "old_id": 10}]
        facts = [{"text": "Conflicting fact", "category": "detail"}]

        result = execute_actions(
            mock_engine, actions, facts,
            source="test/src",
            job_id="ghi789",
        )
        assert result["stored_count"] == 1
        call_kwargs = mock_engine.add_memories.call_args
        meta = call_kwargs.kwargs.get("metadata_list", [{}])[0]
        assert meta["extraction_job_id"] == "ghi789"
        assert meta["extract_source"] == "test/src"

    def test_extraction_without_job_id_omits_origin(self):
        from llm_extract import execute_actions

        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [100]

        actions = [{"action": "ADD", "fact_index": 0}]
        facts = [{"text": "Manual fact", "category": "detail"}]

        execute_actions(mock_engine, actions, facts, source="test/proj")
        call_kwargs = mock_engine.add_memories.call_args
        meta = call_kwargs.kwargs.get("metadata_list", [{}])[0]
        assert "extraction_job_id" not in meta
        assert "extract_source" not in meta


class TestPinAuditEvent:
    """Pinning a memory should emit memory.pinned audit event."""

    def test_pin_emits_audit_event(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        mock_engine.update_memory.return_value = {"id": 1, "pinned": True}
        resp = client.patch(
            "/memory/1",
            json={"pinned": True},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.pinned")
        assert len(entries) >= 1
        assert entries[0]["resource_id"] == "1"

    def test_unpin_emits_audit_event(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        mock_engine.update_memory.return_value = {"id": 1, "pinned": False}
        resp = client.patch(
            "/memory/1",
            json={"pinned": False},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.unpinned")
        assert len(entries) >= 1
        assert entries[0]["resource_id"] == "1"


class TestLinkAuditEvent:
    """Linking memories should emit memory.linked audit event."""

    def test_link_emits_audit_event(self, app_with_keys):
        client, mock_engine, _, mod = app_with_keys
        mock_engine.get_memory.side_effect = lambda mid: {
            1: {"id": 1, "text": "fact one", "source": "claude-code/test"},
            2: {"id": 2, "text": "fact two", "source": "claude-code/test"},
        }.get(mid, {"id": mid, "text": "", "source": "claude-code/test"})
        mock_engine.add_link.return_value = {
            "from_id": 1, "to_id": 2, "type": "related_to",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        resp = client.post(
            "/memory/1/link",
            json={"to_id": 2, "type": "related_to"},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        entries = mod.audit_log.query(action="memory.linked")
        assert len(entries) >= 1
        assert entries[0]["resource_id"] == "1"
