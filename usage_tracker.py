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

    def get_search_quality(self, period: str = "7d") -> Dict[str, Any]:
        return {"enabled": False}

    def get_usage(self, period: str = "7d") -> Dict[str, Any]:
        return {"enabled": False}


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

    def get_search_quality(self, period: str = "7d") -> Dict[str, Any]:
        period_filter = PERIOD_SQL.get(period, PERIOD_SQL["7d"])
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            # Total searches
            row = conn.execute(
                f"SELECT COALESCE(SUM(count), 0) as total FROM api_events WHERE operation = 'search' {period_filter}"
            ).fetchone()
            total_searches = row["total"]

            # Rank distribution (only where rank > 0, i.e. tracked)
            rows = conn.execute(
                f"SELECT rank, COUNT(*) as cnt FROM retrieval_log WHERE rank > 0 {period_filter} GROUP BY rank"
            ).fetchall()
            top_3 = sum(r["cnt"] for r in rows if r["rank"] <= 3)
            rank_4_plus = sum(r["cnt"] for r in rows if r["rank"] > 3)

            # Feedback counts
            fb_rows = conn.execute(
                f"SELECT signal, COUNT(*) as cnt FROM search_feedback WHERE 1=1 {period_filter} GROUP BY signal"
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
                "SELECT COUNT(DISTINCT memory_id) as cnt FROM retrieval_log"
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
