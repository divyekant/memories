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
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.longmemeval import LongMemEvalRunner
from eval.memories_client import MemoriesClient


def run_benchmark(max_questions: int = 0, output_path: str = ""):
    url = os.environ.get("MEMORIES_URL", "http://localhost:8900")
    api_key = os.environ.get("MEMORIES_API_KEY", "god-is-an-astronaut")

    client = MemoriesClient(url=url, api_key=api_key)
    if not client.health_check():
        print("ERROR: Memories service not reachable")
        sys.exit(1)

    runner = LongMemEvalRunner(client=client, judge_provider="anthropic")
    print("Loading dataset...")
    dataset = runner.load_dataset()
    print(f"Loaded {len(dataset)} questions")

    if max_questions > 0:
        dataset = dataset[:max_questions]
        print(f"Running subset: {max_questions} questions")

    prefix = "eval/longmemeval"
    scores = []
    by_type = {}
    total = len(dataset)

    for i, q in enumerate(dataset):
        qid = q.get("question_id", i)
        qtype = q.get("question_type", "unknown")
        question = q.get("question", "")
        expected = q.get("answer", "")
        sessions = q.get("haystack_sessions", [])

        print(f"\n[{i+1}/{total}] Q{qid} ({qtype}): {question[:60]}...")

        # Step 1: Seed this question's sessions as memories
        client.clear_by_prefix(f"{prefix}/q{qid}")
        seeded = 0
        for j, session in enumerate(sessions):
            # Each session is a list of turns [{role, content}, ...]
            lines = []
            for turn in session:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                lines.append(f"{role}: {content}")
            text = "\n\n".join(lines)
            if len(text) > 50:  # skip trivially short sessions
                try:
                    client.extract(
                        messages=text,
                        source=f"{prefix}/q{qid}/s{j}",
                        context="stop",
                    )
                    seeded += 1
                except Exception as e:
                    print(f"  Extract failed for session {j}: {e}")

        print(f"  Seeded {seeded}/{len(sessions)} sessions")

        # Step 2: Search for the answer
        try:
            results = client.search(
                query=question,
                k=10,
                hybrid=True,
                source_prefix=f"{prefix}/q{qid}",
            )
        except Exception as e:
            print(f"  Search failed: {e}")
            results = []

        context = "\n".join(r.get("text", "") for r in results[:5])
        print(f"  Retrieved {len(results)} results, top-5 context: {len(context)} chars")

        # Step 3: Judge — does the context contain the answer?
        try:
            score, reasoning = runner._judge_single({
                "question": question,
                "expected": expected,
                "context": context,
            })
        except Exception as e:
            print(f"  Judge failed: {e}")
            score, reasoning = 0.0, str(e)

        print(f"  Score: {score:.2f} | Expected: {expected[:60]}")
        scores.append({"qid": qid, "type": qtype, "score": score, "reasoning": reasoning})
        by_type.setdefault(qtype, []).append(score)

    # Report
    overall = sum(s["score"] for s in scores) / len(scores) if scores else 0
    print(f"\n{'='*60}")
    print(f"LongMemEval v4.0.0 ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})")
    print(f"Questions: {len(scores)}")
    print(f"Overall: {overall*100:.1f}%")
    print(f"{'='*60}")
    for qtype, type_scores in sorted(by_type.items()):
        avg = sum(type_scores) / len(type_scores) if type_scores else 0
        print(f"  {qtype}: {avg*100:.1f}% ({len(type_scores)} questions)")

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
        print(f"\nResults saved to {output_path}")

    # Cleanup eval memories
    print("\nCleaning up eval memories...")
    client.clear_by_prefix(prefix)
    print("Done.")

    return overall


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run LongMemEval benchmark")
    parser.add_argument("--questions", type=int, default=0, help="Limit to N questions (0=all)")
    parser.add_argument("--output", default="eval/results/longmemeval-v4.0.0.json", help="Output file")
    args = parser.parse_args()
    run_benchmark(max_questions=args.questions, output_path=args.output)
