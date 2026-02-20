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
        """)
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
