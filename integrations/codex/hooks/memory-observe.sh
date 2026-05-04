#!/usr/bin/env bash
# memory-observe.sh — PostToolUse observer
# Fire-and-forget logger that tracks when memory MCP tools are called.

set -euo pipefail

MEMORIES_HOOK_NAME="memory-observe"

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
fi

_exit_if_disabled 2>/dev/null || true

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // "unknown"')
USAGE_LOG="${MEMORIES_TOOL_LOG:-$HOME/.config/memories/tool-usage.log}"
CWD=$(echo "$INPUT" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // empty')
PROJECT=$(basename "${CWD:-}")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  PROJECT="unknown"
fi
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // .sessionId // "unknown"')
SOURCE_PREFIX=$(echo "$INPUT" | jq -r '.tool_input.source_prefix // .tool_input.arguments.source_prefix // .input.source_prefix // .arguments.source_prefix // empty')
SOURCE_PREFIX_QUALITY=$(_source_prefix_quality "$SOURCE_PREFIX" "$PROJECT")

# Append tool usage
CLIENT=$(_memory_client_prefix 2>/dev/null || echo "codex")
echo "$(date -u +%FT%TZ) $TOOL [$CLIENT]" >> "$USAGE_LOG" 2>/dev/null || true

METRICS_EVENT=$(jq -nc \
  --arg ts "$(date -u +%FT%TZ)" \
  --arg client "$CLIENT" \
  --arg session_id "$SESSION_ID" \
  --arg project "$PROJECT" \
  --arg tool_name "$TOOL" \
  --arg source_prefix "$SOURCE_PREFIX" \
  --arg source_prefix_quality "$SOURCE_PREFIX_QUALITY" \
  '{ts: $ts, event: "tool_call", client: $client, session_id: $session_id, project: $project, tool_name: $tool_name, source_prefix: $source_prefix, source_prefix_quality: $source_prefix_quality}')
_active_search_metrics_log "$METRICS_EVENT"

_log_info "Tool used: $TOOL"
exit 0
