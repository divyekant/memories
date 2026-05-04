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

_active_search_metrics_enabled() {
  case "${MEMORIES_ACTIVE_SEARCH_METRICS:-1}" in
    0|false|FALSE|no|NO|off|OFF) return 1 ;;
    *) return 0 ;;
  esac
}

_active_search_metrics_log() {
  _active_search_metrics_enabled || return 0
  local event_json="$1"
  local metrics_log="${MEMORIES_ACTIVE_SEARCH_LOG:-$HOME/.config/memories/active-search.jsonl}"
  local metrics_dir
  metrics_dir=$(dirname "$metrics_log")
  if ! [ -d "$metrics_dir" ] && ! mkdir -p "$metrics_dir" 2>/dev/null; then
    _log_warn "Active-search metrics log unavailable: cannot create $metrics_dir"
    return 0
  fi
  if ! printf '%s\n' "$event_json" >> "$metrics_log" 2>/dev/null; then
    _log_warn "Active-search metrics log unavailable: cannot write $metrics_log"
  fi
}

_hash_for_metrics() {
  local value="$1"
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$value" | shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$value" | sha256sum | awk '{print $1}'
  else
    printf ''
  fi
}

_source_prefix_quality() {
  local source_prefix="${1:-}"
  local project="${2:-}"
  if [ -z "$source_prefix" ]; then
    printf 'broad_or_unscoped'
    return 0
  fi
  if [ -n "$project" ]; then
    case "$source_prefix" in
      "claude-code/$project"|"claude-code/$project/"*|"codex/$project"|"codex/$project/"*|"learning/$project"|"learning/$project/"*|"wip/$project"|"wip/$project/"*)
        printf 'exact_project'
        return 0
        ;;
    esac
  fi
  case "$source_prefix" in
    claude-code/|codex/|learning/|wip/|claude-code|codex|learning|wip)
      printf 'broad_or_unscoped'
      ;;
    *)
      printf 'other'
      ;;
  esac
}

_memories_disabled() {
  case "${MEMORIES_DISABLED:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

_exit_if_disabled() {
  if _memories_disabled; then
    _log_info "Hook disabled by MEMORIES_DISABLED"
    exit 0
  fi
}

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
  if [ "$client_prefix" = "codex" ]; then
    printf 'codex/{project},claude-code/{project},learning/{project},wip/{project}'
  else
    printf 'claude-code/{project},codex/{project},learning/{project},wip/{project}'
  fi
}

_default_extract_source() {
  local client_prefix
  client_prefix="$(_memory_client_prefix)"
  printf '%s/{project}' "$client_prefix"
}

_resolve_env_reference() {
  local raw="$1"
  local env_var
  env_var=$(printf '%s' "$raw" | sed -n 's/.*${\([A-Za-z_][A-Za-z0-9_]*\)}.*/\1/p')
  if [ -z "$env_var" ]; then
    printf '%s' "$raw"
    return 0
  fi
  local env_value
  env_value=$(printenv "$env_var" 2>/dev/null || true)
  if [ -n "$env_value" ]; then
    printf '%s' "$env_value"
  else
    printf '%s' "$raw"
  fi
}

# Health check — returns 0 if service is reachable
_health_check() {
  local url="${MEMORIES_URL:-http://localhost:8900}"
  curl -sf --max-time 2 "$url/health" >/dev/null 2>&1
}

# -- Multi-Backend Config --------------------------------------------------

_BACKENDS_CACHE=""

# Pure-shell YAML parser for backends.yaml — handles the simple flat format only.
# Supports: backends.<name>.url, backends.<name>.api_key, backends.<name>.scenario,
# and routing.<op>: [name1, name2].
_parse_backends_yaml() {
  local file="$1"
  local current_section="" current_name="" backends_json="[]" routing_json="{}"
  local url="" api_key="" scenario="" _routing_current_key=""

  _flush_backend() {
    if [ -n "$current_name" ] && [ -n "$url" ]; then
      # Resolve ${VAR} references without bash 4 indirect expansion.
      local resolved_key resolved_url
      resolved_key="$(_resolve_env_reference "$api_key")"
      resolved_url="$(_resolve_env_reference "$url")"
      backends_json=$(printf '%s' "$backends_json" | jq -c --arg n "$current_name" \
        --arg u "$resolved_url" --arg k "$resolved_key" --arg s "$scenario" \
        '. + [{name: $n, url: $u, api_key: $k, scenario: $s}]')
    fi
    url="" api_key="" scenario="" current_name=""
  }

  while IFS= read -r line; do
    # Skip comments and blank lines
    case "$line" in
      '#'*|'') continue ;;
    esac

    # Top-level sections
    if printf '%s' "$line" | grep -qE '^backends:'; then
      current_section="backends"
      continue
    fi
    if printf '%s' "$line" | grep -qE '^routing:'; then
      current_section="routing"
      continue
    fi

    if [ "$current_section" = "backends" ]; then
      # Backend name line (2-space indent, no further indent)
      if printf '%s' "$line" | grep -qE '^  [a-zA-Z_][a-zA-Z0-9_-]*:'; then
        _flush_backend
        current_name=$(printf '%s' "$line" | sed 's/^ *//;s/:.*//')
      fi
      # Properties (4-space indent)
      if printf '%s' "$line" | grep -qE '^    url:'; then
        url=$(printf '%s' "$line" | sed 's/^    url: *//;s/^ *//;s/ *$//')
      fi
      if printf '%s' "$line" | grep -qE '^    api_key:'; then
        api_key=$(printf '%s' "$line" | sed 's/^    api_key: *//;s/^ *//;s/ *$//')
      fi
      if printf '%s' "$line" | grep -qE '^    scenario:'; then
        scenario=$(printf '%s' "$line" | sed 's/^    scenario: *//;s/^ *//;s/ *$//')
      fi
    fi

    if [ "$current_section" = "routing" ]; then
      # Routing supports two YAML formats:
      #   Inline:  search: [alpha, beta]
      #   Block:   search:\n  - alpha\n  - beta
      if printf '%s' "$line" | grep -qE '^ +- '; then
        # Block list item — append to current routing key (2 or 4 space indent)
        local item
        item=$(printf '%s' "$line" | sed 's/^ *- *//;s/ *$//')
        if [ -n "$item" ] && [ -n "$_routing_current_key" ]; then
          routing_json=$(printf '%s' "$routing_json" | jq -c --arg k "$_routing_current_key" --arg v "$item" \
            '.[$k] = ((.[$k] // []) + [$v])')
        fi
      elif printf '%s' "$line" | grep -qE '^  [a-z_]+:'; then
        local rkey rval
        rkey=$(printf '%s' "$line" | sed 's/^ *//;s/:.*//')
        rval=$(printf '%s' "$line" | sed 's/^[^:]*: *//;s/^ *//;s/ *$//')
        _routing_current_key="$rkey"
        if [ -n "$rval" ]; then
          # Inline format: search: [alpha, beta]
          rval=$(printf '%s' "$rval" | sed 's/\[//;s/\]//;s/,/ /g')
          local rarray="[]"
          for item in $rval; do
            item=$(printf '%s' "$item" | sed 's/^ *//;s/ *$//')
            [ -n "$item" ] && rarray=$(printf '%s' "$rarray" | jq -c --arg v "$item" '. + [$v]')
          done
          routing_json=$(printf '%s' "$routing_json" | jq -c --arg k "$rkey" --argjson v "$rarray" '. + {($k): $v}')
        fi
      fi
    fi
  done < "$file"
  _flush_backend

  jq -nc --argjson b "$backends_json" --argjson r "$routing_json" '{backends: $b, routing: $r}'
}

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
    # Parse YAML → JSON.  Try Node.js + js-yaml first (best fidelity),
    # fall back to a pure-shell parser for the simple backends.yaml format.
    local raw=""

    if command -v node >/dev/null 2>&1; then
      # Try to find js-yaml via multiple search paths.  Installed hooks live at
      # ~/.claude/hooks/memory/ (not in the repo), so the relative path to
      # mcp-server/node_modules won't resolve.  We search:
      #   1. Plain require (works if cwd is inside the repo)
      #   2. Relative to this script's directory (works in-repo)
      #   3. Well-known global config location
      local hooks_dir
      hooks_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
      local search_paths="${hooks_dir}/../../../mcp-server/node_modules:${hooks_dir}/../../mcp-server/node_modules"
      search_paths="${search_paths}:$HOME/.config/memories/mcp-server/node_modules"

      raw=$(NODE_PATH="${search_paths}:${NODE_PATH:-}" node -e "
try {
  const yaml = require('js-yaml');
  const fs = require('fs');
  const data = yaml.load(fs.readFileSync('${config_file}', 'utf8'));
  const interp = (v) => { const m = (v||'').match(/\\\$\{(\w+)\}/); return m ? (process.env[m[1]] || v) : v; };
  const backends = Object.entries(data.backends || {}).map(([name, cfg]) => {
    return { name, url: interp(cfg.url || ''), api_key: interp(cfg.api_key || ''), scenario: cfg.scenario || '' };
  });
  console.log(JSON.stringify({ backends, routing: data.routing || {} }));
} catch(e) {
  process.exit(1);
}
" 2>/dev/null) || raw=""
    fi

    # Fallback: pure-shell parser for the simple flat YAML format
    # (handles: backends.<name>.url, .api_key, .scenario; routing.<op>: [list])
    if [ -z "$raw" ]; then
      raw=$(_parse_backends_yaml "$config_file")
    fi
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

# -- Multi-Backend Search --------------------------------------------------

_search_memories_multi() {
  local query="$1"
  local prefix="${2:-}"
  local limit="${3:-5}"
  local threshold="${4:-0.4}"

  local backends
  backends=$(_get_backends_for_op "search")
  local count
  count=$(echo "$backends" | jq 'length')

  local body
  if [ -n "$prefix" ]; then
    body=$(jq -nc --arg q "$query" --arg p "$prefix" --argjson k "$limit" --argjson t "$threshold" \
      '{query: $q, source_prefix: $p, k: $k, hybrid: true, threshold: $t}')
  else
    body=$(jq -nc --arg q "$query" --argjson k "$limit" --argjson t "$threshold" \
      '{query: $q, k: $k, hybrid: true, threshold: $t}')
  fi

  if [ "$count" -le 1 ]; then
    # Single backend — direct call (backward compat, no overhead)
    local url key
    url=$(echo "$backends" | jq -r '.[0].url')
    key=$(echo "$backends" | jq -r '.[0].api_key')
    curl -sf --max-time 4 -X POST "$url/search" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $key" \
      -d "$body" 2>/dev/null || echo '{"results":[],"count":0}'
    return
  fi

  # Multi-backend: parallel fan-out with background subshells
  # Use process substitution (< <(...)) so the while loop runs in the current
  # shell and `wait` can actually collect the background jobs.
  local tmpdir
  tmpdir=$(mktemp -d)
  local i=0
  while read -r backend; do
    local url key name
    url=$(echo "$backend" | jq -r '.url')
    key=$(echo "$backend" | jq -r '.api_key')
    name=$(echo "$backend" | jq -r '.name')
    (
      result=$(curl -sf --max-time 4 -X POST "$url/search" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $key" \
        -d "$body" 2>/dev/null)
      if [ -n "$result" ]; then
        # Tag results with _backend
        echo "$result" | jq -c --arg b "$name" '.results[] | . + {_backend: $b}' > "$tmpdir/result_${name}.jsonl"
      fi
    ) &
    i=$((i + 1))
  done < <(echo "$backends" | jq -c '.[]')
  wait

  # Merge results: sort by score, dedup keeping highest-scoring duplicate,
  # then re-sort to guarantee global score ordering after dedup.
  cat "$tmpdir"/result_*.jsonl 2>/dev/null | jq -s '
    sort_by(-(.similarity // .rrf_score // 0))
    | unique_by(.text)
    | sort_by(-(.similarity // .rrf_score // 0))
  ' | jq -c '{results: ., count: length}'

  rm -rf "$tmpdir"
}

# -- Multi-Backend Extract -------------------------------------------------

_extract_multi() {
  local messages="$1"
  local source="$2"
  local context="${3:-stop}"

  local backends
  backends=$(_get_backends_for_op "extract")
  # Pass current timestamp as document_at for temporal reasoning
  local doc_at
  doc_at=$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")
  local body
  body=$(jq -nc --arg m "$messages" --arg s "$source" --arg c "$context" --arg d "$doc_at" \
    '{messages: $m, source: $s, context: $c, document_at: $d}')

  echo "$backends" | jq -c '.[]' | while read -r backend; do
    local url key name
    url=$(echo "$backend" | jq -r '.url')
    key=$(echo "$backend" | jq -r '.api_key')
    name=$(echo "$backend" | jq -r '.name')
    curl -sf --max-time 30 -X POST "$url/memory/extract" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $key" \
      -d "$body" > /dev/null 2>&1 || _log_error "Extract failed for backend $name"
  done
}
