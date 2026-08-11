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
      "claude-code/$project"|"claude-code/$project/"*|"codex/$project"|"codex/$project/"*|"opencode/$project"|"opencode/$project/"*|"learning/$project"|"learning/$project/"*|"wip/$project"|"wip/$project/"*)
        printf 'exact_project'
        return 0
        ;;
    esac
  fi
  case "$source_prefix" in
    claude-code/|codex/|opencode/|learning/|wip/|claude-code|codex|opencode|learning|wip)
      printf 'broad_or_unscoped'
      ;;
    *)
      printf 'other'
      ;;
  esac
}

# -- Playbook injection gate -------------------------------------------------

# Prompts that require active memory search (candidate-pointer rendering plus
# prompt_evaluated metrics in memory-query.sh). Single source of truth for the
# regex previously inlined in the query hooks.
_active_search_pattern() {
  printf '%s' '(^|[^a-z])(did we already|do you remember|remember (how|what|where|when|why|the)|recall|already decide|where did we|how did we|what did we|what was the last|where we left|left off|resume|continue where|previous|prior|earlier|last (fix|time|decision|session|run)|deferred|blocked|follow.?up|next steps|what.?s the plan|what is the plan|current plan|existing plan|release gate|gated)([^a-z]|$)'
}

# Additional prior-work shapes that gate the full playbook mandate without
# changing active-search rendering or metrics classification. Includes the
# follow-up shapes from response-hints.json so hint-worthy short follow-ups
# referencing the current topic always carry the full mandate.
_prior_work_extra_pattern() {
  printf '%s' '(^|[^a-z])(weren.?t we|didn.?t we|did we|do we (already|still)|have we|haven.?t we|were we|we were|we did|last time|what version|which version|what mode|how (does|do|did) .{1,60} work|is .{1,40} still|are .{1,40} still|does .{1,40} still|continue|continuing|resume|resuming|pick up where|what about|how about|and for|regarding|still (valid|relevant|true|appl|slow|broken|failing|open|pending)|still on|we.?re still|we are still|don.?t want to change|do not want to change|should we (switch|move|change) to|(okay|fine|good enough|works) for now)([^a-z]|$)'
}

# Decide how much playbook the UserPromptSubmit hook injects for this prompt.
# Usage: _playbook_injection_mode "<prompt>" "<candidate_count>"
# Echoes "full" when retrieval returned >=1 candidate memory OR the prompt is
# prior-work-shaped; echoes "minimal" (1-2 line reminder) otherwise.
_playbook_injection_mode() {
  local prompt="${1:-}"
  local candidate_count="${2:-0}"
  case "$candidate_count" in
    ''|*[!0-9]*) candidate_count=0 ;;
  esac
  local prompt_lower
  prompt_lower=$(printf '%s' "$prompt" | tr '[:upper:]' '[:lower:]')
  if printf '%s' "$prompt_lower" | grep -qiE "$(_active_search_pattern)"; then
    printf 'full'
    return 0
  fi
  if printf '%s' "$prompt_lower" | grep -qiE "$(_prior_work_extra_pattern)"; then
    printf 'full'
    return 0
  fi
  if [ "$candidate_count" -ge 1 ]; then
    printf 'memories'
    return 0
  fi
  printf 'minimal'
}

# Extract memory ids touched by a memory tool call, for the recall-feedback
# loop. Reads ids from tool input (id / memory_id / ids) and parses the
# unambiguous "id=N" / "(id: N)" markers from the tool response text. Result
# index markers like "[1]" are NOT parsed. Emits a JSON array (max 50 ids).
_memory_ids_for_metrics() {
  local input_json="$1"
  local input_ids response_ids
  input_ids=$(printf '%s' "$input_json" | jq -c '
    def ids_of($o): if ($o | type) == "object"
      then [($o | .id? // empty), ($o | .memory_id? // empty)]
        + (($o | .ids? // []) | if type == "array" then . else [] end)
      else [] end;
    (ids_of(.tool_input? // {}) + ids_of(.tool_input.arguments? // {})
      + ids_of(.input? // {}) + ids_of(.arguments? // {}))
    | map(select(type == "number"))
  ' 2>/dev/null) || input_ids='[]'
  [ -z "$input_ids" ] && input_ids='[]'
  response_ids=$(printf '%s' "$input_json" \
    | jq -r '.tool_response // empty | tostring' 2>/dev/null \
    | head -c 20000 \
    | { grep -oE '(^|[^A-Za-z0-9_])id=[0-9]+|\(id: [0-9]+\)' || true; } \
    | { grep -oE '[0-9]+' || true; } \
    | sort -un | head -50 \
    | jq -Rcs '[splits("\n") | select(length > 0) | tonumber]' 2>/dev/null) || response_ids='[]'
  [ -z "$response_ids" ] && response_ids='[]'
  jq -nc --argjson a "$input_ids" --argjson b "$response_ids" '$a + $b | unique | .[0:50]' 2>/dev/null || echo '[]'
}

# Resolve the project name for a cwd. Git worktree checkouts (e.g. Claude
# Code's .claude/worktrees/<name>) resolve to the MAIN repo's directory name,
# not the worktree directory, so worktree sessions share the project's
# memories instead of scoping recall/capture to a throwaway name.
_memories_resolve_project() {
  local cwd="${1:-}"
  local fallback
  fallback=$(basename "${cwd:-unknown}")
  if [ -z "$cwd" ] || ! command -v git >/dev/null 2>&1; then
    printf '%s' "$fallback"; return 0
  fi
  local common
  common=$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null) || { printf '%s' "$fallback"; return 0; }
  [ -z "$common" ] && { printf '%s' "$fallback"; return 0; }
  case "$common" in
    /*) ;;
    *) common="$cwd/$common" ;;
  esac
  if [ "$(basename "$common")" = ".git" ]; then
    local root
    root=$(CDPATH= cd "$(dirname "$common")" 2>/dev/null && pwd)
    if [ -n "$root" ] && [ "$root" != "/" ]; then
      printf '%s' "$(basename "$root")"; return 0
    fi
  fi
  printf '%s' "$fallback"
}

_memories_disabled() {
  case "${MEMORIES_DISABLED:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

# A hook is active only when explicitly enabled or when a backend is actually
# configured. The optional cwd lets callers pass a payload workspace before
# loading the backend file; explicit/project/global precedence remains in the
# resolver itself.
_memories_has_backend_config() {
  local cwd="${1:-}"
  [ -n "${MEMORIES_URL:-}" ] && return 0
  _resolve_backends_file "$cwd" >/dev/null
}

_memories_active() {
  local cwd="${1:-}"
  if _memories_disabled; then
    return 1
  fi
  if [ -n "${MEMORIES_ENABLED+x}" ]; then
    case "${MEMORIES_ENABLED}" in
      1|true|TRUE|yes|YES|on|ON) return 0 ;;
      0|false|FALSE|no|NO|off|OFF) return 1 ;;
    esac
  fi
  _memories_has_backend_config "$cwd"
}

_exit_if_disabled() {
  local cwd="${1:-}"
  if _memories_disabled; then
    _log_info "Hook disabled by MEMORIES_DISABLED"
    exit 0
  fi
  if _memories_active "$cwd"; then
    return 0
  fi
  if [ -n "${MEMORIES_ENABLED+x}" ]; then
    _log_info "Hook disabled by MEMORIES_ENABLED=${MEMORIES_ENABLED}"
  fi
  # No explicit opt-in and no configured backend: true silent no-op. Do not
  # log here because doing so would create a config directory for every
  # contributor who never enabled memory hooks.
  exit 0
}

# Rotate log if over 1000 lines (called from SessionStart only)
_rotate_log() {
  if [ -f "$MEMORIES_LOG" ] && [ "$(wc -l < "$MEMORIES_LOG" 2>/dev/null)" -gt 1000 ]; then
    tail -500 "$MEMORIES_LOG" > "$MEMORIES_LOG.tmp" && mv "$MEMORIES_LOG.tmp" "$MEMORIES_LOG"
    _log_info "Log rotated (kept last 500 lines)"
  fi
}

_memory_client_prefix() {
  printf 'codex'
}

_default_source_prefixes() {
  local client_prefix
  client_prefix="$(_memory_client_prefix)"
  printf 'codex/{project},claude-code/{project},learning/{project},wip/{project}'
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


# -- Hook-wide deadline -------------------------------------------------------
_MEMORIES_HOOK_BUDGET_MS_DEFAULT=5000
_MEMORIES_HOOK_BUDGET_MARGIN_MS=500
_MEMORIES_HOOK_CALL_RESERVE_S="0.15"
_MEMORIES_HOOK_MIN_CALL_S="0.3"

_hook_now_s() {
  jq -n 'now' 2>/dev/null || date +%s
}

_hook_deadline_init() {
  local start
  start=$(_hook_now_s)
  local budget_ms="${MEMORIES_HOOK_BUDGET_MS:-$_MEMORIES_HOOK_BUDGET_MS_DEFAULT}"
  _MEMORIES_HOOK_DEADLINE_S=$(jq -n \
    --argjson start "$start" \
    --argjson budget_ms "$budget_ms" \
    --argjson margin_ms "$_MEMORIES_HOOK_BUDGET_MARGIN_MS" \
    '$start + (($budget_ms - $margin_ms) / 1000)' 2>/dev/null) || _MEMORIES_HOOK_DEADLINE_S=""
}

_hook_remaining_s() {
  if [ -z "${_MEMORIES_HOOK_DEADLINE_S:-}" ]; then
    printf '999999'
    return 0
  fi
  local now
  now=$(_hook_now_s)
  jq -n --argjson deadline "$_MEMORIES_HOOK_DEADLINE_S" --argjson now "$now" \
    '$deadline - $now' 2>/dev/null || printf '999999'
}

_hook_deadline_exhausted() {
  local remaining
  remaining=$(_hook_remaining_s)
  jq -n --argjson remaining "$remaining" --argjson min "$_MEMORIES_HOOK_MIN_CALL_S" \
    '$remaining < $min' 2>/dev/null || printf 'false'
}

_hook_call_budget() {
  local existing_cap="$1"
  local remaining
  remaining=$(_hook_remaining_s)
  local budget
  budget=$(jq -n --argjson cap "$existing_cap" --argjson remaining "$remaining" \
    --argjson reserve "$_MEMORIES_HOOK_CALL_RESERVE_S" \
    '[$cap, ($remaining - $reserve)] | min' 2>/dev/null) || budget="$existing_cap"
  local ok
  ok=$(jq -n --argjson budget "$budget" --argjson min "$_MEMORIES_HOOK_MIN_CALL_S" \
    '$budget >= $min' 2>/dev/null) || ok="false"
  if [ "$ok" != "true" ]; then
    return 1
  fi
  printf '%s' "$budget"
  return 0
}


# -- Backend circuit breaker --------------------------------------------------
# When the backend is down or slow, every hook on every prompt pays full curl
# timeouts (~8s measured across a prompt's hook fan-out). After a failure the
# breaker file makes subsequent hook invocations skip backend calls instantly
# until the cooldown elapses (then one half-open retry).
_MEMORIES_BREAKER_FILE="${MEMORIES_BREAKER_FILE:-$HOME/.config/memories/backend-down}"
_MEMORIES_BREAKER_COOLDOWN="${MEMORIES_BREAKER_COOLDOWN:-60}"

_breaker_file_for() {
  local name="${1-default}"
  if [ "$name" = "default" ]; then
    printf '%s' "$_MEMORIES_BREAKER_FILE"
  else
    # Backend names are arbitrary YAML keys. Encode every byte rather than
    # replacing punctuation, so names such as foo/bar and foo?bar cannot
    # share a breaker file. `od` is available on macOS and Linux; the fixed
    # namespace keeps these paths distinct from the historical default file.
    local encoded
    encoded=$(printf '%s' "$name" | LC_ALL=C od -An -tx1 | tr -d '[:space:]')
    printf '%s.backend-%s' "$_MEMORIES_BREAKER_FILE" "$encoded"
  fi
}

_breaker_open() {
  local file
  file=$(_breaker_file_for "${1:-default}")
  [ -f "$file" ] || return 1
  local ts now age
  ts=$(cat "$file" 2>/dev/null)
  case "$ts" in ''|*[!0-9]*) rm -f "$file" 2>/dev/null; return 1 ;; esac
  now=$(date +%s)
  age=$((now - ts))
  [ "$age" -lt "$_MEMORIES_BREAKER_COOLDOWN" ] && return 0
  return 1
}

_breaker_trip() {
  local name="${1:-default}"
  local file
  file=$(_breaker_file_for "$name")
  mkdir -p "$(dirname "$file")" 2>/dev/null
  date +%s > "$file" 2>/dev/null
  _log_warn "Memories backend '$name' unreachable — circuit open for ${_MEMORIES_BREAKER_COOLDOWN}s" 2>/dev/null || true
}

_breaker_reset() {
  local file
  file=$(_breaker_file_for "${1:-default}")
  rm -f "$file" 2>/dev/null
  return 0
}

# A timeout is evidence about the backend only when it received a fair share
# of the requested per-call budget. Shrinking deadline budgets must not trip a
# healthy backend's breaker.
_MEMORIES_BREAKER_FAIR_BUDGET_RATIO="${MEMORIES_BREAKER_FAIR_BUDGET_RATIO:-0.75}"

_should_trip_breaker() {
  local rc="$1" budget="$2" cap="$3"
  [ "$rc" = "28" ] || return 0
  local fair
  fair=$(jq -n --argjson b "$budget" --argjson c "$cap" \
    --argjson r "$_MEMORIES_BREAKER_FAIR_BUDGET_RATIO" \
    '$b >= ($c * $r)' 2>/dev/null) || fair="true"
  [ "$fair" = "true" ] && return 0
  return 1
}

# Health check probes every routed search backend in parallel and leaves
# per-backend breaker state for the search fan-out to consume.
_health_check() {
  MEMORIES_HEALTH_DOWN_NAMES=""

  local backends
  backends=$(_get_backends_for_op "search" 2>/dev/null) || backends="[]"
  local backend_count
  backend_count=$(printf '%s' "$backends" | jq 'length' 2>/dev/null) || backend_count=0
  if [ "${backend_count:-0}" -eq 0 ]; then
    MEMORIES_HEALTH_DOWN_NAMES="no backend resolved for search routing"
    MEMORIES_BACKEND_DOWN=1
    return 1
  fi

  local probe_budget
  if ! probe_budget=$(_hook_call_budget 2); then
    MEMORIES_HEALTH_DOWN_NAMES="hook budget exhausted before any health probe"
    MEMORIES_BACKEND_DOWN=1
    return 1
  fi

  local tmpdir
  tmpdir=$(mktemp -d)
  local backend url name backend_idx
  backend_idx=0
  while read -r backend; do
    backend_idx=$((backend_idx + 1))
    url=$(printf '%s' "$backend" | jq -r '.url')
    name=$(printf '%s' "$backend" | jq -r '.name')
    if _breaker_open "$name"; then
      printf '%s (%s)' "$name" "$url" > "$tmpdir/down_${backend_idx}"
      continue
    fi
    (
      if curl -sf --max-time "$probe_budget" "$url/health" >/dev/null 2>&1; then
        _breaker_reset "$name"
        : > "$tmpdir/up_${backend_idx}"
      else
        _breaker_trip "$name"
        printf '%s (%s)' "$name" "$url" > "$tmpdir/down_${backend_idx}"
      fi
    ) &
  done < <(printf '%s' "$backends" | jq -c '.[]')
  wait

  local up_count=0
  local f
  for f in "$tmpdir"/up_*; do
    [ -e "$f" ] || continue
    up_count=$((up_count + 1))
  done
  for f in "$tmpdir"/down_*; do
    [ -e "$f" ] || continue
    if [ -n "$MEMORIES_HEALTH_DOWN_NAMES" ]; then
      MEMORIES_HEALTH_DOWN_NAMES="$MEMORIES_HEALTH_DOWN_NAMES, $(cat "$f")"
    else
      MEMORIES_HEALTH_DOWN_NAMES="$(cat "$f")"
    fi
  done
  rm -rf "$tmpdir"

  if [ "$up_count" -gt 0 ]; then
    MEMORIES_BACKEND_DOWN=0
    return 0
  fi
  MEMORIES_BACKEND_DOWN=1
  return 1
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
      local resolved_key resolved_url key_env
      resolved_key="$(_resolve_env_reference "$api_key")"
      resolved_url="$(_resolve_env_reference "$url")"
      key_env=$(printf '%s' "$api_key" | sed -n 's/.*${\([A-Za-z_][A-Za-z0-9_]*\)}.*/\1/p')
      backends_json=$(printf '%s' "$backends_json" | jq -c --arg n "$current_name" \
        --arg u "$resolved_url" --arg k "$resolved_key" --arg s "$scenario" \
        --arg ke "$key_env" --argjson eb false \
        '. + [{name: $n, url: $u, api_key: $k, api_key_env: $ke, env_backed: $eb, scenario: $s}]')
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

# Resolve the backend config using one precedence shared by activation and
# loading: explicit env override, project root, payload cwd, then global file.
_resolve_backends_file() {
  local cwd="${1:-}"
  if [ -n "${MEMORIES_BACKENDS_FILE:-}" ] && [ -f "${MEMORIES_BACKENDS_FILE}" ]; then
    printf '%s' "${MEMORIES_BACKENDS_FILE}"
    return 0
  fi
  local project_dir="${CODEX_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$PWD}}"
  if [ -n "$project_dir" ] && [ -f "$project_dir/.memories/backends.yaml" ]; then
    printf '%s' "$project_dir/.memories/backends.yaml"
    return 0
  fi
  if [ -n "$cwd" ] && [ -f "$cwd/.memories/backends.yaml" ]; then
    printf '%s' "$cwd/.memories/backends.yaml"
    return 0
  fi
  if [ -f "$HOME/.config/memories/backends.yaml" ]; then
    printf '%s' "$HOME/.config/memories/backends.yaml"
    return 0
  fi
  return 1
}

_load_backends() {
  # Return cached if already loaded
  if [ -n "$_BACKENDS_CACHE" ]; then
    echo "$_BACKENDS_CACHE" | jq -c '.backends'
    return 0
  fi

  local config_file=""
  config_file=$(_resolve_backends_file "${CWD:-}" 2>/dev/null) || config_file=""

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
    const keyRef = String(cfg.api_key || '').match(/\\\$\{([A-Za-z_][A-Za-z0-9_]*)\}/);
    return { name, url: interp(cfg.url || ''), api_key: interp(cfg.api_key || ''), api_key_env: keyRef ? keyRef[1] : '', env_backed: false, scenario: cfg.scenario || '' };
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
      '{backends: [{name: "default", url: $url, api_key: $key, api_key_env: "MEMORIES_API_KEY", env_backed: true, scenario: ""}], routing: {}}')
    echo "$_BACKENDS_CACHE" | jq -c '.backends'
  fi
}

_resolve_primary_backend_url() {
  local backends url
  backends=$(_get_backends_for_op "search" 2>/dev/null) || backends="[]"
  url=$(printf '%s' "$backends" | jq -r '.[0].url // empty' 2>/dev/null) || url=""
  if [ -n "$url" ]; then
    printf '%s' "$url"
  else
    printf '%s' "${MEMORIES_URL:-http://localhost:8900}"
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
  local usage_source="${MEMORIES_USAGE_SOURCE:-}"
  local usage_client="${MEMORIES_USAGE_CLIENT:-$(_memory_client_prefix 2>/dev/null || echo codex)}"
  local usage_session_id="${MEMORIES_USAGE_SESSION_ID:-}"
  local usage_invocation="${MEMORIES_USAGE_INVOCATION:-${MEMORIES_HOOK_NAME:-hook}}"

  local backends
  backends=$(_get_backends_for_op "search")
  local count
  count=$(echo "$backends" | jq 'length')

  if [ "$count" -eq 0 ]; then
    MEMORIES_BACKEND_DOWN=1
    echo '{"results":[],"count":0}'
    return
  fi

  local body
  if [ -n "$prefix" ]; then
    body=$(jq -nc --arg q "$query" --arg p "$prefix" --arg s "$usage_source" --argjson k "$limit" --argjson t "$threshold" \
      '{query: $q, source_prefix: $p, source: $s, k: $k, hybrid: true, threshold: $t}')
  else
    body=$(jq -nc --arg q "$query" --arg s "$usage_source" --argjson k "$limit" --argjson t "$threshold" \
      '{query: $q, source: $s, k: $k, hybrid: true, threshold: $t}')
  fi

  if [ "$count" -le 1 ]; then
    # Single backend — direct call (backward compat, no overhead). Breaker
    # state is keyed by this backend's name; the env-only fallback remains
    # "default" and therefore keeps the historical breaker filename.
    local url key name api_key_env env_backed
    url=$(echo "$backends" | jq -r '.[0].url')
    key=$(echo "$backends" | jq -r '.[0].api_key')
    name=$(echo "$backends" | jq -r '.[0].name // "default"')
    api_key_env=$(echo "$backends" | jq -r '.[0].api_key_env // empty')
    env_backed=$(echo "$backends" | jq -r '.[0].env_backed // false')

    if _breaker_open "$name"; then
      MEMORIES_BACKEND_DOWN=1
      echo '{"results":[],"count":0}'
      return
    fi

    local call_budget
    if ! call_budget=$(_hook_call_budget 4); then
      MEMORIES_BACKEND_DOWN=1
      echo '{"results":[],"count":0}'
      return
    fi

    # Keep Codex's usage headers and source payload while retaining the status
    # code so authenticated 401s are distinguishable from reachability errors.
    local raw curl_rc status out
    raw=$(curl -s --max-time "$call_budget" -w '\n%{http_code}' -X POST "$url/search" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $key" \
      -H "X-Memories-Client: $usage_client" \
      -H "X-Memories-Session-Id: $usage_session_id" \
      -H "X-Memories-Invocation: $usage_invocation" \
      -d "$body" 2>/dev/null)
    curl_rc=$?
    status="${raw##*$'\n'}"
    out="${raw%$'\n'*}"
    if [ $curl_rc -eq 0 ] && [ -n "$raw" ] && [ "$status" != "$raw" ]; then
      case "$status" in
        2??)
          _breaker_reset "$name"
          printf '%s' "$out"
          ;;
        401)
          _breaker_reset "$name"
          MEMORIES_AUTH_FAILED=1
          jq -nc --arg name "$name" --arg url "$url" --arg api_key_env "$api_key_env" --argjson env_backed "$env_backed" \
            '{results: [], count: 0, auth_failed: true, auth_failed_backends: [{name: $name, url: $url, api_key_env: $api_key_env, env_backed: $env_backed}]}'
          ;;
        *)
          _breaker_trip "$name"
          MEMORIES_BACKEND_DOWN=1
          echo '{"results":[],"count":0}'
          ;;
      esac
    else
      if _should_trip_breaker "$curl_rc" "$call_budget" 4; then
        _breaker_trip "$name"
      fi
      MEMORIES_BACKEND_DOWN=1
      echo '{"results":[],"count":0}'
    fi
    return
  fi

  # Multi-backend: parallel fan-out. Each backend has independent breaker
  # state, so one failed backend cannot suppress healthy peers.
  local call_budget
  if ! call_budget=$(_hook_call_budget 4); then
    MEMORIES_BACKEND_DOWN=1
    echo '{"results":[],"count":0}'
    return
  fi

  local tmpdir
  tmpdir=$(mktemp -d)
  local attempted=0
  local backend_idx=0
  while read -r backend; do
    backend_idx=$((backend_idx + 1))
    local url key name api_key_env env_backed output_id
    url=$(echo "$backend" | jq -r '.url')
    key=$(echo "$backend" | jq -r '.api_key')
    name=$(echo "$backend" | jq -r '.name')
    api_key_env=$(echo "$backend" | jq -r '.api_key_env // empty')
    env_backed=$(echo "$backend" | jq -r '.env_backed // false')
    output_id="$backend_idx"
    if _breaker_open "$name"; then
      continue
    fi
    attempted=1
    (
      raw=$(curl -s --max-time "$call_budget" -w '\n%{http_code}' -X POST "$url/search" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $key" \
        -H "X-Memories-Client: $usage_client" \
        -H "X-Memories-Session-Id: $usage_session_id" \
        -H "X-Memories-Invocation: $usage_invocation" \
        -d "$body" 2>/dev/null)
      search_rc=$?
      status="${raw##*$'\n'}"
      result="${raw%$'\n'*}"
      if [ $search_rc -eq 0 ] && [ -n "$raw" ] && [ "$status" != "$raw" ]; then
        case "$status" in
          2??)
            _breaker_reset "$name"
            echo "$result" | jq -c --arg b "$name" '.results[] | . + {_backend: $b}' > "$tmpdir/result_${output_id}.jsonl"
            ;;
          401)
            _breaker_reset "$name"
            jq -nc --arg name "$name" --arg url "$url" --arg api_key_env "$api_key_env" --argjson env_backed "$env_backed" \
              '{name: $name, url: $url, api_key_env: $api_key_env, env_backed: $env_backed}' > "$tmpdir/auth_failed_${output_id}.json"
            ;;
          *)
            _breaker_trip "$name"
            ;;
        esac
      elif _should_trip_breaker "$search_rc" "$call_budget" 4; then
        _breaker_trip "$name"
      fi
    ) &
  done < <(echo "$backends" | jq -c '.[]')
  wait

  if [ "$attempted" -eq 0 ]; then
    rm -rf "$tmpdir"
    MEMORIES_BACKEND_DOWN=1
    echo '{"results":[],"count":0}'
    return
  fi

  # Merge results: sort by score, dedup keeping highest-scoring duplicate,
  # then re-sort to guarantee global score ordering after dedup.
  # Intentionally merge on RAW scores (similarity/rrf_score), not relative_score:
  # raw RRF values share one scale across searches against the same backend,
  # while relative_score is normalized per result set (top of every set = 1.0)
  # and would let a single-result set outrank everything. relative_score is for
  # display only.
  local merged
  merged=$(cat "$tmpdir"/result_*.jsonl 2>/dev/null | jq -s '
    sort_by(-(.similarity // .rrf_score // 0))
    | unique_by(.text)
    | sort_by(-(.similarity // .rrf_score // 0))
  ' | jq -c '{results: ., count: length}')
  local auth_backends='[]'
  local auth_file
  for auth_file in "$tmpdir"/auth_failed_*.json; do
    [ -e "$auth_file" ] || continue
    auth_backends=$(cat "$auth_file" | jq -c --argjson existing "$auth_backends" '$existing + [.] | unique_by((.name // "") + "|" + (.url // ""))')
  done
  if [ "$auth_backends" != "[]" ]; then
    printf '%s' "$merged" | jq -c --argjson auth_backends "$auth_backends" \
      '. + {auth_failed: true, auth_failed_backends: $auth_backends}'
  else
    printf '%s' "$merged"
  fi

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
