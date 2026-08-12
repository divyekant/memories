#!/usr/bin/env bash
# memory-commit.sh — SessionEnd hook (Codex)
# Enqueue one final extraction and exit; the backend owns any asynchronous work.

MEMORIES_HOOK_NAME="memory-commit"

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
fi

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // empty')
_exit_if_disabled "$CWD" 2>/dev/null || true

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty')
TAIL_LINES="${MEMORIES_COMMIT_TAIL_LINES:-500}"
MSG_PAIRS="${MEMORIES_COMMIT_MSG_PAIRS:-10}"
MSG_CAP="${MEMORIES_COMMIT_MSG_CAP:-8000}"
TRANSCRIPT_PATH=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty')
LAST_ASSISTANT_MESSAGE=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty')
PROJECT=$(_memories_resolve_project "${CWD:-unknown}" 2>/dev/null || basename "${CWD:-unknown}")
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

_DEFAULT_SRC="$(_default_extract_source)"
_EXTRACT_SRC="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${_EXTRACT_SRC//\{project\}/$PROJECT}"

MESSAGES=""
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  MESSAGES=$(tail -"$TAIL_LINES" "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr --argjson pairs "$MSG_PAIRS" '
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
    | .[-$pairs:]
    | map(.role + ": " + (.text | .[0:2000]))
    | join("\n\n")
  ' 2>/dev/null) || { _log_warn "Transcript parse failed for $TRANSCRIPT_PATH"; true; }
fi

if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  MESSAGES="$LAST_ASSISTANT_MESSAGE"
fi
[ -n "$MESSAGES" ] || exit 0
MESSAGES="${MESSAGES:0:$MSG_CAP}"

_log_info "Commit-extracting from $PROJECT (${#MESSAGES} chars, source=$SOURCE)"

# SessionEnd has a three-second Codex budget. Select only the first routed
# extraction backend and give its single enqueue request a hard two-second
# ceiling; never poll the queued job or contact another backend here.
BACKENDS=$(_get_backends_for_op "extract" 2>/dev/null) || BACKENDS='[]'
BACKEND=$(printf '%s' "$BACKENDS" | jq -c '.[0] // empty' 2>/dev/null || true)
[ -n "$BACKEND" ] || exit 0

URL=$(printf '%s' "$BACKEND" | jq -r '.url // empty')
API_KEY=$(printf '%s' "$BACKEND" | jq -r '.api_key // empty')
BODY=$(jq -nc \
  --arg m "$MESSAGES" \
  --arg s "$SOURCE" \
  --arg d "$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")" \
  '{messages: $m, source: $s, context: "session_end", document_at: $d}')

if ! curl -sf --max-time 2 -X POST "$URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Memories-Client: codex" \
  -H "X-Memories-Session-Id: $SESSION_ID" \
  -H "X-Memories-Invocation: $MEMORIES_HOOK_NAME" \
  -d "$BODY" >/dev/null 2>&1; then
  _log_error "Session-end enqueue failed for backend $(printf '%s' "$BACKEND" | jq -r '.name // "unknown"')"
fi
