#!/bin/bash
# memory-recall.sh — SessionStart hook
# Loads project-relevant memories into Claude Code context.
# Also hydrates auto-memory MEMORY.md with synced memories from MCP,
# so the most important memories are always in context (200-line cap).
# Sync hook: blocks until done, injects additionalContext.

set -euo pipefail

# Load from dedicated env file — avoids requiring shell profile changes
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
MEMORIES_SOURCE_PREFIXES="${MEMORIES_SOURCE_PREFIXES:-}"
if [ -z "$MEMORIES_SOURCE_PREFIXES" ]; then
  MEMORIES_SOURCE_PREFIXES='claude-code/{project},learning/{project},wip/{project}'
fi
MEMORIES_RECALL_SCOPED_THRESHOLD="${MEMORIES_RECALL_SCOPED_THRESHOLD:-0.35}"
MEMORIES_RECALL_FALLBACK_THRESHOLD="${MEMORIES_RECALL_FALLBACK_THRESHOLD:-0.55}"

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // .workspace_roots[0] // empty')
if [ -z "$CWD" ]; then
  exit 0
fi

PROJECT=$(basename "$CWD")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi

search_memories() {
  local query="$1"
  local prefix="${2:-}"
  local limit="${3:-5}"
  local threshold="${4:-0.4}"

  local body
  if [ -n "$prefix" ]; then
    body=$(jq -nc \
      --arg query "$query" \
      --arg prefix "$prefix" \
      --argjson k "$limit" \
      --argjson threshold "$threshold" \
      '{
        query: $query,
        source_prefix: $prefix,
        k: $k,
        hybrid: true,
        threshold: $threshold
      }')
  else
    body=$(jq -nc \
      --arg query "$query" \
      --argjson k "$limit" \
      --argjson threshold "$threshold" \
      '{
        query: $query,
        k: $k,
        hybrid: true,
        threshold: $threshold
      }')
  fi

  curl -sf -X POST "$MEMORIES_URL/search" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $MEMORIES_API_KEY" \
    -d "$body" \
    2>/dev/null || true
}

query_for_prefix() {
  local prefix="$1"
  case "$prefix" in
    claude-code/*)
      printf 'project %s architecture decisions conventions patterns' "$PROJECT"
      ;;
    learning/*)
      printf 'project %s fixes gotchas learnings workarounds' "$PROJECT"
      ;;
    wip/*)
      printf 'project %s deferred work blockers open threads revisit later' "$PROJECT"
      ;;
    *)
      printf 'project %s conventions decisions patterns' "$PROJECT"
      ;;
  esac
}

RAW_RESPONSES=""
SCOPED_PREFIX_LIST=""
IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
for raw_prefix in "${prefix_templates[@]}"; do
  raw_prefix=$(echo "$raw_prefix" | xargs)
  [ -z "$raw_prefix" ] && continue

  prefix=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
  query=$(query_for_prefix "$prefix")
  limit=3
  case "$prefix" in
    claude-code/*) limit=4 ;;
    learning/*|wip/*) limit=2 ;;
  esac

  response=$(search_memories "$query" "$prefix" "$limit" "$MEMORIES_RECALL_SCOPED_THRESHOLD")
  if [ -n "$response" ]; then
    RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
  fi

  if [ -n "$SCOPED_PREFIX_LIST" ]; then
    SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST, "
  fi
  SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST$prefix"
done

RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr '
  map(select(type == "object") | (.results // []))
  | add
  | unique_by(.id)
  | sort_by(-(.similarity // .rrf_score // 0))
  | .[0:8]
' 2>/dev/null) || RESULTS_JSON="[]"

if [ "$RESULTS_JSON" = "[]" ]; then
  FALLBACK_RESPONSE=$(search_memories "project $PROJECT conventions decisions patterns" "" 6 "$MEMORIES_RECALL_FALLBACK_THRESHOLD")
  RESULTS_JSON=$(printf '%s' "$FALLBACK_RESPONSE" | jq -c '.results // []' 2>/dev/null) || RESULTS_JSON="[]"
fi

CONTEXT_RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map("- [\(.source)] \(.text)") | join("\n")
  end
' 2>/dev/null) || true

PLAYBOOK=$(cat <<EOF
## Memory Playbook

- Treat hook-injected memories as a starting point, not the full search space.
- Before answering questions about prior decisions, deferred work, architecture, or conventions, run project-scoped \`memory_search\` first.
- Prefer these scoped prefixes before broader searches: $SCOPED_PREFIX_LIST.
- For short follow-up prompts, include recent conversation context in the query instead of searching with the raw prompt alone.
EOF
)

# --- Hydrate auto-memory MEMORY.md ---
# Claude Code's auto-memory loads MEMORY.md into every conversation (first 200 lines).
# We sync top memories from MCP into a marked section so they're always in context,
# while preserving any manually-pinned content above the marker.
SYNC_MARKER="<!-- SYNCED-FROM-MEMORIES-MCP -->"
ENCODED_CWD=$(echo "$CWD" | tr '/' '-')
MEMORY_DIR="$HOME/.claude/projects/${ENCODED_CWD}/memory"
MEMORY_FILE="$MEMORY_DIR/MEMORY.md"

# Create memory dir if it doesn't exist (enables auto-memory for all projects)
mkdir -p "$MEMORY_DIR" 2>/dev/null || true

MEMORY_RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map("- \(.text)") | join("\n")
  end
' 2>/dev/null) || true

if [ -n "$MEMORY_RESULTS" ] && [ "$MEMORY_RESULTS" != "null" ]; then
  MANUAL_SECTION=""
  if [ -f "$MEMORY_FILE" ]; then
    # Preserve everything above the sync marker (manual/pinned content)
    MARKER_LINE=$(grep -Fn "$SYNC_MARKER" "$MEMORY_FILE" 2>/dev/null | head -1 | cut -d: -f1) || true
    if [ -n "$MARKER_LINE" ] && [ "$MARKER_LINE" -gt 1 ]; then
      MANUAL_SECTION=$(head -n $((MARKER_LINE - 1)) "$MEMORY_FILE")
    else
      MANUAL_SECTION=$(cat "$MEMORY_FILE")
    fi
  fi

  # Write: manual section (preserved) + sync marker + fresh memories
  {
    if [ -n "$MANUAL_SECTION" ]; then
      printf '%s\n' "$MANUAL_SECTION"
      echo ""
    fi
    echo "$SYNC_MARKER"
    echo "## Synced from Memories"
    echo "$MEMORY_RESULTS"
  } > "$MEMORY_FILE"
fi

# --- Output context for Claude Code ---
jq -n --arg memories "$CONTEXT_RESULTS" --arg playbook "$PLAYBOOK" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: (
      (if ($memories | length) > 0 then "## Relevant Memories\n\n" + $memories + "\n\n" else "" end) +
      $playbook
    )
  }
}'
