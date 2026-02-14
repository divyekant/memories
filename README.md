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
| `MODEL_NAME` | `all-MiniLM-L6-v2` | Sentence transformer model |
| `MAX_BACKUPS` | `10` | Number of backups to keep |
| `PORT` | `8000` | Internal service port |

### MCP Server Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `FAISS_URL` | `http://localhost:8900` | FAISS service URL |
| `FAISS_API_KEY` | (empty) | API key if auth is enabled |

---

## Project Structure

```
memories/
  app.py                  # FastAPI REST API
  memory_engine.py        # FAISS engine (search, chunking, BM25, backups)
  Dockerfile              # Docker image (model pre-downloaded)
  requirements.txt        # Python dependencies
  docker-compose.snippet.yml
  mcp-server/
    index.js              # MCP server (wraps REST API as tools)
    package.json
  tests/
    test_memory_engine.py # 25 tests
  integrations/
    claude-code.md        # Claude Code guide
    openclaw-skill.md     # OpenClaw SKILL.md
  data/                   # .gitignored — persistent index + backups
```

---

## Performance

| Metric | Value |
|--------|-------|
| Search latency | <50ms |
| Add latency | ~100ms (includes backup) |
| Model loading | ~2s (pre-downloaded in image) |
| Memory footprint | ~200MB (container) |
| Index size | ~1.5KB per memory |

Tested on Mac mini M4 Pro, 16GB RAM.
