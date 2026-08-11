#!/usr/bin/env bash
# Fallback launcher for repo-wired hooks.
#
# Cloud sessions never install the plugin (no marketplace fetch, no install —
# verified in a real container), so this repo wires the hook scripts directly
# in .claude/settings.json. But a local session DOES have the plugin, and
# Claude Code runs every matching hook: without a gate, both sets fire. That
# means recall injected twice, telemetry double-counted, and — the reason this
# guard exists — two concurrent Stop/SubagentStop extractions racing to write
# the same memories. The hook scripts have no invocation-level locking.
#
# So the repo wiring is strictly a FALLBACK: it stands down whenever the
# plugin is present to supply the same hooks.
#
# Usage: repo-hook.sh <hook-script-name> [args...]

set -euo pipefail

HOOK_NAME="${1:-}"
[ -n "$HOOK_NAME" ] || exit 0
shift

# Invoked BY the plugin? Then this is the plugin's own copy — nothing to gate.
[ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && exit 0

# Is the memories plugin installed for this user? If so its hooks are already
# registered and will run; stand down rather than double-fire. Consuming stdin
# keeps the writer from seeing EPIPE when we exit without reading the payload.
_plugin_installed() {
  local manifest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"
  [ -f "$manifest" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  local n
  n=$(jq -r '[.plugins["memories@dk-marketplace"] // [] | .[]] | length' "$manifest" 2>/dev/null) || return 1
  [ "${n:-0}" -gt 0 ]
}

if [ "${MEMORIES_REPO_HOOKS_FORCE:-}" != "1" ] && _plugin_installed; then
  cat >/dev/null 2>&1 || true
  exit 0
fi

exec "$(dirname "$0")/$HOOK_NAME" "$@"
