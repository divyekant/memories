#!/usr/bin/env bash
# memory-subagent-capture.sh — SubagentStop hook (Codex)
# Extract the final subagent transcript into the Codex project source.

MEMORIES_HOOK_NAME="memory-subagent-capture"

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _exit_if_disabled() { return 0; }
  _memories_resolve_project() { basename "${1:-unknown}"; }
  _default_extract_source() { echo 'codex/{project}'; }
  _extract_multi() { :; }
fi

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // empty')
_exit_if_disabled "$CWD" 2>/dev/null || true

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
TRANSCRIPT_PATH=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty')
LAST_ASSISTANT_MESSAGE=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty')
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
PROJECT=$(_memories_resolve_project "${CWD:-unknown}" 2>/dev/null || basename "${CWD:-unknown}")
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

_DEFAULT_SRC="$(_default_extract_source)"
_EXTRACT_SRC="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${_EXTRACT_SRC//\{project\}/$PROJECT}"

MESSAGES=""
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  MESSAGES=$(tail -200 "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr '
    [
      .[]
      | ((.payload.role // .message.role // .role // .type // "") | tostring) as $role
      | select($role == "user" or $role == "assistant")
      | {
          role: $role,
          text: (
            if (.payload.content // null) != null then
              if (.payload.content | type) == "string" then .payload.content
              elif (.payload.content | type) == "array" then [.payload.content[] | select(.type == "text" or .type == "input_text" or .type == "output_text") | .text? // empty] | join(" ")
              else "" end
            elif (.message.content // null) != null then
              if (.message.content | type) == "string" then .message.content
              elif (.message.content | type) == "array" then [.message.content[] | select(.type == "text" or .type == "input_text" or .type == "output_text") | .text? // empty] | join(" ")
              else "" end
            elif (.content // null) != null then
              if (.content | type) == "string" then .content
              elif (.content | type) == "array" then [.content[] | select(.type == "text" or .type == "input_text" or .type == "output_text") | .text? // empty] | join(" ")
              else "" end
            elif ((.text // null) | type) == "string" then .text
            else "" end
          )
        }
      | select(.text != "" and (.text | length) > 10)
    ]
    | .[-12:]
    | map(.role + ": " + (.text | .[0:2000]))
    | join("\n\n")
  ' 2>/dev/null) || { _log_warn "Failed to parse subagent transcript"; true; }
fi

if [ -n "$LAST_ASSISTANT_MESSAGE" ]; then
  if [ -n "$MESSAGES" ]; then
    MESSAGES=$(printf '%s\n\nassistant: %s' "$MESSAGES" "$LAST_ASSISTANT_MESSAGE")
  else
    MESSAGES="assistant: $LAST_ASSISTANT_MESSAGE"
  fi
fi
[ -n "$MESSAGES" ] || { _log_info "No messages from $AGENT_TYPE subagent"; exit 0; }
MESSAGES="${MESSAGES:0:8000}"

_log_info "Extracting from $AGENT_TYPE subagent ($PROJECT)"
_extract_multi "$MESSAGES" "$SOURCE" "subagent_stop"
