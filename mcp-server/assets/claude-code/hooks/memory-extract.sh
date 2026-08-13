#!/bin/bash
# memory-extract.sh — Stop hook
# Extracts facts from the last user+assistant message pair.
# CC Stop hook provides: session_id, transcript_path, cwd, last_assistant_message

MEMORIES_HOOK_NAME="memory-extract"

set -euo pipefail

# Load ~/.config/memories/env WITHOUT clobbering variables the environment
# already set. A cloud environment supplies MEMORIES_URL/MEMORIES_API_KEY as
# real env vars, while a setup script running `memories-mcp init` before those
# vars exist writes the localhost DEFAULT into this file — sourcing it plainly
# then overwrote the correct URL with a dead one, and the session reported the
# backend unreachable. An explicitly-set environment variable wins.
_memories_env_file="${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
if [ -f "$_memories_env_file" ]; then
  _memories_env_snapshot=$(export -p | grep 'MEMORIES_' || true)
  . "$_memories_env_file"
  eval "$_memories_env_snapshot"
  unset _memories_env_snapshot
fi
unset _memories_env_file
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
PROJECT=$(_memories_resolve_project "$CWD" 2>/dev/null || basename "$CWD")
PROJECT_CONTEXT_JSON=$(_memories_project_context "${CWD:-}" 2>/dev/null || printf '{"active":false}')
PROJECT_CONTEXT_ACTIVE=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.active // false' 2>/dev/null || printf 'false')
if [ "$PROJECT_CONTEXT_ACTIVE" != "true" ] && declare -F _memories_project_context_declared >/dev/null && _memories_project_context_declared "$PROJECT_CONTEXT_JSON"; then
  _log_warn "Collaborative project identity unavailable; skipping automatic extraction"
  exit 0
fi
PROJECT_CONTEXT_ID=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.project_id // empty' 2>/dev/null || true)
PROJECT_CONTEXT_PRINCIPAL=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.principal_id // empty' 2>/dev/null || true)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')

# Expand tilde if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Build extraction source — supports {project} placeholder (Task 1.3)
_DEFAULT_SRC="$(_default_extract_source)"
_EXTRACT_SRC="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${_EXTRACT_SRC//\{project\}/$PROJECT}"
if PROJECT_SOURCE=$(_memories_project_extract_source "$PROJECT_CONTEXT_ACTIVE" "$PROJECT_CONTEXT_ID" "$PROJECT_CONTEXT_PRINCIPAL"); then
  SOURCE="$PROJECT_SOURCE"
fi

MESSAGES=""

# Try to read last user+assistant pair from transcript for decision context
# Hook-injected context items (<system-reminder> blocks carrying recalled
# memories) are dropped before the per-message clip so they can never be
# re-extracted. The backend applies the same hygiene defensively.
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
              [
                .message.content[]
                | select(.type == "text")
                | .text
                | select(test("^\\s*<system-reminder>") | not)
              ] | join(" ")
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
