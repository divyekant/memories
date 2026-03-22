"""Tests for Phase 0 trust/security fixes (#34–#40 + deferred auth fix).

Each class corresponds to one issue. Tests are written BEFORE implementation
so they fail first (TDD red phase).
"""

import asyncio
import importlib
import json
import os
import tempfile
import threading
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# 0.1 — Include source on update/link events (#34)
# ---------------------------------------------------------------------------

class TestEventSourceOnUpdate:
    """update_memory and add_link should include source in event payload."""

    @pytest.fixture
    def engine(self, tmp_path):
        from memory_engine import MemoryEngine
        eng = MemoryEngine(data_dir=str(tmp_path))
        eng.add_memories(
            texts=["Architecture uses microservices"],
            sources=["claude-code/myapp"],
        )
        return eng

    def test_update_event_includes_source(self, engine):
        from event_bus import EventBus
        bus = EventBus()

        with patch("memory_engine.event_bus", bus):
            engine.update_memory(0, text="Architecture uses modular monolith")

        events = bus.recent_events()
        updated = [e for e in events if e["type"] == "memory.updated"]
        assert len(updated) == 1
        assert "source" in updated[0]["data"]
        assert updated[0]["data"]["source"] == "claude-code/myapp"

    def test_link_event_includes_source(self, engine):
        from event_bus import EventBus
        bus = EventBus()

        engine.add_memories(texts=["Second fact"], sources=["claude-code/myapp"])

        with patch("memory_engine.event_bus", bus):
            engine.add_link(0, 1, "related_to")

        events = bus.recent_events()
        linked = [e for e in events if e["type"] == "memory.linked"]
        assert len(linked) == 1
        assert "source" in linked[0]["data"]
        assert linked[0]["data"]["source"] == "claude-code/myapp"


# ---------------------------------------------------------------------------
# 0.2 — Lock down search-quality endpoints to caller scope (#35)
# ---------------------------------------------------------------------------

class TestSearchQualityScope:
    """Scoped keys must only see/submit feedback for memories in their scope."""

    @pytest.fixture
    def app_with_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "keys.db")
            env = {
                "API_KEY": "admin-key",
                "EXTRACT_PROVIDER": "",
                "DATA_DIR": tmpdir,
            }
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = [
                    {"id": 1, "text": "visible fact", "source": "myapp/notes"},
                    {"id": 2, "text": "hidden fact", "source": "other/secret"},
                ]
                mock_engine.get_memory.side_effect = lambda mid: {
                    1: {"id": 1, "text": "visible fact", "source": "myapp/notes"},
                    2: {"id": 2, "text": "hidden fact", "source": "other/secret"},
                }.get(mid, None)
                app_module.memory = mock_engine

                from usage_tracker import UsageTracker
                app_module.usage_tracker = UsageTracker(os.path.join(tmpdir, "usage.db"))

                from key_store import KeyStore
                ks = KeyStore(db_path)
                app_module.key_store = ks

                scoped = ks.create_key(name="scoped-rw", role="read-write", prefixes=["myapp/*"])

                yield TestClient(app_module.app), app_module, scoped["key"]

    def test_scoped_key_cannot_submit_feedback_for_other_source(self, app_with_keys):
        tc, mod, scoped_key = app_with_keys
        resp = tc.post(
            "/search/feedback",
            json={"memory_id": 2, "query": "test", "signal": "useful"},
            headers={"X-API-Key": scoped_key},
        )
        assert resp.status_code == 403

    def test_scoped_key_can_submit_feedback_for_own_source(self, app_with_keys):
        tc, mod, scoped_key = app_with_keys
        resp = tc.post(
            "/search/feedback",
            json={"memory_id": 1, "query": "test", "signal": "useful"},
            headers={"X-API-Key": scoped_key},
        )
        assert resp.status_code == 200

    def test_search_quality_metrics_respects_source_prefix(self, app_with_keys):
        tc, mod, scoped_key = app_with_keys
        # Admin submits feedback for both sources
        tc.post(
            "/search/feedback",
            json={"memory_id": 1, "query": "q", "signal": "useful"},
            headers={"X-API-Key": "admin-key"},
        )
        tc.post(
            "/search/feedback",
            json={"memory_id": 2, "query": "q", "signal": "useful"},
            headers={"X-API-Key": "admin-key"},
        )
        # Scoped key should only see feedback for its prefix
        resp = tc.get(
            "/metrics/search-quality?source_prefix=myapp",
            headers={"X-API-Key": scoped_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Should only count feedback for memory_id=1 (source=myapp/notes)
        assert body["feedback"]["useful"] <= 1


# ---------------------------------------------------------------------------
# 0.3 — Add real rollback to reembed after destructive migration (#36)
# ---------------------------------------------------------------------------

class TestReembedRollback:
    """If re-embedding fails mid-way, the original collection must be preserved."""

    @pytest.fixture
    def engine(self, tmp_path):
        from memory_engine import MemoryEngine
        eng = MemoryEngine(data_dir=str(tmp_path))
        eng.add_memories(
            texts=["Python best practices", "Docker container patterns"],
            sources=["test/a", "test/b"],
        )
        return eng

    def test_failed_encode_preserves_collection(self, engine):
        """If _encode fails, the original collection is untouched (encode-first)."""
        original_count = engine.qdrant_store.count()
        assert original_count == 2

        with patch.object(
            engine, "_encode", side_effect=RuntimeError("Embedding failure")
        ):
            with pytest.raises(RuntimeError):
                engine._reindex_store_from_metadata()

        # Encode failed before recreate_collection — original intact
        final_count = engine.qdrant_store.count()
        assert final_count == original_count, (
            f"Collection was destroyed: had {original_count}, now has {final_count}"
        )

    def test_failed_upsert_attempts_rollback(self, engine):
        """If upsert fails after recreate, rollback restores from cached points."""
        original_count = engine.qdrant_store.count()
        assert original_count == 2

        original_upsert = engine.qdrant_store.upsert_points
        call_count = [0]

        def failing_upsert(points):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Simulated upsert failure")
            # Rollback upserts should succeed
            return original_upsert(points)

        with patch.object(
            engine.qdrant_store, "upsert_points", side_effect=failing_upsert
        ):
            with pytest.raises(RuntimeError):
                engine._reindex_store_from_metadata()

        # Rollback should have restored from cached points
        final_count = engine.qdrant_store.count()
        assert final_count == original_count, (
            f"Rollback failed: had {original_count}, now has {final_count}"
        )


# ---------------------------------------------------------------------------
# 0.4 — Preserve webhook delivery from worker threads (#37)
# ---------------------------------------------------------------------------

class TestWebhookFromThread:
    """Webhook delivery should work from threads without an event loop."""

    def test_dispatch_webhooks_from_thread_without_loop(self):
        from event_bus import EventBus, Event

        bus = EventBus()
        bus.register_webhook("http://localhost:9999/hook", events=["memory.added"])

        delivered = []

        # Patch sync HTTP client to capture calls (used as context manager)
        with patch("event_bus.httpx.Client") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_instance)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_instance.post.side_effect = lambda url, json: delivered.append((url, json))

            event = Event(type="memory.added", data={"id": 1, "text": "test"})

            # Run from a thread that has no event loop
            exc_holder = []
            def thread_fn():
                try:
                    bus._dispatch_webhooks(event)
                except Exception as e:
                    exc_holder.append(e)

            t = threading.Thread(target=thread_fn)
            t.start()
            t.join(timeout=5)

            assert not exc_holder, f"Exception in thread: {exc_holder}"
            assert len(delivered) >= 1


# ---------------------------------------------------------------------------
# 0.5 — Clarify/tighten compaction cluster semantics (#38)
# ---------------------------------------------------------------------------

class TestCompactionSemantics:
    """compact = dry-run clusters, consolidate = LLM merge."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "API_KEY": "admin-key",
                "EXTRACT_PROVIDER": "",
                "DATA_DIR": tmpdir,
            }
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.find_similar_clusters.return_value = [[0, 1]]
                mock_engine.get_memory.return_value = {
                    "id": 0, "text": "fact", "source": "test",
                }
                app_module.memory = mock_engine

                yield TestClient(app_module.app), app_module, mock_engine

    def test_compact_is_read_only(self, client):
        tc, mod, mock_engine = client
        resp = tc.post(
            "/maintenance/compact",
            json={"threshold": 0.85},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "clusters" in body
        assert "cluster_count" in body
        # compact should NOT call delete or add
        mock_engine.delete_memory.assert_not_called()
        mock_engine.delete_memories.assert_not_called()
        mock_engine.add_memories.assert_not_called()

    def test_compact_returns_cluster_details(self, client):
        tc, mod, mock_engine = client
        resp = tc.post(
            "/maintenance/compact",
            json={"threshold": 0.85},
            headers={"X-API-Key": "admin-key"},
        )
        body = resp.json()
        assert body["cluster_count"] >= 1
        assert "memories" in body["clusters"][0]


# ---------------------------------------------------------------------------
# 0.6 — Record real source for admin/env deletes in audit (#39)
# ---------------------------------------------------------------------------

class TestAuditDeleteSource:
    """Admin deletes should record the deleted memory's actual source."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
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
                    {"id": 42, "text": "secret fact", "source": "claude-code/myapp"},
                ]
                mock_engine.get_memory.return_value = {
                    "id": 42, "text": "secret fact", "source": "claude-code/myapp",
                }
                mock_engine.delete_memory.return_value = {
                    "deleted_id": 42, "deleted_text": "secret fact",
                }
                app_module.memory = mock_engine

                from audit_log import AuditLog
                app_module.audit_log = AuditLog(os.path.join(tmpdir, "audit.db"))

                yield TestClient(app_module.app), app_module

    def test_admin_delete_records_source_in_audit(self, client):
        tc, mod = client
        resp = tc.delete(
            "/memory/42",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200

        entries = mod.audit_log.query(action="memory.deleted")
        assert len(entries) >= 1
        assert entries[0]["source_prefix"] == "claude-code/myapp"


# ---------------------------------------------------------------------------
# 0.7 — Fix search-quality metrics period and batch counting (#40)
# ---------------------------------------------------------------------------

class TestBatchSearchMetrics:
    """Batch search must log individual retrievals that appear in search-quality."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "API_KEY": "admin-key",
                "EXTRACT_PROVIDER": "",
                "DATA_DIR": tmpdir,
                "USAGE_TRACKING": "true",
            }
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.metadata = [
                    {"id": 0, "text": "fact one", "source": "test"},
                    {"id": 1, "text": "fact two", "source": "test"},
                ]
                mock_engine.search.return_value = [
                    {"id": 0, "text": "fact one", "source": "test", "similarity": 0.95},
                ]
                mock_engine.hybrid_search.return_value = [
                    {"id": 0, "text": "fact one", "source": "test", "rrf_score": 0.9},
                ]
                app_module.memory = mock_engine

                from usage_tracker import UsageTracker
                app_module.usage_tracker = UsageTracker(os.path.join(tmpdir, "usage.db"))

                yield TestClient(app_module.app), app_module

    def test_batch_search_logs_individual_api_events(self, client):
        tc, mod = client
        resp = tc.post(
            "/search/batch",
            json={"queries": [
                {"query": "first query", "k": 1},
                {"query": "second query", "k": 1},
            ]},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200

        # Check search-quality metrics — batch searches should count
        quality = mod.usage_tracker.get_search_quality(period="all")
        # Each individual search in the batch logs retrievals
        assert quality["rank_distribution"]["top_3"] >= 2

    def test_batch_search_logs_api_event_per_query(self, client):
        tc, mod = client
        resp = tc.post(
            "/search/batch",
            json={"queries": [
                {"query": "first query", "k": 1, "source": "test/batch"},
                {"query": "second query", "k": 1, "source": "test/batch"},
            ]},
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200

        # Batch should log search events so they appear in total_searches
        quality = mod.usage_tracker.get_search_quality(period="all")
        assert quality["total_searches"] >= 2


# ---------------------------------------------------------------------------
# 0.8 — Scope extraction auth (deferred security fix)
# ---------------------------------------------------------------------------

class TestExtractionAuthScope:
    """Scoped key extraction cannot update/delete memories outside its prefix."""

    @pytest.fixture
    def engine(self, tmp_path):
        from memory_engine import MemoryEngine
        eng = MemoryEngine(data_dir=str(tmp_path))
        eng.add_memories(
            texts=["Fact inside scope", "Fact outside scope"],
            sources=["myapp/notes", "other/secret"],
        )
        return eng

    def test_scoped_update_cannot_touch_outside_prefix(self, engine):
        from llm_extract import execute_actions

        actions = [
            {"action": "UPDATE", "fact_index": 0, "old_id": 1, "new_text": "Overwritten"},
        ]
        facts = [{"category": "detail", "text": "Overwritten"}]

        result = execute_actions(
            engine, actions, facts,
            source="myapp/notes",
            allowed_prefixes=["myapp/*"],
        )

        # Memory 1 (source=other/secret) should NOT be deleted/updated
        assert engine._id_exists(1), "Memory outside scope was incorrectly deleted"
        # The action should have been treated as an error
        error_actions = [a for a in result["actions"] if a.get("action") == "error"]
        assert len(error_actions) >= 1

    def test_scoped_delete_cannot_touch_outside_prefix(self, engine):
        from llm_extract import execute_actions

        actions = [
            {"action": "DELETE", "fact_index": 0, "old_id": 1},
        ]
        facts = [{"category": "detail", "text": "irrelevant"}]

        result = execute_actions(
            engine, actions, facts,
            source="myapp/notes",
            allowed_prefixes=["myapp/*"],
        )

        assert engine._id_exists(1), "Memory outside scope was incorrectly deleted"
        error_actions = [a for a in result["actions"] if a.get("action") == "error"]
        assert len(error_actions) >= 1

    def test_scoped_update_works_inside_prefix(self, engine):
        from llm_extract import execute_actions

        actions = [
            {"action": "UPDATE", "fact_index": 0, "old_id": 0, "new_text": "Updated fact"},
        ]
        facts = [{"category": "detail", "text": "Updated fact"}]

        result = execute_actions(
            engine, actions, facts,
            source="myapp/notes",
            allowed_prefixes=["myapp/*"],
        )

        assert result["updated_count"] == 1
        assert not engine._id_exists(0), "Old memory should have been replaced"

    def test_admin_key_can_update_any_source(self, engine):
        """allowed_prefixes=None means admin / unrestricted."""
        from llm_extract import execute_actions

        actions = [
            {"action": "UPDATE", "fact_index": 0, "old_id": 1, "new_text": "Admin override"},
        ]
        facts = [{"category": "detail", "text": "Admin override"}]

        result = execute_actions(
            engine, actions, facts,
            source="other/secret",
            allowed_prefixes=None,
        )

        assert result["updated_count"] == 1
