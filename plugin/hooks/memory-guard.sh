#!/usr/bin/env bash
# memory-guard.sh — PreToolUse hook for Write|Edit
# Blocks writes to MEMORY.md files, which are managed by Memories MCP sync.

set -euo pipefail

MEMORIES_HOOK_NAME="memory-guard"

_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
fi

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Block writes to MEMORY.md (auto-memory managed by Memories MCP sync)
if [[ "$FILE" == */MEMORY.md ]] || [[ "$FILE" == */memory/MEMORY.md ]]; then
  _log_warn "Blocked write to MEMORY.md: $FILE"
  echo "MEMORY.md is managed by Memories MCP sync. Use memory_add or memory_extract instead of writing directly." >&2
  exit 2
fi

exit 0
