#!/usr/bin/env python3
"""Graph eval runner — measures the impact of graph_weight on retrieval.

Seeds memories with explicit links, then compares search results with
graph_weight=0 (off) vs graph_weight=0.1 (on) for each scenario.

Usage:
    MEMORIES_URL=http://localhost:8901 MEMORIES_API_KEY=god-is-an-astronaut \
    python eval/run_graph_eval.py [--output path.json] [--verbose]
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

from eval.setup_validation import DEFAULT_EVAL_MEMORIES_URL, resolve_eval_memories_url, validate_eval_setup

logger = logging.getLogger("eval.graph")

GRAPH_PREFIX = "eval/graph/scenarios"  # scoped to avoid wiping synthetic corpus at eval/graph/synth/


def _load_env():
    """Load .env file if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_scenarios(scenario_dir: str) -> list[dict]:
    """Load all graph scenario YAML files."""
    path = Path(scenario_dir)
    scenarios = []
    for f in sorted(path.glob("graph-*.yaml")):
        with open(f) as fh:
            scenarios.append(yaml.safe_load(fh))
    return scenarios


def score_result(results: list[dict], expected: list[dict]) -> float:
    """Score search results against expected rubric (contains checks)."""
    if not results:
        return 0.0

    combined_text = " ".join(r.get("text", "") for r in results[:3]).lower()
    total_weight = sum(e.get("weight", 1.0) for e in expected)
    score = 0.0

    for exp in expected:
        if exp["type"] == "contains":
            if exp["value"].lower() in combined_text:
                score += exp.get("weight", 1.0)

    return round(score / total_weight, 4) if total_weight > 0 else 0.0


def run_scenario(client: httpx.Client, scenario: dict, verbose: bool = False) -> dict:
    """Run a single graph eval scenario.

    Steps:
    1. Clear eval/graph/ prefix
    2. Seed memories
    3. Create links (map scenario IDs to real IDs)
    4. Search with graph_weight=0 → score_off
    5. Search with graph_weight=0.1 → score_on
    6. Return comparison
    """
    sid = scenario["id"]
    scenario_prefix = f"{GRAPH_PREFIX}/{sid}"

    # Step 1: Clear this scenario's memories only (not the whole graph prefix)
    client.post("/memory/delete-by-prefix", json={"source_prefix": scenario_prefix})

    # Step 2: Seed memories — preserve original source semantics (needed for
    # cross-source and scope-boundary scenarios) but prepend scenario_prefix
    # so cleanup can find them
    id_map = {}  # scenario_id -> real_id
    for mem in scenario["memories"]:
        # Original source from YAML, scoped under scenario prefix for cleanup
        original_source = mem["source"]
        scoped_source = f"{scenario_prefix}/{original_source}"
        resp = client.post("/memory/add", json={
            "text": mem["text"],
            "source": scoped_source,
            "deduplicate": False,
        })
        resp.raise_for_status()
        real_id = resp.json()["id"]
        id_map[mem["id"]] = real_id

    # Step 3: Create links
    links_created = 0
    for link in scenario.get("links", []):
        from_id = id_map.get(link["from"])
        to_id = id_map.get(link["to"])
        if from_id and to_id:
            resp = client.post(f"/memory/{from_id}/link", json={
                "to_id": to_id,
                "type": link.get("type", "related_to"),
            })
            if resp.status_code == 200:
                links_created += 1
            elif verbose:
                logger.warning("Link %s->%s failed: %s", link["from"], link["to"], resp.text)

    # Step 4: Search with graph OFF
    search_body = {
        "query": scenario["prompt"],
        "k": 5,
        "hybrid": True,
        "graph_weight": 0.0,
    }
    # Scope search: if scenario declares a source_prefix (for scope-boundary tests),
    # prepend scenario_prefix to maintain isolation. Otherwise use scenario_prefix.
    if "source_prefix" in scenario:
        search_body["source_prefix"] = f"{scenario_prefix}/{scenario['source_prefix']}"
    else:
        search_body["source_prefix"] = scenario_prefix

    resp_off = client.post("/search", json=search_body)
    resp_off.raise_for_status()
    results_off = resp_off.json().get("results", [])
    score_off = score_result(results_off, scenario["expected"])

    # Step 5: Search with graph ON
    search_body["graph_weight"] = 0.1
    resp_on = client.post("/search", json=search_body)
    resp_on.raise_for_status()
    results_on = resp_on.json().get("results", [])
    score_on = score_result(results_on, scenario["expected"])

    # Collect result IDs for analysis
    ids_off = [r["id"] for r in results_off[:5]]
    ids_on = [r["id"] for r in results_on[:5]]
    graph_only_ids = [r["id"] for r in results_on if r.get("match_type") == "graph"]
    boosted_ids = [r["id"] for r in results_on if r.get("match_type") == "direct+graph"]

    result = {
        "id": sid,
        "name": scenario.get("name", ""),
        "category": scenario.get("category", "graph"),
        "memories_seeded": len(scenario["memories"]),
        "links_created": links_created,
        "score_graph_off": score_off,
        "score_graph_on": score_on,
        "delta": round(score_on - score_off, 4),
        "result_ids_off": ids_off,
        "result_ids_on": ids_on,
        "graph_only_ids": graph_only_ids,
        "boosted_ids": boosted_ids,
    }

    if verbose:
        logger.info(
            "[%s] off=%.2f on=%.2f delta=%+.2f links=%d graph_only=%d boosted=%d",
            sid, score_off, score_on, score_on - score_off,
            links_created, len(graph_only_ids), len(boosted_ids),
        )

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Graph Eval — measure graph_weight impact")
    parser.add_argument("--scenarios", default="eval/scenarios/graph", help="Path to graph scenarios")
    parser.add_argument("--output", default="", help="Output JSON path (default: auto-generated)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _load_env()
    url = resolve_eval_memories_url(DEFAULT_EVAL_MEMORIES_URL)
    api_key = os.environ.get("MEMORIES_API_KEY", "")
    setup_report = validate_eval_setup(
        memories_url=url,
        require_mcp=False,
        require_claude=False,
        allow_unsafe_target=os.environ.get("EVAL_ALLOW_UNSAFE_TARGET") == "1",
    )
    if not setup_report.ok:
        for message in setup_report.errors:
            logger.error(message)
        sys.exit(2)

    client = httpx.Client(
        base_url=url,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=30.0,
    )

    # Health check
    try:
        resp = client.get("/health/ready")
        resp.raise_for_status()
        logger.info("Connected to %s (ready)", url)
    except Exception as e:
        logger.error("Cannot reach %s: %s", url, e)
        sys.exit(1)

    # Load scenarios
    scenarios = load_scenarios(args.scenarios)
    if not scenarios:
        logger.error("No graph scenarios found in %s", args.scenarios)
        sys.exit(1)
    logger.info("Loaded %d graph scenarios", len(scenarios))

    # Run
    start = time.time()
    results = []
    for scenario in scenarios:
        result = run_scenario(client, scenario, verbose=args.verbose)
        results.append(result)

    elapsed = time.time() - start

    # Cleanup
    client.post("/memory/delete-by-prefix", json={"source_prefix": GRAPH_PREFIX})

    # Aggregate
    total_off = sum(r["score_graph_off"] for r in results) / len(results) if results else 0
    total_on = sum(r["score_graph_on"] for r in results) / len(results) if results else 0
    total_delta = total_on - total_off
    improved = sum(1 for r in results if r["delta"] > 0)
    regressed = sum(1 for r in results if r["delta"] < 0)
    unchanged = sum(1 for r in results if r["delta"] == 0)

    report = {
        "version": "1.0.0",
        "eval_type": "graph",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(elapsed, 1),
        "scenarios_run": len(results),
        "overall_graph_off": round(total_off, 4),
        "overall_graph_on": round(total_on, 4),
        "overall_delta": round(total_delta, 4),
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "details": results,
    }

    # Print summary
    print()
    print("=" * 60)
    print(f"Graph Eval v1.0.0 ({time.strftime('%Y-%m-%d')})")
    print(f"Scenarios: {len(results)} | Time: {elapsed:.1f}s")
    print(f"Overall: graph_off={total_off:.1%} graph_on={total_on:.1%} delta={total_delta:+.1%}")
    print(f"Improved: {improved} | Regressed: {regressed} | Unchanged: {unchanged}")
    print("=" * 60)
    for r in results:
        marker = "+" if r["delta"] > 0 else ("-" if r["delta"] < 0 else "=")
        print(f"  [{marker}] {r['id']:30s} off={r['score_graph_off']:.0%} on={r['score_graph_on']:.0%} delta={r['delta']:+.0%}")
    print()

    # Save
    if not args.output:
        args.output = f"eval/results/graph-eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
