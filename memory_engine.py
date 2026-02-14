"""
FAISS Memory Engine
Local semantic search with automatic backups
"""

import faiss
import numpy as np
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from sentence_transformers import SentenceTransformer


class MemoryEngine:
    """FAISS-based semantic memory with backup support"""
    
    def __init__(self, data_dir: str = "/data", model_name: str = "all-MiniLM-L6-v2"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Backup directory
        self.backup_dir = self.data_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        
        # File paths
        self.index_path = self.data_dir / "index.faiss"
        self.metadata_path = self.data_dir / "metadata.json"
        self.config_path = self.data_dir / "config.json"
        
        # Load sentence transformer
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()
        
        # Initialize or load index
        self.index = faiss.IndexFlatIP(self.dim)
        self.metadata: List[Dict[str, Any]] = []
        self.config = {
            "model": model_name,
            "dimension": self.dim,
            "created_at": datetime.utcnow().isoformat(),
            "last_updated": None
        }
        
        # Load existing if available
        if self.index_path.exists():
            self.load()
    
    def _backup(self, prefix: str = "auto"):
        """Create timestamped backup of index and metadata"""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{prefix}_{timestamp}"
        backup_path = self.backup_dir / backup_name
        backup_path.mkdir(exist_ok=True)
        
        # Backup index
        if self.index_path.exists():
            shutil.copy2(self.index_path, backup_path / "index.faiss")
        
        # Backup metadata
        if self.metadata_path.exists():
            shutil.copy2(self.metadata_path, backup_path / "metadata.json")
        
        # Backup config
        if self.config_path.exists():
            shutil.copy2(self.config_path, backup_path / "config.json")
        
        # Keep only last 10 backups
        self._cleanup_old_backups(keep=10)
        
        return backup_path
    
    def _cleanup_old_backups(self, keep: int = 10):
        """Keep only N most recent backups"""
        backups = sorted(self.backup_dir.glob("*_*"), key=lambda p: p.name, reverse=True)
        for old_backup in backups[keep:]:
            shutil.rmtree(old_backup)
    
    def add_memories(self, texts: List[str], sources: List[str], metadata_list: Optional[List[Dict]] = None):
        """Add new memories to index"""
        if not texts:
            return []
        
        # Backup before major changes
        if len(texts) > 10:
            self._backup(prefix="pre_add")
        
        # Generate embeddings
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        
        # Add to index
        start_id = len(self.metadata)
        self.index.add(embeddings.astype('float32'))
        
        # Store metadata
        added_ids = []
        for i, (text, source) in enumerate(zip(texts, sources)):
            mem_id = start_id + i
            meta = {
                "id": mem_id,
                "text": text,
                "source": source,
                "timestamp": datetime.utcnow().isoformat(),
                **(metadata_list[i] if metadata_list and i < len(metadata_list) else {})
            }
            self.metadata.append(meta)
            added_ids.append(mem_id)
        
        # Update config
        self.config["last_updated"] = datetime.utcnow().isoformat()
        
        # Auto-save
        self.save()
        
        return added_ids
    
    def search(self, query: str, k: int = 5, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        """Search for similar memories"""
        if self.index.ntotal == 0:
            return []
        
        # Encode query
        query_vec = self.model.encode([query], normalize_embeddings=True, show_progress_bar=False)
        
        # Search
        distances, indices = self.index.search(query_vec.astype('float32'), min(k, self.index.ntotal))
        
        # Build results
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:  # Invalid result
                continue
            
            similarity = float(dist)
            
            # Apply threshold if specified
            if threshold and similarity < threshold:
                continue
            
            result = {
                **self.metadata[idx],
                "similarity": similarity
            }
            results.append(result)
        
        return results
    
    def is_novel(self, text: str, threshold: float = 0.82) -> Tuple[bool, Optional[Dict]]:
        """Check if text is novel (not too similar to existing memories)"""
        results = self.search(text, k=1)
        
        if not results:
            return True, None
        
        top_match = results[0]
        is_novel = top_match["similarity"] < threshold
        
        return is_novel, top_match
    
    def save(self):
        """Persist index and metadata to disk"""
        # Save index
        faiss.write_index(self.index, str(self.index_path))
        
        # Save metadata
        with open(self.metadata_path, "w") as f:
            json.dump(self.metadata, f, indent=2)
        
        # Save config
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=2)
    
    def load(self):
        """Load index and metadata from disk"""
        # Load index
        self.index = faiss.read_index(str(self.index_path))
        
        # Load metadata
        with open(self.metadata_path) as f:
            self.metadata = json.load(f)
        
        # Load config
        if self.config_path.exists():
            with open(self.config_path) as f:
                self.config.update(json.load(f))
    
    def rebuild_from_files(self, file_paths: List[str]) -> Dict[str, Any]:
        """Rebuild index from markdown files"""
        # Backup current state
        backup_path = self._backup(prefix="pre_rebuild")
        
        # Clear existing
        self.index = faiss.IndexFlatIP(self.dim)
        self.metadata = []
        
        texts = []
        sources = []
        
        # Read all files
        for file_path in file_paths:
            path = Path(file_path)
            if not path.exists():
                continue
            
            try:
                content = path.read_text()
                lines = content.splitlines()
                
                for i, line in enumerate(lines, 1):
                    line = line.strip()
                    if line and len(line) > 20:  # Skip empty/short lines
                        texts.append(line)
                        sources.append(f"{path.name}:{i}")
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
        
        # Add all memories
        if texts:
            self.add_memories(texts, sources)
        
        return {
            "files_processed": len(file_paths),
            "memories_added": len(texts),
            "backup_location": str(backup_path)
        }
    
    def stats(self) -> Dict[str, Any]:
        """Get memory statistics"""
        return {
            "total_memories": self.index.ntotal,
            "dimension": self.dim,
            "model": self.config.get("model"),
            "created_at": self.config.get("created_at"),
            "last_updated": self.config.get("last_updated"),
            "index_size_bytes": self.index_path.stat().st_size if self.index_path.exists() else 0,
            "backup_count": len(list(self.backup_dir.glob("*_*")))
        }
