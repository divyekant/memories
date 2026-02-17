"""
FAISS Memory API Service
FastAPI wrapper for FAISS memory engine with auth, hybrid search,
CRUD operations, and structured logging.
"""

import asyncio
import os
import re
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from memory_engine import MemoryEngine
from runtime_memory import MemoryTrimmer

# -- Logging ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("faiss-memory")

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
EXTRACT_JOB_RETENTION_SEC = _env_int("EXTRACT_JOB_RETENTION_SEC", 3600, minimum=60)
MEMORY_TRIM_ENABLED = _env_bool("MEMORY_TRIM_ENABLED", True)
MEMORY_TRIM_COOLDOWN_SEC = _env_float("MEMORY_TRIM_COOLDOWN_SEC", 15.0)

extract_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
extract_jobs: Dict[str, Dict[str, Any]] = {}
extract_workers: List[asyncio.Task] = []
memory_trimmer = MemoryTrimmer(
    enabled=MEMORY_TRIM_ENABLED,
    cooldown_sec=MEMORY_TRIM_COOLDOWN_SEC,
)


# -- Auth ---------------------------------------------------------------------

async def verify_api_key(request: Request):
    """Check X-API-Key header if API_KEY is configured. Skips /health for Docker healthchecks."""
    if not API_KEY:
        return  # No auth configured
    if request.url.path == "/health":
        return  # Allow unauthenticated health checks
    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# -- App lifecycle ------------------------------------------------------------

memory: MemoryEngine = None  # type: ignore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            if job_state is not None:
                job_state["status"] = "completed"
                job_state["completed_at"] = _utc_now_iso()
                job_state["result"] = result
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global memory
    logger.info("Starting FAISS Memory service...")
    memory = MemoryEngine(data_dir=DATA_DIR)
    logger.info(
        "Loaded %d memories (%s model, %d dims)",
        memory.index.ntotal,
        memory.config.get("model"),
        memory.dim,
    )
    _ensure_extract_workers_started()
    yield
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
    title="FAISS Memory API",
    version="2.0.0",
    lifespan=lifespan,
    dependencies=[Depends(verify_api_key)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- Request / Response models ------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10000)
    k: int = Field(5, ge=1, le=100)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    hybrid: bool = Field(False, description="Use hybrid BM25+vector search")
    vector_weight: float = Field(0.7, ge=0.0, le=1.0)


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
async def health():
    """Lightweight health check (no filesystem I/O)"""
    stats = memory.stats_light()
    return {"status": "ok", "service": "faiss-memory", "version": "2.0.0", **stats}


@app.get("/stats")
async def stats():
    """Full index statistics"""
    return memory.stats()


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
            )
        else:
            results = memory.search(
                query=request.query,
                k=request.k,
                threshold=request.threshold,
            )
        return {"query": request.query, "results": results, "count": len(results)}
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(e))


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
        return {
            "success": True,
            "id": ids[0] if ids else None,
            "message": "Memory added successfully" if ids else "Duplicate skipped",
        }
    except Exception as e:
        logger.exception("Add memory failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/add-batch")
async def add_batch(request: AddBatchRequest):
    """Add multiple memories at once"""
    logger.info("Add batch: count=%d", len(request.memories))
    try:
        texts = [m.text for m in request.memories]
        sources = [m.source for m in request.memories]
        metadata_list = [m.metadata for m in request.memories if m.metadata]
        if len(metadata_list) != len(texts):
            metadata_list = None

        ids = memory.add_memories(
            texts=texts,
            sources=sources,
            metadata_list=metadata_list,
            deduplicate=request.deduplicate,
        )
        return {
            "success": True,
            "ids": ids,
            "count": len(ids),
            "message": f"Added {len(ids)} memories",
        }
    except Exception as e:
        logger.exception("Add batch failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int):
    """Delete a single memory by ID"""
    logger.info("Delete memory: id=%d", memory_id)
    try:
        result = memory.delete_memory(memory_id)
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Delete failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/delete-by-source")
async def delete_by_source(request: DeleteBySourceRequest):
    """Delete all memories matching a source pattern"""
    logger.info("Delete by source: pattern=%s", request.source_pattern)
    try:
        result = memory.delete_by_source(request.source_pattern)
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Delete by source failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/is-novel")
async def is_novel(request: IsNovelRequest):
    """Check if text is novel (not too similar to existing)"""
    try:
        is_new, similar = memory.is_novel(
            text=request.text, threshold=request.threshold
        )
        return {
            "is_novel": is_new,
            "threshold": request.threshold,
            "most_similar": similar,
        }
    except Exception as e:
        logger.exception("Is-novel check failed")
        raise HTTPException(status_code=500, detail=str(e))


# -- Browse / List ------------------------------------------------------------

@app.get("/memories")
async def list_memories(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    source: Optional[str] = Query(None, max_length=500),
):
    """List memories with pagination and optional source filter"""
    return memory.list_memories(offset=offset, limit=limit, source_filter=source)


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
            workspace = Path(WORKSPACE_DIR)
            sources = [str(workspace / s) for s in request.sources]

        sources = [s for s in sources if Path(s).exists()]

        result = memory.rebuild_from_files(sources)
        logger.info("Index rebuilt: %d files, %d memories", result["files_processed"], result["memories_added"])
        return {"success": True, **result, "message": "Index rebuilt successfully"}
    except Exception as e:
        logger.exception("Index build failed")
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/restore")
async def restore_backup(request: RestoreRequest):
    """Restore index and metadata from a named backup"""
    logger.info("Restoring from backup: %s", request.backup_name)
    try:
        result = memory.restore_from_backup(request.backup_name)
        return {"success": True, **result, "message": "Restored successfully"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Restore failed")
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
    except Exception as e:
        logger.exception("Cloud download failed")
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
    except Exception as e:
        logger.exception("Cloud restore failed")
        raise HTTPException(status_code=500, detail=str(e))


# -- Extraction endpoints -----------------------------------------------------

@app.post("/memory/extract", status_code=202)
async def memory_extract(request: ExtractRequest):
    """Queue extraction and return immediately."""
    if extract_provider is None or run_extraction is None:
        raise HTTPException(status_code=501, detail="Extraction not configured. Set EXTRACT_PROVIDER env var.")
    _ensure_extract_workers_started()
    job_id = uuid4().hex
    extract_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "source": request.source,
        "context": request.context,
        "message_length": len(request.messages),
        "created_at": _utc_now_iso(),
    }
    await extract_queue.put(
        {
            "job_id": job_id,
            "request": {
                "messages": request.messages,
                "source": request.source,
                "context": request.context,
            },
        }
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/extract/status")
async def extract_status():
    """Check extraction provider health and configuration."""
    status_payload: Dict[str, Any] = {
        "queue_depth": extract_queue.qsize(),
        "workers": EXTRACT_MAX_INFLIGHT,
        "jobs_tracked": len(extract_jobs),
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
