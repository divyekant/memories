"""
Memories API Service
FastAPI wrapper for the Memories engine with auth, hybrid search,
CRUD operations, and structured logging.
"""

import asyncio
import hmac
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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from embedder_reloader import EmbedderAutoReloadController
from memory_engine import MemoryEngine
from runtime_memory import MemoryTrimmer
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
    """Check X-API-Key header if API_KEY is configured.

    Uses constant-time comparison and per-IP rate limiting on failures.
    """
    if not API_KEY:
        return  # No auth configured
    path = request.url.path
    if path in {"/health", "/health/ready", "/ui"} or path.startswith("/ui/"):
        return  # Allow unauthenticated health checks + UI shell/static files

    # Rate limit failed auth attempts (10 per minute per IP)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < 60]
    if len(_auth_failures[ip]) >= 10:
        raise HTTPException(status_code=429, detail="Too many failed authentication attempts")

    key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(key.encode(), API_KEY.encode()):
        _auth_failures[ip].append(now)
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


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


def _run_fallback_extraction(messages: str, source: str, context: str) -> Dict[str, Any]:
    """Fallback add-only extraction path for disabled or runtime-failed providers."""
    facts = _fallback_extract_facts(messages)
    actions: List[Dict[str, Any]] = []
    stored_count = 0
    source_value = source or "extract/fallback"

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

    return {
        "actions": actions,
        "extracted_count": len(facts),
        "stored_count": stored_count,
        "updated_count": 0,
        "deleted_count": 0,
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
            )
            if EXTRACT_FALLBACK_ADD_ENABLED and _should_use_runtime_fallback(result):
                fallback_result = await run_in_threadpool(
                    _run_fallback_extraction,
                    request_data["messages"],
                    request_data["source"],
                    request_data["context"],
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
            # Log extraction token usage
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


class SupersedeRequest(BaseModel):
    old_id: int = Field(..., description="ID of memory to supersede")
    new_text: str = Field(..., description="Updated memory text")
    source: str = Field(default="", description="Source identifier")


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


@app.get("/ui", include_in_schema=False)
async def ui():
    """Serve the memory browser UI."""
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index_path)


@app.get("/stats")
async def stats():
    """Full index statistics"""
    return memory.stats()


@app.get("/metrics")
async def metrics():
    """Service-level metrics (latency, errors, queue depth, memory trend, process RSS)."""
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
async def usage(period: str = Query("7d", regex="^(today|7d|30d|all)$")):
    """Persistent usage analytics (opt-in via USAGE_TRACKING=true)."""
    return usage_tracker.get_usage(period)


@app.post("/maintenance/embedder/reload")
async def reload_embedder():
    """Reload in-process embedder runtime and release old inference objects."""
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


# -- Search -------------------------------------------------------------------

@app.post("/search")
async def search(request: SearchRequest):
    """Search for similar memories (vector-only or hybrid)"""
    logger.info("Search: q=%r k=%d hybrid=%s", request.query[:80], request.k, request.hybrid)
    try:
        if request.hybrid:
            results = memory.hybrid_search(
                query=request.query,
                k=request.k,
                threshold=request.threshold,
                vector_weight=request.vector_weight,
                source_prefix=request.source_prefix,
            )
        else:
            results = memory.search(
                query=request.query,
                k=request.k,
                threshold=request.threshold,
                source_prefix=request.source_prefix,
            )
        usage_tracker.log_api_event("search", request.source)
        for r in results:
            if "id" in r:
                usage_tracker.log_retrieval(
                    memory_id=r["id"],
                    query=request.query[:200],
                    source=request.source,
                )
        return {"query": request.query, "results": results, "count": len(results)}
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/search/batch")
async def search_batch(request: SearchBatchRequest):
    """Run multiple searches in one request."""
    try:
        outputs = []
        for item in request.queries:
            if item.hybrid:
                results = memory.hybrid_search(
                    query=item.query,
                    k=item.k,
                    threshold=item.threshold,
                    vector_weight=item.vector_weight,
                    source_prefix=item.source_prefix,
                )
            else:
                results = memory.search(
                    query=item.query,
                    k=item.k,
                    threshold=item.threshold,
                    source_prefix=item.source_prefix,
                )
            for r in results:
                if "id" in r:
                    usage_tracker.log_retrieval(
                        memory_id=r["id"],
                        query=item.query[:200],
                        source=item.source,
                    )
            outputs.append({"query": item.query, "results": results, "count": len(results)})
        usage_tracker.log_api_event("search_batch", count=len(request.queries))
        return {"results": outputs, "count": len(outputs)}
    except Exception as e:
        logger.exception("Batch search failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Memory CRUD --------------------------------------------------------------

@app.post("/memory/add")
async def add_memory(request: AddMemoryRequest):
    """Add a new memory"""
    logger.info("Add memory: source=%s len=%d", request.source, len(request.text))
    try:
        ids = memory.add_memories(
            texts=[request.text],
            sources=[request.source],
            metadata_list=[request.metadata] if request.metadata else None,
            deduplicate=request.deduplicate,
        )
        usage_tracker.log_api_event("add", request.source)
        return {
            "success": True,
            "id": ids[0] if ids else None,
            "message": "Memory added successfully" if ids else "Duplicate skipped",
        }
    except Exception as e:
        logger.exception("Add memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/add-batch")
async def add_batch(request: AddBatchRequest):
    """Add multiple memories at once"""
    logger.info("Add batch: count=%d", len(request.memories))
    try:
        texts = [m.text for m in request.memories]
        sources = [m.source for m in request.memories]
        # Preserve per-item metadata (None for rows without metadata)
        metadata_list = [m.metadata for m in request.memories]
        if not any(metadata_list):
            metadata_list = None

        ids = memory.add_memories(
            texts=texts,
            sources=sources,
            metadata_list=metadata_list,
            deduplicate=request.deduplicate,
        )
        usage_tracker.log_api_event("add", count=len(request.memories))
        return {
            "success": True,
            "ids": ids,
            "count": len(ids),
            "message": f"Added {len(ids)} memories",
        }
    except Exception as e:
        logger.exception("Add batch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int):
    """Delete a single memory by ID"""
    logger.info("Delete memory: id=%d", memory_id)
    try:
        result = memory.delete_memory(memory_id)
        usage_tracker.log_api_event("delete")
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Delete failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memory/{memory_id}")
async def get_memory(memory_id: int):
    """Fetch a single memory by ID."""
    try:
        return memory.get_memory(memory_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Get memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/get-batch")
async def get_memory_batch(request: MemoryGetBatchRequest):
    """Fetch multiple memories by IDs."""
    try:
        result = memory.get_memories(request.ids)
        return {**result, "count": len(result["memories"])}
    except Exception as e:
        logger.exception("Get batch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/delete-by-source")
async def delete_by_source(request: DeleteBySourceRequest):
    """Delete all memories matching a source pattern"""
    logger.info("Delete by source: pattern=%s", request.source_pattern)
    try:
        result = memory.delete_by_source(request.source_pattern)
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Delete by source failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/delete-batch")
async def delete_batch(request: DeleteBatchRequest):
    """Delete multiple memories in one operation."""
    logger.info("Delete batch: count=%d", len(request.ids))
    try:
        result = memory.delete_memories(request.ids)
        usage_tracker.log_api_event("delete", count=len(request.ids))
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Delete batch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/delete-by-prefix")
async def delete_by_prefix(request: DeleteByPrefixRequest):
    """Delete all memories whose source starts with a prefix."""
    logger.info("Delete by prefix: prefix=%s", request.source_prefix)
    try:
        result = memory.delete_by_prefix(request.source_prefix)
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Delete by prefix failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.patch("/memory/{memory_id}")
async def patch_memory(memory_id: int, request: PatchMemoryRequest):
    """Patch selected fields on an existing memory."""
    if request.text is None and request.source is None and not request.metadata_patch:
        raise HTTPException(status_code=400, detail="At least one field must be provided")
    try:
        result = memory.update_memory(
            memory_id=memory_id,
            text=request.text,
            source=request.source,
            metadata_patch=request.metadata_patch,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Patch memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/upsert")
async def upsert_memory(request: UpsertMemoryRequest):
    """Upsert a memory by stable key + source."""
    try:
        result = memory.upsert_memory(
            text=request.text,
            source=request.source,
            key=request.key,
            metadata=request.metadata,
        )
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Upsert memory failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/memory/upsert-batch")
async def upsert_memory_batch(request: UpsertBatchRequest):
    """Bulk upsert memories by stable keys."""
    try:
        entries = [
            {
                "text": item.text,
                "source": item.source,
                "key": item.key,
                "metadata": item.metadata,
            }
            for item in request.memories
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

@app.get("/memories")
async def list_memories(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    source: Optional[str] = Query(None, max_length=500),
):
    """List memories with pagination and optional source filter"""
    return memory.list_memories(offset=offset, limit=limit, source_filter=source)


@app.get("/folders")
async def list_folders():
    """List unique source-based folders with memory counts."""
    folder_counts: Dict[str, int] = {}
    for m in memory.metadata:
        source = m.get("source", "")
        folder = source.split("/")[0] if "/" in source else source if source else "(ungrouped)"
        folder_counts[folder] = folder_counts.get(folder, 0) + 1
    folders = [{"name": k, "count": v} for k, v in sorted(folder_counts.items())]
    return {"folders": folders, "total": len(memory.metadata)}


@app.post("/folders/rename")
async def rename_folder(request: RenameFolderRequest):
    """Batch-rename a folder by updating the source prefix on all matching memories."""
    old_prefix = request.old_name
    new_prefix = request.new_name

    # Collect matching IDs first to avoid mutation during iteration
    targets = []
    for i, m in enumerate(memory.metadata):
        source = m.get("source", "")
        if source == old_prefix or source.startswith(old_prefix + "/"):
            new_source = new_prefix + source[len(old_prefix):]
            targets.append((i, new_source))

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
async def build_index(request: BuildIndexRequest):
    """Rebuild index from workspace files using markdown-aware chunking"""
    logger.info("Rebuilding index...")
    try:
        if not request.sources:
            workspace = Path(WORKSPACE_DIR)
            sources = [
                str(workspace / "MEMORY.md"),
                *[str(p) for p in (workspace / "about-dk").glob("*.md")],
                *[str(p) for p in (workspace / "memory").glob("*.md")],
            ]
        else:
            workspace = Path(WORKSPACE_DIR).resolve()
            sources = []
            for s in request.sources:
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
async def deduplicate(request: DeduplicateRequest):
    """Find and optionally remove near-duplicate memories"""
    logger.info("Deduplicate: threshold=%.2f dry_run=%s", request.threshold, request.dry_run)
    try:
        result = memory.deduplicate(
            threshold=request.threshold, dry_run=request.dry_run
        )
        return result
    except Exception as e:
        logger.exception("Deduplication failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -- Backups ------------------------------------------------------------------

@app.get("/backups")
async def list_backups():
    """List available backups"""
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
async def create_backup(prefix: str = Query("manual", max_length=50)):
    """Create manual backup"""
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
async def restore_backup(request: RestoreRequest):
    """Restore index and metadata from a named backup"""
    logger.info("Restoring from backup: %s", request.backup_name)
    try:
        result = memory.restore_from_backup(request.backup_name)
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
async def sync_status():
    """Get cloud sync status"""
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
async def sync_upload():
    """Manually trigger backup upload to cloud"""
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
async def sync_download(backup_name: Optional[str] = None, confirm: bool = False):
    """Download a backup from cloud (requires confirmation)"""
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
async def sync_snapshots():
    """List remote snapshots in cloud storage"""
    if not memory.get_cloud_sync():
        raise HTTPException(status_code=400, detail="Cloud sync not configured")

    try:
        snapshots = memory.get_cloud_sync().list_remote_snapshots()
        return {"snapshots": snapshots, "count": len(snapshots)}
    except Exception as e:
        logger.exception("List remote snapshots failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/sync/restore/{backup_name}")
async def sync_restore(backup_name: str, confirm: bool = False):
    """Download and restore a backup from cloud in one step"""
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


# -- Extraction endpoints -----------------------------------------------------

@app.post("/memory/extract", status_code=202)
async def memory_extract(request: ExtractRequest):
    """Queue extraction and return immediately."""
    if extract_provider is None or run_extraction is None:
        if not EXTRACT_FALLBACK_ADD_ENABLED:
            raise HTTPException(status_code=501, detail="Extraction not configured. Set EXTRACT_PROVIDER env var.")

        job_id = uuid4().hex
        extract_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "source": request.source,
            "context": request.context,
            "message_length": len(request.messages),
            "created_at": _utc_now_iso(),
            "started_at": _utc_now_iso(),
            "mode": "fallback_add",
        }
        try:
            result = await run_in_threadpool(
                _run_fallback_extraction,
                request.messages,
                request.source,
                request.context,
            )
            extract_jobs[job_id]["status"] = "completed"
            extract_jobs[job_id]["completed_at"] = _utc_now_iso()
            extract_jobs[job_id]["result"] = result
            logger.info(
                "Extract fallback completed: job_id=%s source=%s context=%s extracted=%d stored=%d",
                job_id,
                request.source,
                request.context,
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

        return {
            "job_id": job_id,
            "status": extract_jobs[job_id]["status"],
            "queue_depth": extract_queue.qsize(),
            "result_url": f"/memory/extract/{job_id}",
        }

    _ensure_extract_workers_started()
    usage_tracker.log_api_event("extract", request.source)
    job_id = uuid4().hex
    extract_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "source": request.source,
        "context": request.context,
        "message_length": len(request.messages),
        "created_at": _utc_now_iso(),
    }
    try:
        extract_queue.put_nowait(
            {
                "job_id": job_id,
                "request": {
                    "messages": request.messages,
                    "source": request.source,
                    "context": request.context,
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
        request.source,
        request.context,
        extract_queue.qsize(),
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "queue_depth": extract_queue.qsize(),
        "result_url": f"/memory/extract/{job_id}",
    }


@app.get("/memory/extract/{job_id}")
async def memory_extract_job(job_id: str):
    """Get queued extraction job status/result."""
    job = extract_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Extraction job not found: {job_id}")
    return job


@app.post("/memory/supersede")
async def memory_supersede(request: SupersedeRequest):
    """Replace a memory with an updated version (audit trail preserved)."""
    logger.info("Supersede: old_id=%d, source=%s", request.old_id, request.source)
    try:
        result = memory.supersede(
            old_id=request.old_id,
            new_text=request.new_text,
            source=request.source,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Supersede failed")
        raise HTTPException(status_code=500, detail="Internal server error")


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
