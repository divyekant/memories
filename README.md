# Memories

Local semantic memory for AI assistants. Zero-cost, <50ms, hybrid BM25+vector search.

Works with **Claude Code**, **Claude Desktop**, **Claude Chat**, **Codex**, **Cursor**, **ChatGPT**, **OpenClaw**, and anything that can call HTTP or MCP.

**Key capabilities (v5.0.0):**
- **Hybrid search** — BM25 + vector + recency + feedback + confidence + graph (6-signal RRF fusion with PPR-scored graph expansion)
- **Graph-aware retrieval** — automatic `related_to` links between memories, PPR-scored multi-hop traversal, +20% retrieval lift on 2-hop benchmarks
- **Temporal reasoning** — `document_at` timestamps, version preservation on UPDATE (archive + supersedes link), `since`/`until` date-range filters
- **Automatic extraction** — LLM-powered AUDN (Add/Update/Delete/Noop) with dry-run, per-fact approval, and auto-linking
- **Operator workbench** — create, edit, merge, bulk actions, extraction trigger, lifecycle panel, conflict resolution
- **Feedback-weighted ranking** — search learns from useful/not_useful signals
- **Lifecycle policies** — per-prefix TTL and confidence-based auto-archive with operator-visible evidence
- **Quality benchmarks** — LongMemEval + MuSiQue eval harnesses with graph and temporal benchmarking
- **Full audit trail** — every mutation tracked, lifecycle timeline in UI, version chains via supersedes links
- **Self-hosted** — your data, your infrastructure, no cloud dependency

Start here:
- [Getting Started (10-15 min)](GETTING_STARTED.md)
- [LLM-assisted setup guide](integrations/QUICKSTART-LLM.md)

---

## API Quick Start

**Option A — no clone required (pre-built image):**

```bash
# 1. Download the compose file and start
curl -fsSL https://raw.githubusercontent.com/divyekant/memories/main/docker-compose.standalone.yml -o docker-compose.yml
docker compose up -d

# 2. Verify
curl http://localhost:8900/health
```

**Option B — clone and build from source:**

```bash
# 1. Clone and build
git clone git@github.com:divyekant/memories.git
cd memories
docker compose up -d

# 2. Verify
curl http://localhost:8900/health
```

**Then add and search memories:**

```bash
# Add a memory
curl -X POST http://localhost:8900/memory/add \
  -H "Content-Type: application/json" \
  -d '{"text": "Always use TypeScript strict mode", "source": "standards.md"}'

# Search
curl -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -d '{"query": "TypeScript config", "k": 3, "hybrid": true}'
```

The service runs at **[http://localhost:8900](http://localhost:8900)**. API docs at [http://localhost:8900/docs](http://localhost:8900/docs). Web UI at [http://localhost:8900/ui](http://localhost:8900/ui).

### Web UI

The built-in UI at `/ui` provides:
- **Dashboard** — memory stats, extraction metrics, server info
- **Memories** — browse, search, filter, and manage memories with list+detail or grid view
  - Create, inline edit, pin/archive with undo, bulk actions (archive/delete/retag/re-source/merge)
  - Extraction trigger with dry-run preview and per-fact approve/reject
  - Tabbed detail panel: Overview (edit) | Lifecycle (origin, confidence, audit timeline, feedback history) | Links
  - Conflict resolution modal (Keep A/B/Merge/Defer with soft archive)
- **Extractions** — extraction job stats and token usage
- **API Keys** — configure authentication
- **Health** — conflicts, problem queries (negative feedback), stale memories (retrieved but never useful), evidence strength badges
- **Settings** — provider config, server info, theme toggle (dark/light/system), export and maintenance

No build step — vanilla JS + CSS served directly from `webui/`.

---

## CLI

The `memories` CLI provides full access to the API from your terminal.

### Install

```bash
pip install -e .
# Or if using the Docker image, the CLI is included
```

### Usage

```bash
# Search
memories search "TypeScript config"

# Add a memory
memories add "Always use strict mode" --source standards

# List memories
memories list --source standards

# Check novelty before adding
memories is-novel "TypeScript strict mode"

# Batch operations
memories batch add memories.jsonl

# Admin
memories admin stats
memories admin health

# Backups
memories backup create
memories backup list

# Full help
memories --help
```

### Export & Import

```bash
# Export all memories
memories export -o backup.jsonl

# Export filtered by source
memories export --source "claude-code/" -o project.jsonl

# Export with date range
memories export --source "proj/" --since 2026-01-01 -o recent.jsonl

# Import (clean migration)
memories import backup.jsonl

# Import with smart dedup
memories import backup.jsonl --strategy smart

# Import with source remapping
memories import backup.jsonl --source-remap "old/=new/"
```

### Agent Integration

The CLI auto-detects when piped and outputs JSON:

```bash
# JSON output for agents (automatic when piped)
memories search "auth" | jq '.data.results[0].text'

# Force JSON in any context
memories --json search "auth"

# Force human-readable when piped
memories --pretty list
```

### Configuration

```bash
# Set server URL
memories config set url http://localhost:8900

# Set API key
memories config set api_key your-key-here

# View resolved config
memories config show
```

Config resolution: CLI flags > `~/.config/memories/config.json` > env vars > defaults.

---

## Architecture

```
AI Client (Claude, Codex, Cursor, ChatGPT, OpenClaw)
    |
    |-- MCP protocol (Claude Code / Desktop / Codex / Cursor)
    |-- REST API (everything else)
    v
MCP Server (mcp-server/index.js)
    |
    v
Memories Service (Docker :8900)
    |-- FastAPI REST API
    |-- Hybrid Search (Memories vector + BM25 keyword, RRF fusion)
    |-- Markdown-aware chunking
    |-- Event Bus (SSE stream + webhook delivery)
    |-- Audit Log (append-only trail)
    |-- Memory Relationships (graph edges between memories)
    |-- Confidence Decay (time-based relevance attenuation)
    |-- Auto-backups
    v
Persistent Storage (data/)
    |-- Qdrant vector store (embeddings + metadata)
    |-- metadata.json (memory text + metadata)
    |-- backups/ (auto, keeps last 10)
```

Detailed docs:
- [API reference](docs/api.md)
- [Architecture deep dive](docs/architecture.md)
- [Engineering decisions and tradeoffs](docs/decisions.md)

---

## Integration Guides

### Claude Code (CLI)

The MCP server gives Claude Code native `memory_search`, `memory_add`, `memory_extract`, `memory_delete`, `memory_delete_batch`, `memory_delete_by_source`, `memory_count`, `memory_list`, `memory_stats`, `memory_is_novel`, `memory_is_useful`, and `memory_conflicts` tools.

**Setup:**

1. Register the MCP server with Claude Code (user scope — available in every project):

```bash
claude mcp add -s user \
  -e MEMORIES_URL=http://localhost:8900 \
  -e MEMORIES_API_KEY=your-api-key-here \
  -- memories npx -y memories-mcp
```

This writes to `~/.claude.json`. **Do not** add MCP servers to `~/.claude/settings.json` or `~/.claude/.mcp.json` — Claude Code CLI does not read MCP config from those files (Claude Desktop uses separate config, see below).

2. Restart Claude Code. The tools are now available in every project.

3. (Optional) Install the **automatic memory layer** (hooks for proactive recall and extraction):

```bash
npx memories-mcp install --claude
```

4. (Optional) Install the **Memories skill** for disciplined memory capture and proactive recall. Clone the repo and symlink:

```bash
mkdir -p ~/.claude/skills/memories
ln -s /path/to/memories/skills/memories ~/.claude/skills/memories
```

The skill teaches the assistant three responsibilities: *when* to search (proactive recall), *when* and *how* to store (hybrid `memory_add` + `memory_extract`), and *when* to maintain (updates, deletes, cleanup via AUDN). It adds ~11% token overhead but improves memory discipline by ~43% in eval benchmarks.

**Usage** (Claude Code will call these automatically when relevant):

- "Search my memory for authentication patterns"
- "Remember that we decided to use Prisma for the ORM"
- "Check if this pattern is already in memory before adding it"
- "Show me all memories from the bug-fixes source"

For a **single project only**, use project scope instead:

```bash
claude mcp add -s project \
  -e MEMORIES_URL=http://localhost:8900 \
  -e MEMORIES_API_KEY=your-api-key-here \
  -- memories npx -y memories-mcp
```

---

### Claude Desktop (Chat / Cowork)

Same MCP server, different config file. Claude Desktop reads MCP config from its own config file — **not** from `~/.claude.json` (which is Claude Code CLI only).

**Setup:**

1. Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "memories": {
      "command": "npx",
      "args": ["-y", "memories-mcp"],
      "env": {
        "MEMORIES_URL": "http://localhost:8900",
        "MEMORIES_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

2. Restart the Claude Desktop app. Memory tools appear in chat and cowork mode.

---

### Claude Chat (Web at claude.ai)

Claude Chat on the web does not support MCP directly. Two options:

**Option A: Remote MCP via Cloudflare Tunnel (recommended)**

If you expose the Memories service via a tunnel (e.g., `memory.yourdomain.com`), you can use Claude's remote MCP connector feature to connect to it. See the [Remote Access](#remote-access) section below.

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

Codex supports MCP natively via `~/.codex/config.toml`.

**Setup:**

1. Add to `~/.codex/config.toml`:

```toml
[mcp_servers.memories]
command = "npx"
args = ["-y", "memories-mcp"]

[mcp_servers.memories.env]
MEMORIES_URL = "http://localhost:8900"
MEMORIES_API_KEY = "your-api-key-here"
```

If your API key is prefix-scoped and does not allow `codex/*`, set hook source overrides in `~/.config/memories/env`:

```bash
MEMORIES_SOURCE_PREFIXES="your-authorized-prefix/{project},learning/{project},wip/{project}"
MEMORIES_EXTRACT_SOURCE="your-authorized-prefix/{project}"
```

2. Restart Codex. The `memory_search`, `memory_add`, `memory_extract`, `memory_delete`, `memory_delete_by_source`, `memory_count`, `memory_list`, `memory_stats`, `memory_is_novel`, and other tools will be available.

**Automatic memory layer for Codex:**

```bash
npx memories-mcp install --codex
```

This configures:
- 5 Codex hooks in `~/.codex/settings.json` (`SessionStart`, `UserPromptSubmit`, `Stop`, `PreCompact`, `SessionEnd`)
- hook scripts in `~/.codex/hooks/memory/`
- MCP server registration in `~/.codex/config.toml`
- default `developer_instructions` (if not already set) to bias `memory_search` usage on each turn
- hook env loading from `~/.config/memories/env` (or `MEMORIES_ENV_FILE`) for `MEMORIES_URL`, `MEMORIES_API_KEY`, and optional source overrides (`MEMORIES_SOURCE_PREFIXES`, `MEMORIES_EXTRACT_SOURCE`)

Requires a running Memories service (`/health` must respond). For scoped API keys, set `MEMORIES_SOURCE_PREFIXES` and `MEMORIES_EXTRACT_SOURCE` so hook reads/writes stay inside authorized prefixes.

Codex uses `~/.codex/settings.json` for lifecycle hooks and `~/.codex/config.toml` for MCP + developer instructions.

**Multi-backend:** Codex uses the same hook scripts as Claude Code, so [multi-backend routing](#multi-backend-routing-optional) works automatically when configured.

**Usage** (Codex will discover the tools automatically):

- "Search memory for how we handle error logging"
- "Store this architecture decision in memory"
- "List all memories from the project-setup source"

---

### Cursor

Cursor supports MCP with the same server.

**Setup:**

1. Add to Cursor MCP config:
- Global: `~/.cursor/mcp.json`
- Project: `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "memories": {
      "command": "npx",
      "args": ["-y", "memories-mcp"],
      "env": {
        "MEMORIES_URL": "http://localhost:8900",
        "MEMORIES_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

2. Restart Cursor.

Cursor also supports the full hook lifecycle via its "Third-party skills" feature. Run the one-liner to install hooks alongside the MCP config:

```bash
npx memories-mcp install --cursor
```

**Multi-backend:** Cursor uses the same hook scripts as Claude Code, so [multi-backend routing](#multi-backend-routing-optional) works automatically when configured.

---

### ChatGPT (Custom GPT)

ChatGPT uses **Custom Actions** (OpenAPI schema) rather than MCP. This requires exposing the Memories service over the internet.

**Prerequisites:** Memories service accessible via HTTPS (see [Remote Access](#remote-access)).

**Setup:**

1. Enable API key auth on the Memories service (set `API_KEY` env var in docker-compose).

2. In ChatGPT, go to **Explore GPTs > Create a GPT > Configure > Actions**.

3. Import this OpenAPI schema (replace `memory.yourdomain.com` with your URL):

```yaml
openapi: 3.0.0
info:
  title: Memories
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
      summary: Browse stored memories with pagination
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
            maximum: 5000
        - name: source
          in: query
          description: Source prefix filter
          schema:
            type: string
      responses:
        '200':
          description: List of memories
    delete:
      operationId: deleteMemoriesByPrefix
      summary: Bulk delete all memories matching a source prefix
      parameters:
        - name: source
          in: query
          required: true
          description: Source prefix to match
          schema:
            type: string
      responses:
        '200':
          description: Delete count

  /memories/count:
    get:
      operationId: countMemories
      summary: Count memories optionally filtered by source prefix
      parameters:
        - name: source
          in: query
          description: Source prefix filter
          schema:
            type: string
      responses:
        '200':
          description: Memory count

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
mkdir -p ~/.openclaw/skills/memories
cp integrations/openclaw-skill.md ~/.openclaw/skills/memories/SKILL.md
```

Or see the full SKILL.md in this repo at `integrations/openclaw-skill.md`.

2. Add the API key to the OpenClaw gateway config so skill exec calls can authenticate:

```bash
openclaw config patch '{"env": {"vars": {"MEMORIES_URL": "http://localhost:8900", "MEMORIES_API_KEY": "your-api-key-here"}}}'
```

Or edit `~/.openclaw/openclaw.json` directly under `env.vars`, then restart the gateway.
The SKILL.md reads `$MEMORIES_API_KEY` from the environment and includes automatic lifecycle guidance for when to recall, extract, and sync memories.

**Key commands available to OpenClaw agents:**

```bash
memory_search_memories "query" [k] [threshold] [hybrid]
memory_add_memories "text" "source" [deduplicate]
memory_is_novel "text" [threshold]
memory_delete_memories <id>
memory_delete_source_memories "pattern"
memory_delete_by_prefix "source_prefix"
memory_count_memories [source_prefix]
memory_list_memories [offset] [limit] [source]
memory_rebuild_index
memory_dedup_memories [dry_run] [threshold]
memory_stats
memory_health
memory_backup [prefix]
memory_restore "backup_name"
```

All functions use `jq` for safe JSON construction and read auth from `$MEMORIES_API_KEY` in the gateway environment (no hardcoded secrets).

**Multi-backend:** Not yet supported for OpenClaw. OpenClaw uses skill-based extraction (not hooks), so multi-backend routing does not apply. This is planned for a future release.

---

## Remote Access

To use Memories from anywhere (Claude Chat web, ChatGPT, mobile, other machines), expose it via a Cloudflare Tunnel or similar.

### Setup with Cloudflare Tunnel

1. **Enable API key auth** in your docker-compose:

```yaml
environment:
  - API_KEY=your-secret-key-here
```

Rebuild and restart: `docker compose build memories && docker compose up -d memories`

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
    "MEMORIES_URL": "https://memory.yourdomain.com",
    "MEMORIES_API_KEY": "your-secret-key-here"
  }
}
```

Now every client — Claude Code on your laptop, Cursor, Claude Desktop on your phone, ChatGPT, OpenClaw — all hit the same memory store running on your Mac mini.

---

## Multi-Backend Routing (Optional)

A single agent session can talk to **multiple Memories instances** simultaneously. This is useful when you want to:

- **Dev + Prod**: search both local dev and remote production, extract to dev only
- **Personal + Shared**: search both personal and team memories, route decisions to shared

Multi-backend is configured via YAML files and is fully backward compatible — no config file means single-backend behavior from environment variables, exactly as before.

**Config locations:**
- Global: `~/.config/memories/backends.yaml`
- Per-project: `.memories/backends.yaml` (should be gitignored)

**Three tiers:**
1. **Scenario-based** — pick a preset (`dev+prod`, `personal+shared`, `single`) and routing is automatic
2. **Scenario + overrides** — start from a scenario, then customize routing rules
3. **DIY** — define backends and routing rules from scratch

**Quick example (dev + prod):**

```yaml
backends:
  dev:
    url: http://localhost:8900
    api_key: ${MEMORIES_DEV_KEY}
    scenario: dev
  prod:
    url: https://memory.yourdomain.com
    api_key: ${MEMORIES_PROD_KEY}
    scenario: prod
```

Config supports env var interpolation (`${VAR_NAME}`) for API keys and URLs.

Multi-backend works automatically with **Claude Code**, **Codex**, and **Cursor** because they all use the same hook scripts. **OpenClaw** does not yet support multi-backend (it uses skill-based extraction, not hooks).

For full setup instructions, config format, and verification steps, see the [Multi-Backend Setup](integrations/QUICKSTART-LLM.md#multi-backend-setup-optional) section in the LLM quickstart guide.

---

## Authentication

Memories supports **multiple API keys** with role-based access control:

- **Three tiers**: `read-only` (search/list), `read-write` (search/list + add/delete), `admin` (full access + key management)
- **Prefix scoping**: keys can be restricted to specific source prefixes for tenant isolation
- **Key management**: create, list, update, and revoke keys via `POST/GET/PATCH/DELETE /api/keys` or the Web UI (admin-only)
- **Backward compatible**: the existing `API_KEY` env var still works as an implicit admin key

See the [multi-auth design doc](docs/plans/2026-03-05-multi-auth-design.md) for details.

---

## API Reference

All endpoints accept/return JSON. Auth via `X-API-Key` header.

### Search

```
POST /search
{"query": "...", "k": 5, "hybrid": true, "threshold": 0.3,
 "vector_weight": 0.7, "recency_weight": 0.1, "recency_half_life_days": 30,
 "source_prefix": "team/project/"}

POST /search/batch
{"queries": [{"query": "...", "k": 5}, {"query": "...", "hybrid": true}]}
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
DELETE /memories?source=<prefix>              # Bulk delete by source prefix; returns {"count": N}
POST /memory/delete-batch     {"ids": [1, 2, 3]}
POST /memory/delete-by-source  {"source_pattern": "credentials"}
POST /memory/delete-by-prefix {"source_prefix": "team/project/"}
```

### Get

```
GET  /memory/{id}
POST /memory/get-batch {"ids": [1, 2, 3]}
```

### Upsert / Patch

```
POST  /memory/upsert
{"text":"...", "source":"team/project/file", "key":"entity-1", "metadata": {"owner":"team"}}

POST  /memory/upsert-batch
{"memories":[{"text":"...", "source":"...", "key":"..."}]}

PATCH /memory/{id}
{"text":"optional", "source":"optional", "metadata_patch":{"tag":"v2"}}
```

### Novelty Check

```
POST /memory/is-novel
{"text": "...", "threshold": 0.88}
```

### Browse

```
GET /memories?offset=0&limit=20&source=filter   # limit up to 5000; source uses prefix matching
GET /memories/count?source=<prefix>             # returns {"count": N}
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
GET  /health/ready
GET  /metrics
POST /maintenance/embedder/reload
```

### Backups

```
GET  /backups
POST /backup?prefix=manual
POST /restore          {"backup_name": "manual_20260213_120000"}
```

### Extraction

```
POST /memory/extract    {"messages": "...", "source": "proj", "context": "stop", "debug": true}  # 202 queued
GET  /memory/extract/{job_id}
GET  /extract/status
```

### Memory Relationships

```
POST   /memory/{id}/link          {"target_id": N, "type": "related"}
GET    /memory/{id}/links
DELETE /memory/{id}/link/{link_id}
```

### Conflict Detection

```
GET /memory/conflicts?limit=10
```

### Events (SSE + Webhooks)

```
GET    /events/stream              # SSE stream (auth-filtered)
POST   /webhooks                   # Register webhook
GET    /webhooks
DELETE /webhooks/{id}
```

### Search Explainability

```
POST /search/explain               # Admin-only scoring breakdown
```

### Quality & Metrics

```
GET  /metrics/search-quality?period=7d
POST /search/feedback              # Submit relevance feedback
GET  /metrics/quality-summary?period=7d
GET  /metrics/failures?type=retrieval&limit=10
```

### Maintenance

```
POST /maintenance/reembed          # Migrate embedding model
POST /maintenance/compact          # Find similar clusters (dry-run)
POST /maintenance/consolidate      # LLM-powered merge
```

### Audit

```
GET /audit/log?limit=50&source=prefix
```

Full OpenAPI schema at http://localhost:8900/docs.

### Future API Candidates (Swarm Scale)

- `POST /memory/compare` (pairwise conflict scoring for concurrent agent writes)
- `POST /memory/resolve-conflicts` (policy-driven merge: latest/manual/model)
- `POST /memory/lock` + `DELETE /memory/lock/{key}` (explicit lock reservation APIs)
- `POST /memory/events` + `GET /memory/events/stream` (change feed for agent synchronization)
- `POST /search/stream` (progressive search responses for very large corpora)
- `POST /memory/ttl` (time-bound memories with auto-expiry)

---

## MCP Tools Reference

When connected via MCP (Claude Code, Claude Desktop, Codex, Cursor), these tools are available:

| Tool | Description |
|------|-------------|
| `memory_search` | Hybrid search (BM25 + vector). Default mode. |
| `memory_add` | Store a memory with auto-dedup. |
| `memory_extract` | LLM-based extraction with AUDN (Add/Update/Delete/Noop/Conflict) from conversation text. |
| `memory_delete` | Delete by ID. |
| `memory_delete_batch` | Delete multiple IDs in one operation. |
| `memory_delete_by_source` | Bulk delete all memories matching a source prefix. |
| `memory_count` | Count memories, optionally filtered by source prefix. |
| `memory_list` | Browse with pagination and source prefix filter. |
| `memory_stats` | Index stats (count, model, last updated). |
| `memory_is_novel` | Check if text is already known. |
| `memory_is_useful` | Submit search feedback (positive/negative). |
| `memory_conflicts` | List memories with unresolved conflicts. |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `/data` | Persistent storage path |
| `WORKSPACE_DIR` | `/workspace` | Read-only workspace for index rebuilds |
| `API_KEY` | (empty) | API key for auth. Empty = no auth. |
| `EMBED_PROVIDER` | `onnx` | Embedding provider: `onnx` (local) or `openai` (BYOK) |
| `EMBED_MODEL` | (unset) | Provider-specific embedding model override |
| `MODEL_NAME` | `all-MiniLM-L6-v2` | Default ONNX model used when `EMBED_PROVIDER=onnx` and `EMBED_MODEL` is unset |
| `MODEL_CACHE_DIR` | (unset; Docker image sets `/data/model-cache`) | Optional writable cache path for downloaded model files |
| `PRELOADED_MODEL_CACHE_DIR` | (unset; Docker image sets `/opt/model-cache`) | Optional read-only cache to seed `MODEL_CACHE_DIR` when empty |
| `MAX_BACKUPS` | `10` | Number of backups to keep |
| `MAX_EXTRACT_MESSAGE_CHARS` | `120000` | Max characters accepted by `/memory/extract` |
| `EXTRACT_MAX_INFLIGHT` | `2` | Max concurrent extraction jobs |
| `MEMORY_TRIM_ENABLED` | `true` | Run post-extract GC/allocator trim |
| `MEMORY_TRIM_COOLDOWN_SEC` | `15` | Minimum seconds between trim attempts |
| `MEMORY_TRIM_PERIODIC_SEC` | `5` | Periodic trim probe interval (seconds). Set `0` to disable background trim loop. |
| `EMBEDDER_AUTO_RELOAD_ENABLED` | `false` | Enable periodic auto-reload of in-process embedder runtime |
| `EMBEDDER_AUTO_RELOAD_RSS_KB_THRESHOLD` | `1200000` | RSS threshold (KB) required before auto-reload decisions |
| `EMBEDDER_AUTO_RELOAD_CHECK_SEC` | `15` | Seconds between auto-reload checks |
| `EMBEDDER_AUTO_RELOAD_HIGH_STREAK` | `3` | Consecutive high-RSS checks required before trigger |
| `EMBEDDER_AUTO_RELOAD_MIN_INTERVAL_SEC` | `900` | Cooldown between reload attempts |
| `EMBEDDER_AUTO_RELOAD_WINDOW_SEC` | `3600` | Rolling window size for reload cap |
| `EMBEDDER_AUTO_RELOAD_MAX_PER_WINDOW` | `2` | Max reloads allowed per rolling window |
| `EMBEDDER_AUTO_RELOAD_MAX_ACTIVE_REQUESTS` | `2` | Skip reload when active HTTP requests exceed this |
| `EMBEDDER_AUTO_RELOAD_MAX_QUEUE_DEPTH` | `0` | Skip reload when extract queue depth exceeds this |
| `METRICS_LATENCY_SAMPLES` | `200` | Per-route latency sample window for `/metrics` percentiles |
| `METRICS_TREND_SAMPLES` | `120` | Memory trend sample window exposed by `/metrics` |
| `AUDIT_LOG` | (none) | Path to audit log file |
| `CONFIDENCE_DECAY_HALF_LIFE_DAYS` | `90` | Half-life for confidence decay |
| `PORT` | `8000` | Internal service port |

### Docker Compose guardrails

Default compose files now include:
- `mem_limit: ${MEMORIES_MEM_LIMIT:-3g}` to bound container memory growth
- `MALLOC_ARENA_MAX=2` to reduce glibc arena fragmentation in multithreaded workloads
- `MALLOC_TRIM_THRESHOLD_=131072` and `MALLOC_MMAP_THRESHOLD_=131072` to encourage earlier allocator release
- extraction env passthrough (`EXTRACT_PROVIDER`, `EXTRACT_MODEL`, provider keys/URL) so deploys keep extraction enabled when set in shell or `.env`
- embedder auto-reload env passthrough with anti-loop defaults (`EMBEDDER_AUTO_RELOAD_*`)

### MCP Server Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORIES_URL` | `http://localhost:8900` | Memories service URL |
| `MEMORIES_API_KEY` | (empty) | API key if auth is enabled |

---

## Automatic Memory Layer

Memories supports automatic retrieval/extraction, with client-specific behavior:
- Claude Code: full 10-hook lifecycle (session start, each prompt, after response, pre-compact, post-compact, subagent stop, tool use, file write guard, config change, session end)
- Cursor: same 10-hook lifecycle via Third-party skills (loads from `~/.claude/settings.json`)
- Codex: 5-hook lifecycle via `~/.codex/settings.json` + MCP/developer instructions in `~/.codex/config.toml`
- OpenClaw: skill-driven retrieval/extraction flow

### Claude Code / Cursor Hook Lifecycle

| Event | Hook | What happens |
|-------|------|-------------|
| Session start | `memory-recall.sh` | Loads project-scoped memories, hydrates MEMORY.md, checks service health |
| Every prompt | `memory-query.sh` | Retrieves relevant memories with transcript context |
| After response | `memory-extract.sh` | Extracts facts via AUDN |
| Before compaction | `memory-flush.sh` | Aggressive extraction before context loss |
| After compaction | `memory-rehydrate.sh` | Re-injects memories using compact summary |
| Subagent stop | `memory-subagent-capture.sh` | Captures decisions from Plan/Explore agents |
| Tool use observed | `memory-observe.sh` | Logs MCP tool invocations (observability) |
| File write attempt | `memory-guard.sh` | Blocks direct MEMORY.md writes |
| Config changed | `memory-config-guard.sh` | Warns if hooks removed from settings |
| Session end | `memory-commit.sh` | Final extraction pass |

**Cursor compatibility note:** Cursor sends `workspace_roots[]` (not `cwd`) and `transcript_path` (not inline `messages`) in hook payloads. The hook scripts handle both formats automatically — no separate configuration needed.

### Codex Lifecycle

| Event | Mechanism | What happens |
|-------|-----------|--------------|
| Session start | `settings.json` -> `memory-recall.sh` | Loads project-scoped memories and recall guidance for the session |
| Every prompt | `settings.json` -> `memory-query.sh` | Retrieves relevant memories using transcript context for short follow-ups |
| After response | `settings.json` -> `memory-extract.sh` | Extracts facts via AUDN |
| Before compaction | `settings.json` -> `memory-flush.sh` | Aggressive extraction before context loss |
| Session end | `settings.json` -> `memory-commit.sh` | Final extraction pass |
| On new turns | MCP tools + developer instructions | Encourages focused `memory_search` before implementation-heavy responses |

Codex uses `~/.codex/settings.json` for these hooks and `~/.codex/config.toml` for MCP + developer instructions.

### Quick setup

**Prerequisites:**
- Running Memories service (`curl -s http://localhost:8900/health`)

**One-command auto-detect installer (recommended):**

```bash
npx memories-mcp install
```

This auto-detects installed tools and configures:
- Claude Code hooks (`~/.claude/settings.json`) + MCP
- Codex hooks (`~/.codex/settings.json`) + MCP/developer instructions (`~/.codex/config.toml`)
- Cursor MCP config (`~/.cursor/mcp.json`) + hooks
- OpenClaw skill (`~/.openclaw/skills/memories/SKILL.md`)

The installer writes runtime config to `~/.config/memories/env` for `MEMORIES_URL`, optional `MEMORIES_API_KEY`, and optional source overrides (`MEMORIES_SOURCE_PREFIXES` / `MEMORIES_EXTRACT_SOURCE`).

Claude/Cursor hooks also support `MEMORIES_SOURCE_PREFIXES` in `~/.config/memories/env` — a comma-separated list of source prefix templates (default: `claude-code/{project},learning/{project},wip/{project}`).

**Target specific tools:**

```bash
npx memories-mcp install --claude
npx memories-mcp install --cursor
npx memories-mcp install --codex
npx memories-mcp install --openclaw
```

**Uninstall:**

```bash
npx memories-mcp uninstall
```

**LLM-assisted setup:** Feed [`integrations/QUICKSTART-LLM.md`](integrations/QUICKSTART-LLM.md) to your AI assistant and it will configure everything automatically.

### Extraction providers

| Provider | Cost | AUDN | Speed |
|----------|------|------|-------|
| Anthropic (recommended) | ~$0.001/turn | Full (Add/Update/Delete/Noop/Conflict) | ~1-2s |
| OpenAI | ~$0.001/turn | Full | ~1-2s |
| ChatGPT Subscription | Free (uses your subscription) | Full | ~1-2s |
| Ollama | Free | Full | ~5s |
| Skip | Free | None | N/A |

Extraction is optional. Without it, retrieval still works.

By default, automatic write hooks do not store new memories when extraction is disabled.
If you want a degraded automatic-write mode, set `EXTRACT_FALLBACK_ADD=true` to enable a strict
heuristic + novelty-check fallback that writes at most a small number of high-confidence facts
when extraction is disabled or the configured provider fails at runtime (for example rate limits/timeouts).

### AUDN in plain English

AUDN is the memory decision loop:

- `ADD`: store a genuinely new fact
- `UPDATE`: refine an existing memory that is close but outdated/incomplete
- `DELETE`: remove a stale/conflicting memory
- `NOOP`: ignore non-useful or duplicate facts
- `CONFLICT`: flag when two memories directly contradict each other

Why it matters:
- cleaner memory store over time (less duplicate/stale data)
- better retrieval quality in later sessions
- less "memory drift" when decisions change

### Cost vs quality

- **Anthropic/OpenAI extraction**: small usage cost (typically around ~$0.001/turn), full AUDN quality.
- **ChatGPT Subscription extraction**: no additional API cost (uses your existing subscription), full AUDN quality.
- **Ollama extraction**: no API cost, full AUDN quality (with JSON format constraint).
- **Retrieval only** (`EXTRACT_PROVIDER` unset): no extraction model cost.
- **Optional fallback writes** (`EXTRACT_FALLBACK_ADD=true`): add-only, heuristic extraction path (no AUDN update/delete) used when extraction is disabled or provider calls fail at runtime.

### Cost control knobs

Use these to keep extraction spend bounded:
- `MAX_EXTRACT_MESSAGE_CHARS`: hard cap on transcript size per request
- `EXTRACT_MAX_FACTS`: limits facts considered from each extraction
- `EXTRACT_MAX_FACT_CHARS`: caps per-fact payload size
- `EXTRACT_SIMILAR_TEXT_CHARS` and `EXTRACT_SIMILAR_PER_FACT`: limit context passed into AUDN

### Async extraction API

`POST /memory/extract` is async-first. It enqueues work and returns `202` with a `job_id`.
Poll `GET /memory/extract/{job_id}` for `queued`, `running`, `completed`, or `failed`.
If the queue is full, the API returns `429` with a `Retry-After` header.
When extraction is disabled and `EXTRACT_FALLBACK_ADD=true`, `/memory/extract` runs an immediate
fallback add path and still returns a job object. When extraction is configured but fails at runtime,
the queued worker also falls back to add-only mode when `EXTRACT_FALLBACK_ADD=true`.

### Docker image targets (core / extract)

The Dockerfile publishes two runtime targets:

- `core` (default): search/add/list endpoints, no Anthropic/OpenAI SDKs
- `extract`: includes Anthropic/OpenAI SDKs for `/memory/extract`

Build both images directly:

```bash
docker build --target core -t memories:core .
docker build --target extract -t memories:extract .
```

Use compose with either target:

```bash
# Default (core target)
docker compose up -d --build memories

# Extraction-ready target
MEMORIES_IMAGE_TARGET=extract docker compose up -d --build memories
```

By default, images do **not** bake model weights. On first run, the service downloads them into
`MODEL_CACHE_DIR` (`/data/model-cache` in Docker), so later restarts reuse the volume cache.

If you want a fully preloaded image (faster first boot, larger pull), set `PRELOAD_MODEL=true`:

```bash
docker build --target core --build-arg PRELOAD_MODEL=true -t memories:core .
docker build --target extract --build-arg PRELOAD_MODEL=true -t memories:extract .
```

Ollama uses HTTP directly and does not need the extra SDKs, so `core` is enough for Ollama extraction.

### Extraction environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTRACT_PROVIDER` | (none) | `anthropic`, `openai`, `chatgpt-subscription`, `ollama`, or empty to disable |
| `EXTRACT_MODEL` | (per provider) | Model override |
| `ANTHROPIC_API_KEY` | (none) | Required for Anthropic provider (standard key or `sk-ant-oat01-` OAuth token) |
| `OPENAI_API_KEY` | (none) | Required for OpenAI provider |
| `CHATGPT_REFRESH_TOKEN` | (none) | Required for ChatGPT Subscription provider (from `python -m memories auth chatgpt`) |
| `CHATGPT_CLIENT_ID` | (none) | Required for ChatGPT Subscription provider |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server URL (on Linux, use `http://localhost:11434`) |
| `EXTRACT_FALLBACK_ADD` | `false` | Enable add-only fallback writes when extraction is disabled or provider calls fail at runtime |
| `EXTRACT_FALLBACK_MAX_FACTS` | `1` | Max fallback facts to store per extract request |
| `EXTRACT_FALLBACK_MIN_FACT_CHARS` | `24` | Minimum candidate fact length for fallback |
| `EXTRACT_FALLBACK_MAX_FACT_CHARS` | `280` | Maximum candidate fact length for fallback |
| `EXTRACT_FALLBACK_NOVELTY_THRESHOLD` | `0.88` | Novelty threshold used by fallback add mode |
| `EXTRACT_QUEUE_MAX` | `EXTRACT_MAX_INFLIGHT * 20` | Maximum queued extraction jobs before backpressure (`429`) |
| `EXTRACT_JOB_RETENTION_SEC` | `300` | How long completed/failed extraction jobs stay queryable |
| `EXTRACT_JOBS_MAX` | `200` | Hard cap on stored extraction job records (finished jobs evicted first) |
| `EXTRACT_MAX_FACTS` | `30` | Maximum facts kept from a single extraction |
| `EXTRACT_MAX_FACT_CHARS` | `500` | Max length per extracted fact |
| `EXTRACT_SIMILAR_TEXT_CHARS` | `280` | Max similar-memory text length passed into AUDN |
| `EXTRACT_SIMILAR_PER_FACT` | `5` | Similar memories included per fact during AUDN |

### Burst memory behavior

Extraction can create short-lived allocation spikes (large transcripts, large LLM JSON payloads, concurrent requests).

Mitigations built in:
- `/memory/extract` request size limit (`MAX_EXTRACT_MESSAGE_CHARS`)
- bounded in-flight extraction (`EXTRACT_MAX_INFLIGHT`)
- post-extract + periodic memory reclamation (`MEMORY_TRIM_ENABLED`, `MEMORY_TRIM_COOLDOWN_SEC`, `MEMORY_TRIM_PERIODIC_SEC`)
- optional auto-reload controller for the embedder runtime (`EMBEDDER_AUTO_RELOAD_*`)
- bounded AUDN payload sizes (`EXTRACT_MAX_FACTS`, `EXTRACT_MAX_FACT_CHARS`, `EXTRACT_SIMILAR_TEXT_CHARS`)

Observability:
- `/metrics` includes `embedder_reload.auto` and `embedder_reload.manual` counters/state
- manual reload endpoint: `POST /maintenance/embedder/reload`

Reference benchmark: `docs/benchmarks/2026-02-17-memory-reclamation.md`

### Uninstall

```bash
./integrations/claude-code/install.sh --uninstall
```

Then optionally remove `MEMORIES_*` from `~/.config/memories/env` and `EXTRACT_*` from repo `.env`.

---

## Backup & Recovery

Memories has three layers of backup protection:

### 1. Auto-backup (built-in)

The service automatically saves a snapshot after every write operation. The 10 most recent auto-backups are kept in the Docker volume under `data/backups/`.

```bash
# List backups
curl -H "X-API-Key: $MEMORIES_API_KEY" http://localhost:8900/backups

# Create manual backup
curl -X POST -H "X-API-Key: $MEMORIES_API_KEY" http://localhost:8900/backup?prefix=manual

# Restore from backup
curl -X POST -H "X-API-Key: $MEMORIES_API_KEY" http://localhost:8900/restore \
  -H "Content-Type: application/json" \
  -d '{"backup_name": "manual_20260214_120000"}'
```

### 2. Scheduled local snapshots (cron)

A cron job creates timestamped copies of the Memories index every 30 minutes. Snapshots are stored outside the Docker volume (default: `~/backups/memories/`) with 30-day retention.

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
| `MEMORIES_URL` | `http://localhost:8900` | Service URL |
| `MEMORIES_API_KEY` | (empty) | API key if auth is enabled |
| `MEMORIES_DATA_DIR` | `./data` (relative to repo) | Docker volume data path |
| `BACKUP_DIR` | `~/backups/memories` | Where to store snapshots |
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
| `GDRIVE_FOLDER_NAME` | `memories-backups` | Folder name on Drive |
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
ENABLE_CLOUD_SYNC=true docker compose up -d --build memories
```

See [CLOUD_SYNC_README.md](CLOUD_SYNC_README.md) for configuration details.

---

## Project Structure

```
memories/
  app.py                  # FastAPI REST API
  memory_engine.py        # Memories engine (search, chunking, BM25, backups)
  onnx_embedder.py        # ONNX Runtime embedder (replaces PyTorch)
  llm_provider.py         # LLM provider abstraction (Anthropic/OpenAI/ChatGPT Subscription/Ollama)
  llm_extract.py          # Extraction pipeline with AUDN
  chatgpt_oauth.py        # ChatGPT OAuth2+PKCE token exchange helpers
  key_store.py            # SQLite-backed API key store (SHA-256 hashing)
  event_bus.py            # Event-driven architecture (SSE, webhooks)
  audit_log.py            # Append-only audit trail
  qdrant_store.py         # Qdrant vector store adapter
  usage_tracker.py        # Search quality and extraction metrics
  auth_context.py         # Request-scoped role and prefix enforcement
  memories_auth.py        # CLI auth tool (python -m memories auth chatgpt/status)
  __main__.py             # Entry point for python -m memories
  Dockerfile              # Multi-stage Docker build (core/extract targets)
  pyproject.toml          # Python dependencies (uv)
  uv.lock                 # Locked dependency resolutions
  docker-compose.snippet.yml
  docs/
    api.md                # Complete REST API reference
    architecture.md       # System architecture and runtime flows
    decisions.md          # Key design decisions and tradeoffs
    deployment.md         # Self-hosted deployment guide
    api-coverage.md       # API/MCP/CLI coverage matrix
    benchmarks/           # Reproducible benchmark notes
  mcp-server/
    index.js              # MCP server (wraps REST API as tools)
    package.json
  scripts/
    backup.sh             # Cron backup (local snapshots)
    backup-gdrive.sh      # Optional Google Drive upload
    install-cron.sh       # Cron job installer
  webui/
    index.html            # Memory browser entry page (/ui)
    styles.css            # UI styling
    app.js                # Browser-side pagination/filter logic
  integrations/
    claude-code/
      install.sh          # Auto-detect installer (Claude/Codex/Cursor/OpenClaw)
      hooks/              # Claude Code 10-hook scripts + hooks.json
        _lib.sh               # Shared hook utilities (logging, health check)
        memory-rehydrate.sh   # PostCompact rehydration hook
        memory-observe.sh     # PostToolUse observability hook
        memory-guard.sh       # PreToolUse MEMORY.md write guard
        memory-subagent-capture.sh  # SubagentStop extraction hook
        memory-config-guard.sh      # ConfigChange settings watchdog
        response-hints.json   # Response hint patterns
    codex/
      memory-codex-notify.sh # Legacy Codex notify hook (compatibility/manual fallback)
    claude-code.md        # Claude Code guide
    openclaw-skill.md     # OpenClaw SKILL.md
    QUICKSTART-LLM.md     # LLM-friendly setup guide
  tests/
    test_memory_engine.py # Memory engine tests
    test_llm_provider.py  # LLM provider tests (incl. ChatGPT Subscription)
    test_chatgpt_oauth.py # OAuth PKCE + token exchange tests
    test_memories_auth.py # CLI auth tool tests
    test_llm_extract.py   # Extraction pipeline tests
    test_extract_api.py   # API endpoint tests
    test_web_ui.py        # Web UI route/static tests
  skills/
    memories/
      SKILL.md            # Claude Code skill for memory discipline
  eval/
    benchmarks.py         # Benchmark suite runner
    scenarios/benchmark/  # 6 benchmark scenarios
    __main__.py           # CLI entrypoint (python -m eval)
    models.py             # Pydantic data models (Scenario, EvalReport, etc.)
    loader.py             # YAML scenario loader
    scorer.py             # Deterministic rubric scorer
    judge.py              # LLM-as-judge for non-deterministic rubrics
    memories_client.py    # Memories API client for eval runner
    cc_executor.py        # Claude Code executor with project isolation
    runner.py             # Orchestrates with/without-memory runs
    reporter.py           # JSON reporter and summary formatter
    config.yaml           # Default eval configuration
    scenarios/            # YAML test scenarios by category
    results/              # JSON eval reports (.gitignored)
    tests/                # 82 tests covering all eval components
  data/                   # .gitignored — persistent index + backups
```

---

## Efficacy Eval

Memories includes a built-in eval harness that measures how much Memories improves AI assistant performance. It runs controlled A/B tests: each scenario executes via Claude Code (`claude -p`) both with and without Memories, then scores the outputs against deterministic rubrics.

```bash
# Start the isolated eval stack in OrbStack/Docker
docker compose -f docker-compose.eval.yml up -d --build

# Verify the eval instance is healthy (separate from the main service on :8900)
curl http://localhost:8901/health

# Run all scenarios (via wrapper script)
./eval/run.sh

# Or directly via Python
python -m eval

# Run a specific category
python -m eval --category coding

# Run a single scenario
python -m eval --scenario coding-001 -v
```

The eval defaults target `http://localhost:8901`, which is the isolated instance from [`docker-compose.eval.yml`](/Users/dk/projects/memories/docker-compose.eval.yml). `./eval/run.sh` intentionally ignores your normal `MEMORIES_URL` from `~/.config/memories/env` so it does not accidentally hit the main service. Override the wrapper with `EVAL_MEMORIES_URL=http://host:port ./eval/run.sh ...`, or use `MEMORIES_URL=http://host:port python -m eval ...` for direct Python runs.

### Results

| Category | With Memory | Without Memory | Delta |
|---|---|---|---|
| **Coding** | 1.00 | 0.00 | **+1.00** |
| **Recall** | 1.00 | 0.20 | **+0.80** |
| **Compounding** | 1.00 | 0.27 | **+0.73** |
| **Overall** | **1.00** | **0.14** | **+0.86** |

11 scenarios across 3 categories. Each scenario uses fictional project context ("Voltis") with arbitrary, non-derivable facts — values like `hvt_client`, `vtctl deploy-gate`, `VTX_LEGACY_DSN`, port `7443`, and `73%` that Claude cannot guess from naming patterns or training data.

### What it measures
- **Coding tasks** (4 scenarios) — Does the agent apply project-specific tools and conventions?
- **Knowledge recall** (4 scenarios) — Can the agent recall exact config values and decisions?
- **Compounding value** (3 scenarios) — Can the agent synthesize multiple memories to diagnose problems?

### How it works
1. Purges stale auto-memory from prior eval runs (`~/.claude/projects/cc_eval*`)
2. Clears eval memories, creates an isolated temp project (no CLAUDE.md, no `.claude/`)
3. Runs the prompt **without** Memories via `claude -p --strict-mcp-config` (empty MCP) → scores against rubrics
4. Seeds scenario memories, runs the prompt **with** Memories via `claude -p --strict-mcp-config` (Memories MCP only) → scores again
5. Computes **efficacy delta** = score_with - score_without
6. Aggregates across categories with configurable weights

### Isolation strategy
- `--strict-mcp-config` ensures Claude loads **only** the MCP config provided (or none), ignoring global settings
- Fresh temp directories per run — no CLAUDE.md, no `.claude/`, no conversation history
- Auto-memory cleanup removes `~/.claude/projects/cc_eval*` dirs at startup and after each run
- Scenario memories cleared before each run via Memories API

Results are saved as JSON in `eval/results/` and printed as a human-readable summary.

See the [design doc](docs/plans/2026-03-03-efficacy-design.md) for full details.

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

---

## Development

```bash
# Install dependencies
uv sync                              # core only
uv sync --extra extract              # with extraction (Anthropic SDK)
uv sync --extra cloud                # with cloud sync (boto3)

# Run tests
uv run pytest -q

# Local dev server
uv run uvicorn app:app --reload

# Docker
docker build --target core -t memories:core .
docker build --target extract -t memories:extract .
```

When changing memory/index behavior: add or update tests, validate backup/restore still works, validate extraction if touching extraction paths, update README and/or `docs/architecture.md`.

---

## Roadmap

- [ ] Auto-rebuild on file changes (watch mode)
- [ ] Multi-index support (different projects)
- [ ] Memory tagging system
- [ ] Search filters by date/type (source filter exists)
- [ ] Scheduled index rebuilds via cron

---

## Release Checklist

- [ ] No hardcoded credentials in docs/examples
- [ ] Public docs avoid product-specific assumptions unless the file is intentionally integration-specific
- [ ] Benchmarks describe workload profile and caveats
- [ ] Versioned behavior changes documented in README
