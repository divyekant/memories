# FAISS Memory

[![Docker](https://img.shields.io/badge/docker-ready-blue)](https://github.com/divyekant/memories)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11-blue)](https://python.org)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/divyekant/memories/pulls)

**Give your AI persistent memory that actually works.**

Local semantic memory for AI assistants. Zero-cost, <50ms search, hybrid BM25+vector retrieval.

Works with **Claude Code**, **Claude Desktop**, **ChatGPT**, **Codex**, **OpenClaw**, and anything that can call HTTP or MCP.

---

## 🎯 The Problem

Your AI assistant:
- ❌ Forgets everything between sessions
- ❌ Can't find context it needs from your notes
- ❌ Keeps asking the same questions over and over
- ❌ Loses important decisions and patterns you've discussed

**Result:** You spend time re-explaining context instead of making progress.

## ✅ The Solution

FAISS Memory gives your AI a **persistent semantic search index** that:
- ✅ Remembers conversations across sessions
- ✅ Finds relevant context in <50ms (semantic + keyword hybrid search)
- ✅ Works locally (your data stays on your machine)
- ✅ Costs $0 to run
- ✅ Syncs across machines (optional S3/Google Drive backup)
- ✅ Integrates with any AI tool via REST API or MCP protocol

---

## 🚀 Quick Start

```bash
# 1. Clone and build
git clone git@github.com:divyekant/memories.git
cd memories
docker compose -f docker-compose.snippet.yml up -d

# 2. Verify
curl http://localhost:8900/health

# 3. Add a memory
curl -X POST http://localhost:8900/memory/add \
  -H "Content-Type: application/json" \
  -d '{"text": "Always use TypeScript strict mode", "source": "standards.md"}'

# 4. Search
curl -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -d '{"query": "TypeScript config", "k": 3, "hybrid": true}'
```

The service runs at **http://localhost:8900**. API docs at http://localhost:8900/docs.

---

## 💡 Use Cases

### 1. **AI Agent Memory**
Give Claude Code or ChatGPT persistent memory of your project decisions, coding standards, and architecture choices.

```bash
# Store a decision
curl -X POST localhost:8900/memory/add \
  -d '{"text": "We decided to use Prisma for ORM because...", "source": "decisions.md"}'

# Later, AI searches and finds it
curl -X POST localhost:8900/search \
  -d '{"query": "database ORM choice"}'
```

### 2. **Personal Knowledge Base**
Search your notes semantically, not just by keyword.

```bash
# Add all your markdown notes
curl -X POST localhost:8900/index/build \
  -d '{"sources": ["notes/", "docs/", "journal/"]}'

# Find anything by meaning
curl -X POST localhost:8900/search \
  -d '{"query": "how to fix auth issues"}'
```

### 3. **RAG Pipeline**
Drop-in semantic search for your documents. Works with any AI tool.

```bash
# Build index from your docs
docker run -v /path/to/docs:/workspace faiss-memory

# Query from your app
fetch('http://localhost:8900/search', {
  method: 'POST',
  body: JSON.stringify({query: userQuestion, k: 5})
})
```

### 4. **Multi-Machine Sync**
Work from laptop, deploy on server — your AI's memory follows you via S3 cloud sync.

```bash
# Enable cloud backup
docker compose build --build-arg ENABLE_CLOUD_SYNC=true
docker compose up -d

# Automatic upload after every change
# Auto-download on new machine if local index is empty
```

### 5. **Coding Assistant Context**
Claude Code remembers your project patterns, bug fixes, and refactoring decisions.

```
You: "Remember we fixed the race condition by using a mutex in the UserService"
Claude Code: [stores in memory via MCP]

Later...
You: "How did we handle concurrency in UserService?"
Claude Code: [searches memory, finds the fix]
```

---

## 🏗️ Architecture

```
AI Client (Claude, Codex, ChatGPT, OpenClaw)
    |
    |-- MCP protocol (Claude Code / Desktop)
    |-- REST API (everything else)
    v
MCP Server (mcp-server/index.js)
    |
    v
FAISS Memory Service (Docker :8900)
    |-- FastAPI REST API
    |-- Hybrid Search (FAISS vector + BM25 keyword, RRF fusion)
    |-- Markdown-aware chunking
    |-- Auto-backups
    v
Persistent Storage (data/)
    |-- index.faiss (FAISS binary index)
    |-- metadata.json (memory text + metadata)
    |-- backups/ (auto, keeps last 10)
```

---

## 🔌 Integration Guides

### Claude Code (CLI)

The MCP server gives Claude Code native `memory_search`, `memory_add`, `memory_delete`, `memory_list`, `memory_stats`, and `memory_is_novel` tools.

**Setup:**

1. Install the MCP server dependencies:

```bash
cd memories/mcp-server
npm install
```

2. Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "faiss-memory": {
      "command": "node",
      "args": ["/path/to/memories/mcp-server/index.js"],
      "env": {
        "FAISS_URL": "http://localhost:8900",
        "FAISS_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

3. Restart Claude Code. The tools are now available in every project.

**Usage** (Claude Code will call these automatically when relevant):

- "Search my memory for authentication patterns"
- "Remember that we decided to use Prisma for the ORM"
- "Check if this pattern is already in memory before adding it"
- "Show me all memories from the bug-fixes source"

For a **single project only**, create `.mcp.json` in the project root instead of editing `settings.json`.

---

### Claude Desktop (Chat / Cowork)

Same MCP server, different config file.

**Setup:**

1. Install dependencies (same as above):

```bash
cd memories/mcp-server
npm install
```

2. Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "faiss-memory": {
      "command": "node",
      "args": ["/path/to/memories/mcp-server/index.js"],
      "env": {
        "FAISS_URL": "http://localhost:8900",
        "FAISS_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

3. Restart the Claude Desktop app. Memory tools appear in chat and cowork mode.

---

### Claude Chat (Web at claude.ai)

Claude Chat on the web does not support MCP directly. Two options:

**Option A: Remote MCP via Cloudflare Tunnel (recommended)**

If you expose the FAISS service via a tunnel (e.g., `memory.yourdomain.com`), you can use Claude's remote MCP connector feature to connect to it. See the [Remote Access](#remote-access) section below.

**Option B: Manual curl in prompts**

Paste curl commands in your messages and ask Claude to interpret the results:

```
Search my memory service for React patterns:

curl -X POST https://memory.yourdomain.com/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"query": "React patterns", "k": 5, "hybrid": true}'
```

---

### Codex (OpenAI)

Codex supports MCP natively.

**Setup:**

1. Install dependencies:

```bash
cd memories/mcp-server
npm install
```

2. Add to your Codex MCP config:

```json
{
  "mcpServers": {
    "faiss-memory": {
      "command": "node",
      "args": ["/path/to/memories/mcp-server/index.js"],
      "env": {
        "FAISS_URL": "http://localhost:8900",
        "FAISS_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

3. Restart Codex. The `memory_search`, `memory_add`, and other tools will be available.

**Usage** (Codex will discover the tools automatically):

- "Search memory for how we handle error logging"
- "Store this architecture decision in memory"
- "List all memories from the project-setup source"

---

### ChatGPT (Custom GPT)

ChatGPT uses **Custom Actions** (OpenAPI schema) rather than MCP. This requires exposing the FAISS service over the internet.

**Prerequisites:** FAISS service accessible via HTTPS (see [Remote Access](#remote-access)).

**Setup:**

1. Enable API key auth on the FAISS service (set `API_KEY` env var in docker-compose).

2. In ChatGPT, go to **Explore GPTs > Create a GPT > Configure > Actions**.

3. Import this OpenAPI schema (replace `memory.yourdomain.com` with your URL):

```yaml
openapi: 3.0.0
info:
  title: FAISS Memory
  version: 2.0.0
  description: Semantic memory search and storage
servers:
  - url: https://memory.yourdomain.com
paths:
  /search:
    post:
      operationId: searchMemory
      summary: Search memories by semantic similarity
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [query]
              properties:
                query:
                  type: string
                  description: Natural language search query
                k:
                  type: integer
                  default: 5
                  description: Number of results
                hybrid:
                  type: boolean
                  default: true
                  description: Use hybrid BM25+vector search
      responses:
        '200':
          description: Search results

  /memory/add:
    post:
      operationId: addMemory
      summary: Store a new memory
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [text, source]
              properties:
                text:
                  type: string
                  description: Memory content
                source:
                  type: string
                  description: Source identifier
                deduplicate:
                  type: boolean
                  default: true
      responses:
        '200':
          description: Memory added

  /memory/is-novel:
    post:
      operationId: isNovel
      summary: Check if text is already known
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [text]
              properties:
                text:
                  type: string
                threshold:
                  type: number
                  default: 0.88
      responses:
        '200':
          description: Novelty check result

  /memories:
    get:
      operationId: listMemories
      summary: Browse stored memories
      parameters:
        - name: offset
          in: query
          schema:
            type: integer
            default: 0
        - name: limit
          in: query
          schema:
            type: integer
            default: 20
        - name: source
          in: query
          schema:
            type: string
      responses:
        '200':
          description: List of memories

  /stats:
    get:
      operationId: getStats
      summary: Memory index statistics
      responses:
        '200':
          description: Index stats
```

4. Under **Authentication**, choose **API Key** with header name `X-API-Key`.

5. Add instructions to the GPT system prompt:

```
You have access to a persistent memory system. Use it to:
- Search for relevant context before answering questions (searchMemory)
- Store important decisions, patterns, and learnings (addMemory)
- Check if something is already known before adding (isNovel)
- Browse what's stored (listMemories)

Always search memory at the start of conversations to load context.
```

---

### OpenClaw

OpenClaw uses a **Skill** (SKILL.md) with shell helper functions that call the REST API directly.

**Setup:**

1. Create the skill directory and copy the skill file:

```bash
mkdir -p ~/.openclaw/skills/faiss-memory
cp integrations/openclaw-skill.md ~/.openclaw/skills/faiss-memory/SKILL.md
```

Or see the full SKILL.md in this repo at `integrations/openclaw-skill.md`.

2. Set the API key in your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
export FAISS_API_KEY="your-api-key-here"
```

The SKILL.md reads `$FAISS_API_KEY` from the environment — the key is never stored in the skill file itself.

**Key commands available to OpenClaw agents:**

```bash
memory_search_faiss "query" [k] [threshold] [hybrid]
memory_add_faiss "text" "source" [deduplicate]
memory_is_novel "text" [threshold]
memory_delete_faiss <id>
memory_delete_source_faiss "pattern"
memory_list_faiss [offset] [limit] [source]
memory_rebuild_index
memory_dedup_faiss [dry_run] [threshold]
memory_stats
memory_health
memory_backup [prefix]
memory_restore "backup_name"
```

All functions use `jq` for safe JSON construction and read auth from `$FAISS_API_KEY` env var (no hardcoded secrets).

---

## 🌐 Remote Access

To use FAISS Memory from anywhere (Claude Chat web, ChatGPT, mobile, other machines), expose it via a Cloudflare Tunnel or similar.

### Setup with Cloudflare Tunnel

1. **Enable API key auth** in your docker-compose:

```yaml
environment:
  - API_KEY=your-secret-key-here
```

Rebuild and restart: `docker compose build faiss-memory && docker compose up -d faiss-memory`

2. **Add to your Cloudflare tunnel config** (e.g., in `~/.cloudflared/config.yml`):

```yaml
ingress:
  - hostname: memory.yourdomain.com
    service: http://localhost:8900
```

3. **Update MCP server env** to use the remote URL:

```json
{
  "env": {
    "FAISS_URL": "https://memory.yourdomain.com",
    "FAISS_API_KEY": "your-secret-key-here"
  }
}
```

Now every client — Claude Code on your laptop, Claude Desktop on your phone, ChatGPT, OpenClaw — all hit the same memory store.

---

## 📚 API Reference

All endpoints accept/return JSON. Optional auth via `X-API-Key` header.

### Search

```
POST /search
{"query": "...", "k": 5, "hybrid": true, "threshold": 0.3, "vector_weight": 0.7}
```

### Add Memory

```
POST /memory/add
{"text": "...", "source": "file.md", "deduplicate": true}
```

### Add Batch

```
POST /memory/add-batch
{"memories": [{"text": "...", "source": "..."}, ...], "deduplicate": true}
```

### Delete

```
DELETE /memory/{id}
POST /memory/delete-by-source  {"source_pattern": "credentials"}
```

### Novelty Check

```
POST /memory/is-novel
{"text": "...", "threshold": 0.88}
```

### Browse

```
GET /memories?offset=0&limit=20&source=filter
```

### Deduplication

```
POST /memory/deduplicate
{"threshold": 0.90, "dry_run": true}
```

### Index Operations

```
POST /index/build    {"sources": ["file1.md", "file2.md"]}
GET  /stats
GET  /health
```

### Backups

```
GET  /backups
POST /backup?prefix=manual
POST /restore          {"backup_name": "manual_20260213_120000"}
```

Full OpenAPI schema at http://localhost:8900/docs.

---

## 🛠️ MCP Tools Reference

When connected via MCP (Claude Code, Claude Desktop, Codex), these tools are available:

| Tool | Description |
|------|-------------|
| `memory_search` | Hybrid search (BM25 + vector). Default mode. |
| `memory_add` | Store a memory with auto-dedup. |
| `memory_delete` | Delete by ID. |
| `memory_list` | Browse with pagination and source filter. |
| `memory_stats` | Index stats (count, model, last updated). |
| `memory_is_novel` | Check if text is already known. |

---

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `/data` | Persistent storage path |
| `WORKSPACE_DIR` | `/workspace` | Read-only workspace for index rebuilds |
| `API_KEY` | (empty) | API key for auth. Empty = no auth. |
| `MODEL_NAME` | `all-MiniLM-L6-v2` | Embedding model (ONNX Runtime) |
| `MAX_BACKUPS` | `10` | Number of backups to keep |
| `PORT` | `8000` | Internal service port |

### MCP Server Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `FAISS_URL` | `http://localhost:8900` | FAISS service URL |
| `FAISS_API_KEY` | (empty) | API key if auth is enabled |

---

## 💾 Backup & Recovery

FAISS Memory has three layers of backup protection:

### 1. Auto-backup (built-in)

The service automatically saves a snapshot after every write operation. The 10 most recent auto-backups are kept in the Docker volume under `data/backups/`.

```bash
# List backups
curl -H "X-API-Key: $FAISS_API_KEY" http://localhost:8900/backups

# Create manual backup
curl -X POST -H "X-API-Key: $FAISS_API_KEY" http://localhost:8900/backup?prefix=manual

# Restore from backup
curl -X POST -H "X-API-Key: $FAISS_API_KEY" http://localhost:8900/restore \
  -H "Content-Type: application/json" \
  -d '{"backup_name": "manual_20260214_120000"}'
```

### 2. Scheduled local snapshots (cron)

A cron job creates timestamped copies of the FAISS index every 30 minutes. Snapshots are stored outside the Docker volume (default: `~/backups/faiss-memory/`) with 30-day retention.

```bash
# Install the cron job
./scripts/install-cron.sh install

# Check status
./scripts/install-cron.sh status

# Run a backup manually
./scripts/backup.sh

# Dry run (no changes)
./scripts/backup.sh --test
```

**Environment variables** (all optional, sensible defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `FAISS_URL` | `http://localhost:8900` | Service URL |
| `FAISS_API_KEY` | (empty) | API key if auth is enabled |
| `FAISS_DATA_DIR` | `./data` (relative to repo) | Docker volume data path |
| `BACKUP_DIR` | `~/backups/faiss-memory` | Where to store snapshots |
| `RETENTION_DAYS` | `30` | Days to keep local snapshots |

### 3. Off-site backup to Google Drive (optional)

If you set `GDRIVE_ACCOUNT`, each backup automatically uploads the latest snapshot to Google Drive as a compressed tar.gz. Uploads are throttled to once per hour. 7-day retention on Drive.

**Prerequisites:**

1. Install [gog CLI](https://github.com/skratchdot/gog)
2. Authenticate: `gog auth add your-email@gmail.com --services drive`
3. Set env var in your shell profile:

```bash
export GDRIVE_ACCOUNT="your-email@gmail.com"
```

**Environment variables** (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `GDRIVE_ACCOUNT` | (none) | Google account email. **Required to enable GDrive.** |
| `GDRIVE_FOLDER_NAME` | `faiss-memory-backups` | Folder name on Drive |
| `UPLOAD_INTERVAL_MIN` | `55` | Minimum minutes between uploads |
| `GDRIVE_RETENTION_DAYS` | `7` | Days to keep backups on Drive |

**Manual usage:**

```bash
# Setup (create Drive folder + test auth)
./scripts/backup-gdrive.sh --setup

# Upload now (skip throttle)
./scripts/backup-gdrive.sh --force

# Dry run
./scripts/backup-gdrive.sh --test

# Only clean up old backups on Drive
./scripts/backup-gdrive.sh --cleanup
```

### Alternative: S3-compatible cloud sync

For S3/MinIO/R2 backends, build with cloud sync enabled:

```bash
docker compose build --build-arg ENABLE_CLOUD_SYNC=true faiss-memory
```

See [CLOUD_SYNC_README.md](CLOUD_SYNC_README.md) for configuration details.

---

## 📁 Project Structure

```
memories/
  app.py                  # FastAPI REST API
  memory_engine.py        # FAISS engine (search, chunking, BM25, backups)
  onnx_embedder.py        # ONNX Runtime embedder (replaces PyTorch)
  Dockerfile              # Multi-stage Docker build (model pre-downloaded)
  requirements.txt        # Python dependencies
  docker-compose.snippet.yml
  mcp-server/
    index.js              # MCP server (wraps REST API as tools)
    package.json
  scripts/
    backup.sh             # Cron backup (local snapshots)
    backup-gdrive.sh      # Optional Google Drive upload
    install-cron.sh       # Cron job installer
  tests/
    test_memory_engine.py # 25 tests
  integrations/
    claude-code.md        # Claude Code guide
    openclaw-skill.md     # OpenClaw SKILL.md
  data/                   # .gitignored — persistent index + backups
```

---

## ⚡ Performance

| Metric | Value |
|--------|-------|
| Docker image size | ~650MB (ONNX Runtime, multi-stage build) |
| Search latency | <50ms |
| Add latency | ~100ms (includes backup) |
| Model loading | ~3s (pre-downloaded in image) |
| Memory footprint | ~200MB (container) |
| Index size | ~1.5KB per memory |

Uses **ONNX Runtime** for inference instead of PyTorch — same model (all-MiniLM-L6-v2), same embeddings, 68% smaller image.

Tested on Mac mini M4 Pro, 16GB RAM.

---

## 🤝 Contributing

PRs welcome! Please:
- Add tests for new features
- Follow existing code style
- Update docs if adding new endpoints or features

---

## 📄 License

MIT © [Divyekant Singh](https://github.com/divyekant)

---

## 🙏 Credits

Built with:
- [FAISS](https://github.com/facebookresearch/faiss) by Facebook AI
- [FastAPI](https://fastapi.tiangolo.com/)
- [ONNX Runtime](https://onnxruntime.ai/)
- [sentence-transformers](https://www.sbert.net/) models

---

## 📸 Screenshots & Demo

_Coming soon: Screenshots and demo video will be added here_
