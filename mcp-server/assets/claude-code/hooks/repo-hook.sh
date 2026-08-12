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

# Are these hooks already registered by ANY other means? Two sources, and both
# matter:
#
#   (a) the plugin, registered via installed_plugins.json — the local case;
#   (b) user-scope hooks in ~/.claude/settings.json written by
#       `memories-mcp init` — which is how a cloud environment's setup script
#       would wire them, and the case that makes this repo wiring redundant
#       there rather than additive.
#
# Checking only (a) would let the repo wiring double-fire alongside an
# installer-provisioned container: recall twice, and two concurrent Stop
# extractions racing to write the same memories. The scripts have no
# invocation-level locking, so this gate is the only thing preventing it.
_memory_hooks_already_registered() {
  local dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  command -v jq >/dev/null 2>&1 || return 1

  local manifest="$dir/plugins/installed_plugins.json"
  if [ -f "$manifest" ]; then
    local n
    n=$(jq -r '[.plugins["memories@dk-marketplace"] // [] | .[]] | length' "$manifest" 2>/dev/null) || n=0
    [ "${n:-0}" -gt 0 ] && return 0
  fi

  local settings="$dir/settings.json"
  if [ -f "$settings" ]; then
    jq -e '[.hooks // {} | .[]? | .[]? | .hooks[]? | .command // ""]
           | any(test("/hooks/memory/memory-"))' "$settings" >/dev/null 2>&1 && return 0
  fi

  return 1
}

# Consuming stdin keeps the writer from seeing EPIPE when we exit unread.
if [ "${MEMORIES_REPO_HOOKS_FORCE:-}" != "1" ] && _memory_hooks_already_registered; then
  cat >/dev/null 2>&1 || true
  exit 0
fi

exec "$(dirname "$0")/$HOOK_NAME" "$@"
