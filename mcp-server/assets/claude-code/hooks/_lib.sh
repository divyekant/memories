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

# Resolve the main repository boundary for a cwd. Git worktrees have their
# own checkout root but share the main repository's git-common-dir; using that
# shared directory keeps the committed project declaration authoritative for
# both the main checkout and every worktree.
_memories_resolve_repo_root() {
  local cwd="${1:-}"
  [ -n "$cwd" ] || return 1
  local resolved
  resolved=$(CDPATH= cd -P "$cwd" 2>/dev/null && pwd -P) || return 1
  if ! command -v git >/dev/null 2>&1; then
    printf '%s' "$resolved"
    return 0
  fi
  local common
  common=$(git -C "$resolved" rev-parse --git-common-dir 2>/dev/null) || {
    printf '%s' "$resolved"
    return 0
  }
  [ -z "$common" ] && { printf '%s' "$resolved"; return 0; }
  case "$common" in
    /*) ;;
    *) common="$resolved/$common" ;;
  esac
  if [ "$(basename "$common")" = ".git" ]; then
    local root
    root=$(CDPATH= cd -P "$(dirname "$common")" 2>/dev/null && pwd -P)
    if [ -n "$root" ] && [ "$root" != "/" ]; then
      printf '%s' "$root"
      return 0
    fi
  fi
  printf '%s' "$resolved"
}

# Resolve the project name for a cwd while preserving the historical basename
# fallback for non-git directories and empty input.
_memories_resolve_project() {
  local cwd="${1:-}"
  local fallback
  fallback=$(basename "${cwd:-unknown}")
  [ -n "$cwd" ] || { printf '%s' "$fallback"; return 0; }
  command -v git >/dev/null 2>&1 || { printf '%s' "$fallback"; return 0; }
  local common
  common=$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null) || {
    printf '%s' "$fallback"
    return 0
  }
  [ -n "$common" ] || { printf '%s' "$fallback"; return 0; }
  case "$common" in
    /*) ;;
    *) common="$cwd/$common" ;;
  esac
  if [ "$(basename "$common")" = ".git" ]; then
    local root
    root=$(CDPATH= cd "$(dirname "$common")" 2>/dev/null && pwd)
    if [ -n "$root" ] && [ "$root" != "/" ]; then
      printf '%s' "$(basename "$root")"
      return 0
    fi
  fi
  printf '%s' "$fallback"
}

# -- Collaborative project declaration ---------------------------------------
#
# A repository declaration is an opt-in hint only.  It never grants access;
# the authenticated backend principal below is still required.  Keep this
# parser deliberately small and dependency-free because packaged hooks may be
# copied away from the MCP npm install (and therefore cannot assume Node or
# js-yaml is present).
_memories_parse_project_yaml() {
  local file="$1"
  [ -f "$file" ] || {
    jq -nc '{ok:false,reason:"missing",diagnostic:"no .memories/project.yaml at the repository boundary"}'
    return 0
  }

  local project_id="" shared_memory="" seen_project=0 seen_shared=0 seen_any=0
  local raw line key value
  while IFS= read -r raw || [ -n "$raw" ]; do
    line="${raw%$'\r'}"
    line=$(printf '%s' "$line" | sed 's/[[:space:]]*$//')
    case "$line" in
      ''|'#'*) continue ;;
    esac
    # Top-level scalar mappings only; nested YAML is not part of the contract.
    case "$line" in
      [[:space:]]*)
        jq -nc '{ok:false,reason:"malformed",diagnostic:"project declaration must contain only top-level fields"}'
        return 0
        ;;
    esac
    if ! printf '%s' "$line" | grep -qE '^[A-Za-z_][A-Za-z0-9_-]*:[[:space:]]*[^:]*([[:space:]]+#.*)?$'; then
      jq -nc '{ok:false,reason:"malformed",diagnostic:"project declaration contains malformed YAML"}'
      return 0
    fi
    key=$(printf '%s' "$line" | sed 's/:.*//')
    value=$(printf '%s' "$line" | sed -E 's/^[^:]*:[[:space:]]*//;s/[[:space:]]*$//')
    case "$value" in
      \"*\"|\'*\' ) ;;
      *) value=$(printf '%s' "$value" | sed -E 's/[[:space:]]+#.*$//;s/[[:space:]]*$//') ;;
    esac
    seen_any=1
    case "$key" in
      project_id)
        [ "$seen_project" -eq 0 ] || {
          jq -nc '{ok:false,reason:"malformed",diagnostic:"project_id is declared more than once"}'
          return 0
        }
        seen_project=1
        project_id="$value"
        ;;
      shared_memory)
        [ "$seen_shared" -eq 0 ] || {
          jq -nc '{ok:false,reason:"malformed",diagnostic:"shared_memory is declared more than once"}'
          return 0
        }
        seen_shared=1
        shared_memory="$value"
        ;;
      *)
        jq -nc --arg key "$key" '{ok:false,reason:"unknown_field",diagnostic:("unknown project declaration field: " + $key)}'
        return 0
        ;;
    esac
  done < "$file"

  [ "$seen_any" -eq 1 ] || {
    jq -nc '{ok:false,reason:"malformed",diagnostic:"project declaration must be a YAML mapping"}'
    return 0
  }
  if [ "$seen_project" -eq 0 ] || [ "$seen_shared" -eq 0 ]; then
    jq -nc --arg missing "$( [ "$seen_project" -eq 0 ] && printf 'project_id' || true; [ "$seen_shared" -eq 0 ] && { [ "$seen_project" -eq 0 ] && printf ', '; printf 'shared_memory'; } )" \
      '{ok:false,reason:"missing_field",diagnostic:("missing project declaration field: " + $missing)}'
    return 0
  fi
  local project_quoted=0
  case "$project_id" in
    \"*\"|\'*\') project_quoted=1; project_id="${project_id:1:${#project_id}-2}" ;;
  esac
  case "$project_id" in
    \[*|\{*)
      jq -nc '{ok:false,reason:"malformed",diagnostic:"project declaration contains malformed YAML"}'
      return 0
      ;;
  esac
  if [ "$project_quoted" -eq 0 ] && {
    case "$project_id" in
      true|True|TRUE|false|False|FALSE|null|Null|NULL|~) true ;;
      *) printf '%s' "$project_id" | grep -qE '^[0-9]+$' ;;
    esac
  }; then
    jq -nc '{ok:false,reason:"invalid_project_id",diagnostic:"project_id must be a lowercase path-safe slug"}'
    return 0
  fi
  if ! printf '%s' "$project_id" | grep -qE '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'; then
    jq -nc '{ok:false,reason:"invalid_project_id",diagnostic:"project_id must be a lowercase path-safe slug"}'
    return 0
  fi
  case "$shared_memory" in
    true|True|TRUE|'!!bool true'|'!!bool True'|'!!bool TRUE') ;;
    *)
    jq -nc '{ok:false,reason:"shared_memory_not_true",diagnostic:"shared_memory must be the YAML boolean true"}'
    return 0
    ;;
  esac
  jq -nc --arg id "$project_id" '{ok:true,projectId:$id,project_id:$id,sharedMemory:true,shared_memory:true}'
}

_memories_project_file() {
  local cwd="${1:-}"
  local root
  root=$(_memories_resolve_repo_root "$cwd" 2>/dev/null) || return 1
  local file="$root/.memories/project.yaml"
  [ -f "$file" ] || return 1
  printf '%s' "$file"
}

_memories_project_backends_file() {
  local root="${1:-}"
  if [ -n "${MEMORIES_BACKENDS_FILE:-}" ]; then
    [ -f "$MEMORIES_BACKENDS_FILE" ] || return 1
    printf '%s' "$MEMORIES_BACKENDS_FILE"
    return 0
  fi
  if [ -n "$root" ] && [ -f "$root/.memories/backends.yaml" ]; then
    printf '%s' "$root/.memories/backends.yaml"
    return 0
  fi
  if [ -f "$HOME/.config/memories/backends.yaml" ]; then
    printf '%s' "$HOME/.config/memories/backends.yaml"
    return 0
  fi
  return 1
}

# Validate the small mapping grammar accepted by the strict project-context
# view.  The normal _parse_backends_yaml loader intentionally remains
# permissive for legacy routing; this guard prevents an ignored sequence,
# malformed indentation, or unsupported flow value from activating project
# mode merely because the permissive loader found one backend entry.
_memories_validate_project_backend_yaml() {
  awk '
    function trim(s) {
      sub(/\r$/, "", s)
      sub(/[[:space:]]+$/, "", s)
      return s
    }
    function fail() { exit 1 }
    {
      line = trim($0)
      if (line == "" || line ~ /^[[:space:]]*#/) next
      if (line ~ /\t/) fail()

      # Section headers are the only top-level mappings supported here.
      if (line ~ /^backends:[[:space:]]*(\{\}|#.*)?$/) {
        if (seen_backends++) fail()
        section = "backends"
        have_backend = 0
        next
      }
      if (line ~ /^routing:[[:space:]]*(\{\}|#.*)?$/) {
        if (!seen_backends || seen_routing++) fail()
        section = "routing"
        route = ""
        next
      }

      if (section == "backends") {
        if (line ~ /^  [A-Za-z_][A-Za-z0-9_-]*:[[:space:]]*(#.*)?$/) {
          have_backend = 1
          next
        }
        if (line ~ /^    (url|api_key|scenario):[[:space:]]*(.*)$/) {
          if (!have_backend) fail()
          value = line
          sub(/^    (url|api_key|scenario):[[:space:]]*/, "", value)
          # Values are scalar strings. Flow collections and block scalars are
          # deliberately outside the supported hook grammar.
          if (value ~ /^[\[\{>|]/) fail()
          next
        }
        fail()
      }

      if (section == "routing") {
        if (line ~ /^  [A-Za-z_][A-Za-z0-9_-]*:[[:space:]]*(.*)$/) {
          route = line
          sub(/^  [A-Za-z_][A-Za-z0-9_-]*:[[:space:]]*/, "", route)
          if (route == "" || route ~ /^#/) next
          if (route !~ /^\[[[:space:]]*([A-Za-z_][A-Za-z0-9_-]*([[:space:]]*,[[:space:]]*[A-Za-z_][A-Za-z0-9_-]*)*)?[[:space:]]*\]([[:space:]]+#.*)?$/) fail()
          next
        }
        if (line ~ /^    -[[:space:]]+[A-Za-z_][A-Za-z0-9_-]*([[:space:]]+#.*)?$/) {
          if (route == "") fail()
          next
        }
        fail()
      }

      fail()
    }
    END { if (section == "") exit 1 }
  ' "$1" >/dev/null 2>&1
}

# Strict backend view for collaborative activation.  Legacy _load_backends
# intentionally keeps its env/localhost fallback for ordinary fan-out; this
# helper never turns an absent or malformed project config into a host probe.
_memories_project_backend_config() {
  local root="${1:-}" file="" first="" rhs="" raw=""
  file=$(_memories_project_backends_file "$root" 2>/dev/null) || {
    if [ -n "${MEMORIES_BACKENDS_FILE:-}" ]; then
      jq -nc '{backends:[],error:{reason:"no_backends",diagnostic:"no backend configuration is available"}}'
    elif [ -n "${MEMORIES_URL:-}" ]; then
      jq -nc --arg url "$MEMORIES_URL" --arg key "${MEMORIES_API_KEY:-}" \
        '{backends:[{name:"default",url:$url,api_key:$key,scenario:""}]}'
    else
      jq -nc '{backends:[],error:{reason:"no_backends",diagnostic:"no backend configuration is available"}}'
    fi
    return 0
  }

  first=$(awk '
    /^[[:space:]]*($|#)/ { next }
    { print; exit }
  ' "$file" 2>/dev/null)
  case "$first" in
    true|false|null|~|\[*|\{*)
      jq -nc '{backends:[],error:{reason:"backend_config_invalid",diagnostic:"backend configuration must be a YAML mapping"}}'
      return 0
      ;;
  esac
  if ! printf '%s\n' "$first" | grep -qE '^backends:'; then
    jq -nc '{backends:[],error:{reason:"no_backends",diagnostic:"backend configuration does not define any backends"}}'
    return 0
  fi
  if ! _memories_validate_project_backend_yaml "$file"; then
    jq -nc '{backends:[],error:{reason:"backend_config_invalid",diagnostic:"backend configuration contains unsupported or malformed YAML"}}'
    return 0
  fi
  rhs=$(printf '%s' "$first" | sed -E 's/^backends:[[:space:]]*//;s/[[:space:]]*$//')
  case "$rhs" in
    ""|\{\}) ;;
    \[*|true|false|null|~|[0-9]*)
      jq -nc '{backends:[],error:{reason:"backend_config_invalid",diagnostic:"backend configuration backends must be a YAML mapping"}}'
      return 0
      ;;
  esac

  raw=$(_parse_backends_yaml "$file" 2>/dev/null) || raw=""
  if [ -z "$raw" ] || ! printf '%s' "$raw" | jq -e '(.backends | type == "array") and (.routing | type == "object")' >/dev/null 2>&1; then
    jq -nc '{backends:[],error:{reason:"backend_config_invalid",diagnostic:"backend configuration is malformed"}}'
    return 0
  fi
  printf '%s' "$raw" | jq -c '{backends:(.backends // [])}'
}

_memories_project_context() {
  local cwd="${1:-${CWD:-$PWD}}"
  local file parsed
  file=$(_memories_project_file "$cwd" 2>/dev/null) || {
    jq -nc '{active:false,reason:"missing",diagnostic:"no .memories/project.yaml at the repository boundary"}'
    return 0
  }
  parsed=$(_memories_parse_project_yaml "$file")
  if [ "$(printf '%s' "$parsed" | jq -r '.ok // false' 2>/dev/null)" != "true" ]; then
    printf '%s' "$parsed" | jq -c '{active:false,reason:(.reason // "malformed"),diagnostic:(.diagnostic // "invalid project declaration")}'
    return 0
  fi

  # Load exactly the backend set the hooks already use, but anchor project
  # resolution at the main repository root for worktrees.  This function runs
  # in command-substitution scope, so its temporary CWD/cache cannot alter the
  # caller's legacy routing state.
  local root project_config backends count backend url key response http_status body identity principal
  root=$(_memories_resolve_repo_root "$cwd" 2>/dev/null) || root="$cwd"
  project_config=$(_memories_project_backend_config "$root" 2>/dev/null) || project_config='{"backends":[]}'
  if [ "$(printf '%s' "$project_config" | jq -r '.error.reason // empty' 2>/dev/null)" ]; then
    printf '%s' "$project_config" | jq -c '{active:false,reason:.error.reason,diagnostic:.error.diagnostic}'
    return 0
  fi
  backends=$(printf '%s' "$project_config" | jq -c '.backends // []' 2>/dev/null) || backends="[]"
  count=$(printf '%s' "$backends" | jq 'length' 2>/dev/null) || count=0
  if [ "$count" -eq 0 ]; then
    jq -nc '{active:false,reason:"no_backends",diagnostic:"no backend configuration is available"}'
    return 0
  fi
  if [ "$count" -ne 1 ]; then
    jq -nc --arg diag "collaborative project mode requires exactly one configured backend (found $count)" \
      '{active:false,reason:"multiple_backends",diagnostic:$diag}'
    return 0
  fi
  backend=$(printf '%s' "$backends" | jq -c '.[0]')
  url=$(printf '%s' "$backend" | jq -r '.url // empty')
  key=$(printf '%s' "$backend" | jq -r '.api_key // .apiKey // empty')
  [ -n "$url" ] || {
    jq -nc '{active:false,reason:"principal_unreachable",diagnostic:"the configured backend cannot be reached"}'
    return 0
  }

  if [ -n "$key" ]; then
    response=$(curl -sS --max-time 2 -H "X-API-Key: $key" -w $'\n%{http_code}' "${url%/}/api/keys/me" 2>/dev/null) || response=""
  else
    response=$(curl -sS --max-time 2 -w $'\n%{http_code}' "${url%/}/api/keys/me" 2>/dev/null) || response=""
  fi
  http_status=$(printf '%s\n' "$response" | tail -n 1)
  body=$(printf '%s\n' "$response" | sed '$d')
  case "$http_status" in
    2??) ;;
    *) jq -nc --arg diag "authenticated principal lookup returned HTTP ${http_status:-unknown}" '{active:false,reason:"principal_unreachable",diagnostic:$diag}'; return 0 ;;
  esac
  identity=$(printf '%s' "$body" | jq -c . 2>/dev/null) || {
    jq -nc '{active:false,reason:"principal_unreachable",diagnostic:"authenticated principal lookup returned invalid JSON"}'
    return 0
  }
  case "$(printf '%s' "$identity" | jq -r '.type // empty')" in
    managed) ;;
    env|none) jq -nc '{active:false,reason:"env_principal",diagnostic:"environment or unconfigured admin identity cannot activate collaborative mode"}'; return 0 ;;
    *) jq -nc '{active:false,reason:"invalid_principal_type",diagnostic:"authenticated principal lookup did not return a managed principal"}'; return 0 ;;
  esac
  principal=$(printf '%s' "$identity" | jq -r '.principal_id // empty')
  if [ -z "$principal" ]; then
    jq -nc '{active:false,reason:"missing_principal",diagnostic:"authenticated backend response did not include principal_id"}'
    return 0
  fi
  if ! printf '%s' "$principal" | grep -qE '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'; then
    jq -nc '{active:false,reason:"invalid_principal",diagnostic:"authenticated principal_id must be a lowercase path-safe slug"}'
    return 0
  fi
  local project_id
  project_id=$(printf '%s' "$parsed" | jq -r '.project_id')
  local backend_name
  backend_name=$(printf '%s' "$backend" | jq -r '.name // "default"')
  jq -nc --arg project "$project_id" --arg principal "$principal" --arg backend "$backend_name" \
    '{active:true,reason:"active",projectId:$project,project_id:$project,principalId:$principal,principal_id:$principal,sharedMemory:true,shared_memory:true,backend:$backend}'
}

_memories_project_active() {
  [ "$( _memories_project_context "${1:-${CWD:-$PWD}}" | jq -r '.active // false' 2>/dev/null )" = "true" ]
}

_memories_project_id() {
  _memories_project_context "${1:-${CWD:-$PWD}}" | jq -r '.project_id // empty' 2>/dev/null
}

_memories_principal_id() {
  _memories_project_context "${1:-${CWD:-$PWD}}" | jq -r '.principal_id // empty' 2>/dev/null
}

_memories_project_recall_prefixes() {
  local project="${1:-}" principal="${2:-}" configured="${3:-}" raw prefix existing duplicate
  local -a prefixes=() configured_prefixes=()
  [ -n "$project" ] && prefixes+=("project/$project")
  [ -n "$project" ] && [ -n "$principal" ] && prefixes+=("person/$principal/$project")
  IFS=',' read -r -a configured_prefixes <<< "$configured"
  for raw in "${configured_prefixes[@]}"; do
    prefix=$(printf '%s' "$raw" | xargs)
    [ -z "$prefix" ] && continue
    prefix="${prefix//\{project\}/$project}"
    # Collaborative mode owns the exact project and person namespaces.  A
    # trailing slash is a legacy family prefix, not an exact-project prefix.
    case "$prefix" in
      project/*|person/*|*/|*\*) continue ;;
    esac
    duplicate=0
    for existing in "${prefixes[@]}"; do
      [ "$existing" = "$prefix" ] && duplicate=1 && break
    done
    [ "$duplicate" -eq 0 ] && prefixes+=("$prefix")
  done
  printf '%s\n' "${prefixes[@]}"
}

_memories_project_extract_source() {
  local active="${1:-false}" project="${2:-}" principal="${3:-}"
  [ "$active" = "true" ] || return 1
  [ -n "$project" ] && [ -n "$principal" ] || return 1
  printf 'person/%s/%s/knowledge\n' "$principal" "$project"
}

_memories_project_search_results() {
  local query="${1:-}" project="${2:-}" principal="${3:-}" configured="${4:-}" limit="${5:-3}" threshold="${6:-0.35}" prefix response
  while IFS= read -r prefix; do
    [ -n "$prefix" ] || continue
    response=$(_search_memories_multi "$query" "$prefix" "$limit" "$threshold" || true)
    [ -n "$response" ] && printf '%s\n' "$response" | jq -c --arg requested_prefix "$prefix" '. + {_requested_prefix:$requested_prefix}'
  done < <(_memories_project_recall_prefixes "$project" "$principal" "$configured")
}

_memories_merge_search_results() {
  local ordered="${1:-false}" limit="${2:-6}"
  if [ "$ordered" = "true" ]; then
    jq -sr --argjson limit "$limit" '
      def dedup_key:
        if (.id? != null) then ["id", .id, (.source // "")]
        else ["text", (.text // ""), "source", (.source // "")]
        end;
      map(select(type == "object") | (.results // [])) | add // []
      | reduce .[] as $item ([];
          if any(.[]; dedup_key == ($item | dedup_key)) then . else . + [$item] end
        )
      | .[0:$limit]
    '
  else
    jq -sr --argjson limit "$limit" '
      def dedup_key:
        if (.id? != null) then ["id", .id, (.source // "")]
        else ["text", (.text // ""), "source", (.source // "")]
        end;
      map(select(type == "object") | (.results // [])) | add // []
      | reduce .[] as $item ([];
          if any(.[]; dedup_key == ($item | dedup_key)) then . else . + [$item] end
        )
      | sort_by(-(.similarity // .rrf_score // 0)) | .[0:$limit]
    '
  fi
}

_memories_label_project_results() {
  local project="${1:-}"
  jq -c --arg project "$project" 'map(if ((.source // "") | startswith("project/" + $project + "/")) then . + {provenance_label: ([if (.author // "") != "" then "author=" + (.author|tostring) else empty end, if (.origin_client // "") != "" then "origin-client=" + (.origin_client|tostring) else empty end] | if length > 0 then "[" + join(", ") + "]" else "" end)} else . end)'
}

_memories_disabled() {
  case "${MEMORIES_DISABLED:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

# Any of the supported backend-config sources counts as "configured": a
# single-backend MEMORIES_URL, or a backends.yaml resolved by
# _resolve_backends_file (defined below, in the Multi-Backend Config
# section) — the SAME function _load_backends uses to actually load the
# file. Sharing one resolver is deliberate: an earlier version of this gate
# checked CLAUDE_PROJECT_DIR directly while _load_backends checked only the
# payload's cwd, so a repo with `.memories/backends.yaml` at the project
# root but a session cwd in a subdirectory would activate the hook here and
# then have _load_backends fail to find that same file, silently falling
# back to querying http://localhost:8900 — worse than the no-op this gate is
# for. Routing both checks through one function makes that impossible.
#
# This early check runs BEFORE stdin is read, so it calls
# _resolve_backends_file with no cwd argument (payload cwd isn't known yet);
# see that function's docstring for what that omits and why it's safe.
_memories_has_backend_config() {
  [ -n "${MEMORIES_URL:-}" ] && return 0
  _resolve_backends_file >/dev/null
}

# Decide whether hooks should run at all, evaluated in this precedence:
#   1. MEMORIES_DISABLED truthy         -> inactive (handled by caller, see below)
#   2. MEMORIES_ENABLED explicitly set  -> obey it (truthy/falsy)
#   3. MEMORIES_ENABLED unset           -> auto-detect: active iff any
#      supported backend-config source is present (see
#      _memories_has_backend_config)
# This lets a repo commit .claude/settings.json enabling the plugin (for cloud
# sessions) without forcing every clone-without-credentials to see warnings or
# pay curl timeouts: unconfigured is a true, silent no-op by default.
_memories_active() {
  if _memories_disabled; then
    return 1
  fi
  if [ -n "${MEMORIES_ENABLED+x}" ]; then
    case "${MEMORIES_ENABLED}" in
      1|true|TRUE|yes|YES|on|ON) return 0 ;;
      0|false|FALSE|no|NO|off|OFF) return 1 ;;
    esac
  fi
  _memories_has_backend_config
}

_exit_if_disabled() {
  if _memories_disabled; then
    _log_info "Hook disabled by MEMORIES_DISABLED"
    exit 0
  fi
  if _memories_active; then
    return 0
  fi
  if [ -n "${MEMORIES_ENABLED+x}" ]; then
    # Explicit MEMORIES_ENABLED=false (or an unrecognized value with no
    # backend config either) — the user deliberately set something, so a log
    # line is informative and welcome.
    _log_info "Hook disabled by MEMORIES_ENABLED=${MEMORIES_ENABLED}"
    exit 0
  fi
  # Auto-detected inactive: no MEMORIES_ENABLED override and no backend
  # config found — a contributor who never opted in. Do NOT call _log_info
  # here: it would create ~/.config/memories/hook.log (and the directory) on
  # every session/prompt/tool event for someone who never configured
  # anything, which is exactly the noise this gate exists to prevent.
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


# -- Hook-wide deadline -------------------------------------------------------
# Per-call --max-time tuning cannot bound a hook's TOTAL time by construction,
# and two independent measurements prove it (PR #85 review, round 7):
#   - A single HEALTHY REMOTE backend (~0.7s RTT) across the ~5 sequential
#     recall searches memory-recall.sh makes per session already consumes
#     ~3.7s of a 5s budget with nothing even failing. Sum of per-call caps
#     across N sequential calls has no ceiling tied to the hook's own budget.
#   - A backend whose /health responds promptly but whose /search hangs: the
#     per-backend breaker (7c3d5d8) can't help on the FIRST occurrence,
#     because nothing yet knows THAT backend is bad on THAT endpoint — the
#     first search fan-out still pays a full, flat --max-time against it.
#     Measured 5.65s against a 5s budget even with a healthy local peer.
#
# The fix: one END-TO-END deadline, computed once at hook start, consulted
# before every outbound call. Every call's --max-time is capped to whatever
# is actually left, and once there isn't enough left to justify a call, it
# is skipped rather than attempted — partial context delivered on time
# beats complete context discarded when hooks.json kills the whole process
# at its own timeout (which happens with NO chance to flush partial output).
#
# Only hooks that call _hook_deadline_init opt in. _hook_remaining_s returns
# a large sentinel ("uncapped") when no deadline was initialized this
# invocation, so every OTHER caller of _health_check/_search_memories_multi
# (memory-query.sh, memory-extract.sh, etc. — not wired into this yet) is
# completely unaffected; only memory-recall.sh (SessionStart) and
# memory-subagent-recall.sh (SubagentStart) call _hook_deadline_init.
_MEMORIES_HOOK_BUDGET_MS_DEFAULT=5000   # hooks.json: SessionStart/SubagentStart timeout, ms
_MEMORIES_HOOK_BUDGET_MARGIN_MS=500     # reserved for JSON assembly, MEMORY.md sync, and stdout
                                         # after the last call returns — hooks.json kills the
                                         # whole process at its timeout with no chance to flush,
                                         # so this has to cover the tail, not just network time.
_MEMORIES_HOOK_CALL_RESERVE_S="0.15"    # shell/curl-fork overhead reserved off each call's cap
_MEMORIES_HOOK_MIN_CALL_S="0.3"         # below this much remaining, skip the call entirely

# Wall-clock "now" with sub-second precision, portable across macOS (BSD
# date, no %N) and Linux without adding a new hard dependency: jq is already
# required everywhere in these hooks, and jq's `now` builtin uses the C
# library's own clock, not a shell-out to `date`. Falls back to whole-second
# `date +%s` only if jq itself is somehow unavailable (defensive; jq missing
# is already fatal to virtually everything else these hooks do).
_hook_now_s() {
  jq -n 'now' 2>/dev/null || date +%s
}

# Call once, as early as possible after a hook confirms it's active (right
# after _exit_if_disabled), before any backend call.
_hook_deadline_init() {
  local start
  start=$(_hook_now_s)
  local budget_ms="${MEMORIES_HOOK_BUDGET_MS:-$_MEMORIES_HOOK_BUDGET_MS_DEFAULT}"
  _MEMORIES_HOOK_DEADLINE_S=$(jq -n --argjson start "$start" --argjson budget_ms "$budget_ms" --argjson margin_ms "$_MEMORIES_HOOK_BUDGET_MARGIN_MS" \
    '$start + (($budget_ms - $margin_ms) / 1000)' 2>/dev/null) || _MEMORIES_HOOK_DEADLINE_S=""
}

# Seconds remaining before the deadline (may be negative). Prints a large
# sentinel — "uncapped" — when no deadline was initialized this invocation.
_hook_remaining_s() {
  if [ -z "${_MEMORIES_HOOK_DEADLINE_S:-}" ]; then
    printf '999999'
    return 0
  fi
  local now
  now=$(_hook_now_s)
  jq -n --argjson deadline "$_MEMORIES_HOOK_DEADLINE_S" --argjson now "$now" '$deadline - $now' 2>/dev/null || printf '999999'
}

# True ("true") when there isn't enough deadline left to justify issuing
# another backend call this hook run. Callers (the prefix-search loop, the
# fallback search, the WIP search, ...) should stop issuing further calls,
# log what got skipped, and go with whatever context they already have.
_hook_deadline_exhausted() {
  local remaining
  remaining=$(_hook_remaining_s)
  jq -n --argjson remaining "$remaining" --argjson min "$_MEMORIES_HOOK_MIN_CALL_S" '$remaining < $min' 2>/dev/null || printf 'false'
}

# Prints the --max-time value to use for a single backend call given its
# EXISTING per-call cap ($1) — the smaller of that cap and whatever's left
# on the deadline (minus a small reserve for shell/curl-fork overhead).
# Prints nothing and returns 1 if there isn't enough left to justify issuing
# the call at all; callers MUST check this and skip the call rather than
# pass a near-zero or negative --max-time to curl.
_hook_call_budget() {
  local existing_cap="$1"
  local remaining
  remaining=$(_hook_remaining_s)
  local budget
  budget=$(jq -n --argjson cap "$existing_cap" --argjson remaining "$remaining" --argjson reserve "$_MEMORIES_HOOK_CALL_RESERVE_S" \
    '[$cap, ($remaining - $reserve)] | min' 2>/dev/null) || budget="$existing_cap"
  local ok
  ok=$(jq -n --argjson budget "$budget" --argjson min "$_MEMORIES_HOOK_MIN_CALL_S" '$budget >= $min' 2>/dev/null) || ok="false"
  if [ "$ok" != "true" ]; then
    return 1
  fi
  printf '%s' "$budget"
  return 0
}


# -- Backend circuit breaker --------------------------------------------------
# When a backend is down or slow, every hook on every call pays a full curl
# timeout. After a failure the breaker file makes subsequent calls skip that
# backend instantly until the cooldown elapses (then one half-open retry).
#
# PER-BACKEND, not global (PR #85 review, 5th pass — settled, not a
# follow-up). A single shared breaker meant one dead backend, once tripped,
# blocked EVERY subsequent call to EVERY backend for the rest of the
# cooldown — including healthy ones. Worse, with a routed multi-backend set
# and no per-backend memory, a single SLOW (not instantly-failing) backend
# got re-tried by every one of the ~5-6 sequential searches a SessionStart
# recall makes, each paying its own timeout — blowing the hook's 5s budget
# even though a healthy backend was reachable the whole time (reviewer
# repro: a listener that accepts and never responds).
#
# Every function takes an optional backend NAME (from backends.yaml, or the
# literal string "default" that _load_backends' single-URL fallback uses
# when there's no config file) and defaults to "default" when omitted, which
# resolves to the exact same file every prior single-backend caller already
# used — a true single-backend install, or any caller not yet passing a
# name, is byte-for-byte unchanged.
_MEMORIES_BREAKER_FILE="${MEMORIES_BREAKER_FILE:-$HOME/.config/memories/backend-down}"
_MEMORIES_BREAKER_COOLDOWN="${MEMORIES_BREAKER_COOLDOWN:-60}"

_breaker_file_for() {
  local name="${1:-default}"
  local safe
  safe=$(printf '%s' "$name" | tr -c 'A-Za-z0-9_-' '_')
  if [ -z "$safe" ] || [ "$safe" = "default" ]; then
    printf '%s' "$_MEMORIES_BREAKER_FILE"
  else
    printf '%s.%s' "$_MEMORIES_BREAKER_FILE" "$safe"
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

# Should a failed call be recorded against the backend? Returns 0 to trip.
#
# A curl timeout (exit 28) is only evidence about the backend if the backend
# got the time we meant to give it. `_hook_call_budget` shrinks each call's
# --max-time as the hook's deadline is consumed, so a SessionStart recall's
# later calls run with budgets far below what a real request needs (measured:
# /search takes 1.2-2.1s against a live backend, while the minimum-call floor
# is 0.3s). Tripping on those blames the backend for our own deadline, and
# because _health_check skips probing any backend whose breaker is already
# open, the NEXT session reports "not reachable" without a single probe — a
# healthy backend, reported down, for the whole cooldown.
#
# _health_check deliberately leaves breaker state untouched when the budget
# is too small to probe fairly; this is the same invariant for the search
# path, which previously lacked it. Any non-timeout failure (connection
# refused, TLS, HTTP error) is real evidence and still trips.
# "Starved" must mean materially less time than we intended, not merely less.
# Health and version probes run before search, so ordinary overhead shaves a
# 4s cap to ~3.9s; treating that as inconclusive would leave a genuinely
# hanging backend un-tripped and re-paying its full timeout every session.
# A call that got most of the intended cap is real evidence.
_MEMORIES_BREAKER_FAIR_BUDGET_RATIO="${MEMORIES_BREAKER_FAIR_BUDGET_RATIO:-0.75}"

_should_trip_breaker() {
  local rc="$1" budget="$2" cap="$3"
  [ "$rc" = "28" ] || return 0
  local fair
  # Default to tripping if jq is unavailable or the comparison fails: losing
  # the breaker entirely is worse than an occasional early trip.
  fair=$(jq -n --argjson b "$budget" --argjson c "$cap" --argjson r "$_MEMORIES_BREAKER_FAIR_BUDGET_RATIO" \
    '$b >= ($c * $r)' 2>/dev/null) || fair="true"
  [ "$fair" = "true" ] && return 0
  return 1
}

# Health check — returns 0 if the ROUTED search backend set has at least
# one reachable member. Establishes PER-BACKEND breaker state (see above)
# up front so the search fan-out that follows can skip known-bad backends
# outright, rather than re-discovering them one slow timeout at a time.
#
# Fifth pass at this bug (PR #85 review):
#   1. probed the bare MEMORIES_URL default instead of any resolved backend.
#   2. fixed the target, but picked _load_backends' raw .[0] (declaration
#      order) instead of the ROUTED set, so an excluded backend could still
#      be probed and trip a single SHARED breaker that blocked everything.
#   3. fixed to consider the whole routed set (any-reachable = healthy) —
#      correct for the "one dead backend" case, but the shared breaker's
#      early-return path never set MEMORIES_HEALTH_DOWN_NAMES, so a
#      previously-tripped session crashed here under `set -u` (P1-A) — and
#      returning "healthy" the moment ONE backend answered left any
#      SLOW-but-not-yet-failed backend un-probed and un-marked, so
#      _search_memories_multi's fan-out re-discovered it, via a full
#      timeout, on every one of the ~5-6 sequential recall searches a
#      SessionStart makes — blowing the hook's 5s budget even though a
#      healthy backend was reachable the whole time (P1-B).
# This pass: probe every routed backend whose OWN breaker isn't already
# open, IN PARALLEL (bounded by one --max-time, not one per backend), and
# record each one's outcome in ITS OWN breaker via _breaker_trip/_reset —
# so by the time this returns, every backend the search fan-out is about to
# try again already has accurate, individual state to skip by.
#
# Publishes on EVERY return path, no exceptions (this is what P1-A missed
# — only the named variable was audited, not the contract as a whole):
#   MEMORIES_HEALTH_DOWN_NAMES — comma-joined "name (url)" for every routed
#                                backend currently considered down (fresh
#                                failure or already-open breaker). Always a
#                                defined string (possibly empty on success),
#                                never left unset — callers run under `set
#                                -u` and read it immediately after a
#                                non-zero return.
#   MEMORIES_BACKEND_DOWN      — "1" when no routed backend answered, unset
#                                on success (existing convention elsewhere
#                                in this file: read via ${VAR:-0}).
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

  # Deadline-aware: cap the probe's --max-time to whatever's actually left
  # (uncapped/2s when no deadline was initialized — see _hook_call_budget).
  # If there isn't enough left to even probe, don't try — every routed
  # backend is unknown/unreachable for THIS invocation. Breaker state is
  # left untouched, so a later invocation with a fresh deadline still gets
  # a fair re-check rather than being penalized for our own budget, not the
  # backend's.
  local probe_budget
  if ! probe_budget=$(_hook_call_budget 2); then
    MEMORIES_HEALTH_DOWN_NAMES="hook budget exhausted before any health probe"
    MEMORIES_BACKEND_DOWN=1
    return 1
  fi

  local tmpdir
  tmpdir=$(mktemp -d)
  local backend url name safe
  while read -r backend; do
    url=$(printf '%s' "$backend" | jq -r '.url')
    name=$(printf '%s' "$backend" | jq -r '.name')
    safe=$(printf '%s' "$name" | tr -c 'A-Za-z0-9_' '_')

    if _breaker_open "$name"; then
      # Already known down from a recent trip — don't re-pay the timeout,
      # just count it among the down set.
      printf '%s (%s)' "$name" "$url" > "$tmpdir/down_${safe}"
      continue
    fi

    (
      if curl -sf --max-time "$probe_budget" "$url/health" >/dev/null 2>&1; then
        _breaker_reset "$name"
        : > "$tmpdir/up_${safe}"
      else
        _breaker_trip "$name"
        printf '%s (%s)' "$name" "$url" > "$tmpdir/down_${safe}"
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

# Single source of truth for "which backends.yaml file, if any, applies" —
# shared by _memories_has_backend_config (the activation gate, called before
# the hook has read stdin) and _load_backends (called after, once the hook
# knows the payload's .cwd). Precedence:
#   1. $MEMORIES_BACKENDS_FILE            — explicit override, if it exists
#   2. $CLAUDE_PROJECT_DIR/.memories/backends.yaml  — stable project root
#      (falls back to $PWD if CLAUDE_PROJECT_DIR is unset: this plugin is
#      agent-agnostic, and non-Claude-Code callers won't have that variable;
#      $PWD doesn't change mid-script, so this is consistent across every
#      call in a single hook invocation, gate included)
#   3. <cwd arg>/.memories/backends.yaml  — the hook's actual working
#      directory, from the payload's .cwd. Kept as a fallback so an existing
#      cwd-only setup (no root-level file, no CLAUDE_PROJECT_DIR match)
#      still loads — backward compat, do not drop.
#   4. $HOME/.config/memories/backends.yaml — global fallback
#
# `cwd` is optional and only step 3 uses it: the activation gate runs before
# the hook parses stdin, so it has no payload cwd yet and simply omits that
# step (calls this with no argument). Residual limitation: a backends.yaml
# that exists ONLY at a cwd that differs from both CLAUDE_PROJECT_DIR and
# $PWD (e.g. a subdirectory session with no project-root config) is invisible
# to the gate — same class of limitation already documented on
# _memories_has_backend_config — and needs MEMORIES_ENABLED=true to force
# activation; but once running, _load_backends (which DOES pass cwd) still
# finds it, since it goes through this same function.
#
# Prints the resolved path and returns 0, or prints nothing and returns 1.
_resolve_backends_file() {
  local cwd="${1:-}"
  if [ -n "${MEMORIES_BACKENDS_FILE:-}" ] && [ -f "${MEMORIES_BACKENDS_FILE}" ]; then
    printf '%s' "${MEMORIES_BACKENDS_FILE}"
    return 0
  fi
  local project_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
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

# A single representative URL for secondary, non-gating purposes (the
# backend-version check curl, naming a host in the 401 credential warning) —
# NOT for health-gating; use _health_check for that, which correctly
# considers the whole routed set rather than one representative pick.
#
# Takes .[0] of the ROUTED search set (_get_backends_for_op "search"), not
# _load_backends' raw declaration order: an earlier version of this function
# used the raw order, which is what let a backend routing.search had
# excluded get treated as "the" backend for warnings and version checks
# (PR #85 review). Reads $CWD from the caller's environment the same way
# _load_backends itself does — call this only after the hook has parsed
# stdin and set $CWD.
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
    # Single backend — direct call (backward compat, no overhead). Breaker
    # keyed by THIS backend's own name (see _breaker_file_for): "default"
    # for a plain MEMORIES_URL install (same file as always), or the
    # backend's real name when routing.search collapsed to exactly one.
    local url key name
    url=$(echo "$backends" | jq -r '.[0].url')
    key=$(echo "$backends" | jq -r '.[0].api_key')
    name=$(echo "$backends" | jq -r '.[0].name // "default"')

    if _breaker_open "$name"; then
      MEMORIES_BACKEND_DOWN=1
      echo '{"results":[],"count":0}'
      return
    fi

    # Deadline-aware --max-time (uncapped/4s when no deadline was
    # initialized). Not enough budget left to justify the call at all ⇒
    # skip it — this is NOT evidence the backend is bad, so its breaker is
    # left untouched (only OUR budget ran out, not the backend).
    local call_budget
    if ! call_budget=$(_hook_call_budget 4); then
      MEMORIES_BACKEND_DOWN=1
      echo '{"results":[],"count":0}'
      return
    fi

    # No -f here: /search is authenticated (unlike /health), and a wrong/missing
    # MEMORIES_API_KEY silently returns nothing forever unless we can tell a 401
    # (credential problem, backend reachable) apart from a connection failure
    # (backend unreachable). -w appends the status code after the body so we can
    # branch on it without a second round-trip.
    local raw curl_rc status out
    raw=$(curl -s --max-time "$call_budget" -w '\n%{http_code}' -X POST "$url/search" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $key" \
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
          # Backend is reachable — this is a credential problem, not downtime.
          # Keep the breaker closed so the session doesn't misreport "unreachable".
          _breaker_reset "$name"
          MEMORIES_AUTH_FAILED=1
          echo '{"results":[],"count":0,"auth_failed":true}'
          ;;
        *)
          _breaker_trip "$name"
          MEMORIES_BACKEND_DOWN=1
          echo '{"results":[],"count":0}'
          ;;
      esac
    else
      # Only record this against the backend if the failure was its fault and
      # not our shrinking deadline — see _should_trip_breaker.
      if _should_trip_breaker "$curl_rc" "$call_budget" 4; then
        _breaker_trip "$name"
      fi
      MEMORIES_BACKEND_DOWN=1
      echo '{"results":[],"count":0}'
    fi
    return
  fi

  # Multi-backend: parallel fan-out with background subshells, PER-BACKEND
  # breaker-aware in both directions (PR #85 review, 5th pass):
  #   - a backend whose OWN breaker is already open is skipped outright —
  #     no curl call at all — instead of being re-tried (and re-paying a
  #     full --max-time) on every one of the several sequential recall
  #     searches a single SessionStart makes.
  #   - each backend's OWN outcome here trips/resets ITS OWN breaker, so a
  #     backend that fails (or hangs, once its --max-time is hit) on THIS
  #     call is already known-bad for the NEXT call, not re-discovered from
  #     scratch every time.
  # Use process substitution (< <(...)) so the while loop runs in the
  # current shell and `wait` can actually collect the background jobs.
  #
  # Deadline-aware: one --max-time is computed ONCE for the whole fan-out
  # (all backends start at roughly the same wall-clock moment, so a single
  # shared budget is both simpler and more accurate than recomputing it
  # per-backend). Not enough left to justify attempting the routed set at
  # all ⇒ skip the whole round rather than issue near-zero-timeout calls.
  local call_budget
  if ! call_budget=$(_hook_call_budget 4); then
    MEMORIES_BACKEND_DOWN=1
    echo '{"results":[],"count":0}'
    return
  fi

  local tmpdir
  tmpdir=$(mktemp -d)
  local attempted=0
  while read -r backend; do
    local url key name safe
    url=$(echo "$backend" | jq -r '.url')
    key=$(echo "$backend" | jq -r '.api_key')
    name=$(echo "$backend" | jq -r '.name')
    safe=$(printf '%s' "$name" | tr -c 'A-Za-z0-9_' '_')

    if _breaker_open "$name"; then
      continue
    fi
    attempted=1
    (
      result=$(curl -sf --max-time "$call_budget" -X POST "$url/search" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $key" \
        -d "$body" 2>/dev/null)
      search_rc=$?
      if [ -n "$result" ]; then
        _breaker_reset "$name"
        # Tag results with _backend
        echo "$result" | jq -c --arg b "$name" '.results[] | . + {_backend: $b}' > "$tmpdir/result_${safe}.jsonl"
      elif _should_trip_breaker "$search_rc" "$call_budget" 4; then
        _breaker_trip "$name"
      fi
    ) &
  done < <(echo "$backends" | jq -c '.[]')
  wait

  if [ "$attempted" -eq 0 ]; then
    # Every routed backend's breaker was already open — nothing to try,
    # nothing to wait on.
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
