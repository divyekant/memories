#!/bin/bash
# memory-flush.sh — PreCompact hook (async)
# Aggressive extraction before context compaction.
# Same as memory-extract.sh but with context=pre_compact.

set -euo pipefail

# Load from dedicated env file — avoids requiring shell profile changes
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

INPUT=$(cat)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // "null"')

# Try inline messages first, fall back to transcript_path (Cursor sends transcript_path, not inline messages)
MESSAGES=$(echo "$INPUT" | jq -r '.messages // empty')
if [ -z "$MESSAGES" ] && [ -n "$TRANSCRIPT_PATH" ] && [ "$TRANSCRIPT_PATH" != "null" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  MESSAGES=$(jq -r '
    select(.role == "user" or .role == "assistant") |
    (if .role == "user" then "User" else "Assistant" end) + ": " +
    ([.message.content[]? | select(.type == "text") | .text] | join(" "))
  ' "$TRANSCRIPT_PATH" 2>/dev/null | head -c 60000) || true
fi
if [ -z "$MESSAGES" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // .workspace_roots[0] // "unknown"')
PROJECT=$(basename "$CWD")

BODY=$(jq -nc --arg msgs "$MESSAGES" --arg src "claude-code/$PROJECT" '{"messages": $msgs, "source": $src, "context": "pre_compact"}')
curl -sf -X POST "$MEMORIES_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "$BODY" \
  > /dev/null 2>&1 || true
