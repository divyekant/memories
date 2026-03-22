"""Tests for scheduled maintenance (consolidation/pruning) not blocking event loop."""

import asyncio
import inspect
import random
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


class TestFindClustersRandomSampling:
    """Verify find_clusters randomizes candidates instead of taking first N."""

    def test_candidates_are_randomly_sampled_not_sliced(self):
        """When max_candidates < len(candidates), selection must be random."""
        from consolidator import find_clusters

        # Create 100 memories with sequential IDs
        mems = [
            {"id": i, "text": f"fact about topic {i}", "source": "test"}
            for i in range(100)
        ]

        engine = MagicMock()
        engine.metadata = mems
        engine.hybrid_search.return_value = []

        # Run multiple times — if we always get IDs 0-19, it's slicing, not random
        seen_ids = set()
        for _ in range(5):
            engine.hybrid_search.reset_mock()
            find_clusters(engine, max_candidates=20)
            # Collect which IDs were searched (from the query text)
            for call_args in engine.hybrid_search.call_args_list:
                query = call_args[1].get("query", call_args[0][0] if call_args[0] else "")
                # Extract ID from "fact about topic {i}"
                for m in mems:
                    if m["text"] == query:
                        seen_ids.add(m["id"])
                        break

        # With random sampling over 5 runs of 20 from 100,
        # we should see more than just the first 20 IDs
        assert len(seen_ids) > 20, (
            f"Expected random sampling to cover >20 unique IDs across 5 runs, "
            f"but only saw {len(seen_ids)}. Candidates are likely sliced, not sampled."
        )

    def test_random_sample_uses_all_candidates_when_fewer_than_max(self):
        """When candidates <= max_candidates, all should be used (no sampling)."""
        from consolidator import find_clusters

        mems = [
            {"id": i, "text": f"fact {i}", "source": "test"}
            for i in range(10)
        ]

        engine = MagicMock()
        engine.metadata = mems
        engine.hybrid_search.return_value = []

        find_clusters(engine, max_candidates=50)

        # All 10 should be searched
        assert engine.hybrid_search.call_count == 10


class TestPruningToleratesConcurrentDeletes:
    """Verify _run_scheduled_pruning handles missing memories gracefully."""

    def test_pruning_continues_when_delete_raises(self):
        """If a memory was already deleted concurrently, pruning should skip it."""
        import app as app_module

        candidates = [
            {"id": 1, "text": "stale 1"},
            {"id": 2, "text": "stale 2"},
            {"id": 3, "text": "stale 3"},
        ]

        delete_calls = []

        def fake_delete(mem_id):
            delete_calls.append(mem_id)
            if mem_id == 2:
                raise ValueError(f"Memory {mem_id} not found")

        with patch.object(app_module, "memory") as mock_memory, \
             patch.object(app_module, "usage_tracker") as mock_tracker, \
             patch("consolidator.find_prune_candidates", return_value=candidates):
            mock_memory.metadata = [
                {"id": 1, "text": "t", "source": "s"},
                {"id": 2, "text": "t", "source": "s"},
                {"id": 3, "text": "t", "source": "s"},
            ]
            mock_tracker.get_unretrieved_memory_ids.return_value = [1, 2, 3]
            mock_memory.delete_memory.side_effect = fake_delete

            result = app_module._run_scheduled_pruning()

            # Should have attempted all 3 deletes
            assert delete_calls == [1, 2, 3]
            # Should NOT have raised — the function completed

    def test_pruning_continues_when_delete_raises_key_error(self):
        """KeyError from concurrent delete should also be tolerated."""
        import app as app_module

        candidates = [
            {"id": 10, "text": "stale 10"},
            {"id": 20, "text": "stale 20"},
        ]

        def fake_delete(mem_id):
            if mem_id == 10:
                raise KeyError(f"ID {mem_id}")

        with patch.object(app_module, "memory") as mock_memory, \
             patch.object(app_module, "usage_tracker") as mock_tracker, \
             patch("consolidator.find_prune_candidates", return_value=candidates):
            mock_memory.metadata = [
                {"id": 10, "text": "t", "source": "s"},
                {"id": 20, "text": "t", "source": "s"},
            ]
            mock_tracker.get_unretrieved_memory_ids.return_value = [10, 20]
            mock_memory.delete_memory.side_effect = fake_delete

            # Should not raise
            result = app_module._run_scheduled_pruning()

    def test_pruning_returns_count_of_successful_deletes(self):
        """Return value should reflect only successful deletions."""
        import app as app_module

        candidates = [
            {"id": 1, "text": "stale 1"},
            {"id": 2, "text": "stale 2"},
            {"id": 3, "text": "stale 3"},
        ]

        def fake_delete(mem_id):
            if mem_id == 2:
                raise ValueError("already gone")

        with patch.object(app_module, "memory") as mock_memory, \
             patch.object(app_module, "usage_tracker") as mock_tracker, \
             patch("consolidator.find_prune_candidates", return_value=candidates):
            mock_memory.metadata = [
                {"id": 1, "text": "t", "source": "s"},
                {"id": 2, "text": "t", "source": "s"},
                {"id": 3, "text": "t", "source": "s"},
            ]
            mock_tracker.get_unretrieved_memory_ids.return_value = [1, 2, 3]
            mock_memory.delete_memory.side_effect = fake_delete

            result = app_module._run_scheduled_pruning()

            # Only 2 out of 3 succeeded
            assert result == 2
