#!/usr/bin/env python3
"""Load testing harness for Memories API.

Simulates concurrent AI assistant workloads using httpx + threading.
No external dependencies beyond httpx (already in project).

Usage:
    # Against local instance:
    python benchmarks/load_test.py --url http://localhost:8900 --key god-is-an-astronaut

    # Custom scenarios:
    python benchmarks/load_test.py --url http://localhost:8900 --key KEY --scenario search --concurrency 10 --duration 30

Scenarios:
    search      - concurrent hybrid search queries
    add         - concurrent memory additions
    mixed       - realistic mix: 70% search, 20% add, 10% delete
    burst       - burst of rapid-fire searches
    extract     - extraction queue saturation (fallback mode)
"""

import argparse
import json
import random
import statistics
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import httpx

# Sample queries for search scenarios
SAMPLE_QUERIES = [
    "authentication architecture decisions",
    "database migration strategy",
    "API rate limiting approach",
    "caching layer design",
    "deployment pipeline configuration",
    "error handling patterns",
    "logging and observability setup",
    "security hardening measures",
    "performance optimization notes",
    "testing strategy and coverage goals",
    "dependency management approach",
    "configuration management decisions",
    "data model and schema design",
    "CI/CD pipeline setup",
    "monitoring and alerting rules",
]

SAMPLE_TEXTS = [
    "We decided to use JWT tokens with short-lived access tokens and refresh token rotation",
    "The database uses PostgreSQL with read replicas for search-heavy workloads",
    "Rate limiting is implemented at the API gateway level with per-key quotas",
    "Redis is used as the primary cache layer with TTL-based invalidation",
    "Deployments use blue-green strategy with automatic rollback on health check failures",
    "All errors are logged with structured JSON and forwarded to the observability platform",
    "Application logs use structured format with correlation IDs for request tracing",
    "TLS 1.3 is enforced for all external connections with certificate pinning",
    "Database queries are optimized with covering indexes and query plan analysis",
    "Unit tests target 80% coverage with integration tests for critical paths",
]


@dataclass
class RequestResult:
    status: int
    latency_ms: float
    operation: str
    error: str = ""


@dataclass
class BenchmarkReport:
    scenario: str
    concurrency: int
    duration_sec: float
    results: List[RequestResult] = field(default_factory=list)

    @property
    def total_requests(self) -> int:
        return len(self.results)

    @property
    def successful(self) -> int:
        return sum(1 for r in self.results if 200 <= r.status < 400)

    @property
    def failed(self) -> int:
        return self.total_requests - self.successful

    @property
    def rps(self) -> float:
        return self.total_requests / self.duration_sec if self.duration_sec > 0 else 0

    def latencies(self, operation: str = "") -> List[float]:
        filtered = [r.latency_ms for r in self.results if r.status < 400]
        if operation:
            filtered = [r.latency_ms for r in self.results if r.operation == operation and r.status < 400]
        return filtered

    def summary(self) -> Dict[str, Any]:
        lats = self.latencies()
        by_op: Dict[str, List[float]] = defaultdict(list)
        for r in self.results:
            if r.status < 400:
                by_op[r.operation].append(r.latency_ms)

        op_stats = {}
        for op, times in by_op.items():
            op_stats[op] = _lat_stats(times)

        return {
            "scenario": self.scenario,
            "concurrency": self.concurrency,
            "duration_sec": round(self.duration_sec, 1),
            "total_requests": self.total_requests,
            "successful": self.successful,
            "failed": self.failed,
            "rps": round(self.rps, 1),
            "latency_ms": _lat_stats(lats),
            "by_operation": op_stats,
        }

    def print_report(self):
        s = self.summary()
        print(f"\n{'=' * 60}")
        print(f"  Load Test Report: {s['scenario']}")
        print(f"{'=' * 60}")
        print(f"  Concurrency:  {s['concurrency']}")
        print(f"  Duration:     {s['duration_sec']}s")
        print(f"  Total reqs:   {s['total_requests']}")
        print(f"  Successful:   {s['successful']}")
        print(f"  Failed:       {s['failed']}")
        print(f"  Throughput:   {s['rps']} req/s")
        print()
        lat = s["latency_ms"]
        if lat:
            print(f"  Latency (all operations):")
            print(f"    p50: {lat['p50']}ms  p95: {lat['p95']}ms  p99: {lat['p99']}ms  avg: {lat['avg']}ms")
        print()
        for op, stats in s["by_operation"].items():
            print(f"  {op}:")
            print(f"    p50: {stats['p50']}ms  p95: {stats['p95']}ms  p99: {stats['p99']}ms  count: {stats['count']}")
        print(f"{'=' * 60}\n")


def _lat_stats(latencies: List[float]) -> Dict[str, Any]:
    if not latencies:
        return {}
    latencies.sort()
    return {
        "min": round(latencies[0], 1),
        "max": round(latencies[-1], 1),
        "avg": round(statistics.mean(latencies), 1),
        "p50": round(latencies[int(len(latencies) * 0.50)], 1),
        "p95": round(latencies[int(len(latencies) * 0.95)], 1),
        "p99": round(latencies[int(len(latencies) * 0.99)], 1),
        "count": len(latencies),
    }


class LoadRunner:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._client = httpx.Client(timeout=30.0)
        self._benchmark_ids: list = []  # track IDs we created
        self._ids_lock = threading.Lock()

    def close(self):
        self._client.close()

    def _request(self, method: str, path: str, json_body: Any = None) -> RequestResult:
        url = f"{self.base_url}{path}"
        start = time.perf_counter()
        try:
            resp = self._client.request(method, url, json=json_body, headers=self.headers)
            latency = (time.perf_counter() - start) * 1000
            return RequestResult(status=resp.status_code, latency_ms=latency, operation=f"{method} {path}")
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return RequestResult(status=0, latency_ms=latency, operation=f"{method} {path}", error=str(e))

    def search(self) -> RequestResult:
        query = random.choice(SAMPLE_QUERIES)
        return self._request("POST", "/search", {"query": query, "k": 5, "hybrid": True})

    def add(self) -> RequestResult:
        text = random.choice(SAMPLE_TEXTS)
        source = f"benchmark/load-test-{random.randint(1, 100)}"
        url = f"{self.base_url}/memory/add"
        start = time.perf_counter()
        try:
            resp = self._client.post(url, json={"text": text, "source": source}, headers=self.headers)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                mem_id = resp.json().get("id")
                if mem_id is not None:
                    with self._ids_lock:
                        self._benchmark_ids.append(mem_id)
            return RequestResult(status=resp.status_code, latency_ms=latency, operation="POST /memory/add")
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return RequestResult(status=0, latency_ms=latency, operation="POST /memory/add", error=str(e))

    def delete_random(self) -> RequestResult:
        # Only delete memories we created during this benchmark run
        with self._ids_lock:
            if not self._benchmark_ids:
                # Nothing to delete — search for benchmark memories instead
                return self._request("POST", "/search", {"query": "benchmark load test", "k": 1, "hybrid": True})
            target_id = random.choice(self._benchmark_ids)
        return self._request("DELETE", f"/memory/{target_id}")

    def extract(self) -> RequestResult:
        text = " ".join(random.sample(SAMPLE_TEXTS, min(3, len(SAMPLE_TEXTS))))
        return self._request("POST", "/memory/extract", {
            "messages": text,
            "source": "benchmark/extract",
            "context": "stop",
        })

    def run_scenario(self, scenario: str, concurrency: int, duration_sec: float) -> BenchmarkReport:
        scenarios: Dict[str, List[tuple[Callable, float]]] = {
            "search": [(self.search, 1.0)],
            "add": [(self.add, 1.0)],
            "mixed": [(self.search, 0.7), (self.add, 0.2), (self.delete_random, 0.1)],
            "burst": [(self.search, 1.0)],
            "extract": [(self.extract, 1.0)],
        }

        if scenario not in scenarios:
            print(f"Unknown scenario: {scenario}. Available: {list(scenarios.keys())}")
            sys.exit(1)

        ops = scenarios[scenario]
        report = BenchmarkReport(scenario=scenario, concurrency=concurrency, duration_sec=duration_sec)
        results_lock = threading.Lock()
        stop_event = threading.Event()

        def worker():
            while not stop_event.is_set():
                # Pick operation based on weight
                r = random.random()
                cumulative = 0.0
                for fn, weight in ops:
                    cumulative += weight
                    if r <= cumulative:
                        result = fn()
                        with results_lock:
                            report.results.append(result)
                        break
                # Small sleep for burst scenarios to not overwhelm
                if scenario != "burst":
                    time.sleep(random.uniform(0.01, 0.05))

        threads = []
        start = time.perf_counter()
        for _ in range(concurrency):
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            threads.append(t)

        stop_event.wait(timeout=duration_sec)
        stop_event.set()
        for t in threads:
            t.join(timeout=2)

        report.duration_sec = time.perf_counter() - start
        return report


def main():
    parser = argparse.ArgumentParser(description="Memories API Load Tester")
    parser.add_argument("--url", default="http://localhost:8900", help="Base URL")
    parser.add_argument("--key", default="god-is-an-astronaut", help="API key")
    parser.add_argument("--scenario", default="mixed", choices=["search", "add", "mixed", "burst", "extract"])
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent workers")
    parser.add_argument("--duration", type=float, default=10.0, help="Test duration in seconds")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    runner = LoadRunner(args.url, args.key)

    # Quick health check
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{args.url}/health", headers={"X-API-Key": args.key})
            if resp.status_code != 200:
                print(f"Health check failed: {resp.status_code}")
                sys.exit(1)
    except Exception as e:
        print(f"Cannot reach {args.url}: {e}")
        sys.exit(1)

    print(f"Running {args.scenario} scenario: {args.concurrency} workers for {args.duration}s against {args.url}")
    report = runner.run_scenario(args.scenario, args.concurrency, args.duration)
    runner.close()

    if args.json:
        print(json.dumps(report.summary(), indent=2))
    else:
        report.print_report()


if __name__ == "__main__":
    main()
