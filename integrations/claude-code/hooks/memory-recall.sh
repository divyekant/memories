#!/bin/bash
# memory-recall.sh — SessionStart hook
# Loads project-relevant memories into Claude Code context.
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
RESULTS=$(curl -sf -X POST "$MEMORIES_URL/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "$BODY" \
  2>/dev/null \
  | jq -r '[.results[]] | .[0:8] | map("- \(.text)") | join("\n")' 2>/dev/null) || true

if [ -z "$RESULTS" ] || [ "$RESULTS" = "null" ]; then
  exit 0
fi

jq -n --arg memories "$RESULTS" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: ("## Relevant Memories\n\n" + $memories)
  }
}'
