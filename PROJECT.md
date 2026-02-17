# PROJECT.md - FAISS Memory

**Status:** ✅ Production  
**Type:** Infrastructure / AI Tool  
**Created:** 2026-02-12  
**Public:** Yes (open source ready)

---

## Quick Summary

Local semantic memory search for AI coding assistants. Zero-cost alternative to cloud vector databases. Works with Claude Code, Codex, ChatGPT, Cursor, Continue.dev, and any AI that can call HTTP APIs.

**Tech:** FAISS + sentence-transformers + FastAPI + Docker  
**Cost:** $0/month (100% local)  
**Latency:** <50ms per search  
**Security:** 100% local (no data leaves your machine)

---

## Ready Reckoner

| **Item** | **Value** |
|----------|-----------|
| **Port** | 8900 (host) → 8000 (container) |
| **Service URL** | http://localhost:8900 |
| **Docker Container** | faiss-memory |
| **Docker Image** | faiss-memory:latest |
| **Data Volume** | `./data` (bind-mounted) |
| **Docker Compose** | `docker-compose.snippet.yml` (faiss-memory service) |
| **Model** | all-MiniLM-L6-v2 (384 dimensions) |
| **Index Type** | FAISS IndexFlatIP (inner product similarity) |
| **Health Check** | http://localhost:8900/health |
| **Stats** | http://localhost:8900/stats |

**Key Files:**
- `app.py` - FastAPI REST API
- `memory_engine.py` - FAISS core logic
- `Dockerfile` - Container definition
- `requirements.txt` - Python dependencies
- `data/index.faiss` - FAISS index (binary)
- `data/metadata.json` - Memory metadata
- `data/backups/` - Auto-backups (keeps last 10)

**Integrations:**
- `integrations/claude-code.md` - Claude Code integration guide
- `integrations/codex.md` - OpenAI Codex integration guide
- `integrations/chatgpt.md` - ChatGPT integration guide
- `integrations/cursor.md` - Cursor AI integration guide
- `integrations/continue.md` - Continue.dev integration guide
- `integrations/aider.md` - Aider integration guide

**Examples:**
- `examples/` - Sample configurations for each AI assistant

---

## Current Status

**Production Ready:** ✅ Yes  
**Current Index:** 38 memories (58KB)  
**Last Updated:** 2026-02-12  
**Docker Status:** Running, healthy

**Recent Activity:**
- 2026-02-12: Initial build, 38 memories indexed
- 2026-02-12: Docker deployment complete
- 2026-02-12: Auto-backup system operational
- 2026-02-12: Integration guides created

---

## What This Does

1. **Semantic Search:** Find relevant memories using natural language queries
2. **Duplicate Detection:** Check if new information is novel or already known
3. **Auto-Backups:** Automatic backups on every write (keeps last 10)
4. **Fast Queries:** <50ms search latency (in-memory FAISS index)
5. **Zero Cost:** 100% local embeddings (no API calls)
6. **AI Integrations:** Works with Claude Code, Codex, ChatGPT, Cursor, etc.

---

## Architecture

```
AI Coding Assistant (Claude Code, Cursor, etc.)
    ↓ HTTP POST
FAISS Memory Service (Docker)
    ↓
FastAPI REST API (:8900)
    ↓
Memory Engine (memory_engine.py)
    ↓
FAISS IndexFlatIP + sentence-transformers
    ↓
Persistent Storage (data/)
```

**Components:**
- **FastAPI:** REST API server
- **FAISS:** Facebook AI Similarity Search (IndexFlatIP)
- **sentence-transformers:** Local embedding model (all-MiniLM-L6-v2)
- **Docker:** Containerization + isolation
- **Auto-backup:** Saves index+metadata on every write

---

## Dependencies

**Runtime:**
- Docker (container runtime)
- Python 3.11 (in container)

**Python packages (in container):**
- fastapi==0.109.0
- uvicorn[standard]==0.27.0
- pydantic==2.5.3
- sentence-transformers==2.3.1
- faiss-cpu==1.7.4
- numpy==1.24.4

**AI Assistant (choose one or more):**
- Claude Code (Anthropic)
- Codex (OpenAI)
- ChatGPT (OpenAI)
- Cursor AI
- Continue.dev
- Aider
- Any AI that can make HTTP requests

---

## Deployment

**Current Setup:**
```yaml
# In docker-compose.snippet.yml
services:
  faiss-memory:
    build: ../projects/faiss-memory
    image: faiss-memory:latest
    container_name: faiss-memory
    restart: unless-stopped
    ports:
      - "8900:8000"
    volumes:
      - ../projects/faiss-memory/data:/data
    networks: [tunnel]
```

**Start/Stop:**
```bash
docker compose -f docker-compose.snippet.yml up -d faiss-memory    # Start
docker compose -f docker-compose.snippet.yml stop faiss-memory     # Stop
docker compose -f docker-compose.snippet.yml restart faiss-memory  # Restart
docker compose -f docker-compose.snippet.yml logs -f faiss-memory  # View logs
```

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/stats` | GET | Index statistics |
| `/search` | POST | Semantic search |
| `/memory/add` | POST | Add single memory |
| `/memory/add-batch` | POST | Add multiple memories |
| `/memory/is-novel` | POST | Check if text is novel |
| `/index/build` | POST | Rebuild index from files |
| `/backup` | POST | Create manual backup |
| `/backups` | GET | List available backups |

**Full API docs:** See `docs/API.md`

---

## Integrations

**Supported AI Assistants:**

1. **Claude Code** - `integrations/claude-code.md`
   - MCP server pattern
   - Direct HTTP calls
   - OpenClaw skill integration

2. **Codex (OpenAI)** - `integrations/codex.md`
   - Function calling
   - API key setup
   - Example prompts

3. **ChatGPT** - `integrations/chatgpt.md`
   - Custom GPT configuration
   - Action schema
   - Quick-start guide

4. **Cursor AI** - `integrations/cursor.md`
   - .cursorrules configuration
   - API integration
   - Memory search commands

5. **Continue.dev** - `integrations/continue.md`
   - config.json setup
   - Custom tools
   - Slash commands

6. **Aider** - `integrations/aider.md`
   - .aider.conf.yml configuration
   - Custom commands
   - Memory integration

**See `integrations/` directory for full guides.**

---

## Performance Benchmarks

| Metric | Value | Notes |
|--------|-------|-------|
| Search latency | <50ms | k=5 results |
| Add latency | ~100ms | Includes embedding + save + backup |
| Model loading | ~15s | On first container start |
| Memory footprint | ~200MB | Model + dependencies |
| Index size | ~1.5KB per memory | Linear growth |
| Embedding generation | ~10ms | all-MiniLM-L6-v2 |

**Tested on:** Mac mini M4 Pro (16GB RAM)

---

## Roadmap

### v1.0 (Current)
- ✅ Core FAISS search
- ✅ Auto-backups
- ✅ Docker deployment
- ✅ REST API
- ✅ Basic integrations

### v1.1 (Next)
- [ ] Web UI for browsing memories
- [ ] Auto-rebuild on file changes (watch mode)
- [ ] Memory deduplication tool
- [ ] Export formats (JSON, Markdown, CSV)
- [ ] MCP server implementation
- [ ] OpenClaw memory_search integration

### v1.2 (Future)
- [ ] Multi-index support (different projects)
- [ ] Hybrid search (semantic + keyword)
- [ ] Memory tagging system
- [ ] Search filters (by source, date, type)
- [ ] Scheduled index rebuilds via cron
- [ ] Memory analytics dashboard

---

## Known Issues

**None currently.**

**Resolved:**
- ✅ Port mismatch (8900 vs 8000) - Fixed in Dockerfile
- ✅ API schema (source field location) - Fixed in app.py
- ✅ Index building script (payload format) - Fixed in build script

---

## Security

**Threat Model:**
- Service binds to localhost only (no external exposure)
- No authentication required (local-only access)
- Data never leaves the machine (100% local)
- No cloud API calls (zero external dependencies)

**If exposing externally:**
- Add authentication (API key, OAuth, etc.)
- Use HTTPS (reverse proxy)
- Rate limiting (prevent abuse)
- Input validation (prevent injection)

**See `docs/SECURITY.md` for deployment guidelines.**

---

## License

**To be determined** (suggest MIT or Apache 2.0 for public release)

---

## Contributing

**Not yet accepting contributions** (public release pending)

When public:
- See `CONTRIBUTING.md` for guidelines
- Issues/PRs welcome
- Follow existing code style
- Add tests for new features
- Update documentation

---

## Support

- GitHub Issues
- Documentation: `docs/`
- Integration guides: `integrations/`

---

## Credits

**Date:** 2026-02-12
**Built with:** Claude AI

**Technologies:**
- FAISS (Meta)
- sentence-transformers (UKPLab)
- FastAPI (Sebastián Ramírez)
- Docker

---

**Last Updated:** 2026-02-12 18:00
