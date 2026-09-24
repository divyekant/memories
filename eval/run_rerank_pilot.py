"""P0 pilot: zero-shot Ettin reranker vs engine hybrid search on LongMemEval_s.

Runs MemoryEngine in-process with a temporary local Qdrant per question.
No Docker, no service, no training. See
docs/decisions/2026-09-23-memory-orchestrator-small-model.md (phase P0).

    nice -n 10 .venv/bin/python -m eval.run_rerank_pilot --per-category 10
"""

import argparse
import json
import math
import random
import resource
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from eval.longmemeval import LONGMEMEVAL_CATEGORIES, LongMemEvalRunner, _SESSION_INDEX_RE
from rerank_shadow import EttinReranker

DATASET = Path("eval/scenarios/longmemeval/longmemeval_s_cleaned.json")
RERANK_N = 20


def self_check(reranker: EttinReranker) -> None:
    # Model-card example: bf16 reference scores [6.22, 10.81, 8.56, 9.88].
    q = "Which planet is known as the Red Planet?"
    docs = [
        "Venus is often called Earth's twin because of its similar size and proximity.",
        "Mars, known for its reddish appearance, is often referred to as the Red Planet.",
        "Jupiter, the largest planet in our solar system, has a prominent red spot.",
        "Saturn, famous for its rings, is sometimes mistaken for the Red Planet.",
    ]
    s = reranker.score(q, docs)
    print("self-check scores", np.round(s, 2))
    assert int(np.argmax(s)) == 1 and int(np.argmin(s)) == 0, s


def _iter_questions(path: Path):
    """Stream the 277 MB JSON array one object at a time to keep RAM low."""
    decoder = json.JSONDecoder()
    with path.open() as f:
        buf = f.read(1 << 20).lstrip()[1:]  # drop leading '['
        while True:
            buf = buf.lstrip().lstrip(",").lstrip()
            if buf.startswith("]"):
                return
            try:
                obj, pos = decoder.raw_decode(buf)
            except json.JSONDecodeError:
                more = f.read(1 << 20)
                if not more:
                    return
                buf += more
                continue
            buf = buf[pos:]
            yield obj


def sample_questions(path: Path, per_category: int, seed: int) -> list[dict]:
    ids: dict[str, list[str]] = {c: [] for c in LONGMEMEVAL_CATEGORIES}
    for q in _iter_questions(path):
        qid = str(q.get("question_id", ""))
        if q.get("question_type") in ids and not qid.endswith("_abs"):
            ids[q["question_type"]].append(qid)
    rng = random.Random(seed)
    chosen = {qid for c in LONGMEMEVAL_CATEGORIES for qid in rng.sample(ids[c], min(per_category, len(ids[c])))}
    return [q for q in _iter_questions(path) if str(q.get("question_id", "")) in chosen]


class EngineClient:
    """Minimal adapter so LongMemEvalRunner can seed and search an in-process engine."""

    def __init__(self, engine, retriever: str = "hybrid"):
        self.engine = engine
        self.retriever = retriever

    def clear_by_prefix(self, prefix):
        return 0  # fresh engine per question

    def add_batch(self, memories, deduplicate=False):
        return self.engine.add_memories(
            [m["text"] for m in memories], [m["source"] for m in memories], [m.get("metadata", {}) for m in memories]
        )

    def search(self, query, k, hybrid=True, source_prefix=None, reference_date=None):
        if self.retriever == "vector":
            return self.engine.search(query, k=k, source_prefix=source_prefix, reinforce_results=False)
        return self.engine.hybrid_search(query, k=k, source_prefix=source_prefix)


def _sessions(results: list[dict]) -> list[int]:
    seen, out = set(), []
    for r in results:
        m = _SESSION_INDEX_RE.search(r.get("source", ""))
        if m and int(m.group(1)) not in seen:
            seen.add(int(m.group(1)))
            out.append(int(m.group(1)))
    return out


def metrics(results: list[dict], gold: set[int]) -> dict:
    sess = _sessions(results)
    first = next((i for i, s in enumerate(sess) if s in gold), None)
    dcg = sum(1 / math.log2(i + 2) for i, s in enumerate(sess[:5]) if s in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 5)))
    return {
        "r@1": float(bool(sess[:1]) and sess[0] in gold),
        "r@5": float(any(s in gold for s in sess[:5])),
        "mrr": 0.0 if first is None else 1 / (first + 1),
        "ndcg@5": dcg / idcg if idcg else 0.0,
    }


def main() -> None:
    from memory_engine import MemoryEngine

    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default="cross-encoder/ettin-reranker-32m-v1")
    ap.add_argument("--retriever", choices=["hybrid", "vector"], default="hybrid")
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--output", default="eval/results/rerank-pilot.json")
    args = ap.parse_args()

    reranker = EttinReranker(args.model, onnx_file="onnx/model_qint8_arm64.onnx")
    self_check(reranker)
    questions = sample_questions(args.dataset, args.per_category, args.seed)
    print(f"{len(questions)} questions")

    rows = []
    for i, q in enumerate(questions):
        tmp = tempfile.mkdtemp(prefix="rerank-pilot-")
        try:
            runner = LongMemEvalRunner(EngineClient(MemoryEngine(data_dir=tmp), args.retriever))
            t0 = time.time()
            n = runner.seed_question(q)
            base = runner.run_question(q)["search_results"]
            t1 = time.time()
            head = base[:RERANK_N]
            scores = reranker.score(q["question"], [r.get("text", "") for r in head])
            reranked = [head[j] for j in np.argsort(-scores, kind="stable")] + base[RERANK_N:]
            t2 = time.time()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        sids = q.get("haystack_session_ids", [])
        gold = {j for j, sid in enumerate(sids) if sid in set(q.get("answer_session_ids", []))}
        row = {
            "question_id": q["question_id"],
            "category": q["question_type"],
            "memories": n,
            "ceiling@20": float(any(s in gold for s in _sessions(head))),
            "hybrid": metrics(base, gold),
            "rerank": metrics(reranked, gold),
            "seed_search_s": round(t1 - t0, 2),
            "rerank_s": round(t2 - t1, 3),
        }
        rows.append(row)
        print(i + 1, row["category"], row["hybrid"], row["rerank"], row["rerank_s"], flush=True)

    summary = {"model": args.model, "retriever": args.retriever, "n": len(rows), "rerank_n": RERANK_N,
               "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // (1 << 20)}
    for arm in ("hybrid", "rerank"):
        summary[arm] = {m: round(float(np.mean([r[arm][m] for r in rows])), 4) for m in rows[0][arm]}
    summary["ceiling@20"] = round(float(np.mean([r["ceiling@20"] for r in rows])), 4)
    rng = np.random.default_rng(0)
    diffs = np.array([r["rerank"]["ndcg@5"] - r["hybrid"]["ndcg@5"] for r in rows])
    boots = [rng.choice(diffs, len(diffs)).mean() for _ in range(2000)]
    summary["ndcg@5_delta_ci95"] = [round(float(np.percentile(boots, p)), 4) for p in (2.5, 97.5)]
    summary["by_category"] = {
        c: {arm: {m: round(float(np.mean([r[arm][m] for r in rows if r["category"] == c])), 3) for m in ("r@1", "r@5", "ndcg@5")}
            for arm in ("hybrid", "rerank")}
        for c in LONGMEMEVAL_CATEGORIES if any(r["category"] == c for r in rows)
    }
    summary["rerank_s_p50_p95"] = [round(float(np.percentile([r["rerank_s"] for r in rows], p)), 3) for p in (50, 95)]
    Path(args.output).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
