#!/bin/bash
# memory-extract.sh — Stop hook
# Extracts facts from the last user+assistant message pair.
# CC Stop hook provides: session_id, transcript_path, cwd, last_assistant_message

MEMORIES_HOOK_NAME="memory-extract"

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_extract_source() { echo 'claude-code/{project}'; }
fi

_exit_if_disabled 2>/dev/null || true

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

# Configurable thresholds (Task 1.2)
TAIL_LINES="${MEMORIES_EXTRACT_TAIL_LINES:-200}"
MSG_PAIRS="${MEMORIES_EXTRACT_MSG_PAIRS:-4}"
MSG_CAP="${MEMORIES_EXTRACT_MSG_CAP:-8000}"

INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')

# Expand tilde if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Build extraction source — supports {project} placeholder (Task 1.3)
_DEFAULT_SRC="$(_default_extract_source)"
_EXTRACT_SRC="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${_EXTRACT_SRC//\{project\}/$PROJECT}"

MESSAGES=""

# Try to read last user+assistant pair from transcript for decision context
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
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
fi

# Fallback to last_assistant_message if transcript read failed
if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  MESSAGES=$(echo "$INPUT" | jq -r '.last_assistant_message // empty')
fi

if [ -z "$MESSAGES" ]; then
  exit 0
fi

# Cap at MSG_CAP chars (one pair is plenty for the Stop hook)
MESSAGES="${MESSAGES:0:$MSG_CAP}"

# No pre-filter — extraction runs unconditionally on every Stop event.
# The extraction LLM (AUDN) decides what's worth keeping.
# Cost: ~$0.001/call. Missed memories are more expensive than the filter saves.

_log_info "Extracting from $PROJECT (${#MESSAGES} chars, source=$SOURCE)"

_extract_multi "$MESSAGES" "$SOURCE" "stop"
