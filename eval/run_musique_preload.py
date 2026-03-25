#!/usr/bin/env python3
"""MuSiQue graph eval with preloaded corpus — no per-question add/delete.

Phase 1: Preload all paragraphs + links for all questions (batch seed)
Phase 2: Run all searches as read-only (no writes during eval)
Phase 3: Single cleanup at end

This eliminates the Qdrant mmap thrash that crashed the eval at 220/1000.

Usage:
    MEMORIES_URL=http://localhost:8901 MEMORIES_API_KEY=god-is-an-astronaut \
    python eval/run_musique_preload.py --questions 100 [--hops 3] [--output path.json]
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

logger = logging.getLogger("eval.musique")
PREFIX = "eval/musique"


def load_dataset(path="eval/datasets/musique-multihop.jsonl", max_questions=0, min_hops=0):
    questions = []
    with open(path) as f:
        for line in f:
            q = json.loads(line)
            if not q.get("answerable", True):
                continue
            decomp = q.get("question_decomposition", [])
            if isinstance(decomp, str):
                decomp = json.loads(decomp) if decomp else []
                q["question_decomposition"] = decomp
            paras = q.get("paragraphs", [])
            if isinstance(paras, str):
                paras = json.loads(paras) if paras else []
                q["paragraphs"] = paras
            if min_hops > 0 and len(decomp) < min_hops:
                continue
            questions.append(q)
    if max_questions > 0:
        questions = questions[:max_questions]
    return questions


def preload_corpus(client, questions):
    """Seed all paragraphs and links for all questions in batch.

    Each question's memories get source prefix eval/musique/{qid}/
    so search can be scoped per question without cross-contamination.
    """
    logger.info("Preloading %d questions (%d total paragraphs)...",
                len(questions), sum(len(q["paragraphs"]) for q in questions))

    # Track memory IDs per question: {qid: {para_idx: real_id}}
    id_maps = {}
    total_memories = 0
    total_links = 0

    for qi, q in enumerate(questions):
        qid = q["id"]
        paras = q["paragraphs"]
        decomp = q["question_decomposition"]

        # Batch seed paragraphs
        batch = []
        for para in paras:
            batch.append({
                "text": para["paragraph_text"],
                "source": f"{PREFIX}/{qid}/{para['title'][:50]}",
            })

        r = client.post("/memory/add-batch", json={
            "memories": batch,
            "deduplicate": False,
        })
        r.raise_for_status()
        batch_ids = r.json().get("ids", [])
        total_memories += len(batch_ids)

        # Map para indices to real IDs
        id_map = {}
        for i, para in enumerate(paras):
            if i < len(batch_ids):
                id_map[para["idx"]] = batch_ids[i]
        id_maps[qid] = id_map

        # Create links along the hop chain
        supporting_indices = [d["paragraph_support_idx"] for d in decomp]
        for i in range(len(supporting_indices) - 1):
            from_idx = supporting_indices[i]
            to_idx = supporting_indices[i + 1]
            if from_idx in id_map and to_idx in id_map:
                r = client.post(f"/memory/{id_map[from_idx]}/link", json={
                    "to_id": id_map[to_idx],
                    "type": "related_to",
                })
                if r.status_code == 200:
                    total_links += 1

        if (qi + 1) % 50 == 0:
            logger.info("  Preloaded %d/%d questions (%d memories, %d links)",
                        qi + 1, len(questions), total_memories, total_links)

    logger.info("Preload complete: %d memories, %d links", total_memories, total_links)
    return id_maps


def run_eval(client, questions, id_maps):
    """Run all searches as read-only — no writes during eval."""
    results = []

    for qi, q in enumerate(questions):
        qid = q["id"]
        query = q["question"]
        answer = q["answer"]
        decomp = q["question_decomposition"]
        n_hops = len(decomp)
        id_map = id_maps.get(qid, {})

        # Supporting paragraph IDs
        supporting_indices = [d["paragraph_support_idx"] for d in decomp]
        supporting_ids = set(id_map[idx] for idx in supporting_indices if idx in id_map)

        # Search with graph OFF
        r_off = client.post("/search", json={
            "query": query, "k": 5, "hybrid": True,
            "graph_weight": 0.0, "source_prefix": f"{PREFIX}/{qid}",
        })
        results_off = r_off.json().get("results", [])

        # Search with graph ON
        r_on = client.post("/search", json={
            "query": query, "k": 5, "hybrid": True,
            "graph_weight": 0.1, "source_prefix": f"{PREFIX}/{qid}",
        })
        results_on = r_on.json().get("results", [])

        # Metrics
        def has_answer(res_list):
            return answer.lower() in " ".join(r.get("text", "") for r in res_list).lower()

        def support_recall(res_list):
            found = sum(1 for r in res_list if r["id"] in supporting_ids)
            return found / len(supporting_ids) if supporting_ids else 0

        def answer_rank(res_list):
            for i, r in enumerate(res_list):
                if answer.lower() in r.get("text", "").lower():
                    return i + 1
            return -1  # not found

        hit_off = has_answer(results_off)
        hit_on = has_answer(results_on)

        # Conditional: OFF has at least 1 support but NOT the answer
        off_ids = {r["id"] for r in results_off}
        has_support_off = bool(off_ids & supporting_ids)
        conditional_candidate = has_support_off and not hit_off

        result = {
            "qid": str(qid),
            "n_hops": n_hops,
            "question": query,
            "answer": answer,
            "hit_off": hit_off,
            "hit_on": hit_on,
            "delta": int(hit_on) - int(hit_off),
            "support_recall_off": round(support_recall(results_off), 3),
            "support_recall_on": round(support_recall(results_on), 3),
            "answer_rank_off": answer_rank(results_off),
            "answer_rank_on": answer_rank(results_on),
            "conditional_candidate": conditional_candidate,
            "conditional_rescued": conditional_candidate and hit_on,
            "graph_only": sum(1 for r in results_on if r.get("match_type") == "graph"),
            "boosted": sum(1 for r in results_on if r.get("match_type") == "direct+graph"),
        }
        results.append(result)

        if (qi + 1) % 50 == 0:
            hits_off = sum(r["hit_off"] for r in results)
            hits_on = sum(r["hit_on"] for r in results)
            logger.info("Progress: %d/%d off=%d on=%d",
                        qi + 1, len(questions), hits_off, hits_on)

    return results


def print_report(results, elapsed):
    n = len(results)
    hits_off = sum(r["hit_off"] for r in results)
    hits_on = sum(r["hit_on"] for r in results)
    improved = sum(1 for r in results if r["delta"] > 0)
    regressed = sum(1 for r in results if r["delta"] < 0)

    # Support chain recall
    avg_recall_off = sum(r["support_recall_off"] for r in results) / n
    avg_recall_on = sum(r["support_recall_on"] for r in results) / n

    # Conditional slice
    conditional = [r for r in results if r["conditional_candidate"]]
    rescued = sum(1 for r in conditional if r["conditional_rescued"])

    # By hop count
    by_hops = {}
    for r in results:
        h = r["n_hops"]
        by_hops.setdefault(h, []).append(r)

    # Answer rank improvement
    rank_improved = sum(1 for r in results
                        if r["answer_rank_on"] > 0 and r["answer_rank_off"] > 0
                        and r["answer_rank_on"] < r["answer_rank_off"])

    print()
    print("=" * 70)
    print(f"MuSiQue Multi-Hop Graph Eval ({time.strftime('%Y-%m-%d')})")
    print(f"Questions: {n} | Time: {elapsed:.1f}s ({elapsed/n:.1f}s/q)")
    print("=" * 70)
    print()
    print(f"  Answer Hit Rate:")
    print(f"    Graph OFF: {hits_off}/{n} ({hits_off/n*100:.1f}%)")
    print(f"    Graph ON:  {hits_on}/{n} ({hits_on/n*100:.1f}%)")
    print(f"    DELTA:     {hits_on-hits_off:+d} ({(hits_on-hits_off)/n*100:+.1f}%)")
    print(f"    Improved: {improved} | Regressed: {regressed}")
    print()
    print(f"  Support Chain Recall (avg of supporting paras in top-5):")
    print(f"    Graph OFF: {avg_recall_off:.1%}")
    print(f"    Graph ON:  {avg_recall_on:.1%}")
    print(f"    DELTA:     {avg_recall_on-avg_recall_off:+.1%}")
    print()
    print(f"  Conditional Slice (OFF has support but not answer):")
    print(f"    Candidates: {len(conditional)}/{n}")
    print(f"    Rescued by graph: {rescued}/{len(conditional)}" +
          (f" ({rescued/len(conditional)*100:.0f}%)" if conditional else ""))
    print()
    print(f"  Answer Rank Improvement (both found, graph ranked higher):")
    print(f"    {rank_improved}/{n}")
    print()
    print(f"  By Hop Count:")
    for h in sorted(by_hops):
        items = by_hops[h]
        h_off = sum(r["hit_off"] for r in items)
        h_on = sum(r["hit_on"] for r in items)
        print(f"    {h}-hop: OFF={h_off}/{len(items)} ({h_off/len(items)*100:.0f}%) "
              f"ON={h_on}/{len(items)} ({h_on/len(items)*100:.0f}%) "
              f"delta={h_on-h_off:+d}")
    print()
    print("=" * 70)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MuSiQue preloaded graph eval")
    parser.add_argument("--questions", type=int, default=100)
    parser.add_argument("--hops", type=int, default=0, help="Min hops (0=all, 3=3+ only)")
    parser.add_argument("--output", default="")
    parser.add_argument("--dataset", default="eval/datasets/musique-multihop.jsonl")
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

    # Load
    questions = load_dataset(args.dataset, args.questions, min_hops=args.hops)
    logger.info("Loaded %d questions (min_hops=%d)", len(questions), args.hops)

    # Phase 1: Preload
    client.post("/memory/delete-by-prefix", json={"source_prefix": PREFIX})
    start = time.time()
    id_maps = preload_corpus(client, questions)
    preload_time = time.time() - start
    logger.info("Preload took %.1fs", preload_time)

    # Phase 2: Eval (read-only)
    start = time.time()
    results = run_eval(client, questions, id_maps)
    eval_time = time.time() - start

    # Phase 3: Cleanup
    client.post("/memory/delete-by-prefix", json={"source_prefix": PREFIX})

    # Report
    print_report(results, eval_time)

    # Save
    if not args.output:
        args.output = f"eval/results/musique-preload-{len(results)}q-{time.strftime('%Y%m%d-%H%M%S')}.json"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    n = len(results)
    report = {
        "version": "2.0.0",
        "dataset": "musique-multihop",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "questions_run": n,
        "preload_seconds": round(preload_time, 1),
        "eval_seconds": round(eval_time, 1),
        "graph_off_hits": sum(r["hit_off"] for r in results),
        "graph_on_hits": sum(r["hit_on"] for r in results),
        "graph_off_pct": round(sum(r["hit_off"] for r in results) / n * 100, 1),
        "graph_on_pct": round(sum(r["hit_on"] for r in results) / n * 100, 1),
        "delta_pct": round((sum(r["hit_on"] for r in results) - sum(r["hit_off"] for r in results)) / n * 100, 1),
        "improved": sum(1 for r in results if r["delta"] > 0),
        "regressed": sum(1 for r in results if r["delta"] < 0),
        "avg_support_recall_off": round(sum(r["support_recall_off"] for r in results) / n, 3),
        "avg_support_recall_on": round(sum(r["support_recall_on"] for r in results) / n, 3),
        "conditional_candidates": sum(1 for r in results if r["conditional_candidate"]),
        "conditional_rescued": sum(1 for r in results if r["conditional_rescued"]),
        "details": results,
    }

    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Results saved to %s", args.output)


if __name__ == "__main__":
    main()
