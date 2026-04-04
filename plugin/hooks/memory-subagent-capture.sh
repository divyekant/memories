#!/usr/bin/env bash
set -euo pipefail

MEMORIES_HOOK_NAME="memory-subagent-capture"

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_extract_source() { echo 'claude-code/{project}'; }
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

INPUT=$(cat)

# Extract subagent details
AGENT_TYPE=$(echo "$INPUT" | jq -r '.agent_type // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
# Prefer agent-specific transcript; fall back to main session transcript
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.agent_transcript_path // .transcript_path // .transcriptPath // empty')
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Capture from all subagent types — matcher in hooks.json controls which fire

PROJECT=$(basename "${CWD:-unknown}")
[ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ] || [ -z "$PROJECT" ] && exit 0

# Build extraction source
_DEFAULT_SRC="$(_default_extract_source)"
SOURCE="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${SOURCE//\{project\}/$PROJECT}"

# Extract messages from transcript
MESSAGES=""
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  MESSAGES=$(tail -200 "$TRANSCRIPT_PATH" 2>/dev/null | \
    jq -r 'select(.type == "user" or .type == "assistant") |
      if (.message.content | type) == "string" then
        "\(.type): \(.message.content)"
      elif (.message.content | type) == "array" then
        "\(.type): \([.message.content[] | select(.type == "text") | .text] | join(" "))"
      else empty end' 2>/dev/null | \
    tail -12 | head -c 8000) || _log_warn "Failed to parse subagent transcript"
fi

[ -z "$MESSAGES" ] && { _log_info "No messages from $AGENT_TYPE subagent"; exit 0; }

_log_info "Extracting from $AGENT_TYPE subagent ($PROJECT)"

# Fire-and-forget extraction
_extract_multi "$MESSAGES" "$SOURCE" "subagent_stop"

exit 0
