#!/bin/bash
# memory-query.sh — UserPromptSubmit hook
# Searches Memories for memories relevant to the current prompt.
# Prefers project-scoped sources first and uses recent transcript context
# so short follow-up prompts still retrieve the right memories.
# Sync hook: blocks until done, injects additionalContext.

MEMORIES_HOOK_NAME="memory-query"

set -euo pipefail

# Load from dedicated env file — avoids requiring shell profile changes
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
MEMORIES_SOURCE_PREFIXES="${MEMORIES_SOURCE_PREFIXES:-}"
if [ -z "$MEMORIES_SOURCE_PREFIXES" ]; then
  MEMORIES_SOURCE_PREFIXES='claude-code/{project},learning/{project},wip/{project}'
fi
MEMORIES_QUERY_SCOPED_K="${MEMORIES_QUERY_SCOPED_K:-3}"
MEMORIES_QUERY_FALLBACK_K="${MEMORIES_QUERY_FALLBACK_K:-5}"
MEMORIES_QUERY_SCOPED_THRESHOLD="${MEMORIES_QUERY_SCOPED_THRESHOLD:-0.35}"
MEMORIES_QUERY_FALLBACK_THRESHOLD="${MEMORIES_QUERY_FALLBACK_THRESHOLD:-0.55}"

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // empty')
PROJECT=$(basename "${CWD:-}")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  PROJECT=""
fi
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // .transcriptPath // empty')
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

build_response_hint() {
  local prompt_lower="$1"
  local hints_file="$(dirname "$0")/response-hints.json"

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

extract_recent_context() {
  local transcript_path="$1"
  if [ -z "$transcript_path" ] || [ ! -f "$transcript_path" ]; then
    return 0
  fi

  tail -200 "$transcript_path" 2>/dev/null | jq -sr '
    [
      .[]
      | select(.type == "user" or .type == "assistant")
      | {
          role: .type,
          text: (
            if .message.content | type == "string" then
              .message.content
            elif .message.content | type == "array" then
              [.message.content[] | select(.type == "text") | .text] | join(" ")
            else
              ""
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
  local query="$1"
  local prefix="${2:-}"
  local limit="${3:-5}"
  local threshold="${4:-0.4}"

  local body
  if [ -n "$prefix" ]; then
    body=$(jq -nc \
      --arg query "$query" \
      --arg prefix "$prefix" \
      --argjson k "$limit" \
      --argjson threshold "$threshold" \
      '{
        query: $query,
        source_prefix: $prefix,
        k: $k,
        hybrid: true,
        threshold: $threshold
      }')
  else
    body=$(jq -nc \
      --arg query "$query" \
      --argjson k "$limit" \
      --argjson threshold "$threshold" \
      '{
        query: $query,
        k: $k,
        hybrid: true,
        threshold: $threshold
      }')
  fi

  curl -sf -X POST "$MEMORIES_URL/search" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $MEMORIES_API_KEY" \
    -d "$body" \
    2>/dev/null || { _log_error "Query search failed for prefix=${prefix:-<none>}"; true; }
}

CONTEXT=$(extract_recent_context "$TRANSCRIPT_PATH")
QUERY_TEXT="$PROMPT"
PROMPT_LOWER=$(printf '%s' "$PROMPT" | tr '[:upper:]' '[:lower:]')
if [ -n "$CONTEXT" ]; then
  if [ -n "$PROMPT" ]; then
    QUERY_TEXT=$(printf 'Project: %s\nRecent conversation:\n%s\nCurrent prompt: %s' "${PROJECT:-unknown}" "$CONTEXT" "$PROMPT")
  else
    QUERY_TEXT=$(printf 'Project: %s\nRecent conversation:\n%s' "${PROJECT:-unknown}" "$CONTEXT")
  fi
fi

# Skip only if we have neither a meaningful prompt nor recent conversation.
if [ -z "$QUERY_TEXT" ] || { [ ${#PROMPT} -lt 20 ] && [ -z "$CONTEXT" ]; }; then
  exit 0
fi

RAW_RESPONSES=""
if [ -n "$PROJECT" ]; then
  IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
  for raw_prefix in "${prefix_templates[@]}"; do
    raw_prefix=$(echo "$raw_prefix" | xargs)
    [ -z "$raw_prefix" ] && continue

    prefix=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
    response=$(search_memories "$QUERY_TEXT" "$prefix" "$MEMORIES_QUERY_SCOPED_K" "$MEMORIES_QUERY_SCOPED_THRESHOLD")
    if [ -n "$response" ]; then
      RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
    fi
  done
fi

RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr '
  map(select(type == "object") | (.results // []))
  | add
  | unique_by(.id)
  | sort_by(-(.similarity // .rrf_score // 0))
  | .[0:6]
' 2>/dev/null) || RESULTS_JSON="[]"

if [ "$RESULTS_JSON" = "[]" ]; then
  FALLBACK_RESPONSE=$(search_memories "$QUERY_TEXT" "" "$MEMORIES_QUERY_FALLBACK_K" "$MEMORIES_QUERY_FALLBACK_THRESHOLD")
  RESULTS_JSON=$(printf '%s' "$FALLBACK_RESPONSE" | jq -c '.results // []' 2>/dev/null) || RESULTS_JSON="[]"
fi

RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map("- [\(.source)] \(.text)") | join("\n")
  end
' 2>/dev/null) || true

if [ -z "$RESULTS" ] || [ "$RESULTS" = "null" ]; then
  exit 0
fi

_log_info "Query returned $(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0) results for prompt (${#PROMPT} chars)"

RESPONSE_HINT=$(build_response_hint "$PROMPT_LOWER")

jq -n --arg memories "$RESULTS" --arg response_hint "$RESPONSE_HINT" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: (
      "## Retrieved Memories\n" + $memories +
      (if ($response_hint | length) > 0 then "\n\n" + $response_hint else "" end)
    )
  }
}'
