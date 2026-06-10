#!/usr/bin/env bash
# Install (or uninstall) the transcript-watcher launchd agent on macOS.
#
#   ./install-watcher.sh install     # render plist, load agent
#   ./install-watcher.sh uninstall   # unload + remove
#   ./install-watcher.sh status      # is it loaded / recent log
#
# Reads MEMORIES_URL / MEMORIES_API_KEY from the environment or
# ~/.config/memories/env. The watcher captures memories from hookless
# sessions (Claude Desktop, etc.) that never fire Stop hooks.
set -euo pipefail

LABEL="com.memories.transcript-watcher"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/transcript_watcher.py"
PLIST_SRC="$REPO_ROOT/integrations/launchd/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.config/memories"
ENV_FILE="${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

cmd="${1:-status}"

case "$cmd" in
  install)
    [ -f "$ENV_FILE" ] && . "$ENV_FILE"
    : "${MEMORIES_URL:=http://localhost:8900}"
    : "${MEMORIES_API_KEY:=}"
    PYTHON="$(command -v python3 || true)"
    [ -n "$PYTHON" ] || { echo "python3 not found in PATH" >&2; exit 1; }
    TRANSCRIPT_DIR="${WATCHER_TRANSCRIPT_DIR:-$HOME/.claude/projects}"
    mkdir -p "$LOG_DIR" "$(dirname "$PLIST_DST")"

    sed \
      -e "s|__PYTHON__|$PYTHON|g" \
      -e "s|__SCRIPT__|$SCRIPT|g" \
      -e "s|__MEMORIES_URL__|$MEMORIES_URL|g" \
      -e "s|__MEMORIES_API_KEY__|$MEMORIES_API_KEY|g" \
      -e "s|__TRANSCRIPT_DIR__|$TRANSCRIPT_DIR|g" \
      -e "s|__LOG_DIR__|$LOG_DIR|g" \
      "$PLIST_SRC" > "$PLIST_DST"

    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load "$PLIST_DST"
    echo "Loaded $LABEL. Logs: $LOG_DIR/transcript-watcher.{log,err}"
    ;;
  uninstall)
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "Unloaded and removed $LABEL."
    ;;
  status)
    if launchctl list | grep -q "$LABEL"; then
      echo "$LABEL is loaded."
    else
      echo "$LABEL is NOT loaded."
    fi
    [ -f "$LOG_DIR/transcript-watcher.err" ] && { echo "--- recent log ---"; tail -5 "$LOG_DIR/transcript-watcher.err"; }
    ;;
  *)
    echo "usage: $0 {install|uninstall|status}" >&2
    exit 1
    ;;
esac
