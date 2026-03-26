# Getting Started

This is the fastest path to a working Memories setup with optional automatic extraction.

## 1) Start the service

```bash
git clone git@github.com:divyekant/memories.git
cd memories
docker compose -f docker-compose.snippet.yml up -d
```

### Optional: Start with a vector cluster (N nodes)

```bash
python scripts/render_cluster_compose.py \
  --nodes 3 \
  --output docker-compose.cluster.generated.yml

docker compose \
  -f docker-compose.yml \
  -f docker-compose.cluster.generated.yml \
  up -d
```

## 2) Verify API and UI

```bash
curl -s http://localhost:8900/health | jq .
```

Then open `http://localhost:8900/ui` in your browser.

If `/ui` shows 404, rebuild to pick up current web assets:

```bash
docker compose down
docker compose up -d --build memories
```

## 3) Choose your memory mode

| Mode | Cost | What you get |
|------|------|--------------|
| Retrieval only (`EXTRACT_PROVIDER` unset) | Free | Recalls existing memories, does not learn new ones automatically |
| Retrieval + fallback add (`EXTRACT_FALLBACK_ADD=true`) | Free | Recalls existing memories and stores a tiny set of high-confidence facts (add-only, no AUDN updates/deletes) when extraction is disabled or provider calls fail at runtime |
| Ollama extraction (`EXTRACT_PROVIDER=ollama`) | Free | Full AUDN (`ADD/UPDATE/DELETE/NOOP`) via JSON-constrained local models |
| ChatGPT Subscription (`EXTRACT_PROVIDER=chatgpt-subscription`) | Free (uses your subscription) | Full AUDN — requires one-time OAuth setup: `python -m memories auth chatgpt` |
| Anthropic/OpenAI extraction | Small API cost (~$0.001/turn typical) | Full AUDN (`ADD/UPDATE/DELETE/NOOP`) and better long-term memory quality |

## 4) Install integrations (recommended)

Prerequisites for installer mode:
- `jq` and `curl` installed
- running Memories service (`/health` responds)

If you plan to install Codex integration, install MCP server deps first:

```bash
cd mcp-server
npm install
cd ..
```

```bash
./integrations/claude-code/install.sh --auto
```

This auto-detects and configures:
- Claude Code hooks (`~/.claude/settings.json`)
- Codex hooks (`~/.codex/settings.json`) + MCP/developer instructions (`~/.codex/config.toml`)
- OpenClaw skill (`~/.openclaw/skills/memories/SKILL.md`)

Cursor is supported via MCP config (`~/.cursor/mcp.json` or `.cursor/mcp.json`) and is currently manual.

Note for Codex: source defaults are `codex/{project},learning/{project},wip/{project}`
for retrieval and `codex/{project}` for extraction. For scoped API keys, override them with
`MEMORIES_SOURCE_PREFIXES` and `MEMORIES_EXTRACT_SOURCE` in `~/.config/memories/env`.

The installer writes:
- hook runtime vars to `~/.config/memories/env` (`MEMORIES_URL`, optional `MEMORIES_API_KEY`)
- extraction provider vars to repo `.env` (`EXTRACT_PROVIDER`, provider keys/URL)

For guided LLM setup, use:
- [`integrations/QUICKSTART-LLM.md`](integrations/QUICKSTART-LLM.md)

### Advanced: Multi-backend routing

If you need to search or extract across multiple Memories instances (e.g., local dev + remote prod, or personal + shared team memories), you can configure multi-backend routing via `~/.config/memories/backends.yaml`. This is optional and fully backward compatible — without it, hooks use a single backend from env vars.

The installer offers an interactive multi-backend setup step, or see the [Multi-Backend Setup](integrations/QUICKSTART-LLM.md#multi-backend-setup-optional) guide for manual configuration. Works with Claude Code, Codex, and Cursor. OpenClaw is not yet supported.

## 5) Explore the hook system

The installer configures 10 hooks for Claude Code:

| Hook | Event | Purpose |
|------|-------|---------|
| `memory-recall.sh` | Session start | Load project memories + health check |
| `memory-query.sh` | Each prompt | Search memories with transcript context |
| `memory-extract.sh` | After response | Extract facts (AUDN pipeline) |
| `memory-flush.sh` | Before compaction | Aggressive extraction before context loss |
| `memory-rehydrate.sh` | After compaction | Re-inject memories using compact summary |
| `memory-subagent-capture.sh` | Subagent stop | Capture Plan/Explore agent decisions |
| `memory-observe.sh` | Tool use | Log MCP tool invocations |
| `memory-guard.sh` | File write | Block direct MEMORY.md writes |
| `memory-config-guard.sh` | Config change | Warn if hooks removed |
| `memory-commit.sh` | Session end | Final extraction pass |

All hooks are configurable via env vars in `~/.config/memories/env`. See `docs/deployment.md` for details.

## 6) New MCP tools

In addition to the core tools, these are now available:

| Tool | Description |
|------|-------------|
| `memory_is_useful` | Submit search feedback (positive/negative) |
| `memory_conflicts` | List memories with unresolved conflicts |

## 7) Monitor quality

Check hook logs:
```bash
cat ~/.config/memories/hook.log | tail -20
```

Check tool usage:
```bash
cat ~/.config/memories/tool-usage.log | tail -20
```

Quality metrics (admin):
```bash
curl -s http://localhost:8900/metrics/quality-summary \
  -H "X-API-Key: $API_KEY" | jq .
```

## 8) Verify extraction status (if enabled)

```bash
curl -s http://localhost:8900/extract/status | jq .
```

Expected:
- `enabled: true` when extraction is configured
- selected `provider` and `model`

### Optional: verify embedder auto-reload guardrails

```bash
curl -s http://localhost:8900/metrics | jq '.embedder_reload'
```

Expected (when enabled in compose env):
- `enabled: true`
- policy values under `policy`
- runtime counters under `auto` and `manual`

## 9) First memory smoke test

```bash
curl -X POST http://localhost:8900/memory/add \
  -H "Content-Type: application/json" \
  -d '{"text":"Team prefers strict TypeScript mode","source":"getting-started"}'

curl -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -d '{"query":"TypeScript preferences","k":3,"hybrid":true}'
```

## 10) Install the Memories skill (Claude Code, optional)

The Memories skill teaches Claude *when* to capture context and *when* to proactively search — the judgment layer that makes memory usage disciplined rather than ad-hoc.

```bash
ln -s /path/to/memories/skills/memories ~/.claude/skills/memories
```

**What it adds (three responsibilities):**
- **Read**: Proactively searches memories before asking clarifying questions or entering a domain with prior context
- **Write**: Hybrid approach — uses `memory_add` for simple facts, `memory_extract` for lifecycle operations (decision changes, deferred work completion, contradictions)
- **Maintain**: Handles updates and deletes via AUDN, plus explicit cleanup with `memory_delete` / `memory_delete_by_source`
- Enforces consistent source prefixes (`claude-code/{project}` or `codex/{project}`, plus `learning/{project}` and `wip/{project}`)

The skill does NOT replace hooks (passive baseline) or CC's built-in auto-memory. It complements them with active judgment about what's worth remembering and when to update or remove stale memories.

## 11) Route memory away from MEMORY.md (Claude Code, recommended)

Claude Code has a built-in auto-memory that writes to `MEMORY.md` files. With Memories MCP running, this creates duplicate stores and bloated files. Add this to your **global** `~/.claude/CLAUDE.md` to redirect:

```markdown
## Memory Routing

This environment has Memories MCP for persistent, searchable memory.
Keep MEMORY.md for quick-reference only (ports, credentials, commands).
Store decisions, learnings, deferred work, and architecture context
via Memories MCP tools (memory_add, memory_extract) — NOT in MEMORY.md.
```

This tells Claude Code to prefer Memories MCP for durable facts and keep `MEMORY.md` minimal.

## 12) Graph-aware search (v5.0.0)

Memories automatically creates `related_to` links between similar memories during extraction. Search uses these links to surface related context via graph expansion.

**MCP `memory_search`** has graph expansion enabled by default (`graph_weight=0.1`). No configuration needed — it just works.

**HTTP `/search`** has graph disabled by default (`graph_weight=0.0`). Enable it:
```json
{"query": "database choice", "hybrid": true, "graph_weight": 0.1}
```

## 13) Temporal search (v5.0.0)

Filter memories by date range using `since` and `until`:

```json
{"query": "decisions", "hybrid": true, "since": "2026-03-01T00:00:00Z", "until": "2026-03-31T23:59:59Z"}
```

Set `document_at` when adding memories to provide a stable content date:
```json
{"text": "Chose Redis for caching", "source": "decisions", "metadata": {"document_at": "2026-03-15T10:30:00Z"}}
```

Version history: UPDATE now archives the old memory instead of deleting it. Search with `include_archived=true` to see previous versions.

## 14) Where to go next

- Full architecture: [`docs/architecture.md`](docs/architecture.md)
- Decisions/tradeoffs: [`docs/decisions.md`](docs/decisions.md)
- Full API docs (running service): `http://localhost:8900/docs`
