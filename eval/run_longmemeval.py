#!/usr/bin/env python3
"""Run LongMemEval benchmark against the Memories engine.

Usage:
    python eval/run_longmemeval.py [--questions N] [--output PATH] [--mode tool|system] [--workers N]

This script:
1. Loads the LongMemEval dataset (500 questions)
2. For each question, seeds its haystack sessions as memories
3. Searches for the answer using hybrid search (tool) or agent reasoning (system)
4. Judges the retrieval quality with an LLM
5. Reports per-category and overall scores
"""

import json
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.longmemeval import LongMemEvalRunner
from eval.memories_client import MemoriesClient

# Thread-local storage for per-thread MemoriesClient instances.
# httpx.Client is not thread-safe — each worker needs its own.
_thread_local = threading.local()

_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def _load_local_env() -> None:
    """Best-effort .env loading for local benchmark runs."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv()


def _get_thread_client(url: str, api_key: str) -> MemoriesClient:
    """Get or create a per-thread MemoriesClient (httpx.Client is not thread-safe)."""
    if not hasattr(_thread_local, "client"):
        _thread_local.client = MemoriesClient(url=url, api_key=api_key)
    return _thread_local.client


def _process_question(
    idx: int,
    total: int,
    q: dict,
    runner: LongMemEvalRunner,
    mode: str,
    prefix: str,
    cc_executor=None,
    project_queue: "queue.Queue | None" = None,
    client_url: str = "",
    client_api_key: str = "",
) -> dict:
    """Process a single question. Thread-safe — uses scoped prefix per question.

    For system mode: acquires a project dir from the queue exclusively,
    returns it when done (even on failure). No two questions share a
    project dir simultaneously.

    For parallel mode: uses per-thread MemoriesClient via threading.local()
    since httpx.Client is not thread-safe.
    """
    qid = q.get("question_id", idx)
    qtype = q.get("question_type", "unknown")
    question = str(q.get("question", ""))
    expected = str(q.get("answer", ""))

    _log(f"\n[{idx+1}/{total}] Q{qid} ({qtype}): {question[:60]}...")

    # Acquire exclusive project dir for system mode
    project_dir = ""
    if project_queue is not None:
        project_dir = project_queue.get()

    # Use per-thread client for parallel safety (httpx.Client is not thread-safe)
    if client_url:
        thread_client = _get_thread_client(client_url, client_api_key)
        thread_runner = LongMemEvalRunner(client=thread_client, judge_provider=runner.judge_provider, judge_model=runner.judge_model)
        thread_runner._judge = runner._judge  # Share the judge (LLM provider is thread-safe)
    else:
        thread_runner = runner

    try:
        try:
            seeded = thread_runner.seed_question(q, source_prefix=prefix)
        except Exception as e:
            _log(f"  Seeding failed: {e}")
            seeded = 0

        _log(f"  Seeded {seeded} memory chunks")

        try:
            if mode == "system" and cc_executor:
                question_result = thread_runner.run_question_system(
                    q,
                    cc_executor=cc_executor,
                    source_prefix=prefix,
                    project_dir=project_dir,
                )
                _log(f"  Agent responded: {len(question_result['context'])} chars")
            else:
                question_result = thread_runner.run_question(q, k=10, source_prefix=prefix)
                results = question_result["search_results"]
                _log(
                    f"  Retrieved {len(results)} results, judge context(top-{runner.DEFAULT_CONTEXT_RESULTS}): {len(question_result['context'])} chars"
                )
        except Exception as e:
            _log(f"  {'Agent' if mode == 'system' else 'Search'} failed: {e}")
            question_result = {
                "question": question,
                "expected": expected,
                "context": "",
                "eval_mode": mode,
            }

        if not question_result.get("context"):
            score, reasoning = 0.0, "No context retrieved"
        else:
            try:
                score, reasoning = thread_runner._judge_single(question_result)
            except Exception as e:
                _log(f"  Judge failed: {e}")
                score, reasoning = 0.0, str(e)

        _log(f"  Score: {score:.2f} | Expected: {expected[:60]}")
        return {"qid": qid, "type": qtype, "score": score, "reasoning": reasoning}
    finally:
        try:
            thread_runner.clear_question(q, source_prefix=prefix)
        except Exception as e:
            _log(f"  Cleanup failed: {e}")
        if project_queue is not None and project_dir:
            if cc_executor:
                cc_executor.reset_project(project_dir)
            project_queue.put(project_dir)


def run_benchmark(max_questions: int = 0, output_path: str = "", mode: str = "tool", workers: int = 1):
    _load_local_env()
    url = os.environ.get("MEMORIES_URL", "http://localhost:8900")
    api_key = os.environ.get("MEMORIES_API_KEY", "")

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

    _log("Initializing LLM judge...")
    runner.init_judge()
    if runner._judge is None:
        _log("ERROR: Judge failed to initialize. Set EXTRACT_PROVIDER and ANTHROPIC_API_KEY.")
        sys.exit(1)
    _log(f"Judge ready: {type(runner._judge).__name__}")

    # System mode setup
    cc_executor = None
    project_queue = None
    if mode == "system":
        from eval.cc_executor import CCExecutor
        mcp_server_path = os.environ.get(
            "EVAL_MCP_SERVER_PATH",
            str(Path(__file__).parent.parent / "mcp-server" / "index.js"),
        )
        cc_executor = CCExecutor(
            timeout=120,
            memories_url=url,
            memories_api_key=api_key,
            mcp_server_path=mcp_server_path,
        )
        CCExecutor.cleanup_stale_auto_memory()
        project_queue = queue.Queue()
        for i in range(workers):
            project_queue.put(cc_executor.create_isolated_project(with_memories=True))
        _log(f"System eval mode: {workers} worker(s), MCP server at {mcp_server_path}")

    _log(f"Running in {mode} eval mode with {workers} worker(s)")
    start_time = time.time()

    prefix = "eval/longmemeval"
    total = len(dataset)

    if workers <= 1:
        scores = []
        by_type = {}
        for i, q in enumerate(dataset):
            result = _process_question(i, total, q, runner, mode, prefix, cc_executor, project_queue)
            if result:
                scores.append(result)
                by_type.setdefault(result["type"], []).append(result["score"])
    else:
        scores = [None] * total
        by_type = {}
        _log(f"Distributing {total} questions across {workers} workers...")

        def _worker(idx, q):
            return idx, _process_question(
                idx, total, q, runner, mode, prefix, cc_executor, project_queue,
                client_url=url, client_api_key=api_key,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_worker, i, q): i
                for i, q in enumerate(dataset)
            }
            for future in as_completed(futures):
                try:
                    idx, result = future.result()
                    scores[idx] = result
                except Exception as e:
                    idx = futures[future]
                    _log(f"  Worker error on question {idx}: {e}")
                    scores[idx] = {"qid": idx, "type": "error", "score": 0.0, "reasoning": str(e)}

        # Filter None entries and build by_type
        scores = [s for s in scores if s is not None]
        for s in scores:
            by_type.setdefault(s["type"], []).append(s["score"])

    if cc_executor and project_queue:
        while not project_queue.empty():
            try:
                cc_executor.cleanup_project(project_queue.get_nowait())
            except queue.Empty:
                break

    elapsed = time.time() - start_time

    # Report
    overall = sum(s["score"] for s in scores) / len(scores) if scores else 0
    _log(f"\n{'='*60}")
    _log(f"LongMemEval v4.0.0 ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})")
    _log(f"Mode: {mode} | Workers: {workers} | Time: {elapsed:.0f}s ({elapsed/60:.1f}m)")
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
            "eval_mode": mode,
            "workers": workers,
            "elapsed_seconds": round(elapsed, 1),
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
    parser.add_argument("--output", default=None, help="Output file (default: eval/results/longmemeval-v4.0.0-{mode}.json)")
    parser.add_argument("--mode", choices=["tool", "system"], default="tool",
                        help="Eval mode: 'tool' = raw API search (diagnostic), 'system' = agent + MCP tools (product score)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel workers (default: 1). Each worker processes questions independently.")
    args = parser.parse_args()
    output = args.output or f"eval/results/longmemeval-v4.0.0-{args.mode}.json"
    run_benchmark(max_questions=args.questions, output_path=output, mode=args.mode, workers=args.workers)
