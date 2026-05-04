#!/usr/bin/env python3
"""Run MuSiQue 2-hop benchmark to measure graph expansion impact.

Each question has 20 paragraphs (2 supporting, 18 distractors) and a 2-hop
decomposition. We seed all 20 as memories, create a related_to link between
the two supporting paragraphs, then compare search with graph_weight=0 vs 0.1.

Usage:
    MEMORIES_URL=http://localhost:8901 MEMORIES_API_KEY=god-is-an-astronaut \
    python eval/run_musique_graph_eval.py [--questions 50] [--output path.json]
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

from eval.setup_validation import DEFAULT_EVAL_MEMORIES_URL, resolve_eval_memories_url, validate_eval_setup

logger = logging.getLogger("eval.musique")
PREFIX = "eval/musique"


def load_dataset(path="eval/datasets/musique.jsonl", max_questions=0):
    questions = []
    with open(path) as f:
        for line in f:
            q = json.loads(line)
            if q.get("answerable"):
                questions.append(q)
    if max_questions > 0:
        questions = questions[:max_questions]
    return questions


def run_question(client, question, verbose=False):
    """Seed 20 memories + link between supporting paragraphs, test retrieval."""
    qid = question["id"]
    query = question["question"]
    answer = question["answer"]
    paras = question["paragraphs"]
    decomp = question["question_decomposition"]

    # Step 1: Clear previous memories for this question
    client.post("/memory/delete-by-prefix", json={"source_prefix": f"{PREFIX}/{qid}"})

    # Step 2: Seed all 20 paragraphs as memories
    mem_ids = {}
    for para in paras:
        r = client.post("/memory/add", json={
            "text": para["paragraph_text"],
            "source": f"{PREFIX}/{qid}/{para['title'][:50]}",
            "deduplicate": False,
        })
        r.raise_for_status()
        mem_ids[para["idx"]] = r.json()["id"]

    # Step 3: Create related_to links along the full hop chain
    # For 3-hop: hop0→hop1→hop2. For 4-hop: hop0→hop1→hop2→hop3.
    supporting_indices = [d["paragraph_support_idx"] for d in decomp]
    links_created = 0
    for i in range(len(supporting_indices) - 1):
        from_idx = supporting_indices[i]
        to_idx = supporting_indices[i + 1]
        if from_idx in mem_ids and to_idx in mem_ids:
            r = client.post(f"/memory/{mem_ids[from_idx]}/link", json={
                "to_id": mem_ids[to_idx],
                "type": "related_to",
            })
            if r.status_code == 200:
                links_created += 1

    # Step 4: Search with graph OFF
    r_off = client.post("/search", json={
        "query": query,
        "k": 5,
        "hybrid": True,
        "graph_weight": 0.0,
        "source_prefix": f"{PREFIX}/{qid}",
    })
    results_off = r_off.json().get("results", [])

    # Step 5: Search with graph ON
    r_on = client.post("/search", json={
        "query": query,
        "k": 5,
        "hybrid": True,
        "graph_weight": 0.1,
        "source_prefix": f"{PREFIX}/{qid}",
    })
    results_on = r_on.json().get("results", [])

    # Step 6: Score — check if answer appears in top-5 results
    def has_answer(results, answer_text):
        combined = " ".join(r.get("text", "") for r in results).lower()
        return answer_text.lower() in combined

    hit_off = has_answer(results_off, answer)
    hit_on = has_answer(results_on, answer)

    # Also check if either supporting paragraph is in results
    supporting_ids = set(mem_ids[idx] for idx in supporting_indices if idx in mem_ids)
    support_off = sum(1 for r in results_off if r["id"] in supporting_ids)
    support_on = sum(1 for r in results_on if r["id"] in supporting_ids)

    graph_only = sum(1 for r in results_on if r.get("match_type") == "graph")
    boosted = sum(1 for r in results_on if r.get("match_type") == "direct+graph")

    # Cleanup + throttle to prevent Qdrant mmap thrashing on long runs
    client.post("/memory/delete-by-prefix", json={"source_prefix": f"{PREFIX}/{qid}"})
    import time as _time
    _time.sleep(0.2)  # 200ms cooldown between questions

    result = {
        "qid": str(qid),
        "question": query,
        "answer": answer,
        "hit_off": hit_off,
        "hit_on": hit_on,
        "delta": int(hit_on) - int(hit_off),
        "support_off": support_off,
        "support_on": support_on,
        "graph_only": graph_only,
        "boosted": boosted,
        "links_created": links_created,
    }

    if verbose:
        d = "+" if result["delta"] > 0 else ("-" if result["delta"] < 0 else "=")
        logger.info("[%s] off=%s on=%s [%s] support=%d→%d graph_only=%d q=%s",
                     d, "Y" if hit_off else ".", "Y" if hit_on else ".",
                     d, support_off, support_on, graph_only, query[:60])

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MuSiQue 2-hop graph eval")
    parser.add_argument("--questions", type=int, default=50)
    parser.add_argument("--output", default="")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--dataset", default="eval/datasets/musique.jsonl")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    url = resolve_eval_memories_url(DEFAULT_EVAL_MEMORIES_URL)
    key = os.environ.get("MEMORIES_API_KEY", "")
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
    client = httpx.Client(base_url=url, headers={"X-API-Key": key, "Content-Type": "application/json"}, timeout=30)

    # Health check
    try:
        r = client.get("/health/ready")
        r.raise_for_status()
        logger.info("Connected to %s (ready)", url)
    except Exception as e:
        logger.error("Cannot reach %s: %s", url, e)
        sys.exit(1)

    # Load dataset
    questions = load_dataset(args.dataset, args.questions)
    logger.info("Loaded %d questions (answerable)", len(questions))

    # Run
    start = time.time()
    results = []
    for i, q in enumerate(questions):
        result = run_question(client, q, verbose=args.verbose)
        results.append(result)
        if (i + 1) % 10 == 0:
            elapsed = time.time() - start
            hits_off = sum(r["hit_off"] for r in results)
            hits_on = sum(r["hit_on"] for r in results)
            logger.info("Progress: %d/%d (%.0fs) off=%d/%d on=%d/%d",
                         i + 1, len(questions), elapsed, hits_off, i + 1, hits_on, i + 1)

    elapsed = time.time() - start

    # Aggregate
    n = len(results)
    hits_off = sum(r["hit_off"] for r in results)
    hits_on = sum(r["hit_on"] for r in results)
    improved = sum(1 for r in results if r["delta"] > 0)
    regressed = sum(1 for r in results if r["delta"] < 0)
    unchanged = sum(1 for r in results if r["delta"] == 0)

    report = {
        "version": "1.0.0",
        "dataset": "musique",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "questions_run": n,
        "elapsed_seconds": round(elapsed, 1),
        "graph_off_hits": hits_off,
        "graph_on_hits": hits_on,
        "graph_off_pct": round(hits_off / n * 100, 1) if n else 0,
        "graph_on_pct": round(hits_on / n * 100, 1) if n else 0,
        "delta_pct": round((hits_on - hits_off) / n * 100, 1) if n else 0,
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "details": results,
    }

    # Print summary
    print()
    print("=" * 65)
    print(f"MuSiQue 2-Hop Graph Eval ({time.strftime('%Y-%m-%d')})")
    print(f"Questions: {n} | Time: {elapsed:.1f}s ({elapsed/n:.1f}s/q)")
    print(f"Graph OFF: {hits_off}/{n} ({hits_off/n*100:.1f}%)")
    print(f"Graph ON:  {hits_on}/{n} ({hits_on/n*100:.1f}%)")
    print(f"DELTA:     {hits_on-hits_off:+d} ({(hits_on-hits_off)/n*100:+.1f}%)")
    print(f"Improved: {improved} | Regressed: {regressed} | Unchanged: {unchanged}")
    print("=" * 65)

    # Save
    if not args.output:
        args.output = f"eval/results/musique-graph-{n}q-{time.strftime('%Y%m%d-%H%M%S')}.json"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
