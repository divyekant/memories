#!/bin/bash
# memory-extract.sh — Stop hook (Codex)
# Extracts facts from the conversation transcript.
# Codex only gets ONE extraction opportunity (no PreCompact, no SessionEnd),
# so we use elevated parameters matching CC SessionEnd levels.
# Codex Stop hook provides: stop_hook_active, transcript (JSONL array)

MEMORIES_HOOK_NAME="memory-extract"

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_extract_source() { echo 'codex/{project}'; }
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

# Elevated thresholds — compensates for single extraction opportunity
TAIL_LINES="${MEMORIES_EXTRACT_TAIL_LINES:-500}"
MSG_PAIRS="${MEMORIES_EXTRACT_MSG_PAIRS:-10}"
MSG_CAP="${MEMORIES_EXTRACT_MSG_CAP:-8000}"

INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')

# Expand tilde if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Build extraction source — supports {project} placeholder
_DEFAULT_SRC="$(_default_extract_source)"
_EXTRACT_SRC="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${_EXTRACT_SRC//\{project\}/$PROJECT}"

MESSAGES=""

# Try to read messages from transcript — flexible parsing handles both CC and Codex JSONL formats
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  MESSAGES=$(tail -"$TAIL_LINES" "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr --argjson pairs "$MSG_PAIRS" '
    [
      .[]
      | select(
          ((.type // .message.role // "") | tostring) as $r |
          ($r == "user" or $r == "assistant")
        )
      | {
          role: ((.type // .message.role // "") | tostring),
          text: (
            if (.message.content // null) != null then
              if (.message.content | type) == "string" then .message.content
              elif (.message.content | type) == "array" then [.message.content[] | select(.type == "text") | .text] | join(" ")
              else ""
              end
            elif (.content // null) != null then
              if (.content | type) == "string" then .content
              elif (.content | type) == "array" then [.content[] | select(.type == "text") | .text] | join(" ")
              else ""
              end
            elif ((.text // null) | type) == "string" then .text
            else ""
            end
          )
        }
      | select(.text != "" and (.text | length) > 10)
    ]
    | .[-$pairs:]
    | map(.role + ": " + (.text | .[0:2000]))
    | join("\n\n")
  ' 2>/dev/null) || { _log_warn "Transcript parse failed for $TRANSCRIPT_PATH"; true; }
fi

# Fallback to last_assistant_message if transcript read failed
if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  MESSAGES=$(echo "$INPUT" | jq -r '.last_assistant_message // empty')
fi

if [ -z "$MESSAGES" ]; then
  exit 0
fi

# Cap at MSG_CAP chars
MESSAGES="${MESSAGES:0:$MSG_CAP}"

# No signal keyword filter — every Codex stop should extract

_log_info "Extracting from $PROJECT (${#MESSAGES} chars, source=$SOURCE, stop_hook_active=$STOP_HOOK_ACTIVE)"

_extract_multi "$MESSAGES" "$SOURCE" "stop"
