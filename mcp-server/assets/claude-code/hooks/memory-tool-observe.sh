#!/bin/bash
# memory-tool-observe.sh — PostToolUse hook for Write|Edit|Bash
# Logs tool observations to a session-scoped file so extraction hooks
# have richer context about what the agent actually DID (files changed,
# commands run). Cheap — no LLM call, just append to a JSONL log.

MEMORIES_HOOK_NAME="memory-tool-observe"

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
  _log_info() { :; }; _log_error() { :; }
fi

_exit_if_disabled 2>/dev/null || true

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // empty')

# Extract meaningful details per tool type
case "$TOOL_NAME" in
  Write|Edit)
    FILE_PATH=$(echo "$TOOL_INPUT" | jq -r '.file_path // empty')
    OBSERVATION="$TOOL_NAME: $FILE_PATH"
    ;;
  Bash)
    COMMAND=$(echo "$TOOL_INPUT" | jq -r '.command // empty' | head -c 200)
    OBSERVATION="Bash: $COMMAND"
    ;;
  *)
    # Skip tools we don't care about
    exit 0
    ;;
esac

# Append to session-scoped observation log
OBS_DIR="/tmp/memories-observations"
mkdir -p "$OBS_DIR" 2>/dev/null || true
OBS_FILE="$OBS_DIR/${SESSION_ID}.jsonl"

jq -nc --arg ts "$(date -u +%FT%TZ)" --arg obs "$OBSERVATION" --arg tool "$TOOL_NAME" \
  '{ts: $ts, tool: $tool, observation: $obs}' >> "$OBS_FILE" 2>/dev/null || true
