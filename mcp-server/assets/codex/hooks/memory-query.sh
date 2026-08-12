#!/bin/bash
# memory-query.sh — UserPromptSubmit hook
# Searches Memories for memories relevant to the current prompt.
# Prefers project-scoped sources first and uses recent transcript context
# so short follow-up prompts still retrieve the right memories.
# Sync hook: blocks until done, injects additionalContext.

MEMORIES_HOOK_NAME="memory-query"

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
_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _resolve_primary_backend_url() { printf '%s' "${MEMORIES_URL:-http://localhost:8900}"; }
  _default_source_prefixes() { echo 'codex/{project},claude-code/{project},learning/{project},wip/{project}'; }
  # Degraded fallbacks when _lib.sh is missing: keep the active-search
  # classifier identical and gate the playbook on candidate count only.
  _active_search_pattern() { printf '%s' '(^|[^a-z])(did we already|do you remember|remember (how|what|where|when|why|the)|recall|already decide|where did we|how did we|what did we|what was the last|where we left|left off|resume|continue where|previous|prior|earlier|last (fix|time|decision|session|run)|deferred|blocked|follow.?up|next steps|what.?s the plan|what is the plan|current plan|existing plan|release gate|gated)([^a-z]|$)'; }
  _playbook_injection_mode() { if [ "${2:-0}" -ge 1 ] 2>/dev/null; then printf 'full'; else printf 'minimal'; fi; }
fi

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // empty')
_exit_if_disabled "$CWD" 2>/dev/null || true

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
MEMORIES_SOURCE_PREFIXES="${MEMORIES_SOURCE_PREFIXES:-}"
if [ -z "$MEMORIES_SOURCE_PREFIXES" ]; then
  MEMORIES_SOURCE_PREFIXES="$(_default_source_prefixes)"
fi
MEMORIES_QUERY_SCOPED_K="${MEMORIES_QUERY_SCOPED_K:-3}"
MEMORIES_QUERY_FALLBACK_K="${MEMORIES_QUERY_FALLBACK_K:-5}"
MEMORIES_QUERY_SCOPED_THRESHOLD="${MEMORIES_QUERY_SCOPED_THRESHOLD:-0.35}"
MEMORIES_QUERY_FALLBACK_THRESHOLD="${MEMORIES_QUERY_FALLBACK_THRESHOLD:-0.55}"

PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // .sessionId // "unknown"')
MEMORIES_USAGE_CLIENT=$(_memory_client_prefix 2>/dev/null || echo "codex")
MEMORIES_USAGE_SESSION_ID="$SESSION_ID"
MEMORIES_USAGE_INVOCATION="$MEMORIES_HOOK_NAME"
MEMORIES_USAGE_SOURCE="hook:$MEMORIES_USAGE_CLIENT:$MEMORIES_HOOK_NAME"
PROJECT=$(_memories_resolve_project "${CWD:-}" 2>/dev/null || basename "${CWD:-}")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  PROJECT=""
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
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // .transcriptPath // empty')
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

build_response_hint() {
  local prompt_lower="$1"
  local hints_file="$(dirname "${BASH_SOURCE[0]}")/response-hints.json"

  [ -f "$hints_file" ] || return

  # Check each pattern
  local match template
  while IFS= read -r line; do
    match=$(echo "$line" | jq -r '.match')
    template=$(echo "$line" | jq -r '.template')
    if echo "$prompt_lower" | grep -qiE "$match"; then
      echo "$template"
      return
    fi
  done < <(jq -c '.patterns[]' "$hints_file")
}

build_keyword_bag() {
  local prompt="$1"
  local project="$2"
  local bag="$project"
  local identifiers
  identifiers=$(echo "$prompt" | { grep -oE '[A-Z][a-z]+([A-Z][a-z]+)+|[a-z]+_[a-z_]+|[A-Z_]{3,}' 2>/dev/null || true; } | sort -u | head -10 | tr '\n' ' ')
  local versions
  versions=$(echo "$prompt" | { grep -oE 'v[0-9]+\.[0-9]+[0-9.]*|#[0-9]+|PR[- ]?[0-9]+' 2>/dev/null || true; } | sort -u | head -5 | tr '\n' ' ')
  local nouns
  local stopwords="ok okay wait wtf dammit hmm yes no sure right well so but and the this that is are was were we you i it a an of to in for on with from by at or not do does did dont doesnt didnt can cant could would should have has had been be will just also like think feel want need know see get got let lets go make made way thing stuff something there then than what when where which who how why about into more some only other its here very after before because being our them they these those out uses use used using"
  nouns=$(echo "$prompt" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z' ' ' | tr -s ' ' | \
    awk -v stops="$stopwords" 'BEGIN{n=split(stops,a," ");for(i=1;i<=n;i++)s[a[i]]=1} {for(i=1;i<=NF;i++)if(length($i)>=3 && !($i in s))print $i}' | \
    sort -u | head -15 | tr '\n' ' ')
  bag="$bag $identifiers $versions $nouns"
  echo "$bag" | tr -s ' ' | sed 's/^ //;s/ $//'
}

extract_recent_context() {
  local transcript_path="$1"
  if [ -z "$transcript_path" ] || [ ! -f "$transcript_path" ]; then
    return 0
  fi

  # Flexible transcript parsing: supports Claude Code (.type + .message.content),
  # legacy Codex JSONL (.message.role + .content), and current Codex rollout
  # response items (.payload.role + .payload.content).
  tail -200 "$transcript_path" 2>/dev/null | jq -sr '
    [
      .[]
      | select(
          ((.payload.role // .message.role // .role // .type // "") | tostring) as $r |
          ($r == "user" or $r == "assistant")
        )
      | {
          role: ((.payload.role // .message.role // .role // .type // "") | tostring),
          text: (
            if (.payload.content // null) != null then
              if (.payload.content | type) == "string" then .payload.content
              elif (.payload.content | type) == "array" then [.payload.content[] | .text? // empty] | join(" ")
              else ""
              end
            elif (.message.content // null) != null then
              if (.message.content | type) == "string" then .message.content
              elif (.message.content | type) == "array" then [.message.content[] | .text? // empty] | join(" ")
              else ""
              end
            elif (.content // null) != null then
              if (.content | type) == "string" then .content
              elif (.content | type) == "array" then [.content[] | .text? // empty] | join(" ")
              else ""
              end
            elif ((.text // null) | type) == "string" then .text
            else ""
            end
          )
        }
      | select(.text != "" and (.text | length) > 4)
    ]
    | .[-4:]
    | map(
        (if .role == "user" then "User: " else "Assistant: " end) +
        (
          .text
          | gsub("[\\r\\n]+"; " ")
          | gsub("\\s+"; " ")
          | .[0:500]
        )
      )
    | join("\n")
  ' 2>/dev/null || { _log_warn "Transcript context extraction failed"; true; }
}

search_memories() {
  _search_memories_multi "$@"
}

CONTEXT=$(extract_recent_context "$TRANSCRIPT_PATH")
PROMPT_LOWER=$(printf '%s' "$PROMPT" | tr '[:upper:]' '[:lower:]')
ACTIVE_SEARCH_REQUIRED=0
ACTIVE_SEARCH_PATTERN="$(_active_search_pattern)"
if printf '%s' "$PROMPT_LOWER" | grep -qiE "$ACTIVE_SEARCH_PATTERN"; then
  ACTIVE_SEARCH_REQUIRED=1
fi

# File context extraction — grep recent transcript for Read/Edit/Write tool calls
FILE_CONTEXT=""
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  ACTIVE_FILES=$(tail -20 "$TRANSCRIPT_PATH" 2>/dev/null | { grep -oE '(Read|Edit|Write) /[^ "]+' || true; } | awk '{print $2}' | xargs -I{} basename {} 2>/dev/null | sort -u | head -5 | tr '\n' ', ' | sed 's/,$//')
  [ -n "$ACTIVE_FILES" ] && FILE_CONTEXT="Files: $ACTIVE_FILES"
fi

# Key term extraction — pull identifiers from the prompt
KEY_TERMS=$(echo "$PROMPT" | { grep -oE '[A-Z][a-z]+([A-Z][a-z]+)+|[a-z]+_[a-z_]+|[A-Z_]{3,}' 2>/dev/null || true; } | sort -u | head -10 | tr '\n' ', ' | sed 's/,$//')
[ -n "$KEY_TERMS" ] && KEY_TERMS="Terms: $KEY_TERMS"

# Intent-based prefix biasing
INTENT_PREFIXES=""
case "$PROMPT_LOWER" in
  fix*|debug*|error*|bug*|broken*|crash*)
    INTENT_PREFIXES="learning/$PROJECT bug-fix/$PROJECT" ;;
  how*|setup*|configure*|install*)
    INTENT_PREFIXES="decision/$PROJECT learning/$PROJECT" ;;
esac

# Build enriched keyword-bag query
KEYWORD_BAG=""
if [ -n "$PROJECT" ]; then
  KEYWORD_BAG=$(build_keyword_bag "$PROMPT" "$PROJECT")
fi

# Include conversation context identifiers in the enriched query
if [ -n "$CONTEXT" ]; then
  CONTEXT_TERMS=$(echo "$CONTEXT" | { grep -oE '[A-Z][a-z]+([A-Z][a-z]+)+|[a-z]+_[a-z_]+' 2>/dev/null || true; } | sort -u | head -5 | tr '\n' ' ')
  ENRICHED_QUERY="$KEYWORD_BAG $CONTEXT_TERMS"
else
  ENRICHED_QUERY="$KEYWORD_BAG"
fi

# For very short prompts with no enrichment, fall back to original query
if [ -z "$ENRICHED_QUERY" ] || [ ${#ENRICHED_QUERY} -lt 5 ]; then
  ENRICHED_QUERY="$PROMPT"
  if [ -n "$CONTEXT" ]; then
    ENRICHED_QUERY=$(printf 'Project: %s\nRecent conversation:\n%s\nCurrent prompt: %s' "${PROJECT:-unknown}" "$CONTEXT" "$PROMPT")
  fi
fi

# Preserve original verbose query for fallback
QUERY_TEXT="$PROMPT"
if [ -n "$CONTEXT" ]; then
  FALLBACK_PREFIX=""
  [ -n "$FILE_CONTEXT" ] && FALLBACK_PREFIX="$FILE_CONTEXT\n"
  [ -n "$KEY_TERMS" ] && FALLBACK_PREFIX="${FALLBACK_PREFIX}$KEY_TERMS\n"
  QUERY_TEXT=$(printf '%s\nProject: %s\nRecent conversation:\n%s\nCurrent prompt: %s' "$FALLBACK_PREFIX" "${PROJECT:-unknown}" "$CONTEXT" "$PROMPT")
elif [ -n "$FILE_CONTEXT" ] || [ -n "$KEY_TERMS" ]; then
  QUERY_TEXT=$(printf '%s\n%s\n%s' "$FILE_CONTEXT" "$KEY_TERMS" "$PROMPT")
fi

# Skip if no meaningful input
if [ -z "$ENRICHED_QUERY" ] && [ -z "$QUERY_TEXT" ]; then
  exit 0
fi
# --- Dual search strategy ---
RAW_RESPONSES=""
SEARCH_RESPONSES_SEEN=0
SEARCH_REACHABLE_RESPONSES=0
SEARCH_TMPDIR=$(mktemp -d 2>/dev/null || mktemp -d -t memories-query)
SEARCH_JOBS=()
SEARCH_INDEX=0

_note_search_reachability() {
  local response="$1"
  [ -n "$response" ] || return 0
  SEARCH_RESPONSES_SEEN=$((SEARCH_RESPONSES_SEEN + 1))
  if printf '%s' "$response" | jq -e '(.backend_down // false) == false' >/dev/null 2>&1; then
    SEARCH_REACHABLE_RESPONSES=$((SEARCH_REACHABLE_RESPONSES + 1))
  fi
}

queue_search() {
  local query="$1" prefix="$2" limit="$3" threshold="$4"
  local outfile="$SEARCH_TMPDIR/result_${SEARCH_INDEX}.json"
  SEARCH_INDEX=$((SEARCH_INDEX + 1))
  (
    search_memories "$query" "$prefix" "$limit" "$threshold" > "$outfile" || true
  ) &
  SEARCH_JOBS+=("$!")
}

# Strategy A: enriched unscoped (cross-project, semantic)
queue_search "$ENRICHED_QUERY" "" 6 0.30

# Strategy B: enriched prefix-scoped (project-specific precision across client families)
SCOPED_PREFIX_LIST=""
if [ -n "$PROJECT" ]; then
  IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
  for raw_prefix in "${prefix_templates[@]}"; do
    prefix=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g" | xargs)
    [ -z "$prefix" ] && continue
    queue_search "$ENRICHED_QUERY" "$prefix" "$MEMORIES_QUERY_SCOPED_K" "$MEMORIES_QUERY_SCOPED_THRESHOLD"
    [ -n "$SCOPED_PREFIX_LIST" ] && SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST, "
    SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST$prefix"
  done
fi

# Intent-based prefix biasing (additional search for fix/debug/setup prompts)
if [ -n "$INTENT_PREFIXES" ] && [ -n "$PROJECT" ]; then
  for intent_prefix in $INTENT_PREFIXES; do
    queue_search "$ENRICHED_QUERY" "$intent_prefix" "$MEMORIES_QUERY_SCOPED_K" "$MEMORIES_QUERY_SCOPED_THRESHOLD"
  done
fi

for job in "${SEARCH_JOBS[@]}"; do
  wait "$job" || true
done

if ls "$SEARCH_TMPDIR"/result_*.json >/dev/null 2>&1; then
  RAW_RESPONSES=$(cat "$SEARCH_TMPDIR"/result_*.json 2>/dev/null || true)
fi
rm -rf "$SEARCH_TMPDIR"

if [ -n "$RAW_RESPONSES" ]; then
  while IFS= read -r response; do
    _note_search_reachability "$response"
  done <<< "$RAW_RESPONSES"
  AUTH_FAILED_BACKENDS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sc \
    '[.[].auth_failed_backends[]?] | unique_by((.name // "") + "|" + (.url // ""))' \
    2>/dev/null) || AUTH_FAILED_BACKENDS_JSON='[]'
  if printf '%s\n' "$RAW_RESPONSES" | jq -se 'any(.[]; .auth_failed == true)' >/dev/null 2>&1; then
    AUTH_FAILED="true"
  fi
fi

# Merge, deduplicate, cap at 6
RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr '
  map(select(type == "object") | (.results // []))
  | add
  | if . == null then [] else . end
  | unique_by(.id)
  | sort_by(-(.similarity // .rrf_score // 0))
  | .[0:6]
' 2>/dev/null) || RESULTS_JSON="[]"

# Fallback if dual strategy returns empty
if [ "$RESULTS_JSON" = "[]" ]; then
  SEARCH_INDEX=$((SEARCH_INDEX + 1))
  FALLBACK_RESPONSE=$(search_memories "$QUERY_TEXT" "" "$MEMORIES_QUERY_FALLBACK_K" "$MEMORIES_QUERY_FALLBACK_THRESHOLD")
  _note_search_reachability "$FALLBACK_RESPONSE"
  _note_auth_status "$FALLBACK_RESPONSE"
  RESULTS_JSON=$(printf '%s' "$FALLBACK_RESPONSE" | jq -c '.results // []' 2>/dev/null) || RESULTS_JSON="[]"
fi

SEARCH_BACKEND_DOWN=false
if [ "$SEARCH_RESPONSES_SEEN" -gt 0 ] && [ "$SEARCH_REACHABLE_RESPONSES" -eq 0 ]; then
  SEARCH_BACKEND_DOWN=true
fi

CREDENTIAL_WARNING=""
if [ "$AUTH_FAILED" = "true" ]; then
  AUTH_BACKEND_LABELS=$(printf '%s' "$AUTH_FAILED_BACKENDS_JSON" | jq -r 'map("\(.name) (\(.url))") | join(", ")')
  AUTH_DEFAULT_ENV_ONLY=$(printf '%s' "$AUTH_FAILED_BACKENDS_JSON" | jq -r 'length > 0 and all(.[]; ((.name // "") == "default") and ((.env_backed // false) == true))')
  AUTH_KEY_ENV_REFS=$(printf '%s' "$AUTH_FAILED_BACKENDS_JSON" | jq -r '[.[] | select((.env_backed // false) != true and (.api_key_env // "") != "") | .api_key_env] | unique | join(", ")')
  if [ "$AUTH_DEFAULT_ENV_ONLY" = "true" ]; then
    CREDENTIAL_WARNING="Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Set MEMORIES_API_KEY for this backend."
  elif [ -n "$AUTH_KEY_ENV_REFS" ]; then
    CREDENTIAL_WARNING="Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Update the configured api_key or referenced environment variable(s) $AUTH_KEY_ENV_REFS for those backends."
  else
    CREDENTIAL_WARNING="Search backend(s) $AUTH_BACKEND_LABELS rejected the API key. Update the configured api_key for those backends or its referenced environment variable."
  fi
fi

if [ "$ACTIVE_SEARCH_REQUIRED" = "1" ]; then
  RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
    if length == 0 then
      empty
    else
      map("- candidate memory from \(.source): call memory_search with source_prefix=\"\(.source)\" before answering. Do not use memory_get as a substitute.") | join("\n")
    end
  ' 2>/dev/null) || true
else
  RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
    if length == 0 then
      empty
    else
      map("- [\(.source)] \(.text)") | join("\n")
    end
  ' 2>/dev/null) || true
fi

# Telemetry: log every non-empty Codex prompt, including zero-candidate and
# short follow-up prompts. candidate_ids feed the recall-feedback loop
# (scripts/apply_memory_feedback.py) — they go to the local metrics log only,
# never into model context.
CANDIDATE_COUNT=$(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0)
HOOK_RESULTS_INJECTED=0
[ -n "$RESULTS" ] && [ "$RESULTS" != "null" ] && HOOK_RESULTS_INJECTED=1
if [ -n "$PROMPT" ]; then
  CLIENT=$(_memory_client_prefix 2>/dev/null || echo "codex")
  PROMPT_HASH=$(_hash_for_metrics "$PROMPT" 2>/dev/null || echo "")
  SOURCE_PREFIXES_JSON=$(printf '%s' "$RESULTS_JSON" | jq -c '[.[].source // empty | select(. != "")] | unique' 2>/dev/null || echo '[]')
  CANDIDATE_IDS_JSON=$(printf '%s' "$RESULTS_JSON" | jq -c '[.[].id | select(type == "number")] | unique | .[0:20]' 2>/dev/null || echo '[]')
  ACTIVE_SEARCH_BOOL=false
  [ "$ACTIVE_SEARCH_REQUIRED" = "1" ] && ACTIVE_SEARCH_BOOL=true
  METRICS_EVENT=$(jq -nc \
    --arg ts "$(date -u +%FT%TZ)" \
    --arg client "$CLIENT" \
    --arg session_id "$SESSION_ID" \
    --arg project "${PROJECT:-unknown}" \
    --arg prompt_hash "$PROMPT_HASH" \
    --argjson active_search_required "$ACTIVE_SEARCH_BOOL" \
    --argjson candidate_count "$CANDIDATE_COUNT" \
    --argjson search_count "$SEARCH_INDEX" \
    --argjson hook_results_injected "$HOOK_RESULTS_INJECTED" \
    --argjson source_prefixes "$SOURCE_PREFIXES_JSON" \
    --argjson candidate_ids "$CANDIDATE_IDS_JSON" \
    '{ts: $ts, event: "prompt_evaluated", client: $client, session_id: $session_id, project: $project, prompt_hash: $prompt_hash, active_search_required: $active_search_required, candidate_count: $candidate_count, search_count: $search_count, hook_results_injected: ($hook_results_injected == 1), source_prefixes: $source_prefixes, candidate_ids: $candidate_ids}')
  _active_search_metrics_log "$METRICS_EVENT" 2>/dev/null || true
fi

# Playbook gate: the full directive mandate is keyed on prompt SHAPE
# (prior-work prompts); candidate matches alone get the memories block with a
# short preamble; nothing matched gets a 1-2 line reminder. Keeps per-prompt
# token cost proportional to need.
PLAYBOOK_MODE=$(_playbook_injection_mode "$PROMPT" "$CANDIDATE_COUNT")

if [ "$PLAYBOOK_MODE" = "minimal" ]; then
  _log_info "Playbook gate: minimal reminder (candidates=$CANDIDATE_COUNT, prompt ${#PROMPT} chars)"
  jq -n --arg search_down "$SEARCH_BACKEND_DOWN" --arg credential_warning "$CREDENTIAL_WARNING" '{
	hookSpecificOutput: {
	  hookEventName: "UserPromptSubmit",
	  additionalContext: (if ($credential_warning | length) > 0 then $credential_warning + "\n\n" else "" end) + (if $search_down == "true" then "Memories note: recall/search is unavailable for this prompt because all routed search backends are unreachable." else "Memories MCP note: no stored memories matched this prompt via keyword retrieval. If this task turns out to depend on prior decisions or project history, call memory_search first." end)
		}
}'
  exit 0
fi

RESPONSE_HINT=$(build_response_hint "$PROMPT_LOWER")

if [ "$PLAYBOOK_MODE" = "memories" ]; then
  # Candidates matched but the prompt is not prior-work-shaped: inject the
  # memories with a short preamble instead of the full directive mandate.
  _log_info "Playbook gate: memories without mandate (candidates=$CANDIDATE_COUNT, prompt ${#PROMPT} chars)"
  jq -n --arg memories "$RESULTS" --arg response_hint "$RESPONSE_HINT" --arg credential_warning "$CREDENTIAL_WARNING" '{
	hookSpecificOutput: {
	  hookEventName: "UserPromptSubmit",
	  additionalContext: (
	    (if ($credential_warning | length) > 0 then $credential_warning + "\n\n" else "" end) + "Memories from prior sessions matched this prompt (keyword retrieval; may be incomplete). Consider them; if this task turns out to depend on prior decisions or project history, verify with memory_search before relying on assumptions.\n\n## Retrieved Memories\n" + $memories +
	    (if ($response_hint | length) > 0 then "\n\n" + $response_hint else "" end)
	  )
	}
}'
  exit 0
fi

_log_info "Playbook gate: full mandate (candidates=$CANDIDATE_COUNT, prompt ${#PROMPT} chars)"

if [ -n "$RESULTS" ] && [ "$RESULTS" != "null" ]; then
  jq -n --arg memories "$RESULTS" --arg response_hint "$RESPONSE_HINT" --arg credential_warning "$CREDENTIAL_WARNING" '{
	hookSpecificOutput: {
	  hookEventName: "UserPromptSubmit",
	  additionalContext: (
	    (if ($credential_warning | length) > 0 then $credential_warning + "\n\n" else "" end) + "IMPORTANT: hook-injected memories are keyword-matched starting points, not a substitute for active search.\n\nMANDATORY FIRST ACTION: if this prompt asks about prior decisions, project history, deferred work, conventions, or continuation of prior work, you MUST call memory_search before answering. Do not answer from injected memories alone. Do not use memory_get as a substitute for memory_search. Use exact source prefixes shown below before broad family prefixes or unscoped search.\n\n## Retrieved Memories\n" + $memories +
	    (if ($response_hint | length) > 0 then "\n\n" + $response_hint else "" end)
	  )
	}
}'
else
  # Prior-work-shaped prompt with no candidate memories: keep the directive
  # mandate (directive strength is required to survive context dilution) but
  # without a Retrieved Memories block.
  jq -n --arg response_hint "$RESPONSE_HINT" --arg prefixes "$SCOPED_PREFIX_LIST" --arg credential_warning "$CREDENTIAL_WARNING" '{
	hookSpecificOutput: {
	  hookEventName: "UserPromptSubmit",
	  additionalContext: (
	    (if ($credential_warning | length) > 0 then $credential_warning + "\n\n" else "" end) + "IMPORTANT: This prompt references prior work, but hook keyword retrieval returned no candidate memories. Keyword retrieval is incomplete — stored decisions may still exist.\n\nMANDATORY FIRST ACTION: you MUST call memory_search before answering. Do not answer from assumptions about prior work alone. Do not use memory_get as a substitute for memory_search. Use exact project-scoped source prefixes" +
	    (if ($prefixes | length) > 0 then " (" + $prefixes + ")" else "" end) +
	    " before broad family prefixes or unscoped search." +
	    (if ($response_hint | length) > 0 then "\n\n" + $response_hint else "" end)
	  )
	}
}'
fi
