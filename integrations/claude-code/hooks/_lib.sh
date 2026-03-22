#!/usr/bin/env bash
# Shared utilities for Memories hooks

MEMORIES_LOG="${MEMORIES_LOG:-$HOME/.config/memories/hook.log}"

_log() {
  local level="$1" msg="$2"
  local logdir
  logdir=$(dirname "$MEMORIES_LOG")
  [ -d "$logdir" ] || mkdir -p "$logdir" 2>/dev/null || return 0
  echo "$(date -u +%FT%TZ) [$level] [${MEMORIES_HOOK_NAME:-unknown}] $msg" >> "$MEMORIES_LOG" 2>/dev/null
}

_log_info() { _log "INFO" "$1"; }
_log_error() { _log "ERROR" "$1"; }
_log_warn() { _log "WARN" "$1"; }

# Rotate log if over 1000 lines (called from SessionStart only)
_rotate_log() {
  if [ -f "$MEMORIES_LOG" ] && [ "$(wc -l < "$MEMORIES_LOG" 2>/dev/null)" -gt 1000 ]; then
    tail -500 "$MEMORIES_LOG" > "$MEMORIES_LOG.tmp" && mv "$MEMORIES_LOG.tmp" "$MEMORIES_LOG"
    _log_info "Log rotated (kept last 500 lines)"
  fi
}

_memory_client_prefix() {
  local hook_dir
  hook_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  case "$hook_dir" in
    *"/.codex/"*)
      printf 'codex'
      ;;
    *)
      printf 'claude-code'
      ;;
  esac
}

_default_source_prefixes() {
  local client_prefix
  client_prefix="$(_memory_client_prefix)"
  printf '%s/{project},learning/{project},wip/{project}' "$client_prefix"
}

_default_extract_source() {
  local client_prefix
  client_prefix="$(_memory_client_prefix)"
  printf '%s/{project}' "$client_prefix"
}

# Health check — returns 0 if service is reachable
_health_check() {
  local url="${MEMORIES_URL:-http://localhost:8900}"
  curl -sf --max-time 2 "$url/health" >/dev/null 2>&1
}
