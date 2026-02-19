#!/bin/bash
# memory-commit.sh — SessionEnd hook
# Final extraction pass before session terminates.
# CC SessionEnd provides: session_id, transcript_path, cwd, reason
# Reads recent human+assistant text messages from JSONL transcript.

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

# Extract text-bearing messages from transcript JSONL.
# Transcript format: each line is {type, message: {role, content}, ...}
# Content can be string or array of {type: "text"|"tool_use"|"tool_result", text: "..."}
# We only want entries that have actual text, skipping pure tool_use/tool_result entries.
# Read last 500 lines to find enough text messages (tool calls inflate line count).
MESSAGES=$(tail -500 "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr '
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
  | .[-10:]
  | map(.role + ": " + (.text | .[0:2000]))
  | join("\n\n")
' 2>/dev/null) || true

if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  exit 0
fi

# Truncate to ~8000 chars to stay within extraction limits
MESSAGES="${MESSAGES:0:8000}"

curl -sf -X POST "$MEMORIES_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"session_end\"}" \
  > /dev/null 2>&1 || true
