# FAISS Keyword Audit - Memories Project

This document categorizes all instances of "FAISS" in the codebase into:
- **KEEP**: Technical references to the Facebook AI library (must retain)
- **CHANGE**: Product/branding references (change to "Memories")

---

## Summary

**Total instances found:** ~131
**Files affected:** 20

---

## 📋 Categorized by File

### 1. README.md

#### ❌ CHANGE to "Memories":
- Line ~8: "# Memories" (already changed from "# FAISS Memory")
- Line ~50: "Memories gives your AI..." (already changed from "FAISS Memory gives")
- Docker service name: "Memories Service (Docker :8900)" (change from "FAISS Memory Service")
- API title references in integration examples

#### ✅ KEEP "FAISS" (technical):
- "Hybrid Search (FAISS vector + BM25 keyword, RRF fusion)" - describing the technology
- "index.faiss (FAISS binary index)" - file format reference
- Environment variable names: `FAISS_URL`, `FAISS_API_KEY`, `FAISS_DATA_DIR` - **KEEP AS-IS** (breaking change to rename)
- Docker volume path: `~/backups/faiss-memory/` - can keep for backward compatibility
- File structure: `index.faiss` - technical file extension
- "FAISS engine (search, chunking, BM25, backups)" - code reference
- Attribution: "- [FAISS](https://github.com/facebookresearch/faiss) by Facebook AI" - **MUST KEEP**

---

### 2. PROJECT.md

#### ❌ CHANGE to "Memories":
- "# PROJECT.md - FAISS Memory" → "# PROJECT.md - Memories"
- "FAISS Memory Service (Docker)" → "Memories Service (Docker)"

#### ✅ KEEP "FAISS" (technical):
- "**Tech:** FAISS + sentence-transformers + FastAPI + Docker" - tech stack
- "| **Index Type** | FAISS IndexFlatIP (inner product similarity) |" - algorithm reference
- "`memory_engine.py` - FAISS core logic" - describes what the code does
- "`data/index.faiss` - FAISS index (binary)" - file format
- "FAISS IndexFlatIP + sentence-transformers" - architecture
- "- **FAISS:** Facebook AI Similarity Search (IndexFlatIP)" - technical glossary
- "- ✅ Core FAISS search" - implementation detail
- "- FAISS (Meta)" - attribution

---

### 3. app.py (Python)

#### ❌ CHANGE to "Memories":
- Docstring: "FAISS Memory API Service" → "Memories API Service"
- "FastAPI wrapper for FAISS memory engine..." → "FastAPI wrapper for Memories..."
- Logger: `logger.info("Starting FAISS Memory service...")` → `logger.info("Starting Memories service...")`
- API title: `title="FAISS Memory API"` → `title="Memories API"`

#### ✅ KEEP "FAISS" (technical):
- None in app.py - all references are product branding

---

### 4. memory_engine.py (Python)

#### ❌ CHANGE to "Memories":
- Docstring: "FAISS Memory Engine" → "Memories Engine"

#### ✅ KEEP "FAISS" (technical):
- Class docstring: `"""FAISS-based semantic memory with hybrid search and backup support"""` - describes implementation
- Any imports: `import faiss` - **MUST KEEP**
- Comments describing FAISS index operations

---

### 5. cloud_sync.py (Python)

#### ❌ CHANGE to "Memories":
- Docstring: "S3-compatible backup sync for FAISS Memory" → "S3-compatible backup sync for Memories"

#### ✅ KEEP "FAISS" (technical):
- Any technical comments about FAISS index files

---

### 6. PEERLIST_LAUNCH.md

#### ❌ CHANGE to "Memories":
- "# FAISS Memory - Peerlist Launch Post" → "# Memories - Peerlist Launch Post"
- "## 🧠 Launching FAISS Memory: Give Your AI..." → "## 🧠 Launching Memories: Give Your AI..."
- "**FAISS Memory** - Local semantic memory..." → "**Memories** - Local semantic memory..."
- "### 🧠 FAISS Memory - Persistent Memory for AI Assistants" → "### 🧠 Memories - Persistent Memory for AI Assistants"
- "Your AI forgets everything between sessions. FAISS Memory fixes that." → "Memories fixes that."

#### ✅ KEEP "FAISS" (technical):
- "- **FAISS** (Facebook AI Similarity Search)" - tech stack
- "**Built with:** Python, FastAPI, FAISS, ONNX Runtime, Docker" - tech stack
- Hashtags: "#FAISS" - technical keyword for discoverability

---

### 7. CLOUD_SYNC_README.md

#### ❌ CHANGE to "Memories":
- "Cloud Sync automatically backs up your FAISS memory index..." → "...your Memories index..."

#### ✅ KEEP "FAISS" (technical):
- None - only product reference

---

### 8. docker-compose.yml & docker-compose.snippet.yml

#### ❌ CHANGE to "Memories":
- Service name: `faiss-memory` → `memories` (breaking change - consider keeping)
- Container name: `faiss-memory-service` → `memories-service` (breaking change - consider keeping)

#### ✅ KEEP "FAISS" (technical):
- Volume paths with `faiss` - backward compatibility

**Recommendation:** Keep docker service names as-is to avoid breaking existing deployments. Or provide migration guide.

---

### 9. integrations/claude-code.md

#### ❌ CHANGE to "Memories":
- "> Use FAISS Memory with..." → "> Use Memories with..."
- "### 1. Ensure FAISS Memory is Running" → "### 1. Ensure Memories is Running"
- "# FAISS Memory shortcuts" → "# Memories shortcuts"
- "Claude Code doesn't have built-in FAISS Memory tools..." → "...Memories tools..."
- "- [ ] FAISS Memory service running..." → "- [ ] Memories service running..."
- "- 📖 [FAISS Memory API Docs]" → "- 📖 [Memories API Docs]"

#### ✅ KEEP "FAISS" (technical):
- Environment variable: `FAISS_URL` - **KEEP AS-IS** (breaking change)

---

### 10. integrations/openclaw-skill.md

#### ❌ CHANGE to "Memories":
- "description: FAISS-based semantic memory..." → "description: Memories - semantic memory..."
- "# FAISS Memory" → "# Memories"
- "Local semantic memory using FAISS vector search..." → "Local semantic memory using hybrid search..."
- All usage examples referencing "FAISS Memory"
- Stats output: `"📊 FAISS Memory Stats"` → `"📊 Memories Stats"`
- Health check: `"✅ FAISS Memory: HEALTHY..."` → `"✅ Memories: HEALTHY..."`

#### ✅ KEEP "FAISS" (technical):
- "- **Hybrid**: BM25 keyword + FAISS vector search (RRF fusion)" - tech description
- Environment variable: `FAISS_API_KEY` - **KEEP AS-IS**
- Comment: "🔄 Rebuilding FAISS index from workspace files..." - describes operation

---

### 11. mcp-server/package.json

#### ❌ CHANGE to "Memories":
- `"description": "MCP server for FAISS Memory..."` → `"MCP server for Memories..."`

#### ✅ KEEP "FAISS" (technical):
- None

---

### 12. scripts/*.sh (backup scripts)

#### ❌ CHANGE to "Memories":
- Echo messages: "Backing up FAISS memory..." → "Backing up Memories..."
- Log references

#### ✅ KEEP "FAISS" (technical):
- File paths containing `faiss-memory` - backward compatibility
- `index.faiss` filename references

---

### 13. tests/*.py

#### ❌ CHANGE to "Memories":
- None - tests reference the implementation

#### ✅ KEEP "FAISS" (technical):
- Test data: `"FAISS is a library for efficient similarity search"` - **MUST KEEP** (factual test data)
- `results = populated_engine.hybrid_search("FAISS", k=3)` - testing FAISS keyword search
- `assert any("FAISS" in r["text"] for r in results)` - test assertion

---

### 14. requirements.txt

#### ✅ KEEP "FAISS" (technical):
- `faiss-cpu==1.9.0.post1` - **MUST KEEP** (dependency)

---

### 15. onnx_embedder.py

#### ✅ KEEP "FAISS" (technical):
- Any imports or technical references

---

## 🎯 Environment Variables - DECISION NEEDED

Current env vars use `FAISS_` prefix:
- `FAISS_URL`
- `FAISS_API_KEY`
- `FAISS_DATA_DIR`

**Options:**
1. **Keep as-is** (recommended) - backward compatible, clear what tech is used
2. **Alias both** - support both `FAISS_URL` and `MEMORIES_URL` (migration path)
3. **Rename** - breaking change, requires migration guide

**Recommendation:** Keep `FAISS_*` for now. Users already have these configured.

---

## 🎯 Docker Service Names - DECISION NEEDED

Current names:
- Service: `faiss-memory`
- Container: `faiss-memory-service`
- Volumes: `faiss-memory-data`

**Options:**
1. **Keep as-is** (recommended) - avoid breaking Docker setups
2. **Rename with migration guide** - provide upgrade path

**Recommendation:** Keep Docker names as-is for v1.x, rename in v2.0 with migration guide.

---

## 📝 Action Plan

### Phase 1: Safe Renames (No Breaking Changes)
- [ ] README.md - product references
- [ ] PROJECT.md - product references  
- [ ] app.py - API title, logs, docstrings
- [ ] memory_engine.py - docstrings
- [ ] cloud_sync.py - docstrings
- [ ] PEERLIST_LAUNCH.md - all product references
- [ ] CLOUD_SYNC_README.md - product references
- [ ] integrations/claude-code.md - product references
- [ ] integrations/openclaw-skill.md - product references
- [ ] mcp-server/package.json - description
- [ ] scripts/*.sh - echo messages

### Phase 2: Technical References - KEEP
- [x] Attribution to Facebook AI (MUST KEEP)
- [x] Tech stack descriptions ("FAISS vector search")
- [x] File format references (`index.faiss`)
- [x] Python imports (`import faiss`)
- [x] Test data mentioning FAISS library
- [x] requirements.txt dependency

### Phase 3: Breaking Changes - DEFER
- [ ] Environment variables (`FAISS_*`) - keep for now
- [ ] Docker service names - keep for now
- [ ] File/folder paths - keep for now

---

## 🔍 Search & Replace Commands

**Safe replacements (case-sensitive):**
```bash
# Product name in titles/headings
sed -i 's/FAISS Memory/Memories/g' README.md PROJECT.md PEERLIST_LAUNCH.md

# API titles
sed -i 's/"FAISS Memory API"/"Memories API"/g' app.py

# Log messages
sed -i 's/Starting FAISS Memory service/Starting Memories service/g' app.py

# Docstrings
sed -i 's/FAISS Memory Engine/Memories Engine/g' memory_engine.py
sed -i 's/FAISS Memory API Service/Memories API Service/g' app.py
```

**Avoid these patterns (technical):**
- `FAISS vector`
- `FAISS index`
- `FAISS-based`
- `import faiss`
- `faiss-cpu`
- `index.faiss`
- `FAISS (Meta)` / `FAISS by Facebook`

---

## ✅ Final Recommendations

1. **Change "FAISS Memory" → "Memories"** in all product/branding contexts
2. **Keep "FAISS"** when referring to the technology/library
3. **Keep env vars and Docker names** as-is for backward compatibility
4. **Add note in README** explaining: "Powered by Facebook AI's FAISS library"

This preserves attribution while establishing clean product branding! 🚀
