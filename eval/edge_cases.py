#!/usr/bin/env python3
"""Comprehensive edge case eval for memory-critical features.

Tests items 3 (recency boost), 4 (relationships), 5 (conflict detection),
6 (confidence decay) against a running Memories instance. Designed to
detect regressions in search ranking, link integrity, and memory lifecycle.

Usage:
    # Against eval instance:
    python eval/edge_cases.py --url http://localhost:8901 --key god-is-an-astronaut

    # Against production (read-heavy, still creates/deletes test memories):
    python eval/edge_cases.py --url http://localhost:8900 --key KEY

    # JSON output for CI:
    python eval/edge_cases.py --url http://localhost:8901 --key KEY --json
"""

import argparse
import concurrent.futures
import json
import sys
import time

import httpx


class EdgeCaseEval:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self.c = httpx.Client(base_url=self.base_url, headers=headers, timeout=60)
        self.passed = 0
        self.failed = 0
        self.details: list[dict] = []

    def close(self):
        self.c.close()

    def _ok(self, cond):
        if not cond:
            raise AssertionError()

    def test(self, scenario: str, name: str, fn):
        try:
            fn()
            print(f"  \u2713 {name}")
            self.passed += 1
            self.details.append({"scenario": scenario, "name": name, "pass": True})
        except Exception as e:
            print(f"  \u2717 {name}")
            self.failed += 1
            self.details.append({"scenario": scenario, "name": name, "pass": False, "error": str(e)})

    def cleanup(self, prefix: str):
        self.c.post("/memory/delete-by-prefix", json={"source_prefix": prefix})

    def add(self, text: str, source: str) -> int:
        return self.c.post("/memory/add", json={"text": text, "source": source}).json()["id"]

    def search(self, query: str, k: int = 5, recency: float = 0.0, half_life: int = 30) -> list:
        return self.c.post("/search", json={
            "query": query, "k": k, "hybrid": True,
            "recency_weight": recency, "recency_half_life_days": half_life,
        }).json()["results"]

    # -----------------------------------------------------------------
    # Scenario 1: Recency boost with real time gaps
    # -----------------------------------------------------------------
    def scenario_recency(self):
        """Tests recency boost using fresh memories vs existing old ones."""
        print("\n\u2501\u2501\u2501 S1: Recency Boost with Real Time Gaps \u2501\u2501\u2501")
        S = "evaledge-s1/"
        self.cleanup(S)

        fresh_id = self.add(
            "The memories project switched from polling every 5 minutes to an event-driven "
            "architecture using SSE and webhooks in March 2026. The event bus emits "
            "memory.added, memory.updated, memory.deleted, and extraction.completed events.",
            S + "fresh",
        )

        with_recency = self.search("memories project architecture event system", k=5, recency=0.5, half_life=14)
        without_recency = self.search("memories project architecture event system", k=5, recency=0.0)

        fresh_with = next((i for i, r in enumerate(with_recency) if r["id"] == fresh_id), 99) + 1
        fresh_without = next((i for i, r in enumerate(without_recency) if r["id"] == fresh_id), 99) + 1

        self.test("S1", "Fresh memory found with recency", lambda: self._ok(fresh_with <= 5))
        self.test("S1", "Fresh memory found without recency", lambda: self._ok(fresh_without <= 5))
        self.test("S1", f"Recency improves or maintains rank ({fresh_with} vs {fresh_without})",
                  lambda: self._ok(fresh_with <= fresh_without))

        old_results = [r for r in with_recency if r["id"] != fresh_id]
        self.test("S1", "Old memories not suppressed", lambda: self._ok(len(old_results) >= 2))

        self.cleanup(S)

    # -----------------------------------------------------------------
    # Scenario 2: Relationship chain integrity
    # -----------------------------------------------------------------
    def scenario_relationships(self):
        """Tests link creation, chain traversal, and survival after deletion."""
        print("\n\u2501\u2501\u2501 S2: Relationship Chain Integrity \u2501\u2501\u2501")
        S = "evaledge-s2/"
        self.cleanup(S)

        v1 = self.add("Kestrel uses JWT tokens with 24-hour expiry for API auth.", S + "v1")
        v2 = self.add("Kestrel switched to JWT with 1-hour expiry and refresh tokens.", S + "v2")
        v3 = self.add("Kestrel now uses opaque tokens with 15-minute TTL. JWTs dropped.", S + "v3")
        unrelated = self.add("Kestrel deployment uses ArgoCD with auto-sync disabled.", S + "deploy")

        self.c.post(f"/memory/{v2}/link", json={"to_id": v1, "type": "supersedes"})
        self.c.post(f"/memory/{v3}/link", json={"to_id": v2, "type": "supersedes"})
        self.c.post(f"/memory/{v3}/link", json={"to_id": v1, "type": "related_to"})

        links_v3 = self.c.get(f"/memory/{v3}/links").json().get("links", [])
        links_v2 = self.c.get(f"/memory/{v2}/links").json().get("links", [])

        self.test("S2", "v3 has 2 outgoing links", lambda: self._ok(len(links_v3) >= 2))
        self.test("S2", "v2 supersedes v1", lambda: self._ok(
            any(l["type"] == "supersedes" and l["to_id"] == v1 for l in links_v2)))

        # Delete middle of chain
        self.c.delete(f"/memory/{v2}")
        time.sleep(0.3)

        links_v3_after = self.c.get(f"/memory/{v3}/links").json().get("links", [])
        self.test("S2", "v3 links survive v2 deletion", lambda: self._ok(len(links_v3_after) >= 1))

        results = self.search("Kestrel authentication tokens", k=10)
        result_ids = [r["id"] for r in results]
        self.test("S2", "Deleted v2 not in search", lambda: self._ok(v2 not in result_ids))
        self.test("S2", "v3 (latest) findable", lambda: self._ok(v3 in result_ids))
        self.test("S2", "Unrelated memory intact", lambda: self._ok(
            "ArgoCD" in self.c.get(f"/memory/{unrelated}").json().get("text", "")))

        self.cleanup(S)

    # -----------------------------------------------------------------
    # Scenario 3: Confidence reinforcement
    # -----------------------------------------------------------------
    def scenario_confidence(self):
        """Tests that searched memories maintain or gain confidence."""
        print("\n\u2501\u2501\u2501 S3: Confidence Decay vs Reinforcement \u2501\u2501\u2501")
        S = "evaledge-s3/"
        self.cleanup(S)

        hot = self.add("Meridian cache uses Varnish with 60s TTL for API responses.", S + "hot")
        cold = self.add("Meridian logging uses Fluentd with JSON parsers to Elasticsearch.", S + "cold")

        for _ in range(10):
            self.search("Meridian cache Varnish TTL", k=3)
            time.sleep(0.05)

        conf_hot = self.c.get(f"/memory/{hot}").json().get("confidence", 0)
        conf_cold = self.c.get(f"/memory/{cold}").json().get("confidence", 0)
        print(f"    Hot (10x reinforced): {conf_hot:.6f}")
        print(f"    Cold (never searched): {conf_cold:.6f}")

        self.test("S3", "Hot memory has confidence > 0", lambda: self._ok(conf_hot > 0))
        self.test("S3", "Cold memory has confidence > 0", lambda: self._ok(conf_cold > 0))
        self.test("S3", "Hot >= Cold", lambda: self._ok(conf_hot >= conf_cold))

        r = self.search("Meridian infrastructure caching logging", k=5)
        ids = [x["id"] for x in r]
        self.test("S3", "Hot memory searchable", lambda: self._ok(hot in ids))
        self.test("S3", "Cold memory searchable", lambda: self._ok(cold in ids))

        self.cleanup(S)

    # -----------------------------------------------------------------
    # Scenario 4: Extraction under contradiction
    # -----------------------------------------------------------------
    def scenario_extraction(self):
        """Tests extraction behavior with contradictory and duplicate facts."""
        print("\n\u2501\u2501\u2501 S4: Extraction Under Contradiction \u2501\u2501\u2501")
        S = "evaledge-s4/"
        self.cleanup(S)

        self.add("Orion project uses Python 3.11 with FastAPI. All endpoints return JSON.", S + "base")

        # Contradicting extraction
        ext = self.c.post("/memory/extract", json={
            "messages": "Orion migrated to Python 3.12. FastAPI replaced with Litestar.",
            "source": S + "update", "context": "stop",
        })
        self.test("S4", "Contradiction extraction accepted", lambda: self._ok(ext.status_code == 202))
        time.sleep(2)

        r = self.search("Orion project Python framework", k=5)
        texts = " ".join(x["text"] for x in r)
        self.test("S4", "Original fact (FastAPI) findable", lambda: self._ok("FastAPI" in texts))

        # Duplicate extraction
        ext2 = self.c.post("/memory/extract", json={
            "messages": "Orion project uses Python 3.11 with FastAPI.",
            "source": S + "dupe", "context": "stop",
        })
        self.test("S4", "Duplicate extraction accepted", lambda: self._ok(ext2.status_code == 202))

        eq = self.c.get("/metrics/extraction-quality").json()
        self.test("S4", "Extraction metrics recorded", lambda: self._ok(
            eq.get("extraction_count", 0) >= 1 or eq.get("enabled") is False))

        self.cleanup(S)

    # -----------------------------------------------------------------
    # Scenario 5: Cross-feature interaction
    # -----------------------------------------------------------------
    def scenario_cross_feature(self):
        """Combines recency, relationships, confidence, and search integrity."""
        print("\n\u2501\u2501\u2501 S5: Cross-Feature Interaction \u2501\u2501\u2501")
        S = "evaledge-s5/"
        self.cleanup(S)

        db_old = self.add("Phoenix DB: MySQL 8 on RDS with read replicas.", S + "db-old")
        db_new = self.add("Phoenix migrated to Aurora PostgreSQL Serverless v2 in Jan 2026.", S + "db-new")
        cache = self.add("Phoenix: ElastiCache Redis cluster, 3 shards, pub/sub invalidation.", S + "cache")
        api = self.add("Phoenix API gateway: Kong with rate limiting, 100 req/s per key.", S + "api")
        secret = self.add("Phoenix DB creds in Secrets Manager at phoenix/prod/aurora-credentials.", S + "secret")

        self.c.post(f"/memory/{db_new}/link", json={"to_id": db_old, "type": "supersedes"})
        self.c.post(f"/memory/{secret}/link", json={"to_id": db_new, "type": "related_to"})

        # Reinforce hot memories
        for _ in range(5):
            self.search("Phoenix API rate limiting Kong", k=3)
            self.search("Phoenix Redis cache invalidation", k=3)
            time.sleep(0.05)

        # Q1: Credentials query
        r1 = self.search("Phoenix database credentials secrets", k=5)
        t1 = " ".join(x["text"] for x in r1)
        self.test("S5", "Secrets Manager path found", lambda: self._ok("phoenix/prod/aurora" in t1))

        # Q2: Infrastructure overview
        r2 = self.search("Phoenix infrastructure all systems overview", k=5)
        t2 = " ".join(x["text"] for x in r2)
        self.test("S5", "Cache system in overview", lambda: self._ok("Redis" in t2 or "ElastiCache" in t2))
        self.test("S5", "API gateway in overview", lambda: self._ok("Kong" in t2 or "rate limit" in t2.lower()))
        self.test("S5", "Database in overview", lambda: self._ok(
            "Aurora" in t2 or "PostgreSQL" in t2 or "MySQL" in t2))

        # Q3: Delete cache, verify integrity
        self.c.delete(f"/memory/{cache}")
        time.sleep(0.3)
        r3 = self.search("Phoenix Redis cache", k=5)
        r3_ids = [x["id"] for x in r3]
        self.test("S5", "Deleted cache gone from results", lambda: self._ok(cache not in r3_ids))
        self.test("S5", "API memory survives cache deletion",
                  lambda: self._ok(api in [x["id"] for x in self.search("Phoenix Kong rate", k=3)]))
        self.test("S5", "DB memory survives cache deletion",
                  lambda: self._ok(db_new in [x["id"] for x in self.search("Phoenix Aurora", k=3)]))

        # Q4: Links survive
        links = self.c.get(f"/memory/{db_new}/links").json().get("links", [])
        self.test("S5", "Supersedes link intact", lambda: self._ok(
            any(l["type"] == "supersedes" for l in links)))

        self.cleanup(S)

    # -----------------------------------------------------------------
    # Scenario 6: Concurrent operations
    # -----------------------------------------------------------------
    def scenario_concurrent(self):
        """Tests thread safety under concurrent add + search."""
        print("\n\u2501\u2501\u2501 S6: Concurrent Operations \u2501\u2501\u2501")
        S = "evaledge-s6/"
        self.cleanup(S)

        def add_one(i):
            return self.c.post("/memory/add", json={
                "text": f"Concurrent fact #{i}: Zephyr module {i} uses port {3000+i}",
                "source": S + f"concurrent-{i}",
            }).json()

        def search_one(q):
            return self.c.post("/search", json={"query": q, "k": 3, "hybrid": True}).json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            add_futures = [ex.submit(add_one, i) for i in range(10)]
            search_futures = [ex.submit(search_one, f"Zephyr module {i}") for i in range(5)]
            add_results = [f.result() for f in add_futures]
            search_results = [f.result() for f in search_futures]

        self.test("S6", "All 10 concurrent adds succeeded",
                  lambda: self._ok(all(r.get("success") or r.get("id") for r in add_results)))
        self.test("S6", "All 5 concurrent searches returned results",
                  lambda: self._ok(all("results" in r for r in search_results)))

        # Verify all 10 are searchable
        all_results = self.search("Zephyr module port", k=15)
        found = sum(1 for r in all_results if "Concurrent fact" in r.get("text", ""))
        self.test("S6", f"At least 5 of 10 concurrent memories findable (found {found})",
                  lambda: self._ok(found >= 5))

        self.cleanup(S)

    # -----------------------------------------------------------------
    # Scenario 7: Delete integrity
    # -----------------------------------------------------------------
    def scenario_delete_integrity(self):
        """Tests that deletion is complete — events, search, links."""
        print("\n\u2501\u2501\u2501 S7: Delete Integrity \u2501\u2501\u2501")
        S = "evaledge-s7/"
        self.cleanup(S)

        m1 = self.add("Athena service uses gRPC on port 50051 with mTLS.", S + "target")
        m2 = self.add("Athena monitoring uses Prometheus with custom metrics.", S + "related")
        self.c.post(f"/memory/{m1}/link", json={"to_id": m2, "type": "related_to"})

        # Confirm findable before delete
        r = self.search("Athena gRPC port", k=3)
        self.test("S7", "Memory findable before delete",
                  lambda: self._ok(m1 in [x["id"] for x in r]))

        # Delete
        self.c.delete(f"/memory/{m1}")
        time.sleep(0.5)

        # Verify completeness
        self.test("S7", "GET returns 404", lambda: self._ok(
            self.c.get(f"/memory/{m1}").status_code == 404))

        r2 = self.search("Athena gRPC port 50051", k=10)
        self.test("S7", "Not in search results", lambda: self._ok(
            m1 not in [x["id"] for x in r2]))

        events = self.c.get("/events/recent?limit=20").json().get("events", [])
        self.test("S7", "Delete event emitted", lambda: self._ok(
            any(e["type"] == "memory.deleted" for e in events)))

        # Related memory should survive
        self.test("S7", "Related memory (m2) survives", lambda: self._ok(
            self.c.get(f"/memory/{m2}").status_code == 200))

        self.cleanup(S)

    # -----------------------------------------------------------------
    # Run all
    # -----------------------------------------------------------------
    def run_all(self) -> dict:
        print("=" * 70)
        print("  COMPREHENSIVE EDGE CASE EVAL")
        print(f"  Target: {self.base_url}")
        print("=" * 70)

        # Health check
        try:
            r = self.c.get("/health")
            assert r.status_code == 200
        except Exception:
            print(f"\n  ERROR: Cannot reach {self.base_url}")
            sys.exit(1)

        self.scenario_recency()
        self.scenario_relationships()
        self.scenario_confidence()
        self.scenario_extraction()
        self.scenario_cross_feature()
        self.scenario_concurrent()
        self.scenario_delete_integrity()

        self.close()

        # Summary
        by_scenario: dict[str, dict] = {}
        for d in self.details:
            s = d["scenario"]
            by_scenario.setdefault(s, {"pass": 0, "fail": 0})
            by_scenario[s]["pass" if d["pass"] else "fail"] += 1

        print(f"\n{'=' * 70}")
        print(f"  RESULTS: {self.passed} passed, {self.failed} failed out of {self.passed + self.failed}")
        print(f"{'=' * 70}")
        print("\n  Per-scenario:")
        for s, counts in sorted(by_scenario.items()):
            status = "\u2713" if counts["fail"] == 0 else "\u2717"
            print(f"    {status} {s}: {counts['pass']}/{counts['pass'] + counts['fail']}")

        if self.failed:
            print("\n  Failures:")
            for d in self.details:
                if not d["pass"]:
                    print(f"    {d['scenario']} / {d['name']}")

        return {
            "passed": self.passed,
            "failed": self.failed,
            "total": self.passed + self.failed,
            "scenarios": by_scenario,
            "details": self.details,
        }


def main():
    parser = argparse.ArgumentParser(description="Memories Edge Case Eval")
    parser.add_argument("--url", default="http://localhost:8901", help="Memories API URL")
    parser.add_argument("--key", default="god-is-an-astronaut", help="API key")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    ev = EdgeCaseEval(args.url, args.key)
    result = ev.run_all()

    if args.json:
        print(json.dumps(result, indent=2))

    sys.exit(1 if result["failed"] else 0)


if __name__ == "__main__":
    main()
