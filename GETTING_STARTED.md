# Getting Started

This is the fastest path to a working Memories setup with optional automatic extraction.

## 1) Start the service

### Fastest path: `npx memories-mcp init`

```bash
npx memories-mcp@latest init
```

This single command:
- auto-detects Claude Code, Codex, and Cursor on your machine (or restrict with `--claude` / `--codex` / `--cursor` / `--generic`)
- prompts for the backend URL (default `http://localhost:8900`) and API key, or pass `--url` / `--api-key`
- checks backend health, and if it's unreachable, offers (interactively) to bootstrap it with Docker — writes `~/.config/memories/docker-compose.yml` and runs `docker compose up -d` for you
- wires hooks, skills, and MCP config for each detected target (see step 4 for exactly what gets written)

Non-interactive: add `--yes` to accept all defaults and skip every prompt — including the Docker bootstrap offer, so with `--yes` an unreachable backend is logged and skipped rather than auto-provisioned; run the manual backend setup below first, or omit `--yes` to get the interactive offer. Add `--dry-run` to print the plan and write nothing. Windows has no bash, so hooks aren't supported there — `init` falls back to the generic target and prints an MCP snippet to configure by hand.

> `memories-mcp` has not been published to npm yet as of this release — `npx memories-mcp@latest init` will work once the first publish ships. Until then, use the manual backend setup below plus `npm --prefix mcp-server install` and the generic MCP snippet in step 4, or the still-working (but deprecated) `install.sh` path.

### Manual backend setup (repo checkout, advanced)

If you're already working from a repo checkout, or need options `init`'s auto-bootstrap doesn't offer (e.g. a multi-node vector cluster), start the backend directly:

```bash
git clone https://github.com/divyekant/memories.git
cd memories
docker compose up -d
```

(`docker-compose.yml` brings up Qdrant + the Memories service. `docker-compose.snippet.yml` is NOT standalone — it is a snippet to merge into an existing compose file.)

#### Optional: Start with a vector cluster (N nodes)

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

If you ran `npx memories-mcp@latest init` in step 1, this is already done for Claude Code, Codex, and Cursor — skip ahead to step 5. `init` wires:
- Claude Code hooks (`~/.claude/hooks/memory`), the `memories` and `memories-setup` skills (`~/.claude/skills/`), the MCP entry, and a marked block in `~/.claude/CLAUDE.md`
- Codex hooks (`~/.codex/hooks/memory`), permissions, and marked blocks in `~/.codex/config.toml` (MCP server + developer instructions)
- Cursor's `~/.cursor/mcp.json` MCP entry (plus the shared `~/.claude` pieces above, since Cursor reads them via "Third-party skills") — you still need to flip **Settings → Features → Third-party skills → ON** and restart Cursor
- other commands: `npx memories-mcp@latest doctor` (status + backend health + version check), `npx memories-mcp@latest update` (re-wire after upgrading), `npx memories-mcp@latest uninstall`

### Other MCP clients (generic)

Any MCP-capable client can be wired manually with:

```json
{
  "mcpServers": {
    "memories": {
      "command": "npx",
      "args": ["-y", "memories-mcp"],
      "env": { "MEMORIES_URL": "http://localhost:8900", "MEMORIES_API_KEY": "" }
    }
  }
}
```

`npx memories-mcp@latest init --generic` prints this same snippet with your configured URL/key filled in.

For guided LLM setup, use:
- [`integrations/QUICKSTART-LLM.md`](integrations/QUICKSTART-LLM.md)

### Manual: `install.sh` (deprecated)

`install.sh` still works this release but is superseded by `npx memories-mcp init` above; it will be removed in the next release. It additionally covers OpenCode and OpenClaw, which the npm package does not yet wire.

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
- Codex hooks (`~/.codex/hooks.json`) + permissions (`~/.codex/settings.json`) + MCP/developer instructions (`~/.codex/config.toml`)
- OpenClaw skill (`~/.openclaw/skills/memories/SKILL.md`)

Cursor is supported via MCP config (`~/.cursor/mcp.json` or `.cursor/mcp.json`) and is currently manual.
If you're working inside this repository, you can also install the repo-local Codex plugin from `.agents/plugins/marketplace.json` and run `$memories:setup`, which bootstraps the same `./integrations/claude-code/install.sh --codex` flow from the checkout root.

Note for Codex: source defaults are `codex/{project},learning/{project},wip/{project}`
for retrieval and `codex/{project}` for extraction. For scoped API keys, override them with
`MEMORIES_SOURCE_PREFIXES` and `MEMORIES_EXTRACT_SOURCE` in `~/.config/memories/env`.

The installer writes:
- hook runtime vars to `~/.config/memories/env` (`MEMORIES_URL`, optional `MEMORIES_API_KEY`)
- extraction provider vars to repo `.env` (`EXTRACT_PROVIDER`, provider keys/URL)

### Advanced: Multi-backend routing

If you need to search or extract across multiple Memories instances (e.g., local dev + remote prod, or personal + shared team memories), you can configure multi-backend routing via `~/.config/memories/backends.yaml`. This is optional and fully backward compatible — without it, hooks use a single backend from env vars.

The installer offers an interactive multi-backend setup step, or see the [Multi-Backend Setup](integrations/QUICKSTART-LLM.md#multi-backend-setup-optional) guide for manual configuration. Works with Claude Code, Codex, and Cursor. OpenClaw is not yet supported.

## 5) Explore the hook system

The installer wires the full automatic memory lifecycle for Claude Code / Cursor and a reduced native hook set for Codex.

### Claude Code / Cursor

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

### Codex

| Hook | Event | Purpose |
|------|-------|---------|
| `memory-recall.sh` | Session start | Load project memories + recall guidance |
| `memory-query.sh` | Each prompt | Search memories with transcript context |
| `memory-extract.sh` | Stop | Extract facts via beefier sampling that compensates for missing compaction/session-end hooks |
| `memory-observe.sh` | Memory MCP tool use | Log Memories MCP tool invocations |
| `memory-guard.sh` | File write | Block direct `MEMORY.md` writes |

Codex stores those hooks in `~/.codex/hooks.json`, keeps tool permissions in `~/.codex/settings.json`, and registers the MCP server plus developer instructions in `~/.codex/config.toml`.

All hooks are configurable via env vars in `~/.config/memories/env`. See `docs/deployment.md` for details.

## 6) New MCP tools

In addition to the core tools, these are now available:

| Tool | Description |
|------|-------------|
| `memory_update` | Update/correct a memory by ID — the old version is archived with a supersedes link |
| `memory_get` | Fetch one memory by ID (full text after a compact search) |
| `memory_timeline` | Date-ordered view of matching memories for temporal questions |
| `memory_evidence` | Evidence packet: current answer, supporting/conflicting memories, confidence |
| `memory_is_useful` | Submit search feedback (positive/negative) |
| `memory_conflicts` | List memories with unresolved conflicts (paginated) |
| `memory_missed` | Report a memory that should have been recalled but wasn't |
| `memory_deferred` | List deferred/incomplete work captured in memories |

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

`npx memories-mcp init` already installs this (and a companion `memories-setup` skill) to `~/.claude/skills/` for the `claude-code` target — skip this step if you used it. This section is for the manual/repo-checkout path.

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

## 14) Claude web (claude.ai) connector

Want Memories inside claude.ai (browser or mobile), not just local MCP clients? `mcp-server/remote/` adds an OAuth 2.1 + Streamable HTTP front door in front of your `memories` service.

```bash
# 1. Generate credentials
node -e "import('./mcp-server/remote/oauth.mjs').then(m => console.log(m.hashPassword(process.argv[1])))" 'your-password'
openssl rand -hex 32

# 2. Set REMOTE_MCP_ISSUER, REMOTE_MCP_PASSWORD_HASH, REMOTE_MCP_TOKEN_SECRET
#    in .env, then start it (profile-gated — plain `docker compose up` skips it)
docker compose --profile remote-mcp up -d
```

Then in claude.ai: Settings → Connectors → Add custom connector → `https://<your-domain>/mcp`, and log in with the password from step 1. See the [Remote Access](README.md#remote-access) section in the README for the full walkthrough (tunnel setup, env vars, standalone-deployment notes).

## 15) Where to go next

- Full architecture: [`docs/architecture.md`](docs/architecture.md)
- Decisions/tradeoffs: [`docs/decisions.md`](docs/decisions.md)
- Full API docs (running service): `http://localhost:8900/docs`
