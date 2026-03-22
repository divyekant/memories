"""Tests for scheduled maintenance (consolidation/pruning) not blocking event loop."""

import asyncio
import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


class TestMaintenanceSchedulerUsesThreadpool:
    """Verify scheduled consolidation/pruning runs in threadpool, not on event loop."""

    def test_consolidation_runs_in_threadpool(self):
        """Scheduled consolidation must use run_in_threadpool, not block the loop."""
        import app as app_module

        fake_now = datetime(2026, 3, 22, 3, 2, 0, tzinfo=timezone.utc)
        threadpool_called = []

        async def fake_run_in_threadpool(fn, *args, **kwargs):
            threadpool_called.append(fn.__name__ if hasattr(fn, '__name__') else str(fn))
            return fn(*args, **kwargs)

        with patch.object(app_module, "memory") as mock_memory, \
             patch.object(app_module, "extract_provider") as mock_provider, \
             patch.object(app_module, "usage_tracker") as mock_tracker, \
             patch.object(app_module, "datetime") as mock_dt, \
             patch.object(app_module, "run_in_threadpool", side_effect=fake_run_in_threadpool), \
             patch("consolidator.find_clusters", return_value=[]) as mock_find:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_memory.metadata = []

            async def _run_once():
                task = asyncio.create_task(app_module._maintenance_scheduler())
                await asyncio.sleep(0.1)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            asyncio.run(_run_once())

            assert len(threadpool_called) > 0, \
                "Consolidation must run via run_in_threadpool to avoid blocking the event loop"

    def test_pruning_runs_in_threadpool(self):
        """Scheduled pruning must also use run_in_threadpool."""
        import app as app_module

        # Sunday at 4 AM UTC triggers pruning
        fake_now = datetime(2026, 3, 22, 4, 2, 0, tzinfo=timezone.utc)
        # 2026-03-22 is a Sunday (weekday() == 6)
        assert fake_now.weekday() == 6

        threadpool_called = []

        async def fake_run_in_threadpool(fn, *args, **kwargs):
            threadpool_called.append(fn.__name__ if hasattr(fn, '__name__') else str(fn))
            return fn(*args, **kwargs)

        with patch.object(app_module, "memory") as mock_memory, \
             patch.object(app_module, "extract_provider") as mock_provider, \
             patch.object(app_module, "usage_tracker") as mock_tracker, \
             patch.object(app_module, "datetime") as mock_dt, \
             patch.object(app_module, "run_in_threadpool", side_effect=fake_run_in_threadpool), \
             patch("consolidator.find_prune_candidates", return_value=[]) as mock_prune:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_memory.metadata = [{"id": 1, "text": "t", "source": "s"}]
            mock_tracker.get_unretrieved_memory_ids.return_value = []

            async def _run_once():
                task = asyncio.create_task(app_module._maintenance_scheduler())
                await asyncio.sleep(0.1)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            asyncio.run(_run_once())

            assert len(threadpool_called) > 0, \
                "Pruning must run via run_in_threadpool to avoid blocking the event loop"


class TestFindClustersMaxCandidates:
    """Verify find_clusters respects max_candidates limit."""

    def test_max_candidates_limits_iteration(self):
        from consolidator import find_clusters

        mems = [
            {"id": i, "text": f"fact about topic {i}", "source": "test"}
            for i in range(100)
        ]

        engine = MagicMock()
        engine.metadata = mems
        engine.hybrid_search.return_value = []

        find_clusters(engine, max_candidates=20)

        # Should only have searched up to 20 candidates, not all 100
        assert engine.hybrid_search.call_count <= 20

    def test_max_candidates_zero_means_unlimited(self):
        from consolidator import find_clusters

        mems = [
            {"id": i, "text": f"fact {i}", "source": "test"}
            for i in range(10)
        ]

        engine = MagicMock()
        engine.metadata = mems
        engine.hybrid_search.return_value = []

        find_clusters(engine, max_candidates=0)

        assert engine.hybrid_search.call_count == 10

    def test_default_max_candidates_is_set(self):
        """Default max_candidates should be set to a reasonable limit."""
        from consolidator import find_clusters

        sig = inspect.signature(find_clusters)
        default = sig.parameters["max_candidates"].default
        assert default > 0, "Default max_candidates should be positive to prevent O(n) on large collections"
        assert default <= 1000, "Default max_candidates should be reasonable"


class TestFindClustersProgressLogging:
    """Verify find_clusters logs progress for observability."""

    def test_logs_candidate_count(self):
        from consolidator import find_clusters

        mems = [
            {"id": i, "text": f"fact {i}", "source": "test"}
            for i in range(5)
        ]

        engine = MagicMock()
        engine.metadata = mems
        engine.hybrid_search.return_value = []

        with patch("consolidator.logger") as mock_logger:
            find_clusters(engine, max_candidates=50)
            log_messages = [str(c) for c in mock_logger.info.call_args_list]
            assert any("5" in msg for msg in log_messages), \
                "Should log the number of candidates being processed"
