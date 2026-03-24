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

# -- Multi-Backend Config --------------------------------------------------

_BACKENDS_CACHE=""

_load_backends() {
  # Return cached if already loaded
  if [ -n "$_BACKENDS_CACHE" ]; then
    echo "$_BACKENDS_CACHE" | jq -c '.backends'
    return 0
  fi

  local config_file="${MEMORIES_BACKENDS_FILE:-}"

  # Resolution: explicit env → project → global → env var fallback
  if [ -z "$config_file" ]; then
    if [ -f "${CWD:-}/.memories/backends.yaml" ] 2>/dev/null; then
      config_file="$CWD/.memories/backends.yaml"
    elif [ -f "$HOME/.config/memories/backends.yaml" ]; then
      config_file="$HOME/.config/memories/backends.yaml"
    fi
  fi

  if [ -n "$config_file" ] && [ -f "$config_file" ]; then
    # Parse YAML → JSON using Node.js + js-yaml (guaranteed available — Claude Code requires Node,
    # and js-yaml is installed in mcp-server/node_modules).
    # Resolve mcp-server path relative to hooks directory.
    local hooks_dir
    hooks_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
    local mcp_modules="${hooks_dir}/../../../mcp-server/node_modules"
    # Fallback: check common install locations
    [ ! -d "$mcp_modules" ] && mcp_modules="${hooks_dir}/../../mcp-server/node_modules"
    [ ! -d "$mcp_modules" ] && mcp_modules="$HOME/projects/memories/mcp-server/node_modules"

    local raw
    raw=$(node -e "
const yaml = require('${mcp_modules}/js-yaml');
const fs = require('fs');
const data = yaml.load(fs.readFileSync('${config_file}', 'utf8'));
const backends = Object.entries(data.backends || {}).map(([name, cfg]) => {
  let apiKey = cfg.api_key || '';
  const m = apiKey.match(/\\\$\{(\w+)\}/);
  if (m) apiKey = process.env[m[1]] || apiKey;
  return { name, url: cfg.url || '', api_key: apiKey, scenario: cfg.scenario || '' };
});
console.log(JSON.stringify({ backends, routing: data.routing || {} }));
" 2>/dev/null)
    _BACKENDS_CACHE="$raw"
    # Output just the backends array for simple callers
    echo "$raw" | jq -c '.backends'
  else
    # Fallback to env vars — single backend
    local url="${MEMORIES_URL:-http://localhost:8900}"
    local key="${MEMORIES_API_KEY:-}"
    _BACKENDS_CACHE=$(jq -nc --arg url "$url" --arg key "$key" \
      '{backends: [{name: "default", url: $url, api_key: $key, scenario: ""}], routing: {}}')
    echo "$_BACKENDS_CACHE" | jq -c '.backends'
  fi
}

_get_backends_for_op() {
  local op="$1"  # search | extract | add | feedback

  # Load full config (with routing)
  _load_backends > /dev/null  # populate cache
  local config="$_BACKENDS_CACHE"
  local backends
  backends=$(echo "$config" | jq -c '.backends')
  local routing
  routing=$(echo "$config" | jq -c '.routing // {}')
  local count
  count=$(echo "$backends" | jq 'length')

  # Single backend — always that one
  if [ "$count" -eq 1 ]; then
    echo "$backends"
    return 0
  fi

  # Check explicit routing first
  local explicit
  explicit=$(echo "$routing" | jq -c --arg op "$op" '.[$op] // empty')
  if [ -n "$explicit" ] && [ "$explicit" != "null" ]; then
    # Filter backends by name
    echo "$backends" | jq -c --argjson names "$explicit" \
      '[.[] | select(.name as $n | $names | index($n))]'
    return 0
  fi

  # Scenario-based routing
  case "$op" in
    search)
      # All backends for search
      echo "$backends"
      ;;
    extract)
      # dev or personal backends only
      echo "$backends" | jq -c '[.[] | select(.scenario == "dev" or .scenario == "personal")]'
      ;;
    add)
      # All writable backends (dev + prod, personal + shared)
      echo "$backends"
      ;;
    feedback)
      # dev or personal only
      echo "$backends" | jq -c '[.[] | select(.scenario == "dev" or .scenario == "personal")]'
      ;;
    *)
      # Default: primary (first)
      echo "$backends" | jq -c '[.[0]]'
      ;;
  esac
}
