"""
Memories API Service
FastAPI wrapper for the Memories engine with auth, hybrid search,
CRUD operations, and structured logging.
"""

import asyncio
import hmac
import json
import math
import os
import re
import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from auth_context import AuthContext
from embedder_reloader import EmbedderAutoReloadController
from key_store import KeyStore
from memory_engine import MemoryEngine
from runtime_memory import MemoryTrimmer
from audit_log import AuditLog, NullAuditLog
from usage_tracker import UsageTracker, NullTracker

# -- Logging ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("memories")

# Optional extraction support
try:
    from llm_provider import get_provider
    from llm_extract import run_extraction
    extract_provider = get_provider()
    if extract_provider:
        logger.info(
            "Extraction enabled: provider=%s, model=%s",
            extract_provider.provider_name, extract_provider.model,
        )
    else:
        logger.info("Extraction disabled (EXTRACT_PROVIDER not set)")
except Exception as e:
    logger.warning("Extraction setup failed: %s", e)
    extract_provider = None
    run_extraction = None

# -- Config -------------------------------------------------------------------

DATA_DIR = os.getenv("DATA_DIR", "/data")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
API_KEY = os.getenv("API_KEY", "")  # Empty = no auth (local-only)
key_store: KeyStore = None  # type: ignore  — initialized in lifespan
PORT = int(os.getenv("PORT", "8000"))
BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "webui"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return max(minimum, default)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, float(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %.2f", name, raw, default)
        return max(minimum, default)


MAX_EXTRACT_MESSAGE_CHARS = _env_int("MAX_EXTRACT_MESSAGE_CHARS", 120000)
EXTRACT_MAX_INFLIGHT = _env_int("EXTRACT_MAX_INFLIGHT", 2)
EXTRACT_QUEUE_MAX = _env_int("EXTRACT_QUEUE_MAX", EXTRACT_MAX_INFLIGHT * 20, minimum=1)
EXTRACT_JOB_RETENTION_SEC = _env_int("EXTRACT_JOB_RETENTION_SEC", 300, minimum=60)
EXTRACT_JOBS_MAX = _env_int("EXTRACT_JOBS_MAX", 200, minimum=10)
MEMORY_TRIM_ENABLED = _env_bool("MEMORY_TRIM_ENABLED", True)
MEMORY_TRIM_COOLDOWN_SEC = _env_float("MEMORY_TRIM_COOLDOWN_SEC", 15.0)
MEMORY_TRIM_PERIODIC_SEC = _env_float("MEMORY_TRIM_PERIODIC_SEC", 5.0, minimum=0.0)
EMBEDDER_AUTO_RELOAD_ENABLED = _env_bool("EMBEDDER_AUTO_RELOAD_ENABLED", False)
EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD = _env_int(
    "EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD",
    1200000,
    minimum=100000,
)
EMBEDDER_AUTO_RELOAD_CHECK_SEC = _env_float("EMBEDDER_AUTO_RELOAD_CHECK_SEC", 15.0, minimum=1.0)
EMBEDDER_AUTO_RELOAD_HIGH_STREAK = _env_int("EMBEDDER_AUTO_RELOAD_HIGH_STREAK", 3)
EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC = _env_float(
    "EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC",
    900.0,
    minimum=30.0,
)
EMBEDDER_AUTO_RELOAD_WINDOW_SEC = _env_float(
    "EMBEDDER_AUTO_RELOAD_WINDOW_SEC",
    3600.0,
    minimum=60.0,
)
EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW = _env_int("EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW", 2)
EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS = _env_int(
    "EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS",
    2,
    minimum=0,
)
EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH = _env_int(
    "EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH",
    0,
    minimum=0,
)
MAINTENANCE_ENABLED = _env_bool("MAINTENANCE_ENABLED", False)
METRICS_LATENCY_SAMPLES = _env_int("METRICS_LATENCY_SAMPLES", 200, minimum=20)
METRICS_TREND_SAMPLES = _env_int("METRICS_TREND_SAMPLES", 120, minimum=5)
EXTRACT_FALLBACK_ADD_ENABLED = _env_bool("EXTRACT_FALLBACK_ADD", False)
EXTRACT_FALLBACK_MAX_FACTS = _env_int("EXTRACT_FALLBACK_MAX_FACTS", 1, minimum=1)
EXTRACT_FALLBACK_MIN_FACT_CHARS = _env_int("EXTRACT_FALLBACK_MIN_FACT_CHARS", 24, minimum=5)
EXTRACT_FALLBACK_MAX_FACT_CHARS = _env_int("EXTRACT_FALLBACK_MAX_FACT_CHARS", 280, minimum=32)
EXTRACT_FALLBACK_NOVELTY_THRESHOLD = min(
    1.0,
    _env_float("EXTRACT_FALLBACK_NOVELTY_THRESHOLD", 0.88, minimum=0.0),
)

extract_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=EXTRACT_QUEUE_MAX)
extract_jobs: Dict[str, Dict[str, Any]] = {}
extract_jobs_lock: asyncio.Lock = asyncio.Lock()
extract_workers: List[asyncio.Task] = []
memory_trimmer = MemoryTrimmer(
    enabled=MEMORY_TRIM_ENABLED,
    cooldown_sec=MEMORY_TRIM_COOLDOWN_SEC,
)
embedder_auto_reloader = EmbedderAutoReloadController(
    rss_threshold_kb=EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD,
    required_high_streak=EMBEDDER_AUTO_RELOAD_HIGH_STREAK,
    min_interval_sec=EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC,
    window_sec=EMBEDDER_AUTO_RELOAD_WINDOW_SEC,
    max_per_window=EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW,
    max_active_requests=EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS,
    max_queue_depth=EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH,
)
usage_tracker: UsageTracker | NullTracker = NullTracker()  # replaced in lifespan if enabled
audit_log: AuditLog | NullAuditLog = NullAuditLog()  # replaced in lifespan if enabled
metrics_started_at = time.time()
metrics_lock = threading.Lock()
request_metrics: Dict[str, Dict[str, Any]] = {}
memory_trend: deque[Dict[str, Any]] = deque(maxlen=METRICS_TREND_SAMPLES)
active_http_requests = 0
embedder_reload_metrics: Dict[str, Any] = {
    "enabled": EMBEDDER_AUTO_RELOAD_ENABLED,
    "policy": {
        "rss_kb_threshold": EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD,
        "check_sec": EMBEDDER_AUTO_RELOAD_CHECK_SEC,
        "required_high_streak": EMBEDDER_AUTO_RELOAD_HIGH_STREAK,
        "min_interval_sec": EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC,
        "window_sec": EMBEDDER_AUTO_RELOAD_WINDOW_SEC,
        "max_per_window": EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW,
        "max_active_requests": EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS,
        "max_queue_depth": EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH,
    },
    "auto": {
        "checks_total": 0,
        "skipped_total": 0,
        "triggered_total": 0,
        "succeeded_total": 0,
        "failed_total": 0,
        "decision_reasons": {},
        "last_decision_reason": None,
        "last_rss_kb": 0,
        "last_triggered_at": None,
        "last_completed_at": None,
        "last_reload_duration_ms": 0.0,
        "last_gc_collected": None,
        "last_trim_reason": None,
        "last_error": None,
        "last_error_at": None,
    },
    "manual": {
        "requests_total": 0,
        "succeeded_total": 0,
        "failed_total": 0,
        "last_requested_at": None,
        "last_completed_at": None,
        "last_reload_duration_ms": 0.0,
        "last_gc_collected": None,
        "last_trim_reason": None,
        "last_error": None,
        "last_error_at": None,
    },
}


# -- Auth ---------------------------------------------------------------------

_auth_failures: Dict[str, list] = defaultdict(list)

async def verify_api_key(request: Request):
    """Check X-API-Key header against env key and managed key store.

    Uses constant-time comparison for the env key and per-IP rate limiting
    on failures.  Sets ``request.state.auth`` to an :class:`AuthContext`.
    """
    # No auth configured at all → unrestricted
    if not API_KEY and key_store is None:
        request.state.auth = AuthContext.unrestricted()
        return

    path = request.url.path
    if path in {"/health", "/health/ready", "/ui"} or path.startswith("/ui/"):
        request.state.auth = AuthContext.unrestricted()
        return  # Allow unauthenticated health checks + UI shell/static files

    # Rate limit failed auth attempts (10 per minute per IP)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < 60]
    if len(_auth_failures[ip]) >= 10:
        raise HTTPException(status_code=429, detail="Too many failed authentication attempts")

    raw_key = request.headers.get("X-API-Key", "")

    # No key supplied
    if not raw_key:
        if not API_KEY:
            request.state.auth = AuthContext.unrestricted()
            return
        _auth_failures[ip].append(now)
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Path 1: constant-time compare against env API_KEY
    if API_KEY and hmac.compare_digest(raw_key.encode(), API_KEY.encode()):
        request.state.auth = AuthContext(
            role="admin", prefixes=None, key_type="env",
        )
        return

    # Path 2: look up in managed key store
    if key_store is not None:
        record = await run_in_threadpool(key_store.lookup, raw_key)
        if record is not None:
            request.state.auth = AuthContext(
                role=record["role"],
                prefixes=record["prefixes"],
                key_type="managed",
                key_id=record["id"],
                key_name=record["name"],
            )
            return

    # No match
    _auth_failures[ip].append(now)
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _get_auth(request: Request) -> AuthContext:
    return getattr(request.state, "auth", AuthContext.unrestricted())


def _audit(request: Request, action: str, resource_id: str = "", source: str = "") -> None:
    """Log an audit entry from the current request context."""
    auth = _get_auth(request)
    ip = request.client.host if request.client else ""
    audit_log.log(
        action=action,
        key_id=auth.key_id or "env",
        key_name=auth.key_name or "",
        resource_id=resource_id,
        source_prefix=source,
        ip=ip,
    )


def _require_write(auth: AuthContext, source: str) -> None:
    if not auth.can_write(source):
        raise HTTPException(status_code=403, detail=f"Key does not have write access to source: {source}")


def _require_admin(auth: AuthContext) -> None:
    if not auth.can_manage_keys:
        raise HTTPException(status_code=403, detail="Admin key required")


def _count_accessible_memories(auth: AuthContext, source_prefix: Optional[str] = None) -> Optional[int]:
    """Count memories visible to this auth context.

    Uses Qdrant-level filtered count when available (O(1) via payload index),
    falling back to in-memory metadata scan for backward compatibility.
    """
    if auth.prefixes is None:
        return None

    # Fast path: Qdrant filtered count via payload index
    count_by_filter = getattr(memory, "count_by_filter", None)
    if count_by_filter is not None and hasattr(memory, "distinct_sources"):
        try:
            result = count_by_filter(
                source_prefix=source_prefix,
                allowed_prefixes=auth.prefixes,
            )
            if isinstance(result, int):
                return result
        except Exception:
            pass  # Fall through to O(n) scan

    # Fallback: O(n) in-memory scan (backward compat)
    metadata = getattr(memory, "metadata", None)
    if not isinstance(metadata, list):
        return None

    total = 0
    for record in metadata:
        if not isinstance(record, dict):
            continue
        source = str(record.get("source", ""))
        if source_prefix and not source.startswith(source_prefix):
            continue
        if auth.can_read(source):
            total += 1
    return total


def _can_access_extract_job(auth: AuthContext, job: Dict[str, Any]) -> bool:
    """Authorize visibility of extraction jobs."""
    if auth.can_manage_keys:
        return True

    source = str(job.get("source", ""))
    if source and auth.can_read(source):
        return True

    if auth.key_id and job.get("auth_key_id") == auth.key_id:
        return True

    return False


# -- App lifecycle ------------------------------------------------------------

memory: MemoryEngine = None  # type: ignore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_metrics_path(path: str) -> str:
    normalized = re.sub(r"/[0-9]+(?=/|$)", "/{id}", path)
    normalized = re.sub(r"/[0-9a-f]{8,}(?=/|$)", "/{id}", normalized, flags=re.IGNORECASE)
    return normalized


def _record_request_metric(route_key: str, latency_ms: float, status_code: int) -> None:
    with metrics_lock:
        bucket = request_metrics.setdefault(
            route_key,
            {
                "count": 0,
                "error_count": 0,
                "total_latency_ms": 0.0,
                "max_latency_ms": 0.0,
                "last_status_code": None,
                "latency_samples_ms": deque(maxlen=METRICS_LATENCY_SAMPLES),
            },
        )
        bucket["count"] += 1
        if status_code >= 400:
            bucket["error_count"] += 1
        bucket["total_latency_ms"] += latency_ms
        bucket["max_latency_ms"] = max(bucket["max_latency_ms"], latency_ms)
        bucket["last_status_code"] = status_code
        bucket["latency_samples_ms"].append(latency_ms)


def _latency_percentile(samples: List[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = max(0, math.ceil((pct / 100.0) * len(ordered)) - 1)
    return ordered[rank]


def _record_memory_sample(total_memories: int) -> None:
    sample = {"timestamp": _utc_now_iso(), "total_memories": total_memories}
    with metrics_lock:
        memory_trend.append(sample)


def _read_process_memory_kb() -> Dict[str, int]:
    """Read lightweight process memory stats from /proc/self/status."""
    stats = {
        "rss_kb": 0,
        "rss_anon_kb": 0,
        "rss_file_kb": 0,
        "rss_high_water_kb": 0,
        "vmsize_kb": 0,
    }
    field_map = {
        "VmRSS": "rss_kb",
        "RssAnon": "rss_anon_kb",
        "RssFile": "rss_file_kb",
        "VmHWM": "rss_high_water_kb",
        "VmSize": "vmsize_kb",
    }
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                if ":" not in line:
                    continue
                field, raw = line.split(":", 1)
                key = field_map.get(field.strip())
                if not key:
                    continue
                value_token = raw.strip().split(" ", 1)[0]
                try:
                    stats[key] = int(value_token)
                except ValueError:
                    continue
    except OSError:
        return stats
    return stats


def _build_metrics_snapshot() -> Dict[str, Any]:
    global active_http_requests
    with metrics_lock:
        routes_payload: Dict[str, Any] = {}
        total_count = 0
        total_errors = 0
        for route_key, bucket in request_metrics.items():
            samples = list(bucket["latency_samples_ms"])
            count = int(bucket["count"])
            errors = int(bucket["error_count"])
            total_count += count
            total_errors += errors
            routes_payload[route_key] = {
                "count": count,
                "error_count": errors,
                "error_rate_pct": round((errors / count) * 100.0, 2) if count else 0.0,
                "avg_latency_ms": round(bucket["total_latency_ms"] / count, 2) if count else 0.0,
                "p95_latency_ms": round(_latency_percentile(samples, 95.0), 2),
                "max_latency_ms": round(bucket["max_latency_ms"], 2),
                "last_status_code": bucket["last_status_code"],
            }

        trend_samples = list(memory_trend)
        active_requests_now = active_http_requests
        reload_metrics_payload = {
            "enabled": embedder_reload_metrics["enabled"],
            "policy": dict(embedder_reload_metrics["policy"]),
            "auto": {
                **embedder_reload_metrics["auto"],
                "decision_reasons": dict(embedder_reload_metrics["auto"]["decision_reasons"]),
            },
            "manual": dict(embedder_reload_metrics["manual"]),
        }

    trend_delta = 0
    if len(trend_samples) >= 2:
        trend_delta = trend_samples[-1]["total_memories"] - trend_samples[0]["total_memories"]

    return {
        "requests": {
            "total_count": total_count,
            "error_count": total_errors,
            "error_rate_pct": round((total_errors / total_count) * 100.0, 2) if total_count else 0.0,
            "active_http_requests": active_requests_now,
        },
        "routes": routes_payload,
        "memory_trend": {
            "window_size": METRICS_TREND_SAMPLES,
            "delta": trend_delta,
            "samples": trend_samples,
        },
        "embedder_reload": reload_metrics_payload,
    }


def _trim_finished_extract_jobs() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=EXTRACT_JOB_RETENTION_SEC)
    stale_job_ids: List[str] = []
    for job_id, job in extract_jobs.items():
        completed_at = job.get("completed_at")
        if not completed_at:
            continue
        try:
            completed_dt = datetime.fromisoformat(completed_at)
        except ValueError:
            continue
        if completed_dt < cutoff:
            stale_job_ids.append(job_id)

    for job_id in stale_job_ids:
        extract_jobs.pop(job_id, None)

    # Enforce hard cap: evict oldest finished jobs when dict exceeds limit
    if len(extract_jobs) > EXTRACT_JOBS_MAX:
        finished = [
            (jid, j) for jid, j in extract_jobs.items()
            if j.get("status") in {"completed", "failed"}
        ]
        finished.sort(key=lambda x: x[1].get("completed_at", ""))
        to_evict = len(extract_jobs) - EXTRACT_JOBS_MAX
        for jid, _ in finished[:to_evict]:
            extract_jobs.pop(jid, None)


_FALLBACK_DECISION_PATTERN = re.compile(
    r"\b("
    r"decide(?:d|s|ing)?|decision|prefer|standard|policy|"
    r"we\s+should|we\s+will|let'?s|going\s+with|"
    r"use\s+[a-z0-9_.-]+|remember\s+"
    r")\b",
    flags=re.IGNORECASE,
)


def _normalize_candidate_text(text: str) -> str:
    """Normalize one candidate line for conservative fallback extraction."""
    compact = re.sub(r"\s+", " ", text.strip())
    compact = re.sub(r"^(User|Assistant)\s*:\s*", "", compact, flags=re.IGNORECASE)
    return compact.strip()


def _fallback_extract_facts(messages: str) -> List[str]:
    """
    Extract a tiny set of high-confidence fact candidates from raw transcript text.

    This is intentionally conservative: if unsure, emit no facts.
    """
    candidates: List[str] = []
    seen = set()
    for raw_line in messages.splitlines():
        line = _normalize_candidate_text(raw_line)
        if not line:
            continue
        if line.endswith("?"):
            continue
        if len(line) < EXTRACT_FALLBACK_MIN_FACT_CHARS:
            continue
        if len(line) > EXTRACT_FALLBACK_MAX_FACT_CHARS:
            continue
        if len(line.split()) < 4:
            continue
        if not _FALLBACK_DECISION_PATTERN.search(line):
            continue

        lowered = line.lower()
        if lowered.startswith(("ok ", "okay ", "sure ", "thanks", "thank you")):
            continue

        if line not in seen:
            seen.add(line)
            candidates.append(line)
        if len(candidates) >= EXTRACT_FALLBACK_MAX_FACTS:
            break

    return candidates


def _run_fallback_extraction(
    messages: str,
    source: str,
    context: str,
    allowed_prefixes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fallback add-only extraction path for disabled or runtime-failed providers."""
    facts = _fallback_extract_facts(messages)
    actions: List[Dict[str, Any]] = []
    stored_count = 0

    # Scoped keys must provide an explicit source.
    if allowed_prefixes is not None and not source:
        return {
            "actions": [],
            "extracted_count": len(facts),
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "mode": "fallback_add",
            "error": "source_required",
        }

    source_value = source or "extract/fallback"
    if allowed_prefixes is not None and not AuthContext(
        role="read-write",
        prefixes=allowed_prefixes,
        key_type="managed",
    ).can_write(source_value):
        return {
            "actions": [],
            "extracted_count": len(facts),
            "stored_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "mode": "fallback_add",
            "error": "source_not_authorized",
        }

    for fact in facts:
        is_new, similar = memory.is_novel(fact, threshold=EXTRACT_FALLBACK_NOVELTY_THRESHOLD)
        if is_new:
            ids = memory.add_memories(
                texts=[fact],
                sources=[source_value],
                metadata_list=[{"extraction_mode": "fallback_add", "context": context}],
                deduplicate=False,
            )
            if ids:
                stored_count += 1
                actions.append(
                    {
                        "action": "add",
                        "text": fact,
                        "id": ids[0],
                        "mode": "fallback_add",
                    }
                )
        else:
            actions.append(
                {
                    "action": "noop",
                    "text": fact,
                    "mode": "fallback_add",
                    "matched_id": similar.get("id") if similar else None,
                    "similarity": similar.get("similarity") if similar else None,
                }
            )

    noop_count = sum(1 for a in actions if a.get("action") == "noop")
    return {
        "actions": actions,
        "extracted_count": len(facts),
        "stored_count": stored_count,
        "updated_count": 0,
        "deleted_count": 0,
        "noop_count": noop_count,
        "mode": "fallback_add",
    }


def _should_use_runtime_fallback(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return result.get("error") == "provider_runtime_failure"


def _merge_runtime_fallback_result(primary_result: Dict[str, Any], fallback_result: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(fallback_result)
    merged["fallback_triggered"] = True
    merged["fallback_reason"] = primary_result.get("error")
    if primary_result.get("error_stage"):
        merged["fallback_source_stage"] = primary_result.get("error_stage")
    if primary_result.get("error_message"):
        merged["primary_error"] = primary_result.get("error_message")
    return merged


def _ensure_extract_workers_started() -> None:
    if extract_provider is None or run_extraction is None:
        return
    alive_workers = [task for task in extract_workers if not task.done()]
    if alive_workers:
        if len(alive_workers) != len(extract_workers):
            extract_workers[:] = alive_workers
        return

    extract_workers.clear()
    for worker_id in range(EXTRACT_MAX_INFLIGHT):
        task = asyncio.create_task(
            _extract_worker(worker_id + 1),
            name=f"extract-worker-{worker_id + 1}",
        )
        extract_workers.append(task)
    logger.info("Extraction queue enabled with %d worker(s)", len(extract_workers))


async def _extract_worker(worker_id: int) -> None:
    logger.info("Extraction worker started: id=%d", worker_id)
    while True:
        try:
            job = await extract_queue.get()
        except asyncio.CancelledError:
            logger.info("Extraction worker stopped: id=%d", worker_id)
            break

        job_id = job["job_id"]
        request_data = job["request"]
        job_state = extract_jobs.get(job_id)
        if job_state:
            job_state["status"] = "running"
            job_state["started_at"] = _utc_now_iso()
            job_state["queue_depth"] = extract_queue.qsize()

        try:
            result = await run_in_threadpool(
                run_extraction,
                extract_provider,
                memory,
                request_data["messages"],
                request_data["source"],
                request_data["context"],
                request_data.get("allowed_prefixes"),
                request_data.get("debug", False),
            )
            if EXTRACT_FALLBACK_ADD_ENABLED and _should_use_runtime_fallback(result):
                fallback_result = await run_in_threadpool(
                    _run_fallback_extraction,
                    request_data["messages"],
                    request_data["source"],
                    request_data["context"],
                    request_data.get("allowed_prefixes"),
                )
                result = _merge_runtime_fallback_result(result, fallback_result)
                logger.info(
                    "Extract runtime fallback completed: job_id=%s source=%s context=%s extracted=%d stored=%d",
                    job_id,
                    request_data["source"],
                    request_data["context"],
                    result.get("extracted_count", 0),
                    result.get("stored_count", 0),
                )
            if job_state is not None:
                job_state["status"] = "completed"
                job_state["completed_at"] = _utc_now_iso()
                job_state["result"] = result
                event_bus.emit("extraction.completed", {
                    "job_id": job_state.get("job_id", ""),
                    "source": job_state.get("source", ""),
                    "stored_count": result.get("stored_count", 0),
                    "updated_count": result.get("updated_count", 0),
                    "conflict_count": result.get("conflict_count", 0),
                })
            # Log extraction outcome and token usage
            noop_count = result.get("noop_count")
            if noop_count is None:
                noop_count = sum(1 for a in result.get("actions", []) if a.get("action") == "noop")
            usage_tracker.log_extraction_outcome(
                source=request_data.get("source", ""),
                extracted=result.get("extracted_count", 0),
                stored=result.get("stored_count", 0),
                updated=result.get("updated_count", 0),
                deleted=result.get("deleted_count", 0),
                noop=noop_count,
                conflict=result.get("conflict_count", 0),
            )
            tokens = result.get("tokens", {})
            for stage_name in ("extract", "audn"):
                stage_tokens = tokens.get(stage_name, {})
                inp = stage_tokens.get("input", 0)
                out = stage_tokens.get("output", 0)
                if inp or out:
                    usage_tracker.log_extraction_tokens(
                        provider=extract_provider.provider_name,
                        model=extract_provider.model,
                        stage=stage_name,
                        input_tokens=inp,
                        output_tokens=out,
                        source=request_data.get("source", ""),
                    )
        except Exception as e:
            logger.exception("Extraction failed: job_id=%s", job_id)
            if job_state is not None:
                job_state["status"] = "failed"
                job_state["completed_at"] = _utc_now_iso()
                job_state["error"] = str(e)
        finally:
            trim_result = memory_trimmer.maybe_trim(reason=f"extract:{request_data['context']}")
            if trim_result.get("trimmed"):
                logger.debug(
                    "Post-extract memory trim complete: gc_collected=%s",
                    trim_result.get("gc_collected"),
                )
            extract_queue.task_done()
            _trim_finished_extract_jobs()


async def _periodic_job_cleanup() -> None:
    """Trim stale extract_jobs every 60 seconds."""
    while True:
        try:
            await asyncio.sleep(60)
            _trim_finished_extract_jobs()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("Periodic job cleanup error", exc_info=True)


async def _periodic_memory_trim() -> None:
    """Attempt periodic memory trim to reclaim allocator high-water marks."""
    while True:
        try:
            await asyncio.sleep(MEMORY_TRIM_PERIODIC_SEC)
            trim_result = memory_trimmer.maybe_trim(reason="periodic")
            if trim_result.get("trimmed"):
                logger.debug(
                    "Periodic memory trim complete: gc_collected=%s",
                    trim_result.get("gc_collected"),
                )
        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("Periodic memory trim error", exc_info=True)


async def _periodic_embedder_reload() -> None:
    """Auto-reload embedder when RSS stays high and service is relatively idle."""
    while True:
        try:
            await asyncio.sleep(EMBEDDER_AUTO_RELOAD_CHECK_SEC)
            process_stats = _read_process_memory_kb()
            rss_kb = int(process_stats.get("rss_kb", 0))
            with metrics_lock:
                active_now = active_http_requests
            decision = embedder_auto_reloader.evaluate(
                rss_kb=rss_kb,
                active_requests=active_now,
                queue_depth=extract_queue.qsize(),
            )
            reason = str(decision.get("reason", "unknown"))
            with metrics_lock:
                auto_metrics = embedder_reload_metrics["auto"]
                auto_metrics["checks_total"] += 1
                auto_metrics["last_decision_reason"] = reason
                auto_metrics["last_rss_kb"] = rss_kb
                reason_counts = auto_metrics["decision_reasons"]
                reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
            if not decision.get("trigger"):
                with metrics_lock:
                    embedder_reload_metrics["auto"]["skipped_total"] += 1
                continue

            logger.warning(
                "Auto embedder reload triggered: rss_kb=%s active=%s queue=%s",
                rss_kb,
                active_now,
                extract_queue.qsize(),
            )
            started_at = _utc_now_iso()
            started_monotonic = time.perf_counter()
            with metrics_lock:
                embedder_reload_metrics["auto"]["triggered_total"] += 1
                embedder_reload_metrics["auto"]["last_triggered_at"] = started_at
            try:
                result = await run_in_threadpool(memory.reload_embedder)
                trim_result = memory_trimmer.maybe_trim(reason="auto_embedder_reload")
                elapsed_ms = round((time.perf_counter() - started_monotonic) * 1000.0, 2)
                with metrics_lock:
                    auto_metrics = embedder_reload_metrics["auto"]
                    auto_metrics["succeeded_total"] += 1
                    auto_metrics["last_completed_at"] = _utc_now_iso()
                    auto_metrics["last_reload_duration_ms"] = elapsed_ms
                    auto_metrics["last_gc_collected"] = result.get("gc_collected")
                    auto_metrics["last_trim_reason"] = trim_result.get("reason")
                    auto_metrics["last_error"] = None
                    auto_metrics["last_error_at"] = None
                logger.info(
                    "Auto embedder reload complete: gc_collected=%s trim_reason=%s duration_ms=%s",
                    result.get("gc_collected"),
                    trim_result.get("reason"),
                    elapsed_ms,
                )
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - started_monotonic) * 1000.0, 2)
                with metrics_lock:
                    auto_metrics = embedder_reload_metrics["auto"]
                    auto_metrics["failed_total"] += 1
                    auto_metrics["last_completed_at"] = _utc_now_iso()
                    auto_metrics["last_reload_duration_ms"] = elapsed_ms
                    auto_metrics["last_error"] = str(exc)
                    auto_metrics["last_error_at"] = _utc_now_iso()
                logger.exception("Auto embedder reload failed")
        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("Periodic auto reload error", exc_info=True)


async def _maintenance_scheduler():
    """Run consolidation daily and pruning weekly."""
    _last_consolidation_date = None
    _last_prune_date = None
    while True:
        now = datetime.now(timezone.utc)
        today = now.date()
        # Consolidation: daily at 3 AM UTC (once per day)
        if now.hour == 3 and now.minute < 5 and _last_consolidation_date != today:
            _last_consolidation_date = today
            try:
                logger.info("Running scheduled consolidation")
                from consolidator import find_clusters, consolidate_cluster
                clusters = find_clusters(memory)
                for cluster in clusters:
                    consolidate_cluster(extract_provider, memory, cluster, dry_run=False)
            except Exception:
                logger.exception("Scheduled consolidation failed")
        # Pruning: Sunday at 4 AM UTC (once per week)
        if now.weekday() == 6 and now.hour == 4 and now.minute < 5 and _last_prune_date != today:
            _last_prune_date = today
            try:
                logger.info("Running scheduled pruning")
                from consolidator import find_prune_candidates
                all_mems = [m for m in memory.metadata if m]
                all_ids = [m["id"] for m in all_mems]
                unretrieved = usage_tracker.get_unretrieved_memory_ids(all_ids)
                candidates = find_prune_candidates(all_mems, unretrieved)
                for c in candidates:
                    memory.delete_memory(c["id"])
                logger.info("Pruned %d stale memories", len(candidates))
            except Exception:
                logger.exception("Scheduled pruning failed")
        await asyncio.sleep(300)  # Check every 5 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    global memory
    logger.info("Starting Memories service...")
    _embed_provider = os.getenv("EMBED_PROVIDER", "onnx").strip().lower()
    _embed_model = os.getenv("EMBED_MODEL", "").strip()
    if _embed_provider == "openai":
        _openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not _openai_key:
            raise RuntimeError(
                "EMBED_PROVIDER=openai requires OPENAI_API_KEY. "
                "Set OPENAI_API_KEY or use EMBED_PROVIDER=onnx for local embeddings."
            )
        logger.info(
            "Embedding: provider=openai, model=%s",
            _embed_model or "text-embedding-3-small",
        )
    elif _embed_provider == "onnx":
        logger.info("Embedding: provider=%s, model=%s", _embed_provider, _embed_model or "all-MiniLM-L6-v2")
    else:
        raise RuntimeError(
            f"Unknown EMBED_PROVIDER={_embed_provider!r}. Valid values: openai, onnx"
        )
    memory = MemoryEngine(data_dir=DATA_DIR)
    logger.info(
        "Loaded %d memories (%s model, %d dims)",
        len(memory.metadata),
        memory.config.get("model"),
        memory.dim,
    )
    global usage_tracker
    if _env_bool("USAGE_TRACKING", False):
        usage_tracker = UsageTracker(os.path.join(DATA_DIR, "usage.db"))
        logger.info("Usage tracking enabled")
    global audit_log
    if _env_bool("AUDIT_LOG", False):
        audit_log = AuditLog(os.path.join(DATA_DIR, "audit.db"))
        logger.info("Audit logging enabled")
    global key_store
    key_store = KeyStore(os.path.join(DATA_DIR, "keys.db"))
    logger.info("Key store initialized")
    _ensure_extract_workers_started()
    background_tasks: List[asyncio.Task] = [
        asyncio.create_task(_periodic_job_cleanup(), name="job-cleanup"),
    ]
    if MEMORY_TRIM_ENABLED and MEMORY_TRIM_PERIODIC_SEC > 0:
        background_tasks.append(
            asyncio.create_task(_periodic_memory_trim(), name="memory-trim")
        )
    if EMBEDDER_AUTO_RELOAD_ENABLED:
        background_tasks.append(
            asyncio.create_task(_periodic_embedder_reload(), name="embedder-auto-reload")
        )
    if MAINTENANCE_ENABLED and extract_provider:
        background_tasks.append(
            asyncio.create_task(_maintenance_scheduler(), name="maintenance-scheduler")
        )
    yield
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    if extract_workers:
        logger.info("Stopping extraction workers...")
        for task in extract_workers:
            task.cancel()
        await asyncio.gather(*extract_workers, return_exceptions=True)
        extract_workers.clear()
    logger.info("Shutting down — saving index...")
    memory.save()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Memories API",
    version="2.0.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_api_key)],
)

if UI_DIR.exists():
    app.mount("/ui/static", StaticFiles(directory=str(UI_DIR)), name="ui-static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8900",
        "http://127.0.0.1:8900",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    global active_http_requests
    start = time.perf_counter()
    route_key = f"{request.method} {_normalize_metrics_path(request.url.path)}"
    status_code = 500
    with metrics_lock:
        active_http_requests += 1
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        with metrics_lock:
            active_http_requests = max(0, active_http_requests - 1)
        latency_ms = (time.perf_counter() - start) * 1000.0
        _record_request_metric(route_key, latency_ms, status_code)


# -- Event system -------------------------------------------------------------

from event_bus import event_bus, EVENT_TYPES


class WebhookRequest(BaseModel):
    url: str = Field(..., description="Callback URL")
    events: Optional[List[str]] = Field(None, description="Event types to subscribe to (default: all)")


def _event_visible_to(auth: AuthContext, event_data: dict) -> bool:
    """Check if an event's source is readable by the caller."""
    if auth.prefixes is None:
        return True  # Admin sees all
    source = event_data.get("source", "")
    return auth.can_read(source) if source else True


@app.get("/events/stream")
async def events_stream(request: Request, event_type: Optional[str] = None):
    """Server-Sent Events stream for real-time memory lifecycle events."""
    auth = _get_auth(request)
    q = event_bus.subscribe()

    async def generate():
        try:
            yield "retry: 5000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    if event_type and event.type != event_type:
                        continue
                    if not _event_visible_to(auth, event.data):
                        continue
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/events/recent")
async def events_recent(request: Request, limit: int = 50):
    """Return recent event history."""
    auth = _get_auth(request)
    events = event_bus.recent_events(limit=limit)
    filtered = [e for e in events if _event_visible_to(auth, e.get("data", {}))]
    return {"events": filtered, "count": len(filtered)}


@app.post("/webhooks")
async def create_webhook(request_body: WebhookRequest, request: Request):
    """Register a webhook callback URL for memory events."""
    auth = _get_auth(request)
    _require_admin(auth)
    wh = event_bus.register_webhook(url=request_body.url, events=request_body.events)
    return wh


@app.get("/webhooks")
async def list_webhooks(request: Request):
    """List registered webhooks."""
    auth = _get_auth(request)
    _require_admin(auth)
    return {"webhooks": event_bus.list_webhooks()}


@app.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str, request: Request):
    """Delete a registered webhook."""
    auth = _get_auth(request)
    _require_admin(auth)
    deleted = event_bus.delete_webhook(webhook_id)
    return {"deleted": deleted}


# -- Request / Response models ------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10000)
    k: int = Field(5, ge=1, le=100)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    hybrid: bool = Field(False, description="Use hybrid BM25+vector search")
    vector_weight: float = Field(0.7, ge=0.0, le=1.0)
    source_prefix: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional source path prefix filter",
    )
    recency_weight: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Recency boost weight (0.0=off, 1.0=heavily favor recent). Only applies to hybrid search.",
    )
    recency_half_life_days: float = Field(
        30.0,
        gt=0.0,
        le=365.0,
        description="Half-life in days for recency decay (default 30).",
    )
    source: str = Field("", max_length=500, description="Caller source for usage tracking")


class SearchBatchRequest(BaseModel):
    queries: List[SearchRequest] = Field(..., min_length=1, max_length=200)


class AddMemoryRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    source: str = Field(..., min_length=1, max_length=500)
    metadata: Optional[dict] = None
    deduplicate: bool = False


class AddBatchRequest(BaseModel):
    memories: List[AddMemoryRequest] = Field(..., min_length=1, max_length=500)
    deduplicate: bool = False


class IsNovelRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    threshold: float = Field(0.88, ge=0.0, le=1.0)


class BuildIndexRequest(BaseModel):
    sources: Optional[List[str]] = None


class RestoreRequest(BaseModel):
    backup_name: str = Field(..., min_length=1, max_length=200)


class DeduplicateRequest(BaseModel):
    threshold: float = Field(0.90, ge=0.5, le=1.0)
    dry_run: bool = True


class DeleteBySourceRequest(BaseModel):
    source_pattern: str = Field(..., min_length=1, max_length=500)


class DeleteBatchRequest(BaseModel):
    ids: List[int] = Field(..., min_length=1, max_length=1000)


class MemoryGetBatchRequest(BaseModel):
    ids: List[int] = Field(..., min_length=1, max_length=1000)


class UpsertMemoryRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    source: str = Field(..., min_length=1, max_length=500)
    key: str = Field(..., min_length=1, max_length=500)
    metadata: Optional[dict] = None


class UpsertBatchRequest(BaseModel):
    memories: List[UpsertMemoryRequest] = Field(..., min_length=1, max_length=1000)


class DeleteByPrefixRequest(BaseModel):
    source_prefix: str = Field(..., min_length=1, max_length=500)


class RenameFolderRequest(BaseModel):
    old_name: str = Field(..., min_length=1, max_length=500)
    new_name: str = Field(..., min_length=1, max_length=500)


class PatchMemoryRequest(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=50000)
    source: Optional[str] = Field(None, min_length=1, max_length=500)
    metadata_patch: Optional[dict] = None


class ExtractRequest(BaseModel):
    messages: str = Field(
        ...,
        min_length=1,
        max_length=MAX_EXTRACT_MESSAGE_CHARS,
        description="Conversation text to extract facts from",
    )
    source: str = Field(default="", description="Source identifier (e.g., 'claude-code/my-project')")
    context: str = Field(default="stop", description="Extraction context: stop, pre_compact, session_end")
    debug: bool = Field(default=False, description="When True, return detailed extraction trace")


class SupersedeRequest(BaseModel):
    old_id: int = Field(..., description="ID of memory to supersede")
    new_text: str = Field(..., description="Updated memory text")
    source: str = Field(default="", description="Source identifier")


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    role: str = Field(..., pattern="^(read-only|read-write|admin)$")
    prefixes: List[str] = Field(default_factory=list)


class UpdateKeyRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    role: Optional[str] = Field(None, pattern="^(read-only|read-write|admin)$")
    prefixes: Optional[List[str]] = None


# -- Endpoints ----------------------------------------------------------------

@app.get("/health")
async def health(request: Request):
    """Lightweight health check (no filesystem I/O).

    Unauthenticated callers get minimal response; authenticated callers get full stats.
    """
    base = {"status": "ok", "service": "memories", "version": "2.0.0"}
    # Only include detailed stats for authenticated callers
    if not API_KEY or hmac.compare_digest(
        request.headers.get("X-API-Key", "").encode(), API_KEY.encode()
    ):
        stats = memory.stats_light()
        return {**base, **stats}
    return base


@app.get("/health/ready")
async def health_ready():
    """Readiness check for orchestrators/cutover automation."""
    status = memory.is_ready()
    if not status.get("ready", False):
        raise HTTPException(status_code=503, detail=status)
    return status


@app.get("/api/keys/me")
async def get_my_key(request: Request):
    """Returns the caller's role, type, and allowed prefixes."""
    auth = _get_auth(request)
    return auth.to_me_response()


@app.post("/api/keys")
async def create_key(request_body: CreateKeyRequest, request: Request):
    """Create a new API key. Admin only. Returns raw key once."""
    auth = _get_auth(request)
    _require_admin(auth)
    if request_body.role != "admin" and not request_body.prefixes:
        raise HTTPException(status_code=400, detail="Non-admin keys must have at least one prefix")
    result = key_store.create_key(
        name=request_body.name,
        role=request_body.role,
        prefixes=request_body.prefixes,
    )
    return result


@app.get("/api/keys")
async def list_keys(request: Request):
    """List all API keys (masked). Admin only."""
    auth = _get_auth(request)
    _require_admin(auth)
    keys = key_store.list_keys()
    return {"keys": keys, "count": len(keys)}


@app.patch("/api/keys/{key_id}")
async def update_key(key_id: str, request_body: UpdateKeyRequest, request: Request):
    """Update key name, role, or prefixes. Admin only."""
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        key_store.update_key(
            key_id,
            name=request_body.name,
            role=request_body.role,
            prefixes=request_body.prefixes,
        )
        return {"success": True, "id": key_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/keys/{key_id}")
async def revoke_key(key_id: str, request: Request):
    """Revoke an API key (soft-delete). Admin only."""
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        key_store.revoke(key_id)
        return {"id": key_id, "revoked": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/ui", include_in_schema=False)
async def ui():
    """Serve the memory browser UI."""
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index_path)


@app.get("/stats")
async def stats(request: Request):
    """Full index statistics"""
    auth = _get_auth(request)
    _require_admin(auth)
    return memory.stats()


@app.get("/metrics")
async def metrics(request: Request):
    """Service-level metrics (latency, errors, queue depth, memory trend, process RSS)."""
    auth = _get_auth(request)
    _require_admin(auth)
    light = memory.stats_light()
    current_total = int(light.get("total_memories", 0))
    _record_memory_sample(current_total)

    snapshot = _build_metrics_snapshot()
    return {
        "uptime_sec": int(time.time() - metrics_started_at),
        "extract": {
            "queue_depth": extract_queue.qsize(),
            "queue_max": EXTRACT_QUEUE_MAX,
            "queue_remaining": max(0, EXTRACT_QUEUE_MAX - extract_queue.qsize()),
            "workers": EXTRACT_MAX_INFLIGHT,
            "jobs_tracked": len(extract_jobs),
        },
        "memory": {
            "current_total": current_total,
            "trend": snapshot["memory_trend"],
            "process": _read_process_memory_kb(),
        },
        "embedder_reload": snapshot["embedder_reload"],
        "requests": snapshot["requests"],
        "routes": snapshot["routes"],
    }


@app.get("/usage")
async def usage(
    request: Request,
    period: str = Query("7d", pattern="^(today|7d|30d|all)$"),
):
    """Persistent usage analytics (opt-in via USAGE_TRACKING=true)."""
    auth = _get_auth(request)
    _require_admin(auth)
    return usage_tracker.get_usage(period)


class SearchFeedbackRequest(BaseModel):
    memory_id: int = Field(..., description="Memory ID the feedback applies to")
    query: str = Field("", description="The search query that produced this result")
    signal: str = Field(..., pattern="^(useful|not_useful)$", description="Relevance signal")
    search_id: str = Field("", description="Optional ID grouping results from the same search")


@app.post("/search/feedback")
async def search_feedback(request_body: SearchFeedbackRequest, request: Request):
    """Record explicit relevance feedback for a search result."""
    auth = _get_auth(request)
    # Verify scoped key can read the memory's source
    if auth.prefixes is not None:
        try:
            mem = memory.get_memory(request_body.memory_id)
            if not auth.can_read(mem.get("source", "")):
                raise HTTPException(status_code=403, detail="Memory is outside your allowed source scope")
        except ValueError:
            raise HTTPException(status_code=404, detail=f"Memory {request_body.memory_id} not found")
    usage_tracker.log_search_feedback(
        memory_id=request_body.memory_id,
        query=request_body.query,
        signal=request_body.signal,
        search_id=request_body.search_id,
    )
    return {"status": "recorded"}


@app.get("/metrics/search-quality")
async def search_quality_metrics(
    request: Request,
    period: str = Query("7d", pattern="^(today|7d|30d|all)$"),
    source_prefix: Optional[str] = Query(None, max_length=500, description="Filter metrics to memories matching this source prefix"),
):
    """Aggregated search quality metrics — rank distribution, feedback ratios, volume."""
    auth = _get_auth(request)
    # Resolve accessible memory IDs for scoped callers
    scoped_ids = None
    if auth.prefixes is not None or source_prefix:
        metadata = getattr(memory, "metadata", [])
        ids = []
        for m in metadata:
            src = m.get("source", "")
            if source_prefix and not src.startswith(source_prefix):
                continue
            if auth.prefixes is not None and not auth.can_read(src):
                continue
            ids.append(m["id"])
        scoped_ids = ids
    return usage_tracker.get_search_quality(period, memory_ids=scoped_ids)


@app.get("/metrics/extraction-quality")
async def extraction_quality_metrics(
    request: Request,
    period: str = Query("7d", pattern="^(today|7d|30d|all)$"),
):
    """Extraction outcome metrics — ADD/UPDATE/DELETE/NOOP ratios, per-source breakdown."""
    auth = _get_auth(request)
    _require_admin(auth)
    return usage_tracker.get_extraction_quality(period)


@app.get("/metrics/quality-summary")
async def quality_summary_metrics(
    request: Request,
    period: str = Query("7d", pattern="^(today|7d|30d|all)$"),
):
    """Top-level efficacy metrics — retrieval precision and extraction accuracy."""
    auth = _get_auth(request)
    _require_admin(auth)
    return usage_tracker.get_quality_summary(period)


@app.get("/metrics/failures")
async def quality_failures(
    request: Request,
    type: str = Query("retrieval", pattern="^(retrieval|extraction)$"),
    limit: int = Query(10, ge=1, le=100),
):
    """Recent low-quality results for debugging — negative feedback or high-noop extractions."""
    auth = _get_auth(request)
    _require_admin(auth)
    return usage_tracker.get_failures(failure_type=type, limit=limit)


@app.get("/audit")
async def get_audit_log(
    request: Request,
    action: Optional[str] = None,
    key_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Query the audit trail. Admin only."""
    auth = _get_auth(request)
    _require_admin(auth)
    entries = audit_log.query(action=action, key_id=key_id, limit=limit, offset=offset)
    return {"entries": entries, "count": len(entries), "total": audit_log.count(action=action, key_id=key_id)}


@app.post("/audit/purge")
async def purge_audit_log(
    request: Request,
    retention_days: int = Query(90, ge=1, le=365),
):
    """Purge audit entries older than retention period. Admin only."""
    auth = _get_auth(request)
    _require_admin(auth)
    purged = audit_log.purge(retention_days=retention_days)
    return {"purged": purged, "retention_days": retention_days}


@app.post("/maintenance/embedder/reload")
async def reload_embedder(request: Request):
    """Reload in-process embedder runtime and release old inference objects."""
    auth = _get_auth(request)
    _require_admin(auth)
    started_at = _utc_now_iso()
    started_monotonic = time.perf_counter()
    with metrics_lock:
        manual_metrics = embedder_reload_metrics["manual"]
        manual_metrics["requests_total"] += 1
        manual_metrics["last_requested_at"] = started_at
    try:
        result = await run_in_threadpool(memory.reload_embedder)
        trim_result = memory_trimmer.maybe_trim(reason="embedder_reload")
        elapsed_ms = round((time.perf_counter() - started_monotonic) * 1000.0, 2)
        with metrics_lock:
            manual_metrics = embedder_reload_metrics["manual"]
            manual_metrics["succeeded_total"] += 1
            manual_metrics["last_completed_at"] = _utc_now_iso()
            manual_metrics["last_reload_duration_ms"] = elapsed_ms
            manual_metrics["last_gc_collected"] = result.get("gc_collected")
            manual_metrics["last_trim_reason"] = trim_result.get("reason")
            manual_metrics["last_error"] = None
            manual_metrics["last_error_at"] = None
        return {"success": True, **result, "trim": trim_result}
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - started_monotonic) * 1000.0, 2)
        with metrics_lock:
            manual_metrics = embedder_reload_metrics["manual"]
            manual_metrics["failed_total"] += 1
            manual_metrics["last_completed_at"] = _utc_now_iso()
            manual_metrics["last_reload_duration_ms"] = elapsed_ms
            manual_metrics["last_error"] = str(e)
            manual_metrics["last_error_at"] = _utc_now_iso()
        logger.exception("Embedder reload failed")
        raise HTTPException(status_code=500, detail="Internal server error")


class ReembedRequest(BaseModel):
    model: Optional[str] = Field(None, description="New embedding model name (omit to re-embed with current model)")


@app.post("/maintenance/reembed")
async def reembed(request_body: ReembedRequest, request: Request):
    """Re-embed all memories, optionally with a different embedding model.

    Creates a backup before re-embedding. Admin only.
    """
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        result = await run_in_threadpool(
            memory.reembed,
            model_name=request_body.model,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Re-embed failed")
        raise HTTPException(status_code=500, detail="Internal server error")


class CompactRequest(BaseModel):
    threshold: float = Field(0.85, ge=0.5, le=1.0, description="Similarity threshold for clustering")


@app.post("/maintenance/compact")
async def compact_memories(request_body: CompactRequest, request: Request):
    """Discover clusters of similar memories (read-only / dry-run).

    This endpoint ONLY identifies clusters — it never modifies data.
    Use ``/maintenance/consolidate`` to merge clusters via LLM.

    Returns a list of clusters with their member memories.
    Admin only.
    """
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        clusters = await run_in_threadpool(
            memory.find_similar_clusters,
            threshold=request_body.threshold,
        )
        cluster_details = []
        for cluster_ids in clusters:
            mems = []
            for mid in cluster_ids:
                try:
                    m = memory.get_memory(mid)
                    mems.append({"id": mid, "text": m.get("text", "")[:200], "source": m.get("source", "")})
                except Exception:
                    pass
            if mems:
                cluster_details.append({"ids": cluster_ids, "size": len(cluster_ids), "memories": mems})

        return {
            "clusters": cluster_details,
            "cluster_count": len(cluster_details),
            "total_memories_in_clusters": sum(c["size"] for c in cluster_details),
        }
    except Exception as e:
        logger.exception("Compact failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/maintenance/consolidate")
async def consolidate(
    request: Request,
    dry_run: bool = Query(True),
    source_prefix: str = Query(""),
):
    """Merge redundant memory clusters using an LLM.

    Unlike ``/maintenance/compact`` (which only discovers clusters),
    this endpoint uses the configured LLM provider to merge each
    cluster into 1-2 concise consolidated memories.

    When ``dry_run=true`` (default), returns what would be merged
    without mutating any data. Set ``dry_run=false`` to execute.

    Requires an active LLM provider. Admin only.
    """
    auth = _get_auth(request)
    _require_admin(auth)
    if not extract_provider:
        raise HTTPException(503, "No LLM provider configured for consolidation")
    from consolidator import find_clusters, consolidate_cluster
    clusters = find_clusters(memory, source_prefix=source_prefix)
    results = []
    for cluster in clusters:
        r = await run_in_threadpool(
            consolidate_cluster, extract_provider, memory, cluster, dry_run=dry_run,
        )
        results.append(r)
    return {"clusters_found": len(clusters), "results": results, "dry_run": dry_run}


@app.post("/maintenance/prune")
async def prune(request: Request, dry_run: bool = Query(True)):
    """Prune stale unretrieved memories."""
    auth = _get_auth(request)
    _require_admin(auth)
    all_mems = [m for m in memory.metadata if m]
    all_ids = [m["id"] for m in all_mems]
    unretrieved = usage_tracker.get_unretrieved_memory_ids(all_ids)
    from consolidator import find_prune_candidates
    candidates = find_prune_candidates(all_mems, unretrieved)
    pruned = 0
    if not dry_run:
        for c in candidates:
            memory.delete_memory(c["id"])
            pruned += 1
    return {
        "candidates": len(candidates),
        "pruned": pruned,
        "dry_run": dry_run,
    }


# -- Search -------------------------------------------------------------------

@app.post("/search")
async def search(request_body: SearchRequest, request: Request):
    """Search for similar memories (vector-only or hybrid)"""
    auth = _get_auth(request)
    logger.info("Search: q=%r k=%d hybrid=%s", request_body.query[:80], request_body.k, request_body.hybrid)
    try:
        if request_body.hybrid:
            results = memory.hybrid_search(
                query=request_body.query,
                k=request_body.k,
                threshold=request_body.threshold,
                vector_weight=request_body.vector_weight,
                source_prefix=request_body.source_prefix,
                recency_weight=request_body.recency_weight,
                recency_half_life_days=request_body.recency_half_life_days,
            )
        else:
            results = memory.search(
                query=request_body.query,
                k=request_body.k,
                threshold=request_body.threshold,
                source_prefix=request_body.source_prefix,
            )
        results = auth.filter_results(results)
        result_count = len(results)
        usage_tracker.log_api_event("search", request_body.source)
        for rank, r in enumerate(results, 1):
            if "id" in r:
                usage_tracker.log_retrieval(
                    memory_id=r["id"],
                    query=request_body.query[:200],
                    source=request_body.source,
                    rank=rank,
                    result_count=result_count,
                )
        return {"query": request_body.query, "results": results, "count": result_count}
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/search/explain")
async def search_explain(request_body: SearchRequest, request: Request):
    """Search with detailed scoring breakdown (admin-only)."""
    auth = _get_auth(request)
    _require_admin(auth)
    logger.info("Search explain: q=%r k=%d", request_body.query[:80], request_body.k)
    try:
        explain_result = memory.hybrid_search_explain(
            query=request_body.query,
            k=request_body.k,
            threshold=request_body.threshold,
            vector_weight=request_body.vector_weight,
            source_prefix=request_body.source_prefix,
            recency_weight=request_body.recency_weight,
            recency_half_life_days=request_body.recency_half_life_days,
        )
        # Apply auth filtering to results and track how many were removed
        raw_results = explain_result["results"]
        filtered_results = auth.filter_results(raw_results)
        filtered_by_auth = len(raw_results) - len(filtered_results)
        explain_result["results"] = filtered_results
        explain_result["explain"]["filtered_by_auth"] = filtered_by_auth
        return explain_result
    except Exception as e:
        logger.exception("Search explain failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/search/batch")
async def search_batch(request_body: SearchBatchRequest, request: Request):
    """Run multiple searches in one request."""
    auth = _get_auth(request)
    try:
        outputs = []
        for item in request_body.queries:
            if item.hybrid:
                results = memory.hybrid_search(
                    query=item.query,
                    k=item.k,
                    threshold=item.threshold,
                    vector_weight=item.vector_weight,
                    source_prefix=item.source_prefix,
                    recency_weight=item.recency_weight,
                    recency_half_life_days=item.recency_half_life_days,
                )
            else:
                results = memory.search(
                    query=item.query,
                    k=item.k,
                    threshold=item.threshold,
                    source_prefix=item.source_prefix,
                )
            results = auth.filter_results(results)
            batch_result_count = len(results)
            for rank, r in enumerate(results, 1):
                if "id" in r:
                    usage_tracker.log_retrieval(
                        memory_id=r["id"],
                        query=item.query[:200],
                        source=item.source,
                        rank=rank,
                        result_count=batch_result_count,
                    )
            usage_tracker.log_api_event("search", item.source)
            outputs.append({"query": item.query, "results": results, "count": batch_result_count})
        return {"results": outputs, "count": len(outputs)}
    except Exception as e:
        logger.exception("Batch search failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Memory CRUD --------------------------------------------------------------

@app.post("/memory/add")
async def add_memory(request_body: AddMemoryRequest, request: Request):
    """Add a new memory"""
    auth = _get_auth(request)
    _require_write(auth, request_body.source)
    logger.info("Add memory: source=%s len=%d", request_body.source, len(request_body.text))
    try:
        ids = memory.add_memories(
            texts=[request_body.text],
            sources=[request_body.source],
            metadata_list=[request_body.metadata] if request_body.metadata else None,
            deduplicate=request_body.deduplicate,
        )
        usage_tracker.log_api_event("add", request_body.source)
        result_id = ids[0] if ids else None
        _audit(request, "add", resource_id=str(result_id or ""), source=request_body.source)
        return {
            "success": True,
            "id": result_id,
            "message": "Memory added successfully" if ids else "Duplicate skipped",
        }
    except Exception as e:
        logger.exception("Add memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/add-batch")
async def add_batch(request_body: AddBatchRequest, request: Request):
    """Add multiple memories at once"""
    auth = _get_auth(request)
    for m in request_body.memories:
        _require_write(auth, m.source)
    logger.info("Add batch: count=%d", len(request_body.memories))
    try:
        texts = [m.text for m in request_body.memories]
        sources = [m.source for m in request_body.memories]
        # Preserve per-item metadata (None for rows without metadata)
        metadata_list = [m.metadata for m in request_body.memories]
        if not any(metadata_list):
            metadata_list = None

        ids = memory.add_memories(
            texts=texts,
            sources=sources,
            metadata_list=metadata_list,
            deduplicate=request_body.deduplicate,
        )
        usage_tracker.log_api_event("add", count=len(request_body.memories))
        return {
            "success": True,
            "ids": ids,
            "count": len(ids),
            "message": f"Added {len(ids)} memories",
        }
    except Exception as e:
        logger.exception("Add batch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memory/conflicts")
async def list_conflicts(request: Request):
    """List memories flagged as conflicting with existing memories."""
    auth = _get_auth(request)
    metadata = getattr(memory, "metadata", [])
    conflicts = []
    for m in metadata:
        cw = m.get("conflicts_with")
        if cw is None:
            continue
        if auth.prefixes is not None and not auth.can_read(m.get("source", "")):
            continue
        entry = {**m}
        try:
            conflicting = memory.get_memory(cw)
            if auth.can_read(conflicting.get("source", "")):
                entry["conflicting_memory"] = conflicting
            else:
                entry["conflicting_memory"] = {"id": cw, "redacted": True}
        except Exception:
            entry["conflicting_memory"] = None
        conflicts.append(entry)
    return {"conflicts": conflicts, "count": len(conflicts)}


@app.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int, request: Request):
    """Delete a single memory by ID"""
    auth = _get_auth(request)
    logger.info("Delete memory: id=%d", memory_id)
    try:
        existing = memory.get_memory(memory_id)
        if auth.prefixes is not None:
            _require_write(auth, existing.get("source", ""))
        delete_source = existing.get("source", "")
        result = memory.delete_memory(memory_id)
        usage_tracker.log_api_event("delete")
        _audit(request, "delete", resource_id=str(memory_id), source=delete_source)
        return {"success": True, **result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Delete failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memory/{memory_id}")
async def get_memory(memory_id: int, request: Request):
    """Fetch a single memory by ID."""
    auth = _get_auth(request)
    try:
        result = memory.get_memory(memory_id)
        if not auth.can_read(result.get("source", "")):
            raise HTTPException(status_code=403, detail="Access denied to this memory's source")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Get memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/get-batch")
async def get_memory_batch(request_body: MemoryGetBatchRequest, request: Request):
    """Fetch multiple memories by IDs."""
    auth = _get_auth(request)
    try:
        result = memory.get_memories(request_body.ids)
        result["memories"] = auth.filter_results(result.get("memories", []))
        return {**result, "count": len(result["memories"])}
    except Exception as e:
        logger.exception("Get batch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/delete-by-source")
async def delete_by_source(request_body: DeleteBySourceRequest, request: Request):
    """Delete all memories matching a source pattern"""
    auth = _get_auth(request)
    _require_write(auth, request_body.source_pattern)
    logger.info("Delete by source: pattern=%s", request_body.source_pattern)
    try:
        # Backward-compatible behavior for admin/unrestricted callers.
        if auth.prefixes is None:
            result = memory.delete_by_source(request_body.source_pattern)
            return {"success": True, **result}

        # Scoped keys: resolve matching IDs and enforce source-level checks before deletion.
        matching_ids: List[int] = []
        metadata = getattr(memory, "metadata", [])
        if isinstance(metadata, list):
            for record in metadata:
                if not isinstance(record, dict):
                    continue
                source = str(record.get("source", ""))
                if request_body.source_pattern in source and auth.can_write(source):
                    rid = record.get("id")
                    if isinstance(rid, int):
                        matching_ids.append(rid)

        if not matching_ids:
            return {"success": True, "deleted_count": 0}

        result = memory.delete_memories(matching_ids)
        return {
            "success": True,
            "deleted_count": result.get("deleted_count", 0),
            "deleted_ids": result.get("deleted_ids", []),
            "missing_ids": result.get("missing_ids", []),
        }
    except Exception as e:
        logger.exception("Delete by source failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/memories")
async def delete_memories_by_prefix(
    request: Request,
    source: str = Query(..., min_length=1, max_length=500),
):
    """Bulk-delete all memories whose source starts with the given prefix."""
    auth = _get_auth(request)
    _require_write(auth, source)
    logger.info("Bulk delete by source prefix: %s", source)
    try:
        result = memory.delete_by_prefix(source)
        usage_tracker.log_api_event("delete", count=result["deleted_count"])
        return {"count": result["deleted_count"]}
    except Exception as e:
        logger.exception("Bulk delete by prefix failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/delete-batch")
async def delete_batch(request_body: DeleteBatchRequest, request: Request):
    """Delete multiple memories in one operation."""
    auth = _get_auth(request)
    if auth.prefixes is not None:
        for mid in request_body.ids:
            try:
                existing = memory.get_memory(mid)
                _require_write(auth, existing.get("source", ""))
            except ValueError:
                pass  # will fail on delete anyway
    logger.info("Delete batch: count=%d", len(request_body.ids))
    try:
        result = memory.delete_memories(request_body.ids)
        usage_tracker.log_api_event("delete", count=len(request_body.ids))
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Delete batch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/delete-by-prefix")
async def delete_by_prefix(request_body: DeleteByPrefixRequest, request: Request):
    """Delete all memories whose source starts with a prefix."""
    auth = _get_auth(request)
    _require_write(auth, request_body.source_prefix)
    logger.info("Delete by prefix: prefix=%s", request_body.source_prefix)
    try:
        result = memory.delete_by_prefix(request_body.source_prefix)
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Delete by prefix failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.patch("/memory/{memory_id}")
async def patch_memory(memory_id: int, request_body: PatchMemoryRequest, request: Request):
    """Patch selected fields on an existing memory."""
    auth = _get_auth(request)
    if request_body.text is None and request_body.source is None and not request_body.metadata_patch:
        raise HTTPException(status_code=400, detail="At least one field must be provided")
    try:
        if auth.prefixes is not None:
            existing = memory.get_memory(memory_id)
            _require_write(auth, existing.get("source", ""))
            if request_body.source is not None:
                _require_write(auth, request_body.source)
        result = memory.update_memory(
            memory_id=memory_id,
            text=request_body.text,
            source=request_body.source,
            metadata_patch=request_body.metadata_patch,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Patch memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/upsert")
async def upsert_memory(request_body: UpsertMemoryRequest, request: Request):
    """Upsert a memory by stable key + source."""
    auth = _get_auth(request)
    _require_write(auth, request_body.source)
    try:
        result = memory.upsert_memory(
            text=request_body.text,
            source=request_body.source,
            key=request_body.key,
            metadata=request_body.metadata,
        )
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Upsert memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/upsert-batch")
async def upsert_memory_batch(request_body: UpsertBatchRequest, request: Request):
    """Bulk upsert memories by stable keys."""
    auth = _get_auth(request)
    for item in request_body.memories:
        _require_write(auth, item.source)
    try:
        entries = [
            {
                "text": item.text,
                "source": item.source,
                "key": item.key,
                "metadata": item.metadata,
            }
            for item in request_body.memories
        ]
        result = memory.upsert_memories(entries)
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Upsert batch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/is-novel")
async def is_novel(request: IsNovelRequest):
    """Check if text is novel (not too similar to existing)"""
    try:
        is_new, similar = memory.is_novel(
            text=request.text, threshold=request.threshold
        )
        usage_tracker.log_api_event("is_novel")
        return {
            "is_novel": is_new,
            "threshold": request.threshold,
            "most_similar": similar,
        }
    except Exception as e:
        logger.exception("Is-novel check failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Browse / List ------------------------------------------------------------

@app.get("/memories/count")
async def count_memories(
    request: Request,
    source: Optional[str] = Query(None, max_length=500),
):
    """Count memories, optionally filtered by source prefix."""
    auth = _get_auth(request)
    if source and not auth.can_read(source):
        raise HTTPException(status_code=403, detail=f"Key does not have read access to source: {source}")

    if auth.prefixes is None:
        count = memory.count_memories(source_prefix=source)
    else:
        scoped_count = _count_accessible_memories(auth, source_prefix=source)
        count = scoped_count if scoped_count is not None else 0
    return {"count": count}


@app.get("/memories")
async def list_memories(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=5000),
    source: Optional[str] = Query(None, max_length=500),
):
    """List memories with pagination and optional source filter"""
    auth = _get_auth(request)
    if source and not auth.can_read(source):
        raise HTTPException(status_code=403, detail=f"Key does not have read access to source: {source}")

    result = memory.list_memories(offset=offset, limit=limit, source_filter=source)
    filtered_memories = auth.filter_results(result.get("memories", []))
    result["memories"] = filtered_memories
    if auth.prefixes is not None:
        scoped_total = _count_accessible_memories(auth, source_prefix=source)
        result["total"] = scoped_total if scoped_total is not None else len(filtered_memories)
    return result


@app.get("/folders")
async def list_folders(request: Request):
    """List unique source-based folders with memory counts."""
    auth = _get_auth(request)
    folder_counts: Dict[str, int] = {}
    for m in memory.metadata:
        source = m.get("source", "")
        if not auth.can_read(source):
            continue
        folder = source.split("/")[0] if "/" in source else source if source else "(ungrouped)"
        folder_counts[folder] = folder_counts.get(folder, 0) + 1
    folders = [{"name": k, "count": v} for k, v in sorted(folder_counts.items())]
    return {"folders": folders, "total": sum(folder_counts.values())}


@app.post("/folders/rename")
async def rename_folder(request_body: RenameFolderRequest, request: Request):
    """Batch-rename a folder by updating the source prefix on all matching memories."""
    auth = _get_auth(request)
    _require_admin(auth)
    old_prefix = request_body.old_name
    new_prefix = request_body.new_name

    # Collect matching IDs first to avoid mutation during iteration
    targets = []
    for m in memory.metadata:
        source = m.get("source", "")
        if source == old_prefix or source.startswith(old_prefix + "/"):
            new_source = new_prefix + source[len(old_prefix):]
            targets.append((m["id"], new_source))

    if not targets:
        raise HTTPException(status_code=404, detail=f"No memories found with folder prefix '{old_prefix}'")

    updated = 0
    errors = 0
    for memory_id, new_source in targets:
        try:
            memory.update_memory(memory_id=memory_id, source=new_source)
            updated += 1
        except ValueError as e:
            logger.warning("Folder rename skip id=%d: %s", memory_id, e)
            errors += 1
    return {"success": True, "updated": updated, "errors": errors, "old_name": old_prefix, "new_name": new_prefix}


# -- Index operations ---------------------------------------------------------

@app.post("/index/build")
async def build_index(request_body: BuildIndexRequest, request: Request):
    """Rebuild index from workspace files using markdown-aware chunking"""
    auth = _get_auth(request)
    _require_admin(auth)
    logger.info("Rebuilding index...")
    try:
        if not request_body.sources:
            workspace = Path(WORKSPACE_DIR)
            sources = [
                str(workspace / "MEMORY.md"),
                *[str(p) for p in (workspace / "about-dk").glob("*.md")],
                *[str(p) for p in (workspace / "memory").glob("*.md")],
            ]
        else:
            workspace = Path(WORKSPACE_DIR).resolve()
            sources = []
            for s in request_body.sources:
                full_path = (workspace / s).resolve()
                if full_path.is_relative_to(workspace):
                    sources.append(str(full_path))
                else:
                    logger.warning("Path traversal blocked in index build: %s", s)

        sources = [s for s in sources if Path(s).exists()]

        result = memory.rebuild_from_files(sources)
        logger.info("Index rebuilt: %d files, %d memories", result["files_processed"], result["memories_added"])
        return {"success": True, **result, "message": "Index rebuilt successfully"}
    except Exception as e:
        logger.exception("Index build failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Deduplication ------------------------------------------------------------

@app.post("/memory/deduplicate")
async def deduplicate(request_body: DeduplicateRequest, request: Request):
    """Find and optionally remove near-duplicate memories"""
    auth = _get_auth(request)
    _require_admin(auth)
    logger.info("Deduplicate: threshold=%.2f dry_run=%s", request_body.threshold, request_body.dry_run)
    try:
        result = memory.deduplicate(
            threshold=request_body.threshold, dry_run=request_body.dry_run
        )
        return result
    except Exception as e:
        logger.exception("Deduplication failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Backups ------------------------------------------------------------------

@app.get("/backups")
async def list_backups(request: Request):
    """List available backups"""
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        backups = sorted(
            memory.get_backup_dir().glob("*_*"), key=lambda p: p.name, reverse=True
        )
        return {
            "backups": [
                {"name": b.name, "created": b.stat().st_ctime} for b in backups
            ],
            "count": len(backups),
        }
    except Exception as e:
        logger.exception("List backups failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/backup")
async def create_backup(request: Request, prefix: str = Query("manual", max_length=50)):
    """Create manual backup"""
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        backup_path = memory.create_backup(prefix=prefix)
        return {
            "success": True,
            "backup_path": str(backup_path),
            "message": "Backup created successfully",
        }
    except Exception as e:
        logger.exception("Backup failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/restore")
async def restore_backup(request_body: RestoreRequest, request: Request):
    """Restore index and metadata from a named backup"""
    auth = _get_auth(request)
    _require_admin(auth)
    logger.info("Restoring from backup: %s", request_body.backup_name)
    try:
        result = memory.restore_from_backup(request_body.backup_name)
        return {"success": True, **result, "message": "Restored successfully"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Restore failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Cloud Sync ---------------------------------------------------------------

@app.get("/sync/status")
async def sync_status(request: Request):
    """Get cloud sync status"""
    auth = _get_auth(request)
    _require_admin(auth)
    if not memory.get_cloud_sync():
        return {"enabled": False, "message": "Cloud sync not configured"}

    try:
        remote_snapshots = memory.get_cloud_sync().list_remote_snapshots()
        latest_remote = remote_snapshots[0]["name"] if remote_snapshots else None

        local_backups = sorted(
            memory.get_backup_dir().glob("*_*"), key=lambda p: p.name, reverse=True
        )
        latest_local = local_backups[0].name if local_backups else None

        return {
            "enabled": True,
            "latest_remote": latest_remote,
            "latest_local": latest_local,
            "remote_count": len(remote_snapshots),
            "local_count": len(local_backups),
        }
    except Exception as e:
        logger.exception("Sync status check failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/sync/upload")
async def sync_upload(request: Request):
    """Manually trigger backup upload to cloud"""
    auth = _get_auth(request)
    _require_admin(auth)
    if not memory.get_cloud_sync():
        raise HTTPException(status_code=400, detail="Cloud sync not configured")

    logger.info("Manual cloud upload triggered")
    try:
        # Create a backup first
        backup_path = memory.create_backup(prefix="manual")
        backup_name = backup_path.name

        # Upload happens automatically in create_backup now, but we return the result
        return {
            "success": True,
            "backup_name": backup_name,
            "message": "Backup created and uploaded to cloud",
        }
    except Exception as e:
        logger.exception("Manual upload failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/sync/download")
async def sync_download(request: Request, backup_name: Optional[str] = None, confirm: bool = False):
    """Download a backup from cloud (requires confirmation)"""
    auth = _get_auth(request)
    _require_admin(auth)
    if not memory.get_cloud_sync():
        raise HTTPException(status_code=400, detail="Cloud sync not configured")

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Set confirm=true to proceed. WARNING: This will download from cloud."
        )

    try:
        # Get latest if not specified
        if not backup_name:
            backup_name = memory.get_cloud_sync().get_latest_snapshot()
            if not backup_name:
                raise HTTPException(status_code=404, detail="No backups found in cloud")

        logger.info("Downloading backup from cloud: %s", backup_name)
        result = memory.get_cloud_sync().download_backup(backup_name, memory.get_backup_dir())

        return {
            "success": True,
            **result,
            "message": f"Downloaded {backup_name} from cloud. Use /restore to apply it."
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found in cloud")
    except Exception as e:
        logger.exception("Cloud download failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/sync/snapshots")
async def sync_snapshots(request: Request):
    """List remote snapshots in cloud storage"""
    auth = _get_auth(request)
    _require_admin(auth)
    if not memory.get_cloud_sync():
        raise HTTPException(status_code=400, detail="Cloud sync not configured")

    try:
        snapshots = memory.get_cloud_sync().list_remote_snapshots()
        return {"snapshots": snapshots, "count": len(snapshots)}
    except Exception as e:
        logger.exception("List remote snapshots failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/sync/restore/{backup_name}")
async def sync_restore(backup_name: str, request: Request, confirm: bool = False):
    """Download and restore a backup from cloud in one step"""
    auth = _get_auth(request)
    _require_admin(auth)
    if not memory.get_cloud_sync():
        raise HTTPException(status_code=400, detail="Cloud sync not configured")

    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Set confirm=true to proceed. WARNING: This will overwrite local data."
        )

    try:
        logger.info("Downloading and restoring from cloud: %s", backup_name)

        # Download from cloud
        download_result = memory.get_cloud_sync().download_backup(backup_name, memory.get_backup_dir())

        # Restore locally
        restore_result = memory.restore_from_backup(backup_name)

        return {
            "success": True,
            "downloaded": download_result,
            "restored": restore_result,
            "message": f"Successfully restored {backup_name} from cloud"
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Cloud restore failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Export / Import ----------------------------------------------------------

@app.get("/export")
async def export_memories_endpoint(
    request: Request,
    source: Optional[str] = Query(None, max_length=500),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
):
    """Export memories as streaming NDJSON."""
    auth = _get_auth(request)

    def generate():
        lines = memory.export_memories(
            source_prefix=source, since=since, until=until,
        )
        for i, line in enumerate(lines):
            if i == 0:
                yield line + "\n"
                continue
            record = json.loads(line)
            if auth.can_read(record.get("source", "")):
                yield line + "\n"

    from starlette.responses import StreamingResponse
    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/import")
async def import_memories_endpoint(
    request: Request,
    strategy: str = Query("add"),
    source_remap: Optional[str] = Query(None, max_length=200),
    no_backup: bool = Query(False),
):
    """Import memories from NDJSON body."""
    auth = _get_auth(request)

    # Validate strategy
    valid_strategies = {"add", "smart", "smart+extract"}
    if strategy not in valid_strategies:
        raise HTTPException(status_code=400, detail=f"Invalid strategy. Must be one of: {valid_strategies}")

    body = await request.body()
    raw_lines = body.decode("utf-8").strip().split("\n")

    # Parse source_remap "old=new" format
    remap_tuple = None
    if source_remap and "=" in source_remap:
        parts = source_remap.split("=", 1)
        remap_tuple = (parts[0], parts[1])

    # Auth check: filter lines to only writable sources
    filtered_lines = [raw_lines[0]]  # keep header
    auth_errors = []
    for i, line in enumerate(raw_lines[1:], start=2):
        try:
            record = json.loads(line)
            source = record.get("source", "")
            if remap_tuple and source.startswith(remap_tuple[0]):
                source = remap_tuple[1] + source[len(remap_tuple[0]):]
            if not auth.can_write(source):
                auth_errors.append({"line": i, "error": "source prefix not authorized"})
                continue
            filtered_lines.append(line)
        except json.JSONDecodeError:
            filtered_lines.append(line)  # let engine handle bad JSON

    try:
        result = await run_in_threadpool(
            memory.import_memories,
            filtered_lines,
            strategy=strategy,
            source_remap=remap_tuple,
            create_backup=not no_backup,
        )
        result["errors"].extend(auth_errors)
        return result
    except Exception as exc:
        logger.error("Import failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# -- Extraction endpoints -----------------------------------------------------

@app.post("/memory/extract", status_code=202)
async def memory_extract(request_body: ExtractRequest, request: Request):
    """Queue extraction and return immediately."""
    auth = _get_auth(request)
    if request_body.source:
        _require_write(auth, request_body.source)
    else:
        if auth.role == "read-only":
            raise HTTPException(status_code=403, detail="Read-only keys cannot trigger extraction")
        # Scoped non-admin keys must provide an explicit, writable source.
        if auth.role != "admin" and auth.prefixes is not None:
            raise HTTPException(status_code=400, detail="source is required for scoped keys")

    if extract_provider is None or run_extraction is None:
        if not EXTRACT_FALLBACK_ADD_ENABLED:
            raise HTTPException(status_code=501, detail="Extraction not configured. Set EXTRACT_PROVIDER env var.")

        job_id = uuid4().hex
        extract_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "source": request_body.source,
            "context": request_body.context,
            "message_length": len(request_body.messages),
            "created_at": _utc_now_iso(),
            "started_at": _utc_now_iso(),
            "mode": "fallback_add",
            "auth_key_id": auth.key_id,
        }
        try:
            result = await run_in_threadpool(
                _run_fallback_extraction,
                request_body.messages,
                request_body.source,
                request_body.context,
                auth.prefixes,
            )
            extract_jobs[job_id]["status"] = "completed"
            extract_jobs[job_id]["completed_at"] = _utc_now_iso()
            extract_jobs[job_id]["result"] = result
            event_bus.emit("extraction.completed", {
                "job_id": job_id,
                "source": request_body.source,
                "stored_count": result.get("stored_count", 0),
                "updated_count": result.get("updated_count", 0),
                "conflict_count": result.get("conflict_count", 0),
            })
            effective_source = request_body.source or "extract/fallback"
            usage_tracker.log_extraction_outcome(
                source=effective_source,
                extracted=result.get("extracted_count", 0),
                stored=result.get("stored_count", 0),
                updated=result.get("updated_count", 0),
                deleted=result.get("deleted_count", 0),
                noop=result.get("noop_count", 0),
                conflict=result.get("conflict_count", 0),
            )
            logger.info(
                "Extract fallback completed: job_id=%s source=%s context=%s extracted=%d stored=%d",
                job_id,
                request_body.source,
                request_body.context,
                result.get("extracted_count", 0),
                result.get("stored_count", 0),
            )
        except Exception as e:
            logger.exception("Extract fallback failed: job_id=%s", job_id)
            extract_jobs[job_id]["status"] = "failed"
            extract_jobs[job_id]["completed_at"] = _utc_now_iso()
            extract_jobs[job_id]["error"] = str(e)
        finally:
            _trim_finished_extract_jobs()

        _audit(request, "extract", source=request_body.source or "extract/fallback")
        return {
            "job_id": job_id,
            "status": extract_jobs[job_id]["status"],
            "queue_depth": extract_queue.qsize(),
            "result_url": f"/memory/extract/{job_id}",
        }

    _ensure_extract_workers_started()
    usage_tracker.log_api_event("extract", request_body.source)
    _audit(request, "extract", source=request_body.source)
    job_id = uuid4().hex
    extract_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "source": request_body.source,
        "context": request_body.context,
        "message_length": len(request_body.messages),
        "created_at": _utc_now_iso(),
        "auth_key_id": auth.key_id,
    }
    try:
        extract_queue.put_nowait(
            {
                "job_id": job_id,
                "request": {
                    "messages": request_body.messages,
                    "source": request_body.source,
                    "context": request_body.context,
                    "allowed_prefixes": auth.prefixes,
                    "debug": request_body.debug,
                },
            }
        )
    except asyncio.QueueFull:
        # Remove the job we just registered since it won't be processed
        extract_jobs.pop(job_id, None)
        queue_depth = extract_queue.qsize()
        retry_after_sec = max(1, min(30, (queue_depth // max(1, EXTRACT_MAX_INFLIGHT)) + 1))
        logger.warning(
            "Extract queue full: depth=%d max=%d",
            queue_depth,
            EXTRACT_QUEUE_MAX,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "extract_queue_full",
                "message": "Extraction queue is full. Retry later.",
                "queue_depth": queue_depth,
                "queue_max": EXTRACT_QUEUE_MAX,
                "retry_after_sec": retry_after_sec,
            },
            headers={"Retry-After": str(retry_after_sec)},
        )
    _trim_finished_extract_jobs()
    logger.info(
        "Extract queued: job_id=%s source=%s context=%s queue_depth=%d",
        job_id,
        request_body.source,
        request_body.context,
        extract_queue.qsize(),
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "queue_depth": extract_queue.qsize(),
        "result_url": f"/memory/extract/{job_id}",
    }


@app.get("/memory/extract/{job_id}")
async def memory_extract_job(job_id: str, request: Request):
    """Get queued extraction job status/result."""
    auth = _get_auth(request)
    job = extract_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Extraction job not found: {job_id}")
    if not _can_access_extract_job(auth, job):
        raise HTTPException(status_code=403, detail="Access denied to extraction job")
    return job


@app.post("/memory/supersede")
async def memory_supersede(request_body: SupersedeRequest, request: Request):
    """Replace a memory with an updated version (audit trail preserved)."""
    auth = _get_auth(request)
    _require_write(auth, request_body.source)
    if auth.prefixes is not None:
        existing = memory.get_memory(request_body.old_id)
        _require_write(auth, existing.get("source", ""))
    logger.info("Supersede: old_id=%d, source=%s", request_body.old_id, request_body.source)
    try:
        result = memory.supersede(
            old_id=request_body.old_id,
            new_text=request_body.new_text,
            source=request_body.source,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Supersede failed")
        raise HTTPException(status_code=500, detail="Internal server error")


class AddLinkRequest(BaseModel):
    to_id: int = Field(..., description="Target memory ID")
    type: str = Field(..., description="Link type: supersedes, related_to, blocked_by, caused_by, reinforces")


@app.post("/memory/{memory_id}/link")
async def add_memory_link(memory_id: int, request_body: AddLinkRequest, request: Request):
    """Add a typed link from one memory to another."""
    auth = _get_auth(request)
    try:
        from_mem = memory.get_memory(memory_id)
        _require_write(auth, from_mem.get("source", ""))
        to_mem = memory.get_memory(request_body.to_id)
        _require_write(auth, to_mem.get("source", ""))
        result = memory.add_link(
            from_id=memory_id,
            to_id=request_body.to_id,
            link_type=request_body.type,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Add link failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memory/{memory_id}/links")
async def get_memory_links(
    memory_id: int,
    request: Request,
    type: Optional[str] = None,
    include_incoming: bool = False,
):
    """Get links for a memory (outgoing by default)."""
    auth = _get_auth(request)
    try:
        mem = memory.get_memory(memory_id)
        if not auth.can_read(mem.get("source", "")):
            raise HTTPException(status_code=403, detail="Not authorized to read this memory")
        links = memory.get_links(
            memory_id=memory_id,
            link_type=type,
            include_incoming=include_incoming,
        )
        # Filter links to only include memories the caller can read
        filtered = []
        for link in links:
            linked_id = link.get("to_id") if link["direction"] == "outgoing" else link["from_id"]
            try:
                linked_mem = memory.get_memory(linked_id)
                if auth.can_read(linked_mem.get("source", "")):
                    filtered.append(link)
            except Exception:
                pass
        return {"memory_id": memory_id, "links": filtered}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/memory/{memory_id}/link/{target_id}")
async def remove_memory_link(
    memory_id: int,
    target_id: int,
    request: Request,
    type: str = "related_to",
):
    """Remove a specific link between two memories."""
    auth = _get_auth(request)
    from_mem = memory.get_memory(memory_id)
    _require_write(auth, from_mem.get("source", ""))
    result = memory.remove_link(from_id=memory_id, to_id=target_id, link_type=type)
    return result


@app.get("/extract/status")
async def extract_status():
    """Check extraction provider health and configuration."""
    status_payload: Dict[str, Any] = {
        "queue_depth": extract_queue.qsize(),
        "queue_max": EXTRACT_QUEUE_MAX,
        "queue_remaining": max(0, EXTRACT_QUEUE_MAX - extract_queue.qsize()),
        "workers": EXTRACT_MAX_INFLIGHT,
        "jobs_tracked": len(extract_jobs),
        "fallback_add_enabled": EXTRACT_FALLBACK_ADD_ENABLED,
    }

    if extract_provider is None:
        return {"enabled": False, **status_payload}
    try:
        healthy = extract_provider.health_check()
        return {
            "enabled": True,
            "provider": extract_provider.provider_name,
            "model": extract_provider.model,
            "status": "healthy" if healthy else "unhealthy",
            **status_payload,
        }
    except Exception as e:
        return {
            "enabled": True,
            "provider": extract_provider.provider_name,
            "model": extract_provider.model,
            "status": f"error: {e}",
            **status_payload,
        }


# -- Main ---------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
