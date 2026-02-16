# FAISS Memory - Peerlist Launch Post

## 🧠 Launching FAISS Memory: Give Your AI Persistent Memory That Actually Works

### The Problem

Your AI assistant forgets everything between sessions. You spend time re-explaining context, past decisions, and project standards instead of making progress.

Sound familiar?
- ❌ "We discussed this last week, let me explain again..."
- ❌ "I already told you we're using Prisma for the ORM"
- ❌ "Why can't you remember our coding standards?"

### The Solution

**FAISS Memory** - Local semantic memory for AI assistants.

Give Claude Code, ChatGPT, or any AI tool a persistent memory that:
- ✅ Remembers conversations across sessions
- ✅ Finds relevant context in <50ms (semantic + keyword hybrid search)
- ✅ Works locally (your data stays on your machine)
- ✅ Costs $0 to run
- ✅ Syncs across machines (optional S3/Google Drive backup)

### How It Works

1. **Store memories** - Project decisions, coding standards, architecture choices
2. **AI searches automatically** - When it needs context, it finds it semantically
3. **Works everywhere** - Claude Code, Claude Desktop, ChatGPT, Codex, OpenClaw

```bash
# Quick Start (60 seconds)
docker compose up -d
curl -X POST localhost:8900/memory/add \
  -d '{"text": "Use Prisma for ORM", "source": "decisions.md"}'

# AI finds it later
curl -X POST localhost:8900/search \
  -d '{"query": "database choice"}'
```

### Built With

- **Python** + FastAPI
- **FAISS** (Facebook AI Similarity Search)
- **ONNX Runtime** (fast, lightweight embeddings)
- **Docker** (one-liner deployment)
- **MCP Protocol** (native Claude Code / Desktop integration)
- **S3-compatible storage** (optional cloud sync)

### Perfect For

✅ AI agents that need memory  
✅ Personal knowledge bases  
✅ RAG pipelines  
✅ Semantic search over your notes  
✅ Coding assistants that remember project patterns  

### Key Features

- 🚀 **<50ms search latency**
- 💾 **Persistent across sessions**
- 🔍 **Hybrid search** (semantic + keyword)
- 🐳 **Docker one-liner** deployment
- ☁️ **Cloud sync** (S3/MinIO/Google Drive)
- 🔌 **REST API + MCP protocol**
- 🆓 **Zero cost** (runs locally)
- 🔒 **Private** (your data never leaves your machine)

### Tech Specs

- Docker image: ~650MB (ONNX Runtime, multi-stage build)
- Search: <50ms
- Memory footprint: ~200MB
- Index size: ~1.5KB per memory
- Python 3.11, FastAPI, FAISS, ONNX Runtime

### Open Source

MIT Licensed | PRs Welcome

GitHub: https://github.com/divyekant/memories

### Try It

```bash
git clone git@github.com:divyekant/memories.git
cd memories
docker compose up -d
curl http://localhost:8900/health
```

Full docs in README.

### What Would You Use This For?

I'm curious — if your AI could remember everything persistently, what would you have it remember?

Drop your use case in the comments! 👇

---

**Tags:** #AI #MachineLearning #LLM #OpenSource #Python #Docker #FAISS #SemanticSearch #RAG #ClaudeCode #ChatGPT #DevTools

---

## Alternate Shorter Version (if character limit)

### 🧠 FAISS Memory - Persistent Memory for AI Assistants

Your AI forgets everything between sessions. FAISS Memory fixes that.

**What it does:**
- Gives AI persistent semantic search (<50ms)
- Works locally, costs $0
- Integrates with Claude Code, ChatGPT, Codex
- Optional cloud sync (S3/GDrive)

**Built with:** Python, FastAPI, FAISS, ONNX Runtime, Docker

**Perfect for:** AI agents, personal knowledge bases, RAG pipelines, coding assistants

**Quick start:**
```bash
docker compose up -d
curl localhost:8900/memory/add -d '{"text":"Remember this"}'
```

MIT licensed | GitHub: https://github.com/divyekant/memories

What would you use this for? 👇

#AI #OpenSource #Python #SemanticSearch #DevTools
