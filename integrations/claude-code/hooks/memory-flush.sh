#!/bin/bash
# memory-flush.sh — PreCompact hook
# Aggressive extraction before context compaction.
# CC PreCompact provides: session_id, transcript_path, cwd
# Reads recent messages from JSONL transcript before they're compacted away.

set -euo pipefail

# Load from dedicated env file
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

INPUT=$(cat)

TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

# Expand tilde if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

# Pre-compact: read more aggressively (last 1000 lines) to capture context about to be lost
MESSAGES=$(tail -1000 "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr '
  [
    .[]
    | select(.type == "user" or .type == "assistant")
    | {
        role: .type,
        text: (
          if .message.content | type == "string" then
            .message.content
          elif .message.content | type == "array" then
            [.message.content[] | select(.type == "text") | .text] | join(" ")
          else
            ""
          end
        )
      }
    | select(.text != "" and (.text | length) > 10)
  ]
  | .[-20:]
  | map(.role + ": " + (.text | .[0:2000]))
  | join("\n\n")
' 2>/dev/null) || true

if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  exit 0
fi

# Truncate to ~12000 chars (more aggressive for pre-compact)
MESSAGES="${MESSAGES:0:12000}"

curl -sf -X POST "$MEMORIES_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"pre_compact\"}" \
  > /dev/null 2>&1 || true
