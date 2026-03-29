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

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // "unknown"')
USAGE_LOG="${MEMORIES_TOOL_LOG:-$HOME/.config/memories/tool-usage.log}"

# Append tool usage
echo "$(date -u +%FT%TZ) $TOOL [codex]" >> "$USAGE_LOG" 2>/dev/null || true

_log_info "Tool used: $TOOL"
exit 0
