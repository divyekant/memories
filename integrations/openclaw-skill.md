---
name: faiss-memory
version: 2.0.0
description: FAISS-based semantic memory search via Docker service. Fast (<20ms), local, with hybrid BM25+vector search, auto-dedup, and automatic backups.
metadata:
  clawdbot:
    emoji: "🧠"
    requires:
      commands:
        - curl
        - jq
---

# FAISS Memory

Local semantic memory using FAISS vector search + BM25 hybrid retrieval (Docker service).

## Features

- **Fast**: <20ms semantic searches
- **Hybrid**: BM25 keyword + FAISS vector search (RRF fusion)
- **Local**: 100% on-device, no external APIs
- **Secure**: Localhost-only Docker service
- **Backed up**: Automatic backups before operations
- **Independent**: Survives OpenClaw upgrades
- **CRUD**: Full create, read, update, delete support

## Prerequisites

- Docker service running: `docker ps | grep faiss-memory`
- If not running: `cd ~/web && docker-compose up -d faiss-memory`

## Commands

### Search Memories (Hybrid)

```bash
function memory_search_faiss() {
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
        -d "$payload" \
    | jq -r '.results[] | "\(.similarity // .rrf_score | tonumber | . * 100 | floor)% | \(.source): \(.text[:200])"'
}

# Usage
memory_search_faiss "database preferences"
memory_search_faiss "database preferences" 3       # Top 3 results
memory_search_faiss "database preferences" 5 0.7   # Min 70% similarity
memory_search_faiss "exact keyword match" 5 0 true # Hybrid mode (default)
```

### Add New Memory

```bash
function memory_add_faiss() {
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
        -d "$payload" \
    | jq -r '.message'
}

# Usage
memory_add_faiss "Prefer Brave Search API" "technical.md:180"
memory_add_faiss "New fact" "source.md" true  # auto-dedup (default)
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
function memory_delete_faiss() {
    local id="$1"
    curl -s -X DELETE "http://localhost:8900/memory/$id" \
    | jq -r '.deleted_text // .detail'
}

# Usage
memory_delete_faiss 42
```

### Delete by Source

```bash
function memory_delete_source_faiss() {
    local pattern="$1"

    local payload
    payload=$(jq -n --arg p "$pattern" '{source_pattern: $p}')

    curl -s -X POST http://localhost:8900/memory/delete-by-source \
        -H "Content-Type: application/json" \
        -d "$payload" \
    | jq -r '"Deleted \(.deleted_count) memories"'
}

# Usage
memory_delete_source_faiss "credentials"
memory_delete_source_faiss "old-file.md"
```

### Browse Memories

```bash
function memory_list_faiss() {
    local offset="${1:-0}"
    local limit="${2:-20}"
    local source="${3:-}"

    local url="http://localhost:8900/memories?offset=$offset&limit=$limit"
    if [[ -n "$source" ]]; then
        url="${url}&source=$source"
    fi

    curl -s "$url" | jq -r '.memories[] | "[\(.id)] \(.source): \(.text[:120])"'
}

# Usage
memory_list_faiss             # First 20
memory_list_faiss 20 10       # Next 10
memory_list_faiss 0 50 "lang" # Filter by source
```

### Rebuild Index

```bash
function memory_rebuild_index() {
    echo "🔄 Rebuilding FAISS index from workspace files..."

    curl -s -X POST http://localhost:8900/index/build \
        -H "Content-Type: application/json" \
        -d '{}' \
    | jq -r '"✅ \(.message)\n   Files: \(.files_processed)\n   Memories: \(.memories_added)\n   Backup: \(.backup_location)"'
}

# Usage
memory_rebuild_index
```

### Deduplicate

```bash
function memory_dedup_faiss() {
    local dry_run="${1:-true}"
    local threshold="${2:-0.90}"

    local payload
    payload=$(jq -n \
        --argjson d "$dry_run" \
        --argjson t "$threshold" \
        '{dry_run: $d, threshold: $t}')

    curl -s -X POST http://localhost:8900/memory/deduplicate \
        -H "Content-Type: application/json" \
        -d "$payload" \
    | jq '.'
}

# Usage
memory_dedup_faiss true       # Dry run (preview)
memory_dedup_faiss false      # Actually remove duplicates
memory_dedup_faiss true 0.85  # Lower threshold = more aggressive
```

### View Stats

```bash
function memory_stats() {
    curl -s http://localhost:8900/stats | jq '
        "📊 FAISS Memory Stats",
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
    curl -s http://localhost:8900/backups \
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
    curl -s http://localhost:8900/health | jq '
        if .status == "ok" then
            "✅ FAISS Memory: HEALTHY (v\(.version // "?"))\n   Memories: \(.total_memories)\n   Model: \(.model)"
        else
            "❌ FAISS Memory: UNHEALTHY"
        end
    ' -r
}

# Usage
memory_health
```

## Typical Workflows

### Daily Use (Automatic)

When I (Jack) need to search memories:
```bash
memory_search_faiss "your query" 5
```

### Add Important Fact

When storing new information (auto-dedup enabled by default):
```bash
memory_add_faiss "New preference or fact" "MEMORY.md:100"
```

### Weekly Maintenance

Rebuild index from updated files and deduplicate:
```bash
memory_rebuild_index
memory_dedup_faiss true   # Preview
memory_dedup_faiss false  # Execute
```

### Before OpenClaw Upgrade

Create safety backup:
```bash
memory_backup "before_openclaw_upgrade_$(date +%Y%m%d)"
```

## Integration with Self-Learning

During heartbeats, I can:

1. **Extract new learnings** from conversation
2. **Check novelty** with `memory_is_novel`
3. **Add if novel** with `memory_add_faiss` (auto-dedup enabled)
4. **Auto-backup** handled by service

## Troubleshooting

### Service not responding
```bash
docker ps | grep faiss-memory
docker logs -f faiss-memory
cd ~/web && docker-compose restart faiss-memory
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

## Notes

- Service runs independently of OpenClaw
- Survives OpenClaw restarts/upgrades
- Backups created automatically before destructive ops
- Read-only access to workspace files
- All data persists in bind mount: `/Users/dk/projects/memories/data/`
- API docs available at http://localhost:8900/docs
