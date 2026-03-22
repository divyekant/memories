"""Optional usage analytics with SQLite persistence.

Enabled via USAGE_TRACKING=true env var. When disabled, NullTracker provides
zero-overhead no-op stubs with the same interface.
"""
import logging
import sqlite3
import threading
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Per-model pricing (USD per 1M tokens)
MODEL_PRICING = {
    # Anthropic
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-5-20250514": {"input": 3.00, "output": 15.00},
    # OpenAI
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    # Ollama (free / local)
    "gemma3:4b": {"input": 0.0, "output": 0.0},
}

PERIOD_SQL = {
    "today": "AND ts >= strftime('%Y-%m-%dT00:00:00Z', 'now')",
    "7d": "AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')",
    "30d": "AND ts >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days')",
    "all": "",
}


class NullTracker:
    """No-op tracker when usage tracking is disabled."""

    def log_api_event(self, operation: str, source: str = "", count: int = 1) -> None:
        pass

    def log_extraction_tokens(
        self,
        provider: str,
        model: str,
        stage: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        source: str = "",
    ) -> None:
        pass

    def log_retrieval(self, memory_id: int, query: str = "", source: str = "", rank: int = 0, result_count: int = 0) -> None:
        pass

    def get_retrieval_stats(self, memory_ids: list[int]) -> dict:
        return {}

    def get_unretrieved_memory_ids(self, all_memory_ids: list[int]) -> list[int]:
        return []

    def log_search_feedback(self, memory_id: int, query: str = "", signal: str = "", search_id: str = "") -> None:
        pass

    def get_feedback_scores(self, memory_ids: list[int]) -> dict[int, int]:
        return {}

    def get_feedback_history(self, memory_id: int, limit: int = 50) -> list[dict]:
        return []

    def delete_feedback(self, feedback_id: int) -> bool:
        return False

    def get_search_quality(self, period: str = "7d", memory_ids: list | None = None) -> Dict[str, Any]:
        return {"enabled": False}

    def log_extraction_outcome(self, source: str = "", extracted: int = 0, stored: int = 0,
                               updated: int = 0, deleted: int = 0, noop: int = 0, conflict: int = 0) -> None:
        pass

    def get_extraction_quality(self, period: str = "7d") -> Dict[str, Any]:
        return {"enabled": False}

    def get_quality_summary(self, period: str = "7d") -> Dict[str, Any]:
        return {"enabled": False}

    def get_failures(self, failure_type: str = "retrieval", limit: int = 10) -> Dict[str, Any]:
        return {"enabled": False}

    def get_usage(self, period: str = "7d") -> Dict[str, Any]:
        return {"enabled": False}

    def get_problem_queries(self, min_feedback: int = 2, min_negative_ratio: float = 0.5,
                            limit: int = 20, memory_ids: list | None = None) -> list[dict]:
        return []

    def get_stale_memories(self, min_retrievals: int = 3, limit: int = 20,
                           memory_ids: list | None = None) -> list[dict]:
        return []


class UsageTracker:
    """SQLite-backed usage tracker with WAL mode for non-blocking writes."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()
        # Create tables on init using a dedicated connection
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                operation TEXT NOT NULL,
                source TEXT DEFAULT '',
                count INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS extraction_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                stage TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                source TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_api_events_ts ON api_events(ts);
            CREATE INDEX IF NOT EXISTS idx_api_events_op ON api_events(operation);
            CREATE INDEX IF NOT EXISTS idx_extraction_tokens_ts ON extraction_tokens(ts);
            CREATE TABLE IF NOT EXISTS retrieval_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                memory_id INTEGER NOT NULL,
                query TEXT DEFAULT '',
                source TEXT DEFAULT '',
                rank INTEGER DEFAULT 0,
                result_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_retrieval_memory ON retrieval_log(memory_id);
            CREATE INDEX IF NOT EXISTS idx_retrieval_ts ON retrieval_log(ts);
            CREATE TABLE IF NOT EXISTS search_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                memory_id INTEGER NOT NULL,
                query TEXT DEFAULT '',
                signal TEXT NOT NULL,
                search_id TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_ts ON search_feedback(ts);
            CREATE INDEX IF NOT EXISTS idx_feedback_memory ON search_feedback(memory_id);
            CREATE TABLE IF NOT EXISTS extraction_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                source TEXT DEFAULT '',
                extracted INTEGER DEFAULT 0,
                stored INTEGER DEFAULT 0,
                updated INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                noop INTEGER DEFAULT 0,
                conflict INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_extraction_outcomes_ts ON extraction_outcomes(ts);
        """)
        # Migrate existing DBs: add rank/result_count columns if missing
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(retrieval_log)").fetchall()}
            if "rank" not in cols:
                conn.execute("ALTER TABLE retrieval_log ADD COLUMN rank INTEGER DEFAULT 0")
            if "result_count" not in cols:
                conn.execute("ALTER TABLE retrieval_log ADD COLUMN result_count INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.close()
        logger.info("Usage tracker initialized: %s", db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        """Thread-local connection for write operations."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._connect()
        return self._local.conn

    def log_api_event(self, operation: str, source: str = "", count: int = 1) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO api_events (operation, source, count) VALUES (?, ?, ?)",
                (operation, source, count),
            )
            conn.commit()
        except Exception:
            logger.debug("Failed to log api event", exc_info=True)

    def log_extraction_tokens(
        self,
        provider: str,
        model: str,
        stage: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        source: str = "",
    ) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO extraction_tokens (provider, model, stage, input_tokens, output_tokens, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (provider, model, stage, input_tokens, output_tokens, source),
            )
            conn.commit()
        except Exception:
            logger.debug("Failed to log extraction tokens", exc_info=True)

    def log_retrieval(self, memory_id: int, query: str = "", source: str = "", rank: int = 0, result_count: int = 0) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO retrieval_log (memory_id, query, source, rank, result_count) VALUES (?, ?, ?, ?, ?)",
                (memory_id, query[:500], source, rank, result_count),
            )
            conn.commit()
        except Exception:
            logger.debug("Failed to log retrieval", exc_info=True)

    def get_retrieval_stats(self, memory_ids: list[int]) -> dict[int, dict]:
        if not memory_ids:
            return {}
        conn = self._connect()
        try:
            placeholders = ",".join("?" * len(memory_ids))
            rows = conn.execute(
                f"SELECT memory_id, COUNT(*) as cnt, MAX(ts) as last_ts "
                f"FROM retrieval_log WHERE memory_id IN ({placeholders}) "
                f"GROUP BY memory_id",
                memory_ids,
            ).fetchall()
            stats = {mid: {"count": 0, "last_retrieved_at": None} for mid in memory_ids}
            for row in rows:
                stats[row[0]] = {"count": row[1], "last_retrieved_at": row[2]}
            return stats
        finally:
            conn.close()

    def get_unretrieved_memory_ids(self, all_memory_ids: list[int]) -> list[int]:
        conn = self._connect()
        try:
            retrieved = set(
                row[0] for row in
                conn.execute("SELECT DISTINCT memory_id FROM retrieval_log").fetchall()
            )
            return [mid for mid in all_memory_ids if mid not in retrieved]
        finally:
            conn.close()

    VALID_SIGNALS = {"useful", "not_useful"}

    def log_search_feedback(self, memory_id: int, query: str = "", signal: str = "", search_id: str = "") -> None:
        if signal not in self.VALID_SIGNALS:
            return
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO search_feedback (memory_id, query, signal, search_id) VALUES (?, ?, ?, ?)",
                (memory_id, query[:500], signal, search_id),
            )
            conn.commit()
        except Exception:
            logger.debug("Failed to log search feedback", exc_info=True)

    def get_feedback_scores(self, memory_ids: list[int]) -> dict[int, int]:
        """Batch fetch net feedback score (useful - not_useful) for given memory IDs."""
        if not memory_ids:
            return {}
        conn = self._connect()
        try:
            placeholders = ",".join("?" * len(memory_ids))
            rows = conn.execute(
                f"SELECT memory_id, "
                f"SUM(CASE WHEN signal='useful' THEN 1 ELSE 0 END) - "
                f"SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as net "
                f"FROM search_feedback WHERE memory_id IN ({placeholders}) "
                f"GROUP BY memory_id",
                memory_ids,
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()

    def get_feedback_history(self, memory_id: int, limit: int = 50) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, ts, memory_id, query, signal, search_id "
                "FROM search_feedback WHERE memory_id = ? ORDER BY ts DESC LIMIT ?",
                (memory_id, limit),
            ).fetchall()
            return [dict(zip(["id", "ts", "memory_id", "query", "signal", "search_id"], r)) for r in rows]
        finally:
            conn.close()

    def delete_feedback(self, feedback_id: int) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM search_feedback WHERE id = ?", (feedback_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_search_quality(self, period: str = "7d", memory_ids: list | None = None) -> Dict[str, Any]:
        period_filter = PERIOD_SQL.get(period, PERIOD_SQL["7d"])
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            # Build optional memory_id filter for scoped callers
            mem_filter = ""
            mem_params: list = []
            if memory_ids is not None:
                if not memory_ids:
                    # No accessible memories — return empty metrics
                    return {
                        "period": period,
                        "total_searches": 0,
                        "rank_distribution": {"top_3": 0, "rank_4_plus": 0},
                        "feedback": {"useful": 0, "not_useful": 0, "useful_ratio": 0.0},
                        "unique_memories_retrieved": 0,
                    }
                placeholders = ",".join("?" * len(memory_ids))
                mem_filter = f" AND memory_id IN ({placeholders})"
                mem_params = list(memory_ids)

            # Total searches — scope-aware
            if memory_ids is not None:
                # For scoped callers, count retrievals that touched their memories
                # (no per-search request ID in retrieval_log, so count rows as proxy)
                row = conn.execute(
                    f"SELECT COUNT(*) as total FROM retrieval_log WHERE memory_id IN ({placeholders}) {period_filter}",
                    mem_params,
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT COALESCE(SUM(count), 0) as total FROM api_events WHERE operation IN ('search', 'search_batch') {period_filter}"
                ).fetchone()
            total_searches = row["total"]

            # Rank distribution (only where rank > 0, i.e. tracked)
            rows = conn.execute(
                f"SELECT rank, COUNT(*) as cnt FROM retrieval_log WHERE rank > 0 {period_filter}{mem_filter} GROUP BY rank",
                mem_params,
            ).fetchall()
            top_3 = sum(r["cnt"] for r in rows if r["rank"] <= 3)
            rank_4_plus = sum(r["cnt"] for r in rows if r["rank"] > 3)

            # Feedback counts
            fb_rows = conn.execute(
                f"SELECT signal, COUNT(*) as cnt FROM search_feedback WHERE 1=1 {period_filter}{mem_filter} GROUP BY signal",
                mem_params,
            ).fetchall()
            useful = 0
            not_useful = 0
            for r in fb_rows:
                if r["signal"] == "useful":
                    useful = r["cnt"]
                elif r["signal"] == "not_useful":
                    not_useful = r["cnt"]
            total_fb = useful + not_useful
            useful_ratio = round(useful / total_fb, 4) if total_fb > 0 else 0.0

            # Unique memories ever retrieved
            unretrieved_row = conn.execute(
                f"SELECT COUNT(DISTINCT memory_id) as cnt FROM retrieval_log WHERE 1=1 {period_filter}{mem_filter}",
                mem_params,
            ).fetchone()
            retrieved_unique = unretrieved_row["cnt"]

            return {
                "period": period,
                "total_searches": total_searches,
                "rank_distribution": {
                    "top_3": top_3,
                    "rank_4_plus": rank_4_plus,
                },
                "feedback": {
                    "useful": useful,
                    "not_useful": not_useful,
                    "useful_ratio": useful_ratio,
                },
                "unique_memories_retrieved": retrieved_unique,
            }
        finally:
            conn.close()

    def log_extraction_outcome(self, source: str = "", extracted: int = 0, stored: int = 0,
                               updated: int = 0, deleted: int = 0, noop: int = 0, conflict: int = 0) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO extraction_outcomes (source, extracted, stored, updated, deleted, noop, conflict) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source, extracted, stored, updated, deleted, noop, conflict),
            )
            conn.commit()
        except Exception:
            logger.debug("Failed to log extraction outcome", exc_info=True)

    def get_extraction_quality(self, period: str = "7d") -> Dict[str, Any]:
        period_filter = PERIOD_SQL.get(period, PERIOD_SQL["7d"])
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            # Aggregated totals
            row = conn.execute(
                f"SELECT COUNT(*) as cnt, "
                f"COALESCE(SUM(extracted), 0) as extracted, "
                f"COALESCE(SUM(stored), 0) as stored, "
                f"COALESCE(SUM(updated), 0) as updated, "
                f"COALESCE(SUM(deleted), 0) as deleted, "
                f"COALESCE(SUM(noop), 0) as noop, "
                f"COALESCE(SUM(conflict), 0) as conflict "
                f"FROM extraction_outcomes WHERE 1=1 {period_filter}"
            ).fetchone()
            extraction_count = row["cnt"]
            total_extracted = row["extracted"]
            noop_ratio = round(row["noop"] / total_extracted, 4) if total_extracted > 0 else 0.0

            # Per-source breakdown
            source_rows = conn.execute(
                f"SELECT source, "
                f"SUM(extracted) as extracted, SUM(stored) as stored, "
                f"SUM(updated) as updated, SUM(deleted) as deleted, "
                f"SUM(noop) as noop, SUM(conflict) as conflict, "
                f"COUNT(*) as cnt "
                f"FROM extraction_outcomes WHERE 1=1 {period_filter} GROUP BY source"
            ).fetchall()
            by_source = {}
            for sr in source_rows:
                src = sr["source"] or "(unknown)"
                by_source[src] = {
                    "extraction_count": sr["cnt"],
                    "extracted": sr["extracted"],
                    "stored": sr["stored"],
                    "updated": sr["updated"],
                    "deleted": sr["deleted"],
                    "noop": sr["noop"],
                    "conflict": sr["conflict"],
                }

            return {
                "period": period,
                "extraction_count": extraction_count,
                "totals": {
                    "extracted": row["extracted"],
                    "stored": row["stored"],
                    "updated": row["updated"],
                    "deleted": row["deleted"],
                    "noop": row["noop"],
                    "conflict": row["conflict"],
                    "noop_ratio": noop_ratio,
                },
                "by_source": by_source,
            }
        finally:
            conn.close()

    def get_usage(self, period: str = "7d") -> Dict[str, Any]:
        period_filter = PERIOD_SQL.get(period, PERIOD_SQL["7d"])
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            # Operations by source
            rows = conn.execute(
                f"SELECT operation, source, SUM(count) as total FROM api_events WHERE 1=1 {period_filter} GROUP BY operation, source"
            ).fetchall()
            operations: Dict[str, Any] = {}
            for row in rows:
                op = row["operation"]
                if op not in operations:
                    operations[op] = {"total": 0, "by_source": {}}
                operations[op]["total"] += row["total"]
                src = row["source"] or "(unknown)"
                operations[op]["by_source"][src] = operations[op]["by_source"].get(src, 0) + row["total"]

            # Extraction tokens
            rows = conn.execute(
                f"SELECT provider, model, stage, SUM(input_tokens) as inp, SUM(output_tokens) as out, COUNT(*) as calls "
                f"FROM extraction_tokens WHERE 1=1 {period_filter} GROUP BY provider, model, stage"
            ).fetchall()
            total_input = 0
            total_output = 0
            total_calls = 0
            by_model: Dict[str, Any] = {}
            for row in rows:
                total_input += row["inp"]
                total_output += row["out"]
                total_calls += row["calls"]
                model_key = row["model"]
                if model_key not in by_model:
                    by_model[model_key] = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
                by_model[model_key]["calls"] += row["calls"]
                by_model[model_key]["input_tokens"] += row["inp"]
                by_model[model_key]["output_tokens"] += row["out"]

            # Estimate cost
            estimated_cost = 0.0
            for model_key, data in by_model.items():
                pricing = MODEL_PRICING.get(model_key)
                if pricing is None:
                    logger.warning("Unknown model %r for pricing; using fallback $1/$4 per 1M tokens", model_key)
                    pricing = {"input": 1.0, "output": 4.0}
                estimated_cost += (data["input_tokens"] / 1_000_000) * pricing["input"]
                estimated_cost += (data["output_tokens"] / 1_000_000) * pricing["output"]

            return {
                "enabled": True,
                "period": period,
                "operations": operations,
                "extraction": {
                    "total_calls": total_calls,
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "by_model": by_model,
                    "estimated_cost_usd": round(estimated_cost, 4),
                },
            }
        finally:
            conn.close()

    def get_quality_summary(self, period: str = "7d") -> Dict[str, Any]:
        """Top-level efficacy metrics combining retrieval and extraction quality."""
        period_filter = PERIOD_SQL.get(period, PERIOD_SQL["7d"])
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            # --- Retrieval precision (from search feedback) ---
            row = conn.execute(
                f"SELECT COALESCE(SUM(count), 0) as total FROM api_events "
                f"WHERE operation IN ('search', 'search_batch') {period_filter}"
            ).fetchone()
            total_searches = row["total"]

            fb_rows = conn.execute(
                f"SELECT signal, COUNT(*) as cnt FROM search_feedback "
                f"WHERE 1=1 {period_filter} GROUP BY signal"
            ).fetchall()
            useful = 0
            not_useful = 0
            for r in fb_rows:
                if r["signal"] == "useful":
                    useful = r["cnt"]
                elif r["signal"] == "not_useful":
                    not_useful = r["cnt"]
            total_fb = useful + not_useful
            positive_rate = round(useful / total_fb, 4) if total_fb > 0 else 0.0

            # --- Extraction accuracy (from extraction outcomes) ---
            ext_row = conn.execute(
                f"SELECT COUNT(*) as cnt, "
                f"COALESCE(SUM(extracted), 0) as extracted, "
                f"COALESCE(SUM(stored), 0) as stored, "
                f"COALESCE(SUM(updated), 0) as updated, "
                f"COALESCE(SUM(deleted), 0) as deleted, "
                f"COALESCE(SUM(noop), 0) as noop, "
                f"COALESCE(SUM(conflict), 0) as conflict "
                f"FROM extraction_outcomes WHERE 1=1 {period_filter}"
            ).fetchone()
            total_extractions = ext_row["cnt"]
            total_extracted = ext_row["extracted"]
            add_rate = round(ext_row["stored"] / total_extracted, 4) if total_extracted > 0 else 0.0
            update_rate = round(ext_row["updated"] / total_extracted, 4) if total_extracted > 0 else 0.0
            noop_rate = round(ext_row["noop"] / total_extracted, 4) if total_extracted > 0 else 0.0
            delete_rate = round(ext_row["deleted"] / total_extracted, 4) if total_extracted > 0 else 0.0
            conflict_rate = round(ext_row["conflict"] / total_extracted, 4) if total_extracted > 0 else 0.0

            return {
                "retrieval_precision": {
                    "positive_feedback_rate": positive_rate,
                    "total_searches": total_searches,
                    "searches_with_feedback": total_fb,
                },
                "extraction_accuracy": {
                    "total_extractions": total_extractions,
                    "add_rate": add_rate,
                    "update_rate": update_rate,
                    "noop_rate": noop_rate,
                    "delete_rate": delete_rate,
                    "conflict_rate": conflict_rate,
                },
                "period": period,
            }
        finally:
            conn.close()

    def get_failures(self, failure_type: str = "retrieval", limit: int = 10) -> Dict[str, Any]:
        """Return recent low-quality results for debugging.

        Args:
            failure_type: 'retrieval' for negative search feedback,
                          'extraction' for high-noop extraction batches.
            limit: Maximum number of failures to return.
        """
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            failures: list[Dict[str, Any]] = []

            if failure_type == "retrieval":
                rows = conn.execute(
                    "SELECT sf.ts, sf.memory_id, sf.query, sf.signal, sf.search_id "
                    "FROM search_feedback sf "
                    "WHERE sf.signal = 'not_useful' "
                    "ORDER BY sf.ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                for r in rows:
                    failures.append({
                        "type": "retrieval",
                        "query": r["query"],
                        "timestamp": r["ts"],
                        "feedback": "negative",
                        "memory_id": r["memory_id"],
                        "search_id": r["search_id"],
                    })

            elif failure_type == "extraction":
                rows = conn.execute(
                    "SELECT ts, source, extracted, stored, updated, deleted, noop, conflict "
                    "FROM extraction_outcomes "
                    "WHERE extracted > 0 AND noop > 0 "
                    "ORDER BY CAST(noop AS REAL) / extracted DESC, ts DESC "
                    "LIMIT ?",
                    (limit,),
                ).fetchall()
                for r in rows:
                    noop_ratio = round(r["noop"] / r["extracted"], 4) if r["extracted"] > 0 else 0.0
                    failures.append({
                        "type": "extraction",
                        "source": r["source"],
                        "timestamp": r["ts"],
                        "extracted": r["extracted"],
                        "stored": r["stored"],
                        "noop": r["noop"],
                        "noop_ratio": noop_ratio,
                        "conflict": r["conflict"],
                    })

            return {"failures": failures}
        finally:
            conn.close()

    def get_problem_queries(self, min_feedback: int = 2, min_negative_ratio: float = 0.5,
                            limit: int = 20, memory_ids: list | None = None) -> list[dict]:
        conn = self._connect()
        try:
            mem_filter = ""
            params: list = []
            if memory_ids is not None:
                placeholders = ",".join("?" * len(memory_ids))
                mem_filter = f"AND memory_id IN ({placeholders}) "
                params.extend(memory_ids)
            params.extend([min_feedback, min_negative_ratio, limit])
            rows = conn.execute(
                f"SELECT query, COUNT(*) as total, "
                f"SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as not_useful "
                f"FROM search_feedback WHERE query != '' {mem_filter}"
                f"GROUP BY query "
                f"HAVING COUNT(*) >= ? AND CAST(not_useful AS FLOAT) / COUNT(*) >= ? "
                f"ORDER BY not_useful DESC LIMIT ?",
                params,
            ).fetchall()
            return [{"query": r[0], "total": r[1], "not_useful": r[2],
                     "ratio": round(r[2] / r[1], 2) if r[1] > 0 else 0} for r in rows]
        finally:
            conn.close()

    def get_stale_memories(self, min_retrievals: int = 3, limit: int = 20,
                           memory_ids: list | None = None) -> list[dict]:
        conn = self._connect()
        try:
            mem_filter = ""
            params: list = []
            if memory_ids is not None:
                placeholders = ",".join("?" * len(memory_ids))
                mem_filter = f"WHERE memory_id IN ({placeholders}) "
                params.extend(memory_ids)
            params.extend([min_retrievals, limit])
            rows = conn.execute(
                f"SELECT r.memory_id, r.retrievals, "
                f"COALESCE(f.useful, 0) as useful, COALESCE(f.not_useful, 0) as not_useful "
                f"FROM (SELECT memory_id, COUNT(*) as retrievals FROM retrieval_log "
                f"      {mem_filter}GROUP BY memory_id) r "
                f"LEFT JOIN (SELECT memory_id, "
                f"  SUM(CASE WHEN signal='useful' THEN 1 ELSE 0 END) as useful, "
                f"  SUM(CASE WHEN signal='not_useful' THEN 1 ELSE 0 END) as not_useful "
                f"  FROM search_feedback GROUP BY memory_id) f ON r.memory_id = f.memory_id "
                f"WHERE r.retrievals >= ? AND COALESCE(f.useful, 0) = 0 "
                f"AND (COALESCE(f.useful, 0) + COALESCE(f.not_useful, 0)) > 0 "
                f"ORDER BY r.retrievals DESC LIMIT ?",
                params,
            ).fetchall()
            return [{"memory_id": r[0], "retrievals": r[1], "useful": r[2], "not_useful": r[3]} for r in rows]
        finally:
            conn.close()
