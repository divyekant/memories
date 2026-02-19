---
name: memories
version: 2.1.0
description: Memories-based semantic memory search via Docker service. Fast (<20ms), local, with hybrid BM25+vector search, auto-dedup, and automatic backups. Includes OpenClaw QMD bridge for unified memory_search integration.
metadata:
  clawdbot:
    emoji: "🧠"
    requires:
      commands:
        - curl
        - jq
---

# Memories

Local semantic memory using Memories vector search + BM25 hybrid retrieval (Docker service).

## Features

- **Fast**: <20ms semantic searches
- **Hybrid**: BM25 keyword + Memories vector search (RRF fusion)
- **Local**: 100% on-device, no external APIs
- **Secure**: Localhost-only Docker service
- **Backed up**: Automatic backups before operations
- **Independent**: Survives OpenClaw upgrades
- **CRUD**: Full create, read, update, delete support
- **QMD Bridge**: Sync Memories into OpenClaw's native `memory_search` via QMD

## Prerequisites

- Docker service running: `docker ps | grep memories`
- If not running: `cd /path/to/memories && docker compose up -d memories`
- `MEMORIES_API_KEY` env var must be set (loaded from shell profile)

---

## OpenClaw QMD Bridge Integration

### The Problem

OpenClaw's built-in `memory_search` tool only queries its native backends (built-in SQLite or QMD). Memories runs as a separate Docker service with its own vector index. Without a bridge, Memories content is invisible to `memory_search` — the agent must explicitly call Memories API functions to access it.

### The Solution

A sync script exports all Memories content as grouped markdown files into a directory that QMD indexes as a collection. This makes Memories content discoverable through OpenClaw's native `memory_search` alongside workspace files and session transcripts.

For writes, the agent dual-writes: storing insights in both workspace markdown files (for QMD) and the Memories API (for the vector index). This keeps both systems in sync.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  OpenClaw memory_search (unified query)             │
│  ├── memory files (MEMORY.md + memory/*.md)         │  ← QMD collection: memory
│  ├── session transcripts                            │  ← QMD collection: sessions-<agentId>
│  └── Memories content (synced)                      │  ← QMD collection: memories-<agentId>
│           ↑                                         │
│     sync script (daily)                             │
│           ↑                                         │
│     Memories API (localhost:8900)                    │
└─────────────────────────────────────────────────────┘

Writes:
  Agent ──→ memory/YYYY-MM-DD.md  (QMD indexes directly)
       └──→ POST /memory/add      (Memories API, synced to QMD via script)
```

### Setup: QMD Bridge Sync Script

Create this script in your workspace (e.g. `scripts/sync-memories-to-qmd.sh`):

```bash
#!/bin/bash
# Sync Memories → QMD collection for unified memory_search
# Run daily via heartbeat or cron to keep QMD in sync with Memories.
set -euo pipefail

MEMORIES_API="${MEMORIES_API:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
AGENT_ID="${OPENCLAW_AGENT_ID:-jack}"
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
EXPORT_DIR="$STATE_DIR/agents/$AGENT_ID/qmd/memories-export"
COLLECTION="memories-$AGENT_ID"

export XDG_CONFIG_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-config"
export XDG_CACHE_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-cache"

# Auth header (optional — only needed if MEMORIES_API_KEY is set)
AUTH_HEADER=""
if [[ -n "$MEMORIES_API_KEY" ]]; then
  AUTH_HEADER="-H \"X-API-Key: $MEMORIES_API_KEY\""
fi

# Check API is up
if ! curl -sf "$MEMORIES_API/health" >/dev/null 2>&1; then
  echo "❌ Memories API not responding at $MEMORIES_API"
  exit 1
fi

mkdir -p "$EXPORT_DIR"

TOTAL=$(curl -sf "$MEMORIES_API/health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_memories',0))")
echo "📦 Exporting $TOTAL memories from Memories..."

# Export all memories and write as grouped markdown files
python3 - "$MEMORIES_API" "$EXPORT_DIR" "$MEMORIES_API_KEY" << 'PYEOF'
import json, os, re, sys, urllib.request

api = sys.argv[1]
export_dir = sys.argv[2]
api_key = sys.argv[3] if len(sys.argv) > 3 else ""

# Clean old exports
for f in os.listdir(export_dir):
    if f.endswith('.md'):
        os.remove(os.path.join(export_dir, f))

# Fetch all memories (paginated)
memories = []
offset = 0
while True:
    url = f"{api}/memories?offset={offset}&limit=100"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("X-API-Key", api_key)
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    batch = data.get("memories", [])
    memories.extend(batch)
    if len(batch) < 100:
        break
    offset += 100

# Group by top-level source folder
folders = {}
for m in memories:
    source = m.get('source', 'uncategorized') or 'uncategorized'
    folder = source.split('/')[0] if '/' in source else source
    folder = re.sub(r'[^\w\-]', '_', folder)
    folders.setdefault(folder, []).append(m)

# Write one markdown file per folder
count = 0
for folder, mems in folders.items():
    filepath = os.path.join(export_dir, f"{folder}.md")
    with open(filepath, 'w') as f:
        f.write(f"# Memories: {folder}\n\n")
        f.write(f"> Auto-synced from Memories project\n")
        f.write(f"> {len(mems)} entries\n\n")
        for m in mems:
            text = m.get('text', '').strip()
            source = m.get('source', '')
            ts = (m.get('timestamp') or '')[:10]
            f.write(f"## [{source}] ({ts})\n\n{text}\n\n---\n\n")
            count += 1

print(f"✅ Exported {count} memories to {len(folders)} files")
PYEOF

# Create or update QMD collection
if qmd collection list 2>/dev/null | grep -q "$COLLECTION"; then
  echo "🔄 Updating QMD collection '$COLLECTION'..."
  qmd update -c "$COLLECTION" 2>&1 | tail -3
else
  echo "📁 Creating QMD collection '$COLLECTION'..."
  qmd collection add --name "$COLLECTION" "$EXPORT_DIR" '**/*.md' 2>&1
  qmd update -c "$COLLECTION" 2>&1 | tail -3
fi

# Embed new chunks (required for vector search)
echo "🧠 Embedding new chunks..."
qmd embed 2>&1 | tail -3

echo "✅ Sync complete — Memories are now searchable via memory_search"
```

**Make it executable:**
```bash
chmod +x scripts/sync-memories-to-qmd.sh
```

**Run the initial sync:**
```bash
bash scripts/sync-memories-to-qmd.sh
```

### Setup: QMD Session Collection Fix

OpenClaw expects session transcript collections named `sessions-<agentId>` (e.g. `sessions-jack`). If QMD created the collection with a different name, `memory_search` will fail to find session transcripts and fall back to the built-in search (which doesn't cover sessions).

**Check if sessions are indexed:**
```bash
openclaw memory status
# Look for: sessions · 0/N files · 0 chunks
# If 0 chunks but N files exist, the collection name is wrong.
```

**Fix the naming:**
```bash
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
AGENT_ID="jack"  # Change to your agent ID
export XDG_CONFIG_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-config"
export XDG_CACHE_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-cache"

# Remove old collection and recreate with correct name
qmd collection remove sessions 2>/dev/null
qmd collection remove "sessions-$AGENT_ID" 2>/dev/null
qmd collection add --name "sessions-$AGENT_ID" \
  "$STATE_DIR/agents/$AGENT_ID/qmd/sessions" '**/*.md'
```

### Setup: SQLite WAL Lock Recovery

If QMD searches fail with `SQLITE_BUSY_RECOVERY`, a stale WAL lock file is blocking access. This can happen after a crash or ungraceful shutdown.

**Fix:**
```bash
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
AGENT_ID="jack"
sqlite3 "$STATE_DIR/agents/$AGENT_ID/qmd/xdg-cache/qmd/index.sqlite" \
  "PRAGMA wal_checkpoint(TRUNCATE);"
```

### Scheduling the Sync

**Option A: OpenClaw Heartbeat (recommended)**

Add to your `HEARTBEAT.md`:
```markdown
### Memories → QMD Sync (daily)
- Run `bash ~/your-workspace/scripts/sync-memories-to-qmd.sh`
- Keeps `memory_search` results unified across all memory sources
```

**Option B: OpenClaw Cron Job**

```bash
# Via OpenClaw cron — runs daily at 6 AM
openclaw cron add --name "Memories QMD Sync" \
  --schedule '{"kind":"cron","expr":"0 6 * * *"}' \
  --payload '{"kind":"systemEvent","text":"Run: bash ~/your-workspace/scripts/sync-memories-to-qmd.sh"}' \
  --session-target main
```

**Option C: System Crontab**

```bash
crontab -e
# Add:
0 6 * * * /path/to/workspace/scripts/sync-memories-to-qmd.sh >> /tmp/memories-sync.log 2>&1
```

### Dual-Write Pattern (Recommended)

To keep Memories and QMD in sync without waiting for the daily sync, the agent should dual-write: store significant insights in both systems simultaneously.

**When the agent learns something worth remembering:**

1. **Write to daily markdown file** (QMD picks this up automatically):
   ```
   Append to memory/YYYY-MM-DD.md
   ```

2. **Write to Memories API** (immediate vector search availability):
   ```bash
   memory_add_memories "The insight or learning" "source/topic"
   ```

3. **The daily sync script** acts as a safety net — if either write was missed, the sync reconciles by exporting all Memories content to QMD.

**When to dual-write:**
- Decisions with rationale
- User preferences (explicit or observed)
- Bug root causes and fixes
- Architectural patterns
- Lessons learned from failures

**When NOT to dual-write (markdown only is fine):**
- Routine task completions
- Temporary context
- Raw session logs

---

## Commands

### Search Memories (Hybrid)

```bash
function memory_search_memories() {
    local query="$1"
    local k="${2:-5}"
    local threshold="${3:-0.0}"
    local hybrid="${4:-true}"

    local payload
    payload=$(jq -n \
        --arg q "$query" \
        --argjson k "$k" \
        --argjson t "$threshold" \
        --argjson h "$hybrid" \
        '{query: $q, k: $k, threshold: $t, hybrid: $h}')

    curl -s -X POST http://localhost:8900/search \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload" \
    | jq -r '.results[] | "\(.similarity // .rrf_score | tonumber | . * 100 | floor)% | \(.source): \(.text[:200])"'
}

# Usage
memory_search_memories "database preferences"
memory_search_memories "database preferences" 3       # Top 3 results
memory_search_memories "database preferences" 5 0.7   # Min 70% similarity
memory_search_memories "exact keyword match" 5 0 true # Hybrid mode (default)
```

### Add New Memory

```bash
function memory_add_memories() {
    local text="$1"
    local source="$2"
    local dedup="${3:-true}"

    local payload
    payload=$(jq -n \
        --arg t "$text" \
        --arg s "$source" \
        --argjson d "$dedup" \
        '{text: $t, source: $s, deduplicate: $d}')

    curl -s -X POST http://localhost:8900/memory/add \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload" \
    | jq -r '.message'
}

# Usage
memory_add_memories "Prefer Brave Search API" "technical.md:180"
memory_add_memories "New fact" "source.md" true  # auto-dedup (default)
```

### Check if Novel

```bash
function memory_is_novel() {
    local text="$1"
    local threshold="${2:-0.88}"

    local payload
    payload=$(jq -n \
        --arg t "$text" \
        --argjson th "$threshold" \
        '{text: $t, threshold: $th}')

    local result
    result=$(curl -s -X POST http://localhost:8900/memory/is-novel \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload")

    local is_novel
    is_novel=$(echo "$result" | jq -r '.is_novel')

    if [[ "$is_novel" == "true" ]]; then
        echo "✅ Novel (no similar memories found)"
        return 0
    else
        echo "❌ Not novel - similar memory exists:"
        echo "$result" | jq -r '.most_similar | "\(.similarity | tonumber | . * 100 | floor)% | \(.source): \(.text[:200])"'
        return 1
    fi
}

# Usage
memory_is_novel "New preference to check"
```

### Delete Memory

```bash
function memory_delete_memories() {
    local id="$1"
    curl -s -X DELETE "http://localhost:8900/memory/$id" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
    | jq -r '.deleted_text // .detail'
}

# Usage
memory_delete_memories 42
```

### Delete by Source

```bash
function memory_delete_source_memories() {
    local pattern="$1"

    local payload
    payload=$(jq -n --arg p "$pattern" '{source_pattern: $p}')

    curl -s -X POST http://localhost:8900/memory/delete-by-source \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload" \
    | jq -r '"Deleted \(.deleted_count) memories"'
}

# Usage
memory_delete_source_memories "credentials"
memory_delete_source_memories "old-file.md"
```

### Browse Memories

```bash
function memory_list_memories() {
    local offset="${1:-0}"
    local limit="${2:-20}"
    local source="${3:-}"

    local url="http://localhost:8900/memories?offset=$offset&limit=$limit"
    if [[ -n "$source" ]]; then
        url="${url}&source=$source"
    fi

    curl -s "$url" -H "X-API-Key: $MEMORIES_API_KEY" | jq -r '.memories[] | "[\(.id)] \(.source): \(.text[:120])"'
}

# Usage
memory_list_memories             # First 20
memory_list_memories 20 10       # Next 10
memory_list_memories 0 50 "lang" # Filter by source
```

### Rebuild Index

```bash
function memory_rebuild_index() {
    echo "🔄 Rebuilding Memories index from workspace files..."

    curl -s -X POST http://localhost:8900/index/build \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d '{}' \
    | jq -r '"✅ \(.message)\n   Files: \(.files_processed)\n   Memories: \(.memories_added)\n   Backup: \(.backup_location)"'
}

# Usage
memory_rebuild_index
```

### Deduplicate

```bash
function memory_dedup_memories() {
    local dry_run="${1:-true}"
    local threshold="${2:-0.90}"

    local payload
    payload=$(jq -n \
        --argjson d "$dry_run" \
        --argjson t "$threshold" \
        '{dry_run: $d, threshold: $t}')

    curl -s -X POST http://localhost:8900/memory/deduplicate \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload" \
    | jq '.'
}

# Usage
memory_dedup_memories true       # Dry run (preview)
memory_dedup_memories false      # Actually remove duplicates
memory_dedup_memories true 0.85  # Lower threshold = more aggressive
```

### View Stats

```bash
function memory_stats() {
    curl -s http://localhost:8900/stats -H "X-API-Key: $MEMORIES_API_KEY" | jq '
        "📊 Memories Stats",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Total memories: \(.total_memories)",
        "Dimensions: \(.dimension)",
        "Model: \(.model)",
        "Index size: \(.index_size_bytes / 1024 | floor)KB",
        "Backups: \(.backup_count)",
        "Last updated: \(.last_updated // "never")"
    ' -r
}

# Usage
memory_stats
```

### List Backups

```bash
function memory_backups() {
    curl -s http://localhost:8900/backups -H "X-API-Key: $MEMORIES_API_KEY" \
    | jq -r '.backups[] | "\(.name)"'
}

# Usage
memory_backups
```

### Create Manual Backup

```bash
function memory_backup() {
    local prefix="${1:-manual}"

    curl -s -X POST "http://localhost:8900/backup?prefix=$prefix" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
    | jq -r '"✅ \(.message)\n   Location: \(.backup_path)"'
}

# Usage
memory_backup
memory_backup "before_upgrade"
```

### Restore from Backup

```bash
function memory_restore() {
    local backup_name="$1"

    local payload
    payload=$(jq -n --arg b "$backup_name" '{backup_name: $b}')

    curl -s -X POST http://localhost:8900/restore \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload" \
    | jq -r '"✅ \(.message)\n   Memories: \(.total_memories)"'
}

# Usage
memory_backups                           # List available
memory_restore "manual_20260213_120000"  # Restore specific backup
```

### Health Check

```bash
function memory_health() {
    curl -s http://localhost:8900/health -H "X-API-Key: $MEMORIES_API_KEY" | jq '
        if .status == "ok" then
            "✅ Memories: HEALTHY (v\(.version // "?"))\n   Memories: \(.total_memories)\n   Model: \(.model)"
        else
            "❌ Memories: UNHEALTHY"
        end
    ' -r
}

# Usage
memory_health
```

### Recall Project Context (Auto)

Call this at the start of any task to load relevant project memories:

```bash
function memory_recall_memories() {
    local project="${1:-$(basename "$PWD")}"
    local k="${2:-8}"

    local payload
    payload=$(jq -n \
        --arg q "project $project conventions decisions patterns" \
        --argjson k "$k" \
        '{query: $q, k: $k, hybrid: true}')

    local results
    results=$(curl -s -X POST http://localhost:8900/search \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload" \
    | jq -r '[.results[] | select(.similarity > 0.3)] | .[0:8] | map("- \(.text)") | join("\n")')

    if [[ -n "$results" && "$results" != "null" ]]; then
        echo "## Relevant Memories"
        echo ""
        echo "$results"
    else
        echo "No relevant memories found for project: $project"
    fi
}

# Usage — call at the start of every task
memory_recall_memories
memory_recall_memories "my-project" 10
```

### Extract Facts from Conversation (Auto)

Call this after completing significant tasks to store new learnings:

```bash
function memory_extract_memories() {
    local messages="$1"
    local source="${2:-openclaw/$(basename "$PWD")}"
    local context="${3:-stop}"

    local payload
    payload=$(jq -n \
        --arg m "$messages" \
        --arg s "$source" \
        --arg c "$context" \
        '{messages: $m, source: $s, context: $c}')

    curl -s -X POST http://localhost:8900/memory/extract \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MEMORIES_API_KEY" \
        -d "$payload" \
    | jq '.'
}

# Usage — call after completing tasks
memory_extract_memories "User: use drizzle\nAssistant: Good choice, switching from Prisma"
memory_extract_memories "conversation text" "openclaw/my-project" "session_end"
```

## Typical Workflows

### Daily Use (Automatic)

At the start of every task, recall relevant project context:
```bash
memory_recall_memories
```

When you need to search for something specific:
```bash
memory_search_memories "your query" 5
```

### After Task Completion

Extract and store new learnings from the conversation:
```bash
memory_extract_memories "summary of conversation or key decisions"
```

### Add Important Fact

When storing new information (auto-dedup enabled by default):
```bash
memory_add_memories "New preference or fact" "MEMORY.md:100"
```

### Weekly Maintenance

Rebuild index from updated files and deduplicate:
```bash
memory_rebuild_index
memory_dedup_memories true   # Preview
memory_dedup_memories false  # Execute
```

### Before OpenClaw Upgrade

Create safety backup:
```bash
memory_backup "before_openclaw_upgrade_$(date +%Y%m%d)"
```

## Integration with Self-Learning

During heartbeats, I can:

1. **Extract new learnings** using `memory_extract_memories` (preferred — sends conversation to the extract endpoint which handles fact extraction, novelty checking, and storage in one call)
2. **Manual alternative**: Check novelty with `memory_is_novel`, then add if novel with `memory_add_memories` (auto-dedup enabled)
3. **Recall context** at task start with `memory_recall_memories`
4. **Auto-backup** handled by service
5. **Sync to QMD** via `sync-memories-to-qmd.sh` (daily, keeps `memory_search` unified)

## Troubleshooting

### Service not responding
```bash
docker ps | grep memories
docker logs -f memories
cd /path/to/memories && docker compose restart memories
```

### Empty search results
```bash
memory_stats
memory_rebuild_index
```

### Restore from backup
```bash
memory_backups
memory_restore "BACKUP_NAME"
```

### QMD bridge not finding Memories content
```bash
# Re-run the sync
bash scripts/sync-memories-to-qmd.sh

# Verify the collection exists
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
AGENT_ID="jack"
export XDG_CONFIG_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-config"
export XDG_CACHE_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-cache"
qmd collection list | grep memories

# Check OpenClaw sees it
openclaw memory status
```

### QMD SQLite lock errors (SQLITE_BUSY_RECOVERY)
```bash
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
AGENT_ID="jack"
sqlite3 "$STATE_DIR/agents/$AGENT_ID/qmd/xdg-cache/qmd/index.sqlite" \
  "PRAGMA wal_checkpoint(TRUNCATE);"
```

### Session transcripts not searchable (0 chunks)
```bash
# Fix collection naming — OpenClaw expects sessions-<agentId>
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
AGENT_ID="jack"
export XDG_CONFIG_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-config"
export XDG_CACHE_HOME="$STATE_DIR/agents/$AGENT_ID/qmd/xdg-cache"

qmd collection remove sessions 2>/dev/null
qmd collection remove "sessions-$AGENT_ID" 2>/dev/null
qmd collection add --name "sessions-$AGENT_ID" \
  "$STATE_DIR/agents/$AGENT_ID/qmd/sessions" '**/*.md'
```

## Notes

- Service runs independently of OpenClaw
- Survives OpenClaw restarts/upgrades
- Backups created automatically before destructive ops
- Read-only access to workspace files
- All data persists in the bind-mounted `./data/` directory (configured in `docker-compose.yml`)
- API docs available at http://localhost:8900/docs
- QMD bridge sync is one-way (Memories → QMD); writes to QMD markdown don't flow back to Memories
