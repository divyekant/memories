#!/usr/bin/env bash
# memory-rehydrate.sh — PostCompact hook (Codex)
#
# PostCompact's Codex output schema is intentionally narrow: it accepts only
# continue, stopReason, suppressOutput, and systemMessage. It cannot inject
# additionalContext. SessionStart(source=compact) remains the supported
# rehydration/injection surface, so this hook is a silent schema-valid no-op.

MEMORIES_HOOK_NAME="memory-rehydrate"

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
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
