#!/usr/bin/env python3
"""Scenario eval with extraction-based seeding.

Instead of seeding memories via memory_add (bypassing extraction),
this script seeds via /memory/extract so the extraction model is
in the critical path. Then runs the same Claude Code agent to answer
questions, comparing results across extraction models.

Usage:
    python eval/run_scenario_extraction_eval.py [--model MODEL] [--output PATH]
"""

import json
import os
import sys
import time
import argparse
import yaml
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.memories_client import MemoriesClient
from eval.setup_validation import DEFAULT_EVAL_MEMORIES_URL, resolve_eval_memories_url, validate_eval_setup


def load_scenarios(scenarios_dir: str = "eval/scenarios") -> list[dict]:
    """Load all scenario YAML files."""
    scenarios = []
    for path in sorted(Path(scenarios_dir).rglob("*.yaml")):
        with open(path) as f:
            s = yaml.safe_load(f)
        if s and "memories" in s and "prompt" in s:
            scenarios.append(s)
    return scenarios


def seed_via_extraction(client: MemoriesClient, memories: list[dict], source_prefix: str) -> dict:
    """Seed memories through the extraction pipeline instead of memory_add.

    Returns extraction stats (extracted_count, stored_count, etc.)
    """
    # Clear existing eval data
    client.clear_by_prefix(source_prefix)

    total_extracted = 0
    total_stored = 0
    total_errors = 0

    for i, mem in enumerate(memories):
        # Submit extraction
        result = client.extract(
            messages=mem["text"],
            source=mem.get("source", f"{source_prefix}/mem-{i}"),
            context="stop",
            dry_run=False,  # Actually store the extracted facts
        )
        if result.get("status") == "completed":
            r = result.get("result", {})
            total_extracted += r.get("extracted_count", 0)
            total_stored += r.get("stored_count", 0)
        elif result.get("status") == "failed":
            total_errors += 1

    return {
        "extracted": total_extracted,
        "stored": total_stored,
        "errors": total_errors,
    }


def score_scenario(expected: list[dict], output: str) -> tuple[float, list[dict]]:
    """Score output against expected rubrics (contains/regex/llm_judge)."""
    if not expected:
        return 0.0, []

    details = []
    for rubric in expected:
        rtype = rubric.get("type", "contains")
        value = rubric.get("value", "")
        weight = rubric.get("weight", 1.0)

        if rtype == "contains":
            score = 1.0 if value.lower() in output.lower() else 0.0
        elif rtype == "not_contains":
            score = 0.0 if value.lower() in output.lower() else 1.0
        else:
            score = 0.0  # Unknown rubric type

        details.append({"type": rtype, "value": value, "weight": weight, "score": score})

    total_weight = sum(d["weight"] for d in details)
    if total_weight == 0:
        return 0.0, details
    weighted_score = sum(d["score"] * d["weight"] for d in details) / total_weight
    return weighted_score, details


def run_agent_prompt(prompt: str, memories_url: str, memories_api_key: str, mcp_server_path: str) -> str:
    """Run a prompt through Claude Code with MCP tools."""
    from eval.cc_executor import CCExecutor

    executor = CCExecutor(
        timeout=120,
        memories_url=memories_url,
        memories_api_key=memories_api_key,
        mcp_server_path=mcp_server_path,
    )
    CCExecutor.cleanup_stale_auto_memory()

    project_dir = executor.create_isolated_project(with_memories=True)
    try:
        return executor.run_prompt(prompt, project_dir)
    finally:
        executor.cleanup_project(project_dir)


def run_eval(extraction_model: str = "", output_path: str = ""):
    """Run scenario eval with extraction-based seeding."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    url = resolve_eval_memories_url(DEFAULT_EVAL_MEMORIES_URL)
    api_key = os.environ.get("MEMORIES_API_KEY", "god-is-an-astronaut")
    mcp_server_path = str(Path(__file__).parent.parent / "mcp-server" / "index.js")
    setup_report = validate_eval_setup(
        memories_url=url,
        mcp_server_path=mcp_server_path,
        require_claude=True,
        allow_unsafe_target=os.environ.get("EVAL_ALLOW_UNSAFE_TARGET") == "1",
    )
    if not setup_report.ok:
        for message in setup_report.errors:
            print(f"ERROR: {message}")
        sys.exit(2)

    client = MemoriesClient(url=url, api_key=api_key)
    if not client.health_check():
        print(f"ERROR: Memories service not reachable at {url}")
        sys.exit(1)

    # Check extraction provider
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{url}/extract/status",
            headers={"X-API-Key": api_key},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            extract_status = json.loads(resp.read())
        print(f"Extraction provider: {extract_status.get('provider')}/{extract_status.get('model')}")
        if not extract_status.get("enabled"):
            print("ERROR: Extraction not enabled on eval service")
            sys.exit(1)
    except Exception as e:
        print(f"WARNING: Could not check extraction status: {e}")

    scenarios = load_scenarios()
    print(f"Loaded {len(scenarios)} scenarios")
    print(f"Eval service: {url}")
    print(f"{'='*60}")

    results = []
    start_time = time.time()

    for i, scenario in enumerate(scenarios):
        sid = scenario.get("id", f"scenario-{i}")
        name = scenario.get("name", sid)
        category = scenario.get("category", "unknown")
        memories = scenario.get("memories", [])
        prompt = scenario.get("prompt", "")
        expected = scenario.get("expected", [])

        print(f"\n[{i+1}/{len(scenarios)}] {sid}: {name}")

        # Phase 1: Seed via extraction
        seed_start = time.time()
        seed_stats = seed_via_extraction(client, memories, f"eval/{sid}")
        seed_time = time.time() - seed_start
        print(f"  Seeded: {seed_stats['extracted']} extracted, {seed_stats['stored']} stored ({seed_time:.1f}s)")

        if seed_stats["stored"] == 0 and seed_stats["extracted"] == 0:
            print(f"  WARNING: No facts extracted — extraction model may have failed")

        # Phase 2: Run agent
        agent_start = time.time()
        try:
            output = run_agent_prompt(prompt, url, api_key, mcp_server_path)
            agent_time = time.time() - agent_start
            print(f"  Agent responded: {len(output)} chars ({agent_time:.1f}s)")
        except Exception as e:
            output = ""
            agent_time = time.time() - agent_start
            print(f"  Agent failed: {e} ({agent_time:.1f}s)")

        # Phase 3: Score
        score, details = score_scenario(expected, output)
        print(f"  Score: {score:.2f}")

        results.append({
            "id": sid,
            "name": name,
            "category": category,
            "seed_stats": seed_stats,
            "seed_time": round(seed_time, 1),
            "agent_time": round(agent_time, 1),
            "score": score,
            "rubric_details": details,
            "output_excerpt": output[:500] if output else "",
        })

        # Cleanup
        client.clear_by_prefix(f"eval/{sid}")

    elapsed = time.time() - start_time

    # Aggregate
    by_category = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["score"])

    overall = sum(r["score"] for r in results) / len(results) if results else 0

    print(f"\n{'='*60}")
    print(f"RESULTS — Extraction model: {extraction_model or 'configured on service'}")
    print(f"{'='*60}")
    print(f"Overall: {overall*100:.1f}% ({len(results)} scenarios, {elapsed:.0f}s)")
    for cat, scores in sorted(by_category.items()):
        avg = sum(scores) / len(scores) if scores else 0
        print(f"  {cat}: {avg*100:.1f}% ({len(scores)} scenarios)")

    # Save
    output_data = {
        "eval_type": "scenario_extraction",
        "extraction_model": extraction_model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(elapsed, 1),
        "overall": round(overall, 4),
        "categories": {cat: round(sum(s)/len(s), 4) for cat, s in by_category.items()},
        "results": results,
    }

    if not output_path:
        model_label = extraction_model.replace(":", "-").replace("/", "-") if extraction_model else "default"
        output_path = f"eval/results/scenario-extraction-{model_label}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(output_data, indent=2))
    print(f"\nResults saved to {output_path}")

    return overall


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scenario eval with extraction-based seeding")
    parser.add_argument("--model", default="", help="Label for the extraction model (for results file)")
    parser.add_argument("--output", default="", help="Output file path")
    args = parser.parse_args()
    run_eval(extraction_model=args.model, output_path=args.output)
