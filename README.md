# FAISS Memory

Local semantic memory for AI assistants. Zero-cost, <50ms, hybrid BM25+vector search.

Works with **Claude Code**, **Claude Desktop**, **Claude Chat**, **Codex**, **ChatGPT**, **OpenClaw**, and anything that can call HTTP or MCP.

---

## Quick Start

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

## Architecture

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

Detailed docs:
- [Architecture deep dive](docs/architecture.md)
- [Engineering decisions and tradeoffs](docs/decisions.md)

---

## Integration Guides

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

## Remote Access

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

Now every client — Claude Code on your laptop, Claude Desktop on your phone, ChatGPT, OpenClaw — all hit the same memory store running on your Mac mini.

---

## API Reference

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

### Extraction

```
POST /memory/extract    {"messages": "...", "source": "proj", "context": "stop"}  # 202 queued
GET  /memory/extract/{job_id}
GET  /extract/status
```

Full OpenAPI schema at http://localhost:8900/docs.

---

## MCP Tools Reference

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

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `/data` | Persistent storage path |
| `WORKSPACE_DIR` | `/workspace` | Read-only workspace for index rebuilds |
| `API_KEY` | (empty) | API key for auth. Empty = no auth. |
| `MODEL_NAME` | `all-MiniLM-L6-v2` | Embedding model (ONNX Runtime) |
| `MODEL_CACHE_DIR` | (unset; Docker image sets `/data/model-cache`) | Optional writable cache path for downloaded model files |
| `PRELOADED_MODEL_CACHE_DIR` | (unset; Docker image sets `/opt/model-cache`) | Optional read-only cache to seed `MODEL_CACHE_DIR` when empty |
| `MAX_BACKUPS` | `10` | Number of backups to keep |
| `MAX_EXTRACT_MESSAGE_CHARS` | `120000` | Max characters accepted by `/memory/extract` |
| `EXTRACT_MAX_INFLIGHT` | `2` | Max concurrent extraction jobs |
| `MEMORY_TRIM_ENABLED` | `true` | Run post-extract GC/allocator trim |
| `MEMORY_TRIM_COOLDOWN_SEC` | `15` | Minimum seconds between trim attempts |
| `PORT` | `8000` | Internal service port |

### MCP Server Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `FAISS_URL` | `http://localhost:8900` | FAISS service URL |
| `FAISS_API_KEY` | (empty) | API key if auth is enabled |

---

## Automatic Memory Layer

Makes memory retrieval and extraction automatic — no manual search/store needed. Hooks inject relevant memories into every prompt and extract facts from every conversation turn.

### How it works

| Event | Hook | What happens |
|-------|------|-------------|
| Session start | `memory-recall.sh` | Loads project-specific memories into context |
| Every prompt | `memory-query.sh` | Retrieves memories relevant to the question |
| After response | `memory-extract.sh` | Extracts facts and stores via AUDN pipeline |
| Before compaction | `memory-flush.sh` | Aggressive extraction before context loss |
| Session end | `memory-commit.sh` | Final extraction pass |

### Quick setup

**Claude Code:**
```bash
./integrations/claude-code/install.sh
```

**Codex:**
```bash
./integrations/claude-code/install.sh --codex
```

**OpenClaw:** Use the updated skill at `integrations/openclaw-skill.md`

**LLM-assisted setup:** Feed [`integrations/QUICKSTART-LLM.md`](integrations/QUICKSTART-LLM.md) to your AI assistant and it will configure everything automatically.

### Extraction providers

| Provider | Cost | AUDN | Speed |
|----------|------|------|-------|
| Anthropic (recommended) | ~$0.001/turn | Full (Add/Update/Delete/Noop) | ~1-2s |
| OpenAI | ~$0.001/turn | Full | ~1-2s |
| Ollama | Free | Extract only (Add/Noop) | ~5s |
| Skip | Free | None | N/A |

Extraction is optional. Without it, hooks still retrieve memories — they just don't learn new ones automatically.

### Async extraction API

`POST /memory/extract` is async-first. It enqueues work and returns `202` with a `job_id`.
Poll `GET /memory/extract/{job_id}` for `queued`, `running`, `completed`, or `failed`.

### Docker image targets (core / extract)

The Dockerfile publishes two runtime targets:

- `core` (default): search/add/list endpoints, no Anthropic/OpenAI SDKs
- `extract`: includes Anthropic/OpenAI SDKs for `/memory/extract`

Build both images directly:

```bash
docker build --target core -t faiss-memory:core .
docker build --target extract -t faiss-memory:extract .
```

Use compose with either target:

```bash
# Default (core target)
docker compose up -d --build faiss-memory

# Extraction-ready target
FAISS_IMAGE_TARGET=extract docker compose up -d --build faiss-memory
```

By default, images do **not** bake model weights. On first run, the service downloads them into
`MODEL_CACHE_DIR` (`/data/model-cache` in Docker), so later restarts reuse the volume cache.

If you want a fully preloaded image (faster first boot, larger pull), set `PRELOAD_MODEL=true`:

```bash
docker build --target core --build-arg PRELOAD_MODEL=true -t faiss-memory:core .
docker build --target extract --build-arg PRELOAD_MODEL=true -t faiss-memory:extract .
```

Ollama uses HTTP directly and does not need the extra SDKs, so `core` is enough for Ollama extraction.

### Extraction environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRACT_PROVIDER` | (none) | `anthropic`, `openai`, `ollama`, or empty to disable |
| `EXTRACT_MODEL` | (per provider) | Model override |
| `ANTHROPIC_API_KEY` | (none) | Required for Anthropic provider |
| `OPENAI_API_KEY` | (none) | Required for OpenAI provider |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server URL (on Linux, use `http://localhost:11434`) |
| `EXTRACT_JOB_RETENTION_SEC` | `3600` | How long completed/failed extraction jobs stay queryable |
| `EXTRACT_MAX_FACTS` | `30` | Maximum facts kept from a single extraction |
| `EXTRACT_MAX_FACT_CHARS` | `500` | Max length per extracted fact |
| `EXTRACT_SIMILAR_TEXT_CHARS` | `280` | Max similar-memory text length passed into AUDN |
| `EXTRACT_SIMILAR_PER_FACT` | `5` | Similar memories included per fact during AUDN |

### Burst memory behavior

Extraction can create short-lived allocation spikes (large transcripts, large LLM JSON payloads, concurrent requests).

Mitigations built in:
- `/memory/extract` request size limit (`MAX_EXTRACT_MESSAGE_CHARS`)
- bounded in-flight extraction (`EXTRACT_MAX_INFLIGHT`)
- post-extract memory reclamation (`MEMORY_TRIM_ENABLED`, `MEMORY_TRIM_COOLDOWN_SEC`)
- bounded AUDN payload sizes (`EXTRACT_MAX_FACTS`, `EXTRACT_MAX_FACT_CHARS`, `EXTRACT_SIMILAR_TEXT_CHARS`)

Reference benchmark: `docs/benchmarks/2026-02-17-memory-reclamation.md`

### Uninstall

```bash
./integrations/claude-code/install.sh --uninstall
```

---

## Backup & Recovery

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
ENABLE_CLOUD_SYNC=true docker compose up -d --build faiss-memory
```

See [CLOUD_SYNC_README.md](CLOUD_SYNC_README.md) for configuration details.

---

## Project Structure

```
memories/
  app.py                  # FastAPI REST API
  memory_engine.py        # FAISS engine (search, chunking, BM25, backups)
  onnx_embedder.py        # ONNX Runtime embedder (replaces PyTorch)
  llm_provider.py         # LLM provider abstraction (Anthropic/OpenAI/Ollama)
  llm_extract.py          # Extraction pipeline with AUDN
  Dockerfile              # Multi-stage Docker build (core/extract targets)
  requirements.txt        # Python dependencies
  requirements-extract.txt # Optional extraction deps (Anthropic/OpenAI SDKs)
  docker-compose.snippet.yml
  docs/
    architecture.md       # System architecture and runtime flows
    decisions.md          # Key design decisions and tradeoffs
    benchmarks/           # Reproducible benchmark notes
  mcp-server/
    index.js              # MCP server (wraps REST API as tools)
    package.json
  scripts/
    backup.sh             # Cron backup (local snapshots)
    backup-gdrive.sh      # Optional Google Drive upload
    install-cron.sh       # Cron job installer
  integrations/
    claude-code/
      install.sh          # Interactive installer for hooks
      hooks/              # 5 hook scripts + hooks.json
    claude-code.md        # Claude Code guide
    openclaw-skill.md     # OpenClaw SKILL.md
    QUICKSTART-LLM.md     # LLM-friendly setup guide
  tests/
    test_memory_engine.py # Memory engine tests
    test_llm_provider.py  # LLM provider tests
    test_llm_extract.py   # Extraction pipeline tests
    test_extract_api.py   # API endpoint tests
  data/                   # .gitignored — persistent index + backups
```

---

## Performance

| Metric | Value |
|--------|-------|
| Docker image size | ~430MB core / ~436MB extract (no baked model cache by default) |
| Search latency | <50ms |
| Add latency | ~100ms (includes backup) |
| Model loading | Cold boot downloads model once; warm boots reuse `/data/model-cache` |
| Memory footprint | ~180-260MB baseline; higher during extraction bursts |
| Index size | ~1.5KB per memory |

Uses **ONNX Runtime** for inference instead of PyTorch — same model (all-MiniLM-L6-v2), same embeddings, 68% smaller image.

Tested on Mac mini M4 Pro, 16GB RAM.
