"""Snapshot round-trip validation: add → snapshot → delete → restore → verify."""

from __future__ import annotations

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_engine():
    """Create a TestClient with a mock engine that tracks state for round-trip testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
        with patch.dict(os.environ, env):
            import app as app_module
            importlib.reload(app_module)

            # Build a stateful mock engine that simulates add/list/delete/snapshot
            mock_engine = MagicMock()
            _memories = []
            _next_id = [1]
            _snapshots = {}  # name -> list of memories at snapshot time

            def _add_memories(texts, sources, metadata_list=None, deduplicate=False, **kw):
                ids = []
                for text, source in zip(texts, sources):
                    mid = _next_id[0]
                    _next_id[0] += 1
                    _memories.append({"id": mid, "text": text, "source": source})
                    ids.append(mid)
                mock_engine.metadata = list(_memories)
                return ids

            def _list_memories(offset=0, limit=20, source_filter=None):
                filtered = _memories
                if source_filter:
                    filtered = [m for m in _memories if m.get("source", "").startswith(source_filter)]
                page = filtered[offset:offset + limit]
                return {"memories": page, "total": len(filtered)}

            def _delete_memories(ids, skip_snapshot=False):
                deleted = []
                missing = []
                for mid in ids:
                    found = [m for m in _memories if m["id"] == mid]
                    if found:
                        _memories.remove(found[0])
                        deleted.append(mid)
                    else:
                        missing.append(mid)
                mock_engine.metadata = list(_memories)
                return {"deleted_count": len(deleted), "deleted_ids": deleted, "missing_ids": missing}

            def _get_memory(mid):
                for m in _memories:
                    if m["id"] == mid:
                        return m
                raise ValueError(f"Memory {mid} not found")

            def _count_memories(source_prefix=None):
                if source_prefix:
                    return len([m for m in _memories if m.get("source", "").startswith(source_prefix)])
                return len(_memories)

            def _snapshot_before_delete(reason):
                name = f"snap-{len(_snapshots) + 1}"
                _snapshots[name] = [dict(m) for m in _memories]
                return name

            def _list_snapshots():
                return [{"name": n, "reason": "test", "timestamp": "2026-01-01T00:00:00Z", "point_count": len(s)} for n, s in _snapshots.items()]

            def _restore_snapshot(name):
                if name not in _snapshots:
                    raise ValueError(f"Snapshot {name} not found")
                _memories.clear()
                _memories.extend(_snapshots[name])
                mock_engine.metadata = list(_memories)

            mock_engine.add_memories.side_effect = _add_memories
            mock_engine.list_memories.side_effect = _list_memories
            mock_engine.delete_memories.side_effect = _delete_memories
            mock_engine.get_memory.side_effect = _get_memory
            mock_engine.count_memories.side_effect = _count_memories
            mock_engine._snapshot_before_delete.side_effect = _snapshot_before_delete
            mock_engine.list_snapshots.side_effect = _list_snapshots
            mock_engine.metadata = []
            mock_engine.qdrant_store = MagicMock()
            mock_engine.qdrant_store.restore_snapshot.side_effect = _restore_snapshot

            app_module.memory = mock_engine
            yield TestClient(app_module.app), mock_engine, _memories, _snapshots


class TestSnapshotRoundTrip:
    """Full lifecycle: add → snapshot → delete → restore → verify."""

    def test_add_snapshot_delete_restore_verify(self, client_with_engine):
        tc, mock_engine, _memories, _snapshots = client_with_engine

        # 1. Add memories
        for text, source in [
            ("Python uses indentation for scoping", "test/lang"),
            ("Rust has zero-cost abstractions", "test/lang"),
            ("PostgreSQL supports JSONB columns", "test/db"),
        ]:
            resp = tc.post("/memory/add", json={"text": text, "source": source})
            assert resp.status_code == 200
            assert resp.json()["success"] is True

        # Verify 3 memories exist
        resp = tc.get("/memories?limit=100")
        assert resp.status_code == 200
        assert resp.json()["total"] == 3

        # 2. Create snapshot
        resp = tc.post("/snapshots")
        assert resp.status_code == 200
        snapshot_name = resp.json()["snapshot"]
        assert snapshot_name is not None

        # Verify snapshot appears in list
        resp = tc.get("/snapshots")
        assert resp.status_code == 200
        assert len(resp.json()["snapshots"]) == 1

        # 3. Delete some memories
        resp = tc.post("/memory/delete-batch", json={"ids": [1, 2]})
        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 2

        # Verify only 1 memory remains
        resp = tc.get("/memories?limit=100")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        # 4. Restore snapshot
        with patch("app._require_admin"):
            resp = tc.post(f"/snapshots/{snapshot_name}/restore")
        assert resp.status_code == 200

        # 5. Verify all 3 are back
        resp = tc.get("/memories?limit=100")
        assert resp.status_code == 200
        assert resp.json()["total"] == 3
        texts = {m["text"] for m in resp.json()["memories"]}
        assert "Python uses indentation for scoping" in texts
        assert "Rust has zero-cost abstractions" in texts
        assert "PostgreSQL supports JSONB columns" in texts

    def test_snapshot_preserves_sources(self, client_with_engine):
        """Snapshot restore preserves source metadata."""
        tc, mock_engine, _memories, _snapshots = client_with_engine

        tc.post("/memory/add", json={"text": "fact A", "source": "alpha/proj"})
        tc.post("/memory/add", json={"text": "fact B", "source": "beta/proj"})

        # Snapshot
        resp = tc.post("/snapshots")
        snapshot_name = resp.json()["snapshot"]

        # Delete all
        tc.post("/memory/delete-batch", json={"ids": [1, 2]})
        resp = tc.get("/memories?limit=100")
        assert resp.json()["total"] == 0

        # Restore
        with patch("app._require_admin"):
            tc.post(f"/snapshots/{snapshot_name}/restore")

        # Sources should be preserved
        resp = tc.get("/memories?limit=100")
        sources = {m["source"] for m in resp.json()["memories"]}
        assert "alpha/proj" in sources
        assert "beta/proj" in sources

    def test_multiple_snapshots_restore_correct_one(self, client_with_engine):
        """When multiple snapshots exist, restoring a specific one returns that state."""
        tc, mock_engine, _memories, _snapshots = client_with_engine

        # State 1: 1 memory
        tc.post("/memory/add", json={"text": "first fact", "source": "s/"})
        resp = tc.post("/snapshots")
        snap1 = resp.json()["snapshot"]

        # State 2: 2 memories
        tc.post("/memory/add", json={"text": "second fact", "source": "s/"})
        resp = tc.post("/snapshots")
        snap2 = resp.json()["snapshot"]

        # Delete everything
        tc.post("/memory/delete-batch", json={"ids": [1, 2]})
        assert tc.get("/memories?limit=100").json()["total"] == 0

        # Restore snap1 — should have 1 memory
        with patch("app._require_admin"):
            tc.post(f"/snapshots/{snap1}/restore")
        resp = tc.get("/memories?limit=100")
        assert resp.json()["total"] == 1
        assert resp.json()["memories"][0]["text"] == "first fact"

    def test_snapshot_list_reflects_creation(self, client_with_engine):
        """Each snapshot creation adds an entry to the list."""
        tc, mock_engine, _memories, _snapshots = client_with_engine

        assert tc.get("/snapshots").json()["count"] == 0

        tc.post("/memory/add", json={"text": "fact", "source": "s/"})
        tc.post("/snapshots")
        assert tc.get("/snapshots").json()["count"] == 1

        tc.post("/snapshots")
        assert tc.get("/snapshots").json()["count"] == 2
