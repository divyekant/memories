"""
FAISS Memory API Service
FastAPI wrapper for FAISS memory engine
"""

import os
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from memory_engine import MemoryEngine

# Initialize FastAPI
app = FastAPI(title="FAISS Memory API", version="1.0.0")

# Initialize memory engine
DATA_DIR = os.getenv("DATA_DIR", "/data")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
memory = MemoryEngine(data_dir=DATA_DIR)


# Request/Response Models
class SearchRequest(BaseModel):
    query: str
    k: int = 5
    threshold: Optional[float] = None


class SearchResult(BaseModel):
    id: int
    text: str
    source: str
    similarity: float
    timestamp: str


class AddMemoryRequest(BaseModel):
    text: str
    source: str
    metadata: Optional[dict] = None


class IsNovelRequest(BaseModel):
    text: str
    threshold: float = 0.82


class BuildIndexRequest(BaseModel):
    sources: Optional[List[str]] = None  # If None, use default workspace files


# Endpoints
@app.get("/health")
async def health():
    """Health check"""
    stats = memory.stats()
    return {
        "status": "ok",
        "service": "faiss-memory",
        "version": "1.0.0",
        **stats
    }


@app.post("/search")
async def search(request: SearchRequest):
    """Search for similar memories"""
    try:
        results = memory.search(
            query=request.query,
            k=request.k,
            threshold=request.threshold
        )
        return {
            "query": request.query,
            "results": results,
            "count": len(results)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/add")
async def add_memory(request: AddMemoryRequest):
    """Add a new memory"""
    try:
        ids = memory.add_memories(
            texts=[request.text],
            sources=[request.source],
            metadata_list=[request.metadata] if request.metadata else None
        )
        return {
            "success": True,
            "id": ids[0] if ids else None,
            "message": "Memory added successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/add-batch")
async def add_batch(texts: List[str], sources: List[str], metadata_list: Optional[List[dict]] = None):
    """Add multiple memories at once"""
    try:
        if len(texts) != len(sources):
            raise HTTPException(status_code=400, detail="texts and sources must have same length")
        
        ids = memory.add_memories(
            texts=texts,
            sources=sources,
            metadata_list=metadata_list
        )
        return {
            "success": True,
            "ids": ids,
            "count": len(ids),
            "message": f"Added {len(ids)} memories"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/is-novel")
async def is_novel(request: IsNovelRequest):
    """Check if text is novel (not too similar to existing)"""
    try:
        is_new, similar = memory.is_novel(
            text=request.text,
            threshold=request.threshold
        )
        return {
            "is_novel": is_new,
            "threshold": request.threshold,
            "most_similar": similar
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/index/build")
async def build_index(request: BuildIndexRequest):
    """Rebuild index from workspace files"""
    try:
        # Default sources if not provided
        if not request.sources:
            workspace = Path(WORKSPACE_DIR)
            sources = [
                str(workspace / "MEMORY.md"),
                *[str(p) for p in (workspace / "about-dk").glob("*.md")],
                *[str(p) for p in (workspace / "memory").glob("*.md")]
            ]
        else:
            # Resolve relative paths
            workspace = Path(WORKSPACE_DIR)
            sources = [str(workspace / s) for s in request.sources]
        
        # Filter to existing files
        sources = [s for s in sources if Path(s).exists()]
        
        # Rebuild
        result = memory.rebuild_from_files(sources)
        
        return {
            "success": True,
            **result,
            "message": "Index rebuilt successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def stats():
    """Get memory statistics"""
    return memory.stats()


@app.get("/backups")
async def list_backups():
    """List available backups"""
    try:
        backups = sorted(
            memory.backup_dir.glob("*_*"),
            key=lambda p: p.name,
            reverse=True
        )
        return {
            "backups": [
                {
                    "name": b.name,
                    "path": str(b),
                    "created": b.stat().st_ctime
                }
                for b in backups
            ],
            "count": len(backups)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/backup")
async def create_backup(prefix: str = "manual"):
    """Create manual backup"""
    try:
        backup_path = memory._backup(prefix=prefix)
        return {
            "success": True,
            "backup_path": str(backup_path),
            "message": "Backup created successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8900)
