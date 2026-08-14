#!/bin/bash
# memory-recall.sh — SessionStart hook (Codex)
# Loads project-relevant memory pointers into Codex context.
# Sync hook: blocks until done, injects additionalContext.

MEMORIES_HOOK_NAME="memory-recall"

set -euo pipefail

# Load from dedicated env file — avoids requiring shell profile changes
# Load ~/.config/memories/env WITHOUT clobbering variables the environment
# already set. A cloud environment supplies MEMORIES_URL/MEMORIES_API_KEY as
# real env vars, while a setup script running `memories-mcp init` before those
# vars exist writes the localhost DEFAULT into this file — sourcing it plainly
# then overwrote the correct URL with a dead one, and the session reported the
# backend unreachable. An explicitly-set environment variable wins.
_memories_env_file="${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
if [ -f "$_memories_env_file" ]; then
  _memories_env_snapshot=$(export -p | grep 'MEMORIES_' || true)
  . "$_memories_env_file"
  eval "$_memories_env_snapshot"
  unset _memories_env_snapshot
fi
unset _memories_env_file
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _resolve_primary_backend_url() { printf '%s' "${MEMORIES_URL:-http://localhost:8900}"; }
  _default_source_prefixes() { echo 'codex/{project},claude-code/{project},learning/{project},wip/{project}'; }
  _hook_deadline_init() { :; }
  _hook_deadline_exhausted() { printf 'false'; }
  _hook_call_budget() { printf '%s' "$1"; }
fi

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // .workspace_roots[0] // empty')
_exit_if_disabled "$CWD" 2>/dev/null || true

# Bound the full SessionStart hook, not just individual curl calls.
_hook_deadline_init

# Rotate log on session start
_rotate_log

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
MEMORIES_SOURCE_PREFIXES="${MEMORIES_SOURCE_PREFIXES:-}"
if [ -z "$MEMORIES_SOURCE_PREFIXES" ]; then
  MEMORIES_SOURCE_PREFIXES="$(_default_source_prefixes)"
fi
MEMORIES_RECALL_SCOPED_THRESHOLD="${MEMORIES_RECALL_SCOPED_THRESHOLD:-0.35}"
MEMORIES_RECALL_FALLBACK_THRESHOLD="${MEMORIES_RECALL_FALLBACK_THRESHOLD:-0.55}"

# Configurable recall limit (Task 1.2)
RECALL_LIMIT="${MEMORIES_RECALL_LIMIT:-8}"

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // .sessionId // "unknown"')
MEMORIES_USAGE_CLIENT=$(_memory_client_prefix 2>/dev/null || echo "codex")
MEMORIES_USAGE_SESSION_ID="$SESSION_ID"
MEMORIES_USAGE_INVOCATION="$MEMORIES_HOOK_NAME"
MEMORIES_USAGE_SOURCE="hook:$MEMORIES_USAGE_CLIENT:$MEMORIES_HOOK_NAME"
if [ -z "$CWD" ]; then
  exit 0
fi

PROJECT=$(_memories_resolve_project "$CWD" 2>/dev/null || basename "$CWD")
PROJECT_CONTEXT_JSON=$(_memories_project_context "$CWD" 2>/dev/null || printf '{"active":false}')
PROJECT_CONTEXT_ACTIVE=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.active // false' 2>/dev/null || printf 'false')
if [ "$PROJECT_CONTEXT_ACTIVE" != "true" ] && declare -F _memories_project_context_declared >/dev/null && _memories_project_context_declared "$PROJECT_CONTEXT_JSON"; then
  PROJECT_UNAVAILABLE_MESSAGE=$(_memories_project_unavailable_message "$PROJECT_CONTEXT_JSON")
  _log_warn "$PROJECT_UNAVAILABLE_MESSAGE"
  jq -n --arg message "$PROJECT_UNAVAILABLE_MESSAGE" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$message}}'
  exit 0
fi
PROJECT_CONTEXT_ID=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.project_id // empty' 2>/dev/null || true)
PROJECT_CONTEXT_PRINCIPAL=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.principal_id // empty' 2>/dev/null || true)
PROJECT_CONTEXT_LEGACY_PREFIXES=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '(.legacy_source_prefixes // []) | join(",")' 2>/dev/null || true)
PROJECT_CONTEXT_PREFIXES=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -c '.prefixes // []' 2>/dev/null || printf '[]')
PROJECT_CONTEXT_PREFIXES_UNRESTRICTED=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.prefixes_unrestricted // false' 2>/dev/null || printf 'false')
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  PROJECT="$PROJECT_CONTEXT_ID"
  MEMORIES_SOURCE_PREFIXES=$(_memories_project_recall_prefixes "$PROJECT_CONTEXT_ID" "$PROJECT_CONTEXT_PRINCIPAL" "$PROJECT_CONTEXT_LEGACY_PREFIXES" "$PROJECT_CONTEXT_PREFIXES" "$PROJECT_CONTEXT_PREFIXES_UNRESTRICTED" | tr '\n' ',' | sed 's/,$//')
  [ -n "$MEMORIES_SOURCE_PREFIXES" ] || exit 0
fi
PROJECT_SHARING_GUIDANCE=""
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  PROJECT_SHARING_GUIDANCE="Collaborative project memory: apply the durable-sharing test (another contributor will need this fact without the current session). Use memory_add exactly once with project/$PROJECT_CONTEXT_ID/<decisions|knowledge|state|operations> for deliberate shared facts. Automatic extraction remains private in person/$PROJECT_CONTEXT_PRINCIPAL/$PROJECT_CONTEXT_ID/knowledge; never infer project/... ."
fi
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi

# Parse Codex session source (startup, resume, clear)
SESSION_SOURCE=$(echo "$INPUT" | jq -r '.source // "unknown"')

_log_info "Session start for project=$PROJECT cwd=$CWD source=$SESSION_SOURCE"

# Health check — warn if every routed search backend is unreachable.
HEALTH_WARNING=""
if ! _health_check; then
  HEALTH_DOWN_NAMES="${MEMORIES_HEALTH_DOWN_NAMES:-${MEMORIES_URL:-http://localhost:8900}}"
  _log_warn "Service unreachable: $HEALTH_DOWN_NAMES"
  HEALTH_WARNING=$(cat <<HWEOF
## Memories Service Warning

Memories recall/search is unavailable this session ($HEALTH_DOWN_NAMES). Check that the service is running.
HWEOF
)
fi

AUTH_FAILED="false"
AUTH_FAILED_BACKENDS_JSON='[]'
_note_auth_status() {
  local resp="$1"
  local flag backends
  flag=$(printf '%s' "$resp" | jq -r '.auth_failed // false' 2>/dev/null) || flag="false"
  [ "$flag" = "true" ] && AUTH_FAILED="true"
  backends=$(printf '%s' "$resp" | jq -c '.auth_failed_backends // []' 2>/dev/null) || backends='[]'
  AUTH_FAILED_BACKENDS_JSON=$(jq -nc \
    --argjson existing "$AUTH_FAILED_BACKENDS_JSON" \
    --argjson incoming "$backends" \
    '$existing + $incoming | unique_by((.name // "") + "|" + (.url // ""))')
  return 0
}

search_memories() {
  if [ "${PROJECT_CONTEXT_ACTIVE:-false}" = "true" ] && [ -n "${2:-}" ]; then
    _search_memories_multi "$@" | _memories_filter_search_response_for_prefix "$2"
  else
    _search_memories_multi "$@"
  fi
}

query_for_prefix() {
  local prefix="$1"
  case "$prefix" in
    claude-code/*|codex/*)
      printf 'project %s architecture decisions conventions patterns' "$PROJECT"
      ;;
    learning/*)
      printf 'project %s fixes gotchas learnings workarounds' "$PROJECT"
      ;;
    wip/*)
      printf 'project %s deferred work blockers open threads revisit later' "$PROJECT"
      ;;
    *)
      printf 'project %s conventions decisions patterns' "$PROJECT"
      ;;
  esac
}

RAW_RESPONSES=""
SCOPED_PREFIX_LIST=""
SEARCH_COUNT=0
WIP_PREFIX="wip/$PROJECT"
WIP_CONFIGURED=false
WIP_SEARCHED=false
WIP_RESULTS='{"results":[],"count":0}'
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  IFS=',' read -r -a configured_wip_prefixes <<< "$MEMORIES_SOURCE_PREFIXES"
  for configured_wip_prefix in "${configured_wip_prefixes[@]}"; do
    configured_wip_prefix=$(printf '%s' "$configured_wip_prefix" | xargs)
    [ "$configured_wip_prefix" = "$WIP_PREFIX" ] && WIP_CONFIGURED=true
  done
fi
IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
prefix_idx=0
for raw_prefix in "${prefix_templates[@]}"; do
  prefix_idx=$((prefix_idx + 1))
  raw_prefix=$(echo "$raw_prefix" | xargs)
  [ -z "$raw_prefix" ] && continue

  if [ "$(_hook_deadline_exhausted)" = "true" ]; then
    SKIPPED_PREFIXES=$(IFS=,; echo "${prefix_templates[*]:$((prefix_idx - 1))}")
    _log_warn "Hook budget exhausted — skipping remaining prefix searches: $SKIPPED_PREFIXES"
    break
  fi

  prefix=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
  query=$(query_for_prefix "$prefix")
  limit=3
  case "$prefix" in
    claude-code/*|codex/*) limit=4 ;;
    learning/*|wip/*) limit=2 ;;
  esac

  SEARCH_COUNT=$((SEARCH_COUNT + 1))
  response=$(search_memories "$query" "$prefix" "$limit" "$MEMORIES_RECALL_SCOPED_THRESHOLD")
  if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ] && [ "$prefix" = "$WIP_PREFIX" ]; then
    WIP_SEARCHED=true
    [ -n "$response" ] && WIP_RESULTS="$response"
  fi
  _note_auth_status "$response"
  if [ -n "$response" ]; then
    RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
  fi

  if [ -n "$SCOPED_PREFIX_LIST" ]; then
    SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST, "
  fi
  SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST$prefix"
done

if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | _memories_merge_search_results true "$RECALL_LIMIT" 2>/dev/null) || RESULTS_JSON="[]"
else
  RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr --argjson limit "$RECALL_LIMIT" '
    map(select(type == "object") | (.results // []))
    | add
    | unique_by(.id)
    | sort_by(-(.similarity // .rrf_score // 0))
    | .[0:$limit]
  ' 2>/dev/null) || RESULTS_JSON="[]"
fi
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  RESULTS_JSON=$(printf '%s' "$RESULTS_JSON" | _memories_label_project_results "$PROJECT_CONTEXT_ID" 2>/dev/null) || RESULTS_JSON="[]"
fi

if [ "$PROJECT_CONTEXT_ACTIVE" != "true" ] && [ "$RESULTS_JSON" = "[]" ]; then
  if [ "$(_hook_deadline_exhausted)" = "true" ]; then
    _log_warn "Hook budget exhausted — skipping the unscoped fallback search"
  else
    SEARCH_COUNT=$((SEARCH_COUNT + 1))
    FALLBACK_RESPONSE=$(search_memories "project $PROJECT conventions decisions patterns" "" 6 "$MEMORIES_RECALL_FALLBACK_THRESHOLD")
    _note_auth_status "$FALLBACK_RESPONSE"
    RESULTS_JSON=$(printf '%s' "$FALLBACK_RESPONSE" | jq -c '.results // []' 2>/dev/null) || RESULTS_JSON="[]"
  fi
fi

CONTEXT_RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map(("- [\(.source)]" + (if (.provenance_label // "") != "" then " " + .provenance_label else "" end) + " candidate memory id=\(.id // .memory_id // "unknown") found at session start; call memory_search with this source prefix before using it.")) | join("\n")
  end
' 2>/dev/null) || true

_log_info "Recalled $(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0) memories for $PROJECT"

# Dedicated deferred-work surfacing
WIP_QUERY="deferred incomplete blocked todo revisit wip"
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ] && [ "$WIP_SEARCHED" = "true" ]; then
  : # Reuse the authorized scoped response collected above.
elif [ "$(_hook_deadline_exhausted)" = "true" ]; then
  _log_warn "Hook budget exhausted — skipping the deferred-work (WIP) search"
  WIP_RESULTS='{"results":[],"count":0}'
elif [ "$PROJECT_CONTEXT_ACTIVE" != "true" ] || [ "$WIP_CONFIGURED" = "true" ]; then
  SEARCH_COUNT=$((SEARCH_COUNT + 1))
  WIP_RESULTS=$(search_memories "$WIP_QUERY" "$WIP_PREFIX" 5 0.3)
  _note_auth_status "$WIP_RESULTS"
fi
WIP_COUNT=$(echo "$WIP_RESULTS" | jq -r '.count // 0')
DEFERRED_SECTION=""
if [ "$WIP_COUNT" -gt 0 ] && [ "$WIP_COUNT" != "null" ]; then
  DEFERRED_ITEMS=$(echo "$WIP_RESULTS" | jq -r '.results[:5][] | "- [\(.source)] deferred candidate memory id=\(.id // .memory_id // "unknown"); call memory_search with this source prefix before answering deferred-work questions."')
  DEFERRED_SECTION="\n## Deferred Work\n$DEFERRED_ITEMS\n"
fi

CANDIDATE_COUNT=$(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0)
CANDIDATE_IDS_JSON=$(printf '%s' "$RESULTS_JSON" | jq -c '[.[].id | select(type == "number")] | unique | .[0:20]' 2>/dev/null || echo '[]')
SOURCE_PREFIXES_JSON=$(printf '%s' "$RESULTS_JSON" | jq -c '[.[].source // empty | select(. != "")] | unique' 2>/dev/null || echo '[]')
METRICS_EVENT=$(jq -nc \
  --arg ts "$(date -u +%FT%TZ)" \
  --arg client "$MEMORIES_USAGE_CLIENT" \
  --arg session_id "$SESSION_ID" \
  --arg project "$PROJECT" \
  --arg session_source "$SESSION_SOURCE" \
  --argjson candidate_count "$CANDIDATE_COUNT" \
  --argjson candidate_ids "$CANDIDATE_IDS_JSON" \
  --argjson source_prefixes "$SOURCE_PREFIXES_JSON" \
  --argjson search_count "$SEARCH_COUNT" \
  '{ts: $ts, event: "session_recall", client: $client, session_id: $session_id, project: $project, session_source: $session_source, candidate_count: $candidate_count, candidate_ids: $candidate_ids, source_prefixes: $source_prefixes, search_count: $search_count}')
_active_search_metrics_log "$METRICS_EVENT" 2>/dev/null || true

read -r -d '' PLAYBOOK <<EOF || true
## Memory Playbook

IMPORTANT: Search memories BEFORE responding to questions about prior
decisions, architecture, project conventions, deferred work, past bugs, project
history, or resuming a topic. Hook-injected memories below are keyword-matched
starting points — they are NOT complete context.

For self-contained prompts that do not depend on prior/project context
(arithmetic, translation, formatting, generic facts), answer normally without
calling memory_search.

ACTIVE SEARCH ACTION for applicable prompts: use the memory_search tool before
answering.

You MUST call memory_search when the user's message contains:
- Questions about prior decisions ("weren't we...", "didn't we decide...")
- Architecture questions ("how does X work", "what mode does X use")
- Performance/debugging references ("is X still slow", "did we fix Y")
- Release/version context ("what version", "what's deferred")
- Continuation of prior work ("last time we...", "we were doing...")

Do NOT rationalize skipping this step for prior-work prompts:
| Thought | Reality |
| "The injected memories cover it" | They are keyword-matched, not semantic |
| "I can infer from the code" | Prior decisions aren't in code |
| "It's a simple question" | Simple questions about past work need recall |

After searching, use searched memories as the answer source; use hook-injected pointers
only to choose scoped prefixes and candidate ids.
Prefer scoped prefixes: $SCOPED_PREFIX_LIST.
Use exact source prefixes from candidate pointers first. Do not use family-wide
prefixes like claude-code/, codex/, learning/, wip/, or unscoped search until the
exact project prefixes have been tried.
When memories show deferred/blocked work, say "not yet" or "deferred" directly.
Preserve boundary conditions (until/unless/because) verbatim.
Do not ask the user to reconfirm a remembered decision.
EOF
if [ -n "$PROJECT_SHARING_GUIDANCE" ]; then
  PLAYBOOK="$PLAYBOOK

$PROJECT_SHARING_GUIDANCE"
fi

# Credential diagnostic: /health is unauthenticated, so report a rejected
# search key separately from a backend reachability warning.
CREDENTIAL_WARNING=""
if [ "$AUTH_FAILED" = "true" ]; then
  AUTH_BACKEND_LABELS=$(printf '%s' "$AUTH_FAILED_BACKENDS_JSON" | jq -r 'map("\(.name) (\(.url))") | join(", ")')
  AUTH_DEFAULT_ENV_ONLY=$(printf '%s' "$AUTH_FAILED_BACKENDS_JSON" | jq -r 'length > 0 and all(.[]; ((.name // "") == "default") and ((.env_backed // false) == true))')
  AUTH_KEY_ENV_REFS=$(printf '%s' "$AUTH_FAILED_BACKENDS_JSON" | jq -r '[.[] | select((.env_backed // false) != true and (.api_key_env // "") != "") | .api_key_env] | unique | join(", ")')
  _log_warn "Backend(s) rejected the API key (401): $AUTH_BACKEND_LABELS"
  if [ "$AUTH_DEFAULT_ENV_ONLY" = "true" ]; then
    CREDENTIAL_WARNING=$(cat <<CWEOF
## Memories Credential Warning

Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Set MEMORIES_API_KEY; memory recall/search is unavailable for this backend.
CWEOF
    )
  elif [ -n "$AUTH_KEY_ENV_REFS" ] && [ "$CANDIDATE_COUNT" -gt 0 ]; then
    CREDENTIAL_WARNING=$(cat <<CWEOF
## Memories Credential Warning

Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Update the configured api_key or referenced environment variable(s) $AUTH_KEY_ENV_REFS for those backends; healthy routed backends still returned candidates this session.
CWEOF
    )
  elif [ "$CANDIDATE_COUNT" -gt 0 ]; then
    CREDENTIAL_WARNING=$(cat <<CWEOF
## Memories Credential Warning

Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Update the configured api_key for those backends or its referenced environment variable; healthy routed backends still returned candidates this session.
CWEOF
    )
  elif [ -n "$AUTH_KEY_ENV_REFS" ]; then
    CREDENTIAL_WARNING=$(cat <<CWEOF
## Memories Credential Warning

Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Update the configured api_key or referenced environment variable(s) $AUTH_KEY_ENV_REFS for those backends.
CWEOF
    )
  else
    CREDENTIAL_WARNING=$(cat <<CWEOF
## Memories Credential Warning

Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Update the configured api_key for those backends or its referenced environment variable.
CWEOF
    )
  fi
fi

# --- Output context for Codex ---
jq -n --arg memories "$CONTEXT_RESULTS" --arg playbook "$PLAYBOOK" --arg health_warning "$HEALTH_WARNING" --arg credential_warning "$CREDENTIAL_WARNING" --arg deferred "$DEFERRED_SECTION" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: (
      (if ($health_warning | length) > 0 then $health_warning + "\n\n" else "" end) +
      (if ($credential_warning | length) > 0 then $credential_warning + "\n\n" else "" end) +
      (if ($memories | length) > 0 then "## Relevant Memories\n\n" + $memories + "\n\n" else "" end) +
      (if ($deferred | length) > 0 then $deferred + "\n" else "" end) +
      $playbook
    )
  }
}'
