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

# True only when this repository boundary contains a valid collaborative
# declaration.  Hooks use this after context resolution to distinguish a
# legacy checkout (no declaration, keep legacy behavior) from a declared
# checkout whose authenticated principal is temporarily unavailable (skip all
# project-aware reads and automatic writes rather than falling back broadly).
_memories_project_declared() {
  local cwd="${1:-${CWD:-$PWD}}" file parsed
  file=$(_memories_project_file "$cwd" 2>/dev/null) || return 1
  parsed=$(_memories_parse_project_yaml "$file" 2>/dev/null) || return 1
  [ "$(printf '%s' "$parsed" | jq -r '.ok // false' 2>/dev/null)" = "true" ]
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
  local cwd="${1:-}" file="" first="" rhs="" raw=""
  # Use the exact resolver consumed by _load_backends.  Project declaration
  # lookup may use the main repository root for worktrees, but backend
  # identity must bind to the same cwd/override precedence as real requests.
  file=$(_resolve_backends_file "$cwd" 2>/dev/null) || {
    if [ -n "${MEMORIES_URL:-}" ]; then
      jq -nc --arg url "$MEMORIES_URL" --arg key "${MEMORIES_API_KEY:-}" \
        '{backends:[{name:"default",url:$url,api_key:$key,scenario:""}],config_origin:"environment"}'
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
  printf '%s' "$raw" | jq -c --arg origin "$file" '{backends:(.backends // []),config_origin:$origin}'
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

  # Load exactly the backend set the hooks already use for this payload cwd.
  # This function runs in command-substitution scope, so its temporary cache
  # cannot alter the caller's legacy routing state.
  local project_config backends count backend url key response http_status body identity principal config_origin
  project_config=$(_memories_project_backend_config "$cwd" 2>/dev/null) || project_config='{"backends":[]}'
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
  config_origin=$(printf '%s' "$project_config" | jq -r '.config_origin // empty' 2>/dev/null || true)
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
  local project_id identity_prefixes legacy_prefixes
  project_id=$(printf '%s' "$parsed" | jq -r '.project_id')
  local backend_name
  backend_name=$(printf '%s' "$backend" | jq -r '.name // "default"')
  identity_prefixes=$(printf '%s' "$identity" | jq -c '.prefixes // []' 2>/dev/null || printf '[]')
  legacy_prefixes=$(printf '%s' "$identity_prefixes" | jq -c --arg project "$project_id" '
    reduce .[]? as $raw ([ ];
      ($raw | if type == "string" then (gsub("^[[:space:]]+|[[:space:]]+$"; "") | gsub("\\{project\\}"; $project)) else "" end) as $prefix
      | if ($prefix == "" or ($prefix | contains("*")) or ($prefix | endswith("/"))) then .
        else ($prefix | split("/")) as $parts
        | if (($parts | length) >= 2 and $parts[-1] == $project and $parts[0] != "project" and $parts[0] != "person" and (index($prefix) | not)) then . + [$prefix] else . end
        end
    )
  ' 2>/dev/null || printf '[]')
  jq -nc --arg project "$project_id" --arg principal "$principal" --arg backend "$backend_name" \
    --arg url "$url" --arg origin "$config_origin" --argjson prefixes "$identity_prefixes" --argjson legacy "$legacy_prefixes" \
    '{active:true,reason:"active",projectId:$project,project_id:$project,principalId:$principal,principal_id:$principal,sharedMemory:true,shared_memory:true,backend:$backend,backend_name:$backend,backend_url:$url,config_origin:$origin,prefixes:$prefixes,legacy_source_prefixes:$legacy}'
}

_memories_project_recall_prefixes() {
  local project="${1:-}" principal="${2:-}" configured="${3:-}" raw prefix existing duplicate
  local -a prefixes=() configured_prefixes=()
  [ -n "$project" ] && prefixes+=("project/$project")
  [ -n "$project" ] && [ -n "$principal" ] && prefixes+=("person/$principal/$project")
  if [ -n "$configured" ]; then
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
      # An exact legacy prefix names this project in its final segment.  A
      # family prefix or another project's prefix must never widen recall.
      [ "${prefix##*/}" = "$project" ] || continue
      duplicate=0
      for existing in "${prefixes[@]}"; do
        [ "$existing" = "$prefix" ] && duplicate=1 && break
      done
      [ "$duplicate" -eq 0 ] && prefixes+=("$prefix")
    done
  fi
  printf '%s\n' "${prefixes[@]}"
}

_memories_project_extract_source() {
  local active="${1:-false}" project="${2:-}" principal="${3:-}"
  [ "$active" = "true" ] || return 1
  [ -n "$project" ] && [ -n "$principal" ] || return 1
  printf 'person/%s/%s/knowledge\n' "$principal" "$project"
}

_memories_filter_search_response_for_prefix() {
  local prefix="${1:-}"
  jq -c --arg prefix "$prefix" '
    (.results // []) as $results
    | .results = ($results | map(select(
        ((.source // "") == $prefix)
        or ((.source // "") | startswith($prefix + "/"))
      )))
    | .count = (.results | length)
  '
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
  jq -c --arg project "$project" '
    def clean_provenance:
      tostring
      | gsub("[[:cntrl:]]"; " ")
      | gsub("[[:space:]]+"; " ")
      | .[0:80];
    map(
      if ((.source // "") | startswith("project/" + $project + "/")) then
        . + {provenance_label: ([
          if (.author // "") != "" then "author=" + (.author | clean_provenance) else empty end,
          if (.origin_client // "") != "" then "origin-client=" + (.origin_client | clean_provenance) else empty end
        ] | if length > 0 then "[" + join(", ") + "]" else "" end)}
      else . end
    )
  '
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


# -- Backend circuit breaker --------------------------------------------------
# When the backend is down or slow, every hook on every prompt pays full curl
# timeouts (~8s measured across a prompt's hook fan-out). After a failure the
# breaker file makes subsequent hook invocations skip backend calls instantly
# until the cooldown elapses (then one half-open retry).
_MEMORIES_BREAKER_FILE="${MEMORIES_BREAKER_FILE:-$HOME/.config/memories/backend-down}"
_MEMORIES_BREAKER_COOLDOWN="${MEMORIES_BREAKER_COOLDOWN:-60}"

_breaker_open() {
  [ -f "$_MEMORIES_BREAKER_FILE" ] || return 1
  local ts now age
  ts=$(cat "$_MEMORIES_BREAKER_FILE" 2>/dev/null)
  case "$ts" in ''|*[!0-9]*) rm -f "$_MEMORIES_BREAKER_FILE" 2>/dev/null; return 1 ;; esac
  now=$(date +%s)
  age=$((now - ts))
  [ "$age" -lt "$_MEMORIES_BREAKER_COOLDOWN" ] && return 0
  return 1
}

_breaker_trip() {
  mkdir -p "$(dirname "$_MEMORIES_BREAKER_FILE")" 2>/dev/null
  date +%s > "$_MEMORIES_BREAKER_FILE" 2>/dev/null
  _log_warn "Memories backend unreachable — circuit open for ${_MEMORIES_BREAKER_COOLDOWN}s (hooks skip backend calls)" 2>/dev/null || true
}

_breaker_reset() {
  rm -f "$_MEMORIES_BREAKER_FILE" 2>/dev/null
  return 0
}

# Health check — returns 0 if service is reachable (breaker-aware)
_health_check() {
  local url="${MEMORIES_URL:-http://localhost:8900}"
  if _breaker_open; then
    MEMORIES_BACKEND_DOWN=1
    return 1
  fi
  if curl -sf --max-time 2 "$url/health" >/dev/null 2>&1; then
    _breaker_reset
    return 0
  fi
  _breaker_trip
  MEMORIES_BACKEND_DOWN=1
  return 1
}

# -- Multi-Backend Config --------------------------------------------------

_BACKENDS_CACHE=""

# Normalize the simple scalar subset accepted by the strict backends.yaml
# validator. Comments start only at an unquoted # preceded by whitespace;
# matching outer quotes are removed after comment stripping.
_memories_yaml_scalar() {
  printf '%s\n' "${1:-}" | awk '
    {
      value = $0
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      single_quote = sprintf("%c", 39)
      double_quote = sprintf("%c", 34)
      quote = ""
      output = ""
      previous = ""
      for (i = 1; i <= length(value); i++) {
        character = substr(value, i, 1)
        if (quote == "") {
          if (character == single_quote || character == double_quote) {
            quote = character
          } else if (character == "#" && previous ~ /[[:space:]]/) {
            break
          }
        } else if (character == quote) {
          quote = ""
        }
        output = output character
        previous = character
      }
      sub(/[[:space:]]+$/, "", output)
      if (length(output) >= 2) {
        first = substr(output, 1, 1)
        last = substr(output, length(output), 1)
        if ((first == single_quote || first == double_quote) && last == first) {
          output = substr(output, 2, length(output) - 2)
        }
      }
      print output
    }
  '
}

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
        url=$(_memories_yaml_scalar "$(printf '%s' "$line" | sed 's/^    url: *//')")
      fi
      if printf '%s' "$line" | grep -qE '^    api_key:'; then
        api_key=$(_memories_yaml_scalar "$(printf '%s' "$line" | sed 's/^    api_key: *//')")
      fi
      if printf '%s' "$line" | grep -qE '^    scenario:'; then
        scenario=$(_memories_yaml_scalar "$(printf '%s' "$line" | sed 's/^    scenario: *//')")
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

# Resolve the same backend file precedence used by this packaged Codex copy's
# _load_backends implementation: explicit override, payload cwd, then global
# config.  Project activation must probe this exact host rather than the main
# repository root when a worktree carries a divergent backend config.
_resolve_backends_file() {
  local cwd="${1:-}"
  if [ -n "${MEMORIES_BACKENDS_FILE:-}" ]; then
    [ -f "$MEMORIES_BACKENDS_FILE" ] || return 1
    printf '%s' "$MEMORIES_BACKENDS_FILE"
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

  local config_file="${MEMORIES_BACKENDS_FILE:-}"

  # Resolution: explicit env -> project checkout -> global -> env fallback.
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
  local usage_source="${MEMORIES_USAGE_SOURCE:-}"
  local usage_client="${MEMORIES_USAGE_CLIENT:-$(_memory_client_prefix 2>/dev/null || echo codex)}"
  local usage_session_id="${MEMORIES_USAGE_SESSION_ID:-}"
  local usage_invocation="${MEMORIES_USAGE_INVOCATION:-${MEMORIES_HOOK_NAME:-hook}}"

  if _breaker_open; then
    MEMORIES_BACKEND_DOWN=1
    echo '{"results":[],"count":0}'
    return
  fi

  local backends
  backends=$(_get_backends_for_op "search")
  local count
  count=$(echo "$backends" | jq 'length')

  local body
  if [ -n "$prefix" ]; then
    if [ "${PROJECT_CONTEXT_ACTIVE:-false}" = "true" ]; then
      body=$(jq -nc --arg q "$query" --arg p "$prefix" --arg s "$usage_source" --argjson k "$limit" --argjson t "$threshold" \
        '{query: $q, source_prefix: $p, source_boundary: true, source: $s, k: $k, hybrid: true, threshold: $t}')
    else
      body=$(jq -nc --arg q "$query" --arg p "$prefix" --arg s "$usage_source" --argjson k "$limit" --argjson t "$threshold" \
        '{query: $q, source_prefix: $p, source: $s, k: $k, hybrid: true, threshold: $t}')
    fi
  else
    body=$(jq -nc --arg q "$query" --arg s "$usage_source" --argjson k "$limit" --argjson t "$threshold" \
      '{query: $q, source: $s, k: $k, hybrid: true, threshold: $t}')
  fi

  if [ "$count" -le 1 ]; then
    # Single backend — direct call (backward compat, no overhead)
    local url key
    url=$(echo "$backends" | jq -r '.[0].url')
    key=$(echo "$backends" | jq -r '.[0].api_key')
    local out
    if out=$(curl -sf --max-time 4 -X POST "$url/search" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $key" \
      -H "X-Memories-Client: $usage_client" \
      -H "X-Memories-Session-Id: $usage_session_id" \
      -H "X-Memories-Invocation: $usage_invocation" \
      -d "$body" 2>/dev/null); then
      _breaker_reset
      printf '%s' "$out"
    else
      _breaker_trip
      MEMORIES_BACKEND_DOWN=1
      echo '{"results":[],"count":0}'
    fi
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
        -H "X-Memories-Client: $usage_client" \
        -H "X-Memories-Session-Id: $usage_session_id" \
        -H "X-Memories-Invocation: $usage_invocation" \
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
