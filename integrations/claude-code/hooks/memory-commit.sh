#!/bin/bash
# memory-commit.sh — SessionEnd hook
# Final extraction pass before session terminates.
# CC SessionEnd provides: session_id, transcript_path, cwd, reason
# Reads recent human+assistant text messages from JSONL transcript.

MEMORIES_HOOK_NAME="memory-commit"

set -euo pipefail

# Load from dedicated env file
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_extract_source() { echo 'claude-code/{project}'; }
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

# Configurable thresholds (Task 1.2)
TAIL_LINES="${MEMORIES_COMMIT_TAIL_LINES:-500}"
MSG_PAIRS="${MEMORIES_COMMIT_MSG_PAIRS:-10}"
MSG_CAP="${MEMORIES_COMMIT_MSG_CAP:-8000}"

INPUT=$(cat)

TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

# Expand tilde if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Build extraction source — supports {project} placeholder (Task 1.3)
_DEFAULT_SRC="$(_default_extract_source)"
_EXTRACT_SRC="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${_EXTRACT_SRC//\{project\}/$PROJECT}"

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

# Extract text-bearing messages from transcript JSONL.
# Transcript format: each line is {type, message: {role, content}, ...}
# Content can be string or array of {type: "text"|"tool_use"|"tool_result", text: "..."}
# We only want entries that have actual text, skipping pure tool_use/tool_result entries.
MESSAGES=$(tail -"$TAIL_LINES" "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr --argjson pairs "$MSG_PAIRS" '
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
  | .[-$pairs:]
  | map(.role + ": " + (.text | .[0:2000]))
  | join("\n\n")
' 2>/dev/null) || { _log_warn "Transcript parse failed for $TRANSCRIPT_PATH"; true; }

if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  exit 0
fi

# Truncate to MSG_CAP chars to stay within extraction limits
MESSAGES="${MESSAGES:0:$MSG_CAP}"

_log_info "Commit-extracting from $PROJECT (${#MESSAGES} chars, source=$SOURCE)"

curl -sf -X POST "$MEMORIES_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "$(jq -nc --arg messages "$MESSAGES" --arg source "$SOURCE" --arg context "session_end" \
    '{messages: $messages, source: $source, context: $context}')" \
  > /dev/null 2>&1 || _log_error "Commit extract API call failed for $PROJECT"
