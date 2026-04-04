#!/usr/bin/env bash
set -euo pipefail

MEMORIES_HOOK_NAME="memory-config-guard"

_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
fi

INPUT=$(cat)

# Only check user_settings changes
SOURCE=$(echo "$INPUT" | jq -r '.source // empty')
case "$SOURCE" in
  user_settings) ;;
  *) exit 0 ;;
esac

# In plugin mode, hooks are managed by the plugin system — skip settings.json check
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  exit 0
fi

SETTINGS_FILE="$HOME/.claude/settings.json"
[ -f "$SETTINGS_FILE" ] || { _log_warn "Settings file not found"; exit 0; }

# Legacy mode: check if memory hooks are still configured in settings.json
MISSING=""
for hook in "memory-recall" "memory-query" "memory-extract"; do
  if ! grep -q "$hook" "$SETTINGS_FILE" 2>/dev/null; then
    MISSING="$MISSING $hook"
  fi
done

if [ -n "$MISSING" ]; then
  _log_warn "Memory hooks may be missing from settings:$MISSING"
  jq -n --arg ctx "## Memories Configuration Warning

Some memory hooks appear to be missing from settings.json:$MISSING
This may affect memory recall and extraction. Run the Memories installer to restore them." \
    '{"additionalContext": $ctx}'
fi

exit 0
