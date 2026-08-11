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
PROJECT=$(_memories_resolve_project "${CWD:-}" 2>/dev/null || basename "${CWD:-}")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  PROJECT="unknown"
fi
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // .sessionId // "unknown"')
SOURCE_PREFIX=$(echo "$INPUT" | jq -r '.tool_input.source_prefix // .tool_input.arguments.source_prefix // .input.source_prefix // .arguments.source_prefix // empty')
SOURCE_PREFIX_QUALITY=$(_source_prefix_quality "$SOURCE_PREFIX" "$PROJECT")
MEMORY_IDS_JSON=$(_memory_ids_for_metrics "$INPUT" 2>/dev/null || echo '[]')
[ -z "$MEMORY_IDS_JSON" ] && MEMORY_IDS_JSON='[]'

# Append tool usage
CLIENT=$(_memory_client_prefix 2>/dev/null || echo "codex")

if [ "$TOOL" = "exec" ]; then
  EXEC_INPUT=$(printf '%s' "$INPUT" | jq -r '
    (.tool_input // .input // "") as $v |
    if ($v | type) == "string" then $v
    elif ($v | type) == "object" then ($v.input // $v.code // "")
    else ""
    end
  ' 2>/dev/null || true)
  NESTED_TOOLS=$(printf '%s' "$EXEC_INPUT" \
    | { grep -oE 'tools\.mcp__[A-Za-z0-9_-]+__memory_[A-Za-z0-9_]+' || true; } \
    | sed 's/^tools\.//' \
    | sort -u)
  [ -n "$NESTED_TOOLS" ] || exit 0
  SOURCE_PREFIXES_JSON=$(printf '%s' "$EXEC_INPUT" \
    | { grep -oE 'source_prefix[[:space:]]*:[[:space:]]*"[^"]*"' || true; } \
    | sed -E 's/^[^:]+:[[:space:]]*"([^"]*)"$/\1/' \
    | sort -u \
    | jq -Rcs '[splits("\n") | select(length > 0)]' 2>/dev/null || echo '[]')
  [ -z "$SOURCE_PREFIXES_JSON" ] && SOURCE_PREFIXES_JSON='[]'
  NESTED_SOURCE_PREFIX=$(printf '%s' "$SOURCE_PREFIXES_JSON" | jq -r 'if length == 1 then .[0] else "" end')
  if [ "$(printf '%s' "$SOURCE_PREFIXES_JSON" | jq -r 'length')" -eq 1 ]; then
    NESTED_SOURCE_PREFIX_QUALITY=$(_source_prefix_quality "$NESTED_SOURCE_PREFIX" "$PROJECT")
  else
    NESTED_SOURCE_PREFIX_QUALITY="mixed_or_dynamic"
  fi
  while IFS= read -r NESTED_TOOL; do
    [ -n "$NESTED_TOOL" ] || continue
    echo "$(date -u +%FT%TZ) $NESTED_TOOL [$CLIENT] parent=exec" >> "$USAGE_LOG" 2>/dev/null || true
    METRICS_EVENT=$(jq -nc \
      --arg ts "$(date -u +%FT%TZ)" \
      --arg client "$CLIENT" \
      --arg session_id "$SESSION_ID" \
      --arg project "$PROJECT" \
      --arg tool_name "$NESTED_TOOL" \
      --arg source_prefix "$NESTED_SOURCE_PREFIX" \
      --arg source_prefix_quality "$NESTED_SOURCE_PREFIX_QUALITY" \
      --argjson source_prefixes "$SOURCE_PREFIXES_JSON" \
      --argjson memory_ids "$MEMORY_IDS_JSON" \
      '{ts: $ts, event: "tool_call", client: $client, session_id: $session_id, project: $project, tool_name: $tool_name, source_prefix: $source_prefix, source_prefixes: $source_prefixes, source_prefix_quality: $source_prefix_quality, memory_ids: $memory_ids, parent_tool: "exec", observed_via: "nested_exec"}')
    _active_search_metrics_log "$METRICS_EVENT"
  done <<< "$NESTED_TOOLS"
  _log_info "Nested memory tools observed in exec"
  exit 0
fi

echo "$(date -u +%FT%TZ) $TOOL [$CLIENT]" >> "$USAGE_LOG" 2>/dev/null || true

METRICS_EVENT=$(jq -nc \
  --arg ts "$(date -u +%FT%TZ)" \
  --arg client "$CLIENT" \
  --arg session_id "$SESSION_ID" \
  --arg project "$PROJECT" \
  --arg tool_name "$TOOL" \
  --arg source_prefix "$SOURCE_PREFIX" \
  --arg source_prefix_quality "$SOURCE_PREFIX_QUALITY" \
  --argjson memory_ids "$MEMORY_IDS_JSON" \
  '{ts: $ts, event: "tool_call", client: $client, session_id: $session_id, project: $project, tool_name: $tool_name, source_prefix: $source_prefix, source_prefix_quality: $source_prefix_quality, memory_ids: $memory_ids}')
_active_search_metrics_log "$METRICS_EVENT"

_log_info "Tool used: $TOOL"
exit 0
