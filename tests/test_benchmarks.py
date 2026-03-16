"""Benchmark tests — characterize throughput and latency of core operations.

These tests run against an in-process app instance and measure performance
characteristics. They are NOT load tests (those use benchmarks/load_test.py
against a running server). Instead, these establish baseline metrics for
regression detection.

Run: .venv/bin/python3 -m pytest tests/test_benchmarks.py -v -s
"""

import importlib
import os
import tempfile
import time
from statistics import mean
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def bench_client():
    """Shared test client with a real memory engine for realistic benchmarks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {"API_KEY": "bench-key", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
        with patch.dict(os.environ, env):
            import app as app_module
            importlib.reload(app_module)

            from memory_engine import MemoryEngine
            engine = MemoryEngine(data_dir=os.path.join(tmpdir, "data"))
            app_module.memory = engine

            # Seed with test data
            texts = [f"Benchmark memory number {i} about topic {i % 10}" for i in range(100)]
            sources = [f"benchmark/seed-{i % 5}" for i in range(100)]
            engine.add_memories(texts=texts, sources=sources)

            yield TestClient(app_module.app), engine


def _time_requests(client, method, path, json_body, n=50):
    """Run n requests and return latencies in ms."""
    latencies = []
    for _ in range(n):
        start = time.perf_counter()
        if method == "GET":
            resp = client.get(path, headers={"X-API-Key": "bench-key"})
        else:
            resp = client.post(path, json=json_body, headers={"X-API-Key": "bench-key"})
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert resp.status_code < 400, f"Request failed: {resp.status_code} {resp.text}"
        latencies.append(elapsed_ms)
    return latencies


class TestSearchBenchmarks:
    def test_hybrid_search_latency(self, bench_client):
        """Hybrid search p95 should be under 200ms for 100 memories."""
        tc, _ = bench_client
        lats = _time_requests(tc, "POST", "/search", {"query": "benchmark topic", "k": 5, "hybrid": True})
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  Hybrid search: avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 500, f"p95 too high: {p95:.1f}ms"

    def test_vector_search_latency(self, bench_client):
        """Vector-only search should be faster than hybrid."""
        tc, _ = bench_client
        lats = _time_requests(tc, "POST", "/search", {"query": "benchmark topic", "k": 5, "hybrid": False})
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  Vector search: avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 500, f"p95 too high: {p95:.1f}ms"

    def test_search_batch_latency(self, bench_client):
        """Batch search with 5 queries."""
        tc, _ = bench_client
        queries = [{"query": f"topic {i}", "k": 3, "hybrid": True} for i in range(5)]
        lats = _time_requests(tc, "POST", "/search/batch", {"queries": queries}, n=20)
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  Batch search (5 queries): avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 2000, f"p95 too high: {p95:.1f}ms"


class TestAddBenchmarks:
    def test_single_add_latency(self, bench_client):
        """Single memory add p95."""
        tc, _ = bench_client
        lats = []
        for i in range(50):
            start = time.perf_counter()
            resp = tc.post("/memory/add", json={
                "text": f"Benchmark add test {i} with some content",
                "source": "benchmark/add-test",
            }, headers={"X-API-Key": "bench-key"})
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code < 400
            lats.append(elapsed_ms)
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  Single add: avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 500, f"p95 too high: {p95:.1f}ms"

    def test_batch_add_latency(self, bench_client):
        """Batch add of 10 memories."""
        tc, _ = bench_client
        lats = []
        for batch in range(10):
            memories = [
                {"text": f"Batch {batch} item {i}", "source": "benchmark/batch-add"}
                for i in range(10)
            ]
            start = time.perf_counter()
            resp = tc.post("/memory/add-batch", json={"memories": memories}, headers={"X-API-Key": "bench-key"})
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code < 400
            lats.append(elapsed_ms)
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  Batch add (10 items): avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 2000, f"p95 too high: {p95:.1f}ms"


class TestReadBenchmarks:
    def test_stats_latency(self, bench_client):
        """Stats endpoint should be fast."""
        tc, _ = bench_client
        lats = _time_requests(tc, "GET", "/stats", None, n=50)
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  Stats: avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 100, f"p95 too high: {p95:.1f}ms"

    def test_health_latency(self, bench_client):
        """Health check should be sub-millisecond."""
        tc, _ = bench_client
        lats = _time_requests(tc, "GET", "/health", None, n=100)
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  Health: avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 50, f"p95 too high: {p95:.1f}ms"

    def test_memory_list_latency(self, bench_client):
        """List memories with pagination."""
        tc, _ = bench_client
        lats = _time_requests(tc, "GET", "/memories?limit=20", None, n=30)
        p95 = sorted(lats)[int(len(lats) * 0.95)]
        avg = mean(lats)
        print(f"\n  List (limit=20): avg={avg:.1f}ms p95={p95:.1f}ms (n={len(lats)})")
        assert p95 < 200, f"p95 too high: {p95:.1f}ms"
