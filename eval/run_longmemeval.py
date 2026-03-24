#!/usr/bin/env python3
"""Run LongMemEval benchmark against the Memories engine.

Usage:
    python eval/run_longmemeval.py [--questions N] [--output PATH]

This script:
1. Loads the LongMemEval dataset (500 questions)
2. For each question, seeds its haystack sessions as memories
3. Searches for the answer using hybrid search
4. Judges the retrieval quality with an LLM
5. Reports per-category and overall scores
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.longmemeval import LongMemEvalRunner
from eval.memories_client import MemoriesClient


def _log(message: str) -> None:
    print(message, flush=True)


def _load_local_env() -> None:
    """Best-effort .env loading for local benchmark runs."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv()


def run_benchmark(max_questions: int = 0, output_path: str = ""):
    _load_local_env()
    url = os.environ.get("MEMORIES_URL", "http://localhost:8900")
    api_key = os.environ.get("MEMORIES_API_KEY", "god-is-an-astronaut")

    client = MemoriesClient(url=url, api_key=api_key)
    if not client.health_check():
        _log("ERROR: Memories service not reachable")
        sys.exit(1)

    runner = LongMemEvalRunner(client=client, judge_provider="anthropic")
    _log("Loading dataset...")
    dataset = runner.load_dataset()
    _log(f"Loaded {len(dataset)} questions")

    if max_questions > 0:
        dataset = dataset[:max_questions]
        _log(f"Running subset: {max_questions} questions")

    # Initialize judge before the loop
    _log("Initializing LLM judge...")
    runner._init_judge()
    if runner._judge is None:
        _log("ERROR: Judge failed to initialize. Set EXTRACT_PROVIDER and ANTHROPIC_API_KEY.")
        sys.exit(1)
    _log(f"Judge ready: {type(runner._judge).__name__}")

    prefix = "eval/longmemeval"
    scores = []
    by_type = {}
    total = len(dataset)

    for i, q in enumerate(dataset):
        qid = q.get("question_id", i)
        qtype = q.get("question_type", "unknown")
        question = str(q.get("question", ""))
        expected = str(q.get("answer", ""))

        _log(f"\n[{i+1}/{total}] Q{qid} ({qtype}): {question[:60]}...")

        try:
            # Step 1: Seed this question's sessions as direct memories
            try:
                seeded = runner.seed_question(q, source_prefix=prefix)
            except Exception as e:
                _log(f"  Seeding failed: {e}")
                seeded = 0

            _log(f"  Seeded {seeded} memory chunks")

            # Step 2: Search for the answer
            try:
                question_result = runner.run_question(q, k=10, source_prefix=prefix)
                results = question_result["search_results"]
            except Exception as e:
                _log(f"  Search failed: {e}")
                results = []
                question_result = {
                    "question": question,
                    "expected": expected,
                    "context": "",
                }

            context = question_result["context"]
            _log(
                f"  Retrieved {len(results)} results, judge context(top-{runner.DEFAULT_CONTEXT_RESULTS}): {len(context)} chars"
            )

            # Step 3: Judge — does the context contain the answer?
            try:
                score, reasoning = runner._judge_single(
                    {
                        "question": question_result["question"],
                        "expected": question_result["expected"],
                        "context": question_result["context"],
                    }
                )
            except Exception as e:
                _log(f"  Judge failed: {e}")
                score, reasoning = 0.0, str(e)

            _log(f"  Score: {score:.2f} | Expected: {expected[:60]}")
            scores.append({"qid": qid, "type": qtype, "score": score, "reasoning": reasoning})
            by_type.setdefault(qtype, []).append(score)
        finally:
            try:
                runner.clear_question(q, source_prefix=prefix)
            except Exception as e:
                _log(f"  Cleanup failed: {e}")

    # Report
    overall = sum(s["score"] for s in scores) / len(scores) if scores else 0
    _log(f"\n{'='*60}")
    _log(f"LongMemEval v4.0.0 ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})")
    _log(f"Questions: {len(scores)}")
    _log(f"Overall: {overall*100:.1f}%")
    _log(f"{'='*60}")
    for qtype, type_scores in sorted(by_type.items()):
        avg = sum(type_scores) / len(type_scores) if type_scores else 0
        _log(f"  {qtype}: {avg*100:.1f}% ({len(type_scores)} questions)")

    # Save results
    if output_path:
        result = {
            "version": "4.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "questions_run": len(scores),
            "overall": round(overall, 4),
            "categories": {
                t: round(sum(s) / len(s), 4) for t, s in by_type.items()
            },
            "details": scores,
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(result, indent=2))
        _log(f"\nResults saved to {output_path}")

    return overall


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run LongMemEval benchmark")
    parser.add_argument("--questions", type=int, default=0, help="Limit to N questions (0=all)")
    parser.add_argument("--output", default="eval/results/longmemeval-v4.0.0.json", help="Output file")
    args = parser.parse_args()
    run_benchmark(max_questions=args.questions, output_path=args.output)
