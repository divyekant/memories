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
_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_source_prefixes() { echo 'claude-code/{project},learning/{project},wip/{project}'; }
fi

_exit_if_disabled 2>/dev/null || true

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
  _search_memories_multi "$@"
}

CONTEXT=$(extract_recent_context "$TRANSCRIPT_PATH")
PROMPT_LOWER=$(printf '%s' "$PROMPT" | tr '[:upper:]' '[:lower:]')

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
if [ ${#PROMPT} -lt 20 ] && [ -z "$CONTEXT" ]; then
  exit 0
fi

# --- Dual search strategy ---
RAW_RESPONSES=""

# Strategy A: enriched unscoped (cross-project, semantic)
UNSCOPED_RESPONSE=$(search_memories "$ENRICHED_QUERY" "" 6 0.30)
if [ -n "$UNSCOPED_RESPONSE" ]; then
  RAW_RESPONSES="$UNSCOPED_RESPONSE"
fi

# Strategy B: enriched prefix-scoped (project-specific precision)
if [ -n "$PROJECT" ]; then
  CLIENT_PREFIX=$(_memory_client_prefix)
  SCOPED_RESPONSE=$(search_memories "$ENRICHED_QUERY" "${CLIENT_PREFIX}/${PROJECT}" "$MEMORIES_QUERY_SCOPED_K" "$MEMORIES_QUERY_SCOPED_THRESHOLD")
  if [ -n "$SCOPED_RESPONSE" ]; then
    RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$SCOPED_RESPONSE")
  fi
fi

# Intent-based prefix biasing (additional search for fix/debug/setup prompts)
if [ -n "$INTENT_PREFIXES" ] && [ -n "$PROJECT" ]; then
  for intent_prefix in $INTENT_PREFIXES; do
    response=$(search_memories "$ENRICHED_QUERY" "$intent_prefix" "$MEMORIES_QUERY_SCOPED_K" "$MEMORIES_QUERY_SCOPED_THRESHOLD")
    if [ -n "$response" ]; then
      RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
    fi
  done
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
      "IMPORTANT: The following memories from prior sessions are relevant to this prompt. These represent prior decisions and context that MUST be considered before responding. Do not contradict stored decisions without explicitly acknowledging the change.\n\n## Retrieved Memories\n" + $memories +
      (if ($response_hint | length) > 0 then "\n\n" + $response_hint else "" end)
    )
  }
}'
