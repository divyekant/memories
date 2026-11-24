"""Writes usage.db and expected.json with the Python UsageTracker (v5.19.0).

The Rust test `tests/usage_db.rs` reads this usage.db, writes the same rows
with the Rust writer, and compares each read with expected.json.

Run from the repository root: .venv/bin/python server/crates/memories-server/tests/fixtures/usage/make_fixture.py
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, *[".."] * 5)))

from usage_tracker import UsageTracker  # noqa: E402

OLD = "2000-01-01T00:00:00Z"  # outside each period except "all"
NEW = "2999-01-01T00:00:00Z"  # inside each period

# (method, kwargs, ts). The same list is in rows.json for the Rust test.
ROWS = [
    ("log_api_event", {"operation": "search", "source": "", "count": 1,
                       "client": "codex", "session_id": "s-1", "invocation": "inv-1"}, NEW),
    ("log_api_event", {"operation": "search", "source": "", "count": 1}, NEW),
    ("log_api_event", {"operation": "search_batch", "source": "x", "count": 3}, NEW),
    ("log_api_event", {"operation": "add", "source": "codex/a", "count": 5,
                       "client": "claude-code", "session_id": "s-1"}, NEW),
    ("log_api_event", {"operation": "add", "source": "codex/a", "count": 2}, OLD),
    ("log_api_event", {"operation": "extract", "source": "é" * 600, "count": 1,
                       "client": "c" * 150, "session_id": "s" * 250, "invocation": "i" * 150}, NEW),
    ("log_extraction_tokens", {"provider": "anthropic", "model": "claude-haiku-4-5-20251001",
                               "stage": "extract", "input_tokens": 1200, "output_tokens": 300,
                               "source": "codex/a"}, NEW),
    ("log_extraction_tokens", {"provider": "anthropic", "model": "claude-haiku-4-5-20251001",
                               "stage": "audn", "input_tokens": 800, "output_tokens": 100}, NEW),
    ("log_extraction_tokens", {"provider": "ollama", "model": "unknown-model",
                               "stage": "extract", "input_tokens": 333333, "output_tokens": 7}, NEW),
    ("log_extraction_tokens", {"provider": "openai", "model": "gpt-4.1-mini",
                               "stage": "extract", "input_tokens": 50, "output_tokens": 5}, OLD),
    ("log_retrieval", {"memory_id": 1, "query": "q one", "source": "", "rank": 1, "result_count": 3}, NEW),
    ("log_retrieval", {"memory_id": 2, "query": "q one", "source": "", "rank": 2, "result_count": 3}, NEW),
    ("log_retrieval", {"memory_id": 3, "query": "q one", "source": "", "rank": 5, "result_count": 3}, NEW),
    ("log_retrieval", {"memory_id": 1, "query": "q two", "source": "s", "rank": 1, "result_count": 1}, NEW),
    ("log_retrieval", {"memory_id": 1, "query": "q three", "source": "", "rank": 3, "result_count": 2}, NEW),
    ("log_retrieval", {"memory_id": 4, "query": "q " * 300, "source": "", "rank": 0, "result_count": 0}, OLD),
    ("log_search_feedback", {"memory_id": 1, "query": "q one", "signal": "useful"}, NEW),
    ("log_search_feedback", {"memory_id": 1, "query": "q one", "signal": "not_useful", "search_id": "s1"}, NEW),
    ("log_search_feedback", {"memory_id": 2, "query": "q one", "signal": "not_useful"}, NEW),
    ("log_search_feedback", {"memory_id": 2, "query": "q two", "signal": "not_useful"}, NEW),
    ("log_search_feedback", {"memory_id": 3, "query": "", "signal": "not_useful"}, NEW),
    ("log_search_feedback", {"memory_id": 3, "query": "q one", "signal": "bad"}, NEW),
    ("log_search_feedback", {"memory_id": 4, "query": "old", "signal": "useful"}, OLD),
    ("log_extraction_outcome", {"source": "codex/a", "extracted": 4, "stored": 2, "updated": 1,
                                "deleted": 0, "noop": 1, "conflict": 0, "fallback": 0, "links_created": 2}, NEW),
    ("log_extraction_outcome", {"source": "", "extracted": 3, "stored": 0, "updated": 0,
                                "deleted": 0, "noop": 3, "conflict": 1, "fallback": 1}, NEW),
    ("log_extraction_outcome", {"source": "codex/b", "extracted": 0}, NEW),
    ("log_extraction_outcome", {"source": "codex/a", "extracted": 7, "stored": 7}, OLD),
    ("log_graph_search", {"query": "g", "graph_weight": 0.1, "direct_count": 3, "graph_count": 1,
                          "total_results": 4}, NEW),
    ("log_graph_search", {"query": "g2", "graph_weight": 0.3, "direct_count": 2, "graph_count": 0,
                          "total_results": 2}, NEW),
    ("log_graph_search", {"query": "old", "graph_weight": 0.1, "direct_count": 1, "graph_count": 1,
                          "total_results": 2}, OLD),
    ("log_temporal_search", {"query": "t", "has_since": True, "has_until": False, "auto_detected": True}, NEW),
    ("log_temporal_search", {"query": "t2", "has_since": True, "has_until": True}, NEW),
    ("log_temporal_search", {"query": "t3", "has_since": False, "has_until": True}, NEW),
    ("log_temporal_search", {"query": "t4", "has_since": True, "has_until": False}, OLD),
]

TABLE_OF = {
    "log_api_event": "api_events",
    "log_extraction_tokens": "extraction_tokens",
    "log_retrieval": "retrieval_log",
    "log_search_feedback": "search_feedback",
    "log_extraction_outcome": "extraction_outcomes",
    "log_graph_search": "graph_search_events",
    "log_temporal_search": "temporal_search_events",
}

# (name, method, kwargs): the reads to compare.
READS = []
for p in ("today", "7d", "30d", "all", "bogus"):
    READS += [
        (f"usage-{p}", "get_usage", {"period": p}),
        (f"search-quality-{p}", "get_search_quality", {"period": p}),
        (f"extraction-quality-{p}", "get_extraction_quality", {"period": p}),
        (f"quality-summary-{p}", "get_quality_summary", {"period": p}),
        (f"graph-{p}", "get_graph_search_stats", {"period": p}),
        (f"temporal-{p}", "get_temporal_search_stats", {"period": p}),
    ]
READS += [
    ("usage-session", "get_usage", {"period": "all", "session_id": "s-1"}),
    ("search-quality-scoped", "get_search_quality", {"period": "all", "memory_ids": [1, 2, 99]}),
    ("search-quality-scoped-7d", "get_search_quality", {"period": "7d", "memory_ids": [4, 1]}),
    ("search-quality-empty-scope", "get_search_quality", {"period": "7d", "memory_ids": []}),
    ("failures-retrieval", "get_failures", {"failure_type": "retrieval", "limit": 10}),
    ("failures-retrieval-1", "get_failures", {"failure_type": "retrieval", "limit": 1}),
    ("failures-extraction", "get_failures", {"failure_type": "extraction", "limit": 10}),
    ("problem-queries", "get_problem_queries", {}),
    ("problem-queries-1", "get_problem_queries", {"min_feedback": 1, "limit": 20}),
    ("problem-queries-limit", "get_problem_queries", {"min_feedback": 1, "limit": 1}),
    ("stale", "get_stale_memories", {}),
    ("stale-1", "get_stale_memories", {"min_retrievals": 1, "limit": 20}),
    ("feedback-scores", "get_feedback_scores", {}),
    ("feedback-history-1", "get_feedback_history", {"memory_id": 1, "limit": 50}),
    ("feedback-history-2-limit", "get_feedback_history", {"memory_id": 2, "limit": 1}),
    ("feedback-history-none", "get_feedback_history", {"memory_id": 77, "limit": 50}),
]


def main():
    db = os.path.join(HERE, "usage.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)
    t = UsageTracker(db)
    conn = sqlite3.connect(db)
    for method, kwargs, ts in ROWS:
        getattr(t, method)(**kwargs)
        table = TABLE_OF[method]
        conn.execute(f"UPDATE {table} SET ts = ? WHERE id = (SELECT MAX(id) FROM {table})", (ts,))
        conn.commit()
    expected = {}
    for name, method, kwargs in READS:
        out = getattr(t, method)(**kwargs)
        if isinstance(out, dict) and method == "get_feedback_scores":
            out = [[k, v] for k, v in out.items()]
        expected[name] = out
    t._local.conn.close()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    with open(os.path.join(HERE, "rows.json"), "w") as f:
        json.dump([{"method": m, "args": a, "ts": ts} for m, a, ts in ROWS], f, indent=1, ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(HERE, "reads.json"), "w") as f:
        json.dump([{"name": n, "method": m, "args": a} for n, m, a in READS], f, indent=1)
        f.write("\n")
    with open(os.path.join(HERE, "expected.json"), "w") as f:
        json.dump(expected, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
