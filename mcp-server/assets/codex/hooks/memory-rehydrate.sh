#!/usr/bin/env bash
# memory-rehydrate.sh — PostCompact hook (Codex)
#
# PostCompact's Codex output schema is intentionally narrow: it accepts only
# continue, stopReason, suppressOutput, and systemMessage. It cannot inject
# additionalContext. SessionStart(source=compact) remains the supported
# rehydration/injection surface, so this hook is a silent schema-valid no-op.

MEMORIES_HOOK_NAME="memory-rehydrate"

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
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _exit_if_disabled() { return 0; }
fi

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // empty')
_exit_if_disabled "$CWD" 2>/dev/null || true

printf '%s\n' '{"suppressOutput":true}'
