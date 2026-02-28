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

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // .workspace_roots[0] // empty')
if [ -z "$CWD" ]; then
  exit 0
fi

PROJECT=$(basename "$CWD")

BODY=$(jq -nc --arg q "project $PROJECT conventions decisions patterns" '{"query": $q, "k": 10, "hybrid": true}')

# Get raw search results
RAW_RESULTS=$(curl -sf -X POST "$MEMORIES_URL/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "$BODY" \
  2>/dev/null) || true

if [ -z "$RAW_RESULTS" ] || [ "$RAW_RESULTS" = "null" ]; then
  exit 0
fi

# Format for context injection
CONTEXT_RESULTS=$(echo "$RAW_RESULTS" | jq -r '[.results[]] | .[0:8] | map("- \(.text)") | join("\n")' 2>/dev/null) || true

if [ -z "$CONTEXT_RESULTS" ] || [ "$CONTEXT_RESULTS" = "null" ]; then
  exit 0
fi

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

MEMORY_RESULTS=$(echo "$RAW_RESULTS" | jq -r '[.results[]] | .[0:8] | map("- \(.text)") | join("\n")' 2>/dev/null) || true

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
jq -n --arg memories "$CONTEXT_RESULTS" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: ("## Relevant Memories\n\n" + $memories)
  }
}'
