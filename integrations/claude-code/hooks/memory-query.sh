#!/bin/bash
# memory-query.sh — UserPromptSubmit hook
# Searches Memories for memories relevant to the current prompt.
# Sync hook: blocks until done, injects additionalContext.

set -euo pipefail

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

# Skip short/trivial prompts
if [ ${#PROMPT} -lt 20 ]; then
  exit 0
fi

RESULTS=$(curl -sf -X POST "$MEMORIES_URL/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "{\"query\": $(echo "$PROMPT" | jq -Rs), \"k\": 5, \"hybrid\": true, \"threshold\": 0.4}" \
  2>/dev/null \
  | jq -r '[.results[]] | .[0:5] | map("- [\(.source)] \(.text)") | join("\n")' 2>/dev/null) || true

if [ -z "$RESULTS" ] || [ "$RESULTS" = "null" ]; then
  exit 0
fi

jq -n --arg memories "$RESULTS" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: ("## Retrieved Memories\n" + $memories)
  }
}'
