#!/usr/bin/env python3
"""Scalable graph eval runner with windowed execution.

Supports two modes:
- Isolated: per-window passages + links (MuSiQue)
- Shared corpus: preload passages once, window links only (2WikiMultiHopQA)

Usage:
    MEMORIES_URL=http://localhost:8901 MEMORIES_API_KEY=god-is-an-astronaut \
    python eval/graph_runner.py --adapter musique --dataset eval/datasets/musique-multihop.jsonl \
        --questions 200 --hops 3 --window 50
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

from eval.adapters.base import DatasetAdapter

logger = logging.getLogger("eval.graph_runner")


def run_windowed_eval(
    client: httpx.Client,
    adapter: DatasetAdapter,
    questions: list[dict],
    window_size: int = 50,
    graph_weights: list[float] = None,
    k: int = 5,
    cooldown: float = 0.5,
) -> list[dict]:
    """Run graph eval in windows to prevent Qdrant thrash.

    For isolated mode: seeds + links per window, cleanup after.
    For shared corpus: corpus preloaded, only links windowed.
    """
    if graph_weights is None:
        graph_weights = [0.0, 0.1]

    results = []
    corpus_ids = {}

    # Phase 1: Preload shared corpus if applicable
    if adapter.mode == "shared_corpus":
        logger.info("Preloading shared corpus...")
        corpus_ids = adapter.seed_corpus(client)
        logger.info("Corpus preloaded: %d memories", len(corpus_ids))

    # Phase 2: Windowed evaluation
    total = len(questions)
    for start in range(0, total, window_size):
        window = questions[start:start + window_size]
        window_num = start // window_size + 1
        total_windows = (total + window_size - 1) // window_size

        logger.info("Window %d/%d: questions %d-%d",
                     window_num, total_windows, start + 1, start + len(window))

        # Seed window
        id_maps = adapter.seed_window(client, window, corpus_ids)

        # Run searches for each question at each graph_weight
        for q in window:
            qid = q["id"] if "id" in q else str(start)
            scope = adapter.search_scope(q)
            id_map = id_maps.get(qid, id_maps.get(str(qid), {}))

            search_results = {}
            for gw in graph_weights:
                r = client.post("/search", json={
                    "query": q["question"],
                    "k": k,
                    "hybrid": True,
                    "graph_weight": gw,
                    "source_prefix": scope,
                })
                search_results[gw] = r.json().get("results", []) if r.status_code == 200 else []

            # Score
            results_off = search_results.get(0.0, [])
            results_on = search_results.get(0.1, search_results.get(max(graph_weights), []))
            score = adapter.score(q, results_off, results_on, id_map)
            results.append(score)

        # Cleanup window
        adapter.cleanup_window(client, window, corpus_ids)

        # Cooldown
        if cooldown > 0:
            time.sleep(cooldown)

        # Progress
        hits_off = sum(r["hit_off"] for r in results)
        hits_on = sum(r["hit_on"] for r in results)
        logger.info("  Progress: %d/%d off=%d on=%d", len(results), total, hits_off, hits_on)

    # Phase 3: Cleanup shared corpus
    if adapter.mode == "shared_corpus":
        adapter.cleanup_corpus(client)

    return results


def print_report(results: list[dict], elapsed: float, adapter_name: str):
    """Print detailed eval report with all metrics."""
    n = len(results)
    if n == 0:
        print("No results.")
        return

    hits_off = sum(r["hit_off"] for r in results)
    hits_on = sum(r["hit_on"] for r in results)
    improved = sum(1 for r in results if r["delta"] > 0)
    regressed = sum(1 for r in results if r["delta"] < 0)

    avg_recall_off = sum(r.get("support_recall_off", 0) for r in results) / n
    avg_recall_on = sum(r.get("support_recall_on", 0) for r in results) / n

    conditional = [r for r in results if r.get("conditional_candidate")]
    rescued = sum(1 for r in conditional if r.get("conditional_rescued"))

    rank_improved = sum(1 for r in results
                        if r.get("answer_rank_on", -1) > 0 and r.get("answer_rank_off", -1) > 0
                        and r["answer_rank_on"] < r["answer_rank_off"])

    by_hops = {}
    for r in results:
        h = r.get("n_hops", 0)
        by_hops.setdefault(h, []).append(r)

    print()
    print("=" * 70)
    print(f"Graph Eval: {adapter_name} ({time.strftime('%Y-%m-%d')})")
    print(f"Questions: {n} | Time: {elapsed:.1f}s ({elapsed/n:.2f}s/q)")
    print("=" * 70)
    print()
    print(f"  Answer Hit Rate:")
    print(f"    Graph OFF: {hits_off}/{n} ({hits_off/n*100:.1f}%)")
    print(f"    Graph ON:  {hits_on}/{n} ({hits_on/n*100:.1f}%)")
    print(f"    DELTA:     {hits_on-hits_off:+d} ({(hits_on-hits_off)/n*100:+.1f}%)")
    print(f"    Improved: {improved} | Regressed: {regressed}")
    print()
    print(f"  Support Chain Recall:")
    print(f"    Graph OFF: {avg_recall_off:.1%}")
    print(f"    Graph ON:  {avg_recall_on:.1%}")
    print(f"    DELTA:     {avg_recall_on-avg_recall_off:+.1%}")
    print()
    print(f"  Conditional Slice (OFF has support but not answer):")
    print(f"    Candidates: {len(conditional)}/{n}")
    if conditional:
        print(f"    Rescued: {rescued}/{len(conditional)} ({rescued/len(conditional)*100:.0f}%)")
    print()
    print(f"  Answer Rank Improvement: {rank_improved}/{n}")
    print()
    if by_hops:
        print(f"  By Hop Count:")
        for h in sorted(by_hops):
            if h == 0:
                continue
            items = by_hops[h]
            h_off = sum(r["hit_off"] for r in items)
            h_on = sum(r["hit_on"] for r in items)
            print(f"    {h}-hop: OFF={h_off}/{len(items)} ({h_off/len(items)*100:.0f}%) "
                  f"ON={h_on}/{len(items)} ({h_on/len(items)*100:.0f}%) "
                  f"delta={h_on-h_off:+d}")
    print()
    print("=" * 70)


def save_report(results: list[dict], elapsed: float, adapter_name: str, output: str):
    """Save JSON report."""
    n = len(results)
    report = {
        "version": "3.0.0",
        "adapter": adapter_name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "questions_run": n,
        "eval_seconds": round(elapsed, 1),
        "graph_off_hits": sum(r["hit_off"] for r in results),
        "graph_on_hits": sum(r["hit_on"] for r in results),
        "graph_off_pct": round(sum(r["hit_off"] for r in results) / n * 100, 1) if n else 0,
        "graph_on_pct": round(sum(r["hit_on"] for r in results) / n * 100, 1) if n else 0,
        "delta_pct": round((sum(r["hit_on"] for r in results) - sum(r["hit_off"] for r in results)) / n * 100, 1) if n else 0,
        "improved": sum(1 for r in results if r["delta"] > 0),
        "regressed": sum(1 for r in results if r["delta"] < 0),
        "avg_support_recall_off": round(sum(r.get("support_recall_off", 0) for r in results) / n, 3) if n else 0,
        "avg_support_recall_on": round(sum(r.get("support_recall_on", 0) for r in results) / n, 3) if n else 0,
        "details": results,
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Results saved to %s", output)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scalable graph eval runner")
    parser.add_argument("--adapter", required=True, choices=["musique", "twowiki"],
                        help="Dataset adapter to use")
    parser.add_argument("--dataset", required=True, help="Path to dataset file")
    parser.add_argument("--questions", type=int, default=100)
    parser.add_argument("--hops", type=int, default=0, help="Min hops (0=all)")
    parser.add_argument("--window", type=int, default=50, help="Window size")
    parser.add_argument("--output", default="")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    url = os.environ.get("MEMORIES_URL", "http://localhost:8900")
    key = os.environ.get("MEMORIES_API_KEY", "")
    client = httpx.Client(base_url=url, headers={"X-API-Key": key, "Content-Type": "application/json"}, timeout=60)

    try:
        r = client.get("/health")
        r.raise_for_status()
        logger.info("Connected to %s (%s)", url, r.json().get("version", "?"))
    except Exception as e:
        logger.error("Cannot reach %s: %s", url, e)
        sys.exit(1)

    # Load adapter
    if args.adapter == "musique":
        from eval.adapters.musique import MuSiQueAdapter
        adapter = MuSiQueAdapter(min_hops=args.hops)
    else:
        logger.error("Adapter %s not yet implemented", args.adapter)
        sys.exit(1)

    # Load questions
    questions = adapter.load_questions(args.dataset, args.questions)
    logger.info("Loaded %d questions via %s adapter", len(questions), adapter.name)

    # Run
    start = time.time()
    results = run_windowed_eval(client, adapter, questions, window_size=args.window)
    elapsed = time.time() - start

    # Report
    print_report(results, elapsed, adapter.name)

    if not args.output:
        args.output = f"eval/results/graph-{args.adapter}-{len(results)}q-{time.strftime('%Y%m%d-%H%M%S')}.json"
    save_report(results, elapsed, adapter.name, args.output)


if __name__ == "__main__":
    main()
