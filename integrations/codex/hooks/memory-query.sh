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
  _default_source_prefixes() { echo 'codex/{project},learning/{project},wip/{project}'; }
fi

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
  # Codex JSONL (.message.role + .content), and other layouts.
  tail -200 "$transcript_path" 2>/dev/null | jq -sr '
    [
      .[]
      | select(
          ((.type // .message.role // "") | tostring) as $r |
          ($r == "user" or $r == "assistant")
        )
      | {
          role: ((.type // .message.role // "") | tostring),
          text: (
            if (.message.content // null) != null then
              if (.message.content | type) == "string" then .message.content
              elif (.message.content | type) == "array" then [.message.content[] | select(.type == "text") | .text] | join(" ")
              else ""
              end
            elif (.content // null) != null then
              if (.content | type) == "string" then .content
              elif (.content | type) == "array" then [.content[] | select(.type == "text") | .text] | join(" ")
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

# Enrich the search query with extracted context
ENRICHED=""
[ -n "$FILE_CONTEXT" ] && ENRICHED="$FILE_CONTEXT\n"
[ -n "$KEY_TERMS" ] && ENRICHED="${ENRICHED}$KEY_TERMS\n"

QUERY_TEXT="$PROMPT"
if [ -n "$CONTEXT" ]; then
  if [ -n "$PROMPT" ]; then
    QUERY_TEXT=$(printf '%s%s\nProject: %s\nRecent conversation:\n%s\nCurrent prompt: %s' "$ENRICHED" "" "${PROJECT:-unknown}" "$CONTEXT" "$PROMPT")
  else
    QUERY_TEXT=$(printf '%s%s\nProject: %s\nRecent conversation:\n%s' "$ENRICHED" "" "${PROJECT:-unknown}" "$CONTEXT")
  fi
elif [ -n "$ENRICHED" ]; then
  QUERY_TEXT=$(printf '%s\n%s' "$ENRICHED" "$PROMPT")
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

  # Search intent-based prefixes if present (in addition to standard prefixes)
  if [ -n "$INTENT_PREFIXES" ]; then
    for intent_prefix in $INTENT_PREFIXES; do
      response=$(search_memories "$QUERY_TEXT" "$intent_prefix" "$MEMORIES_QUERY_SCOPED_K" "$MEMORIES_QUERY_SCOPED_THRESHOLD")
      if [ -n "$response" ]; then
        RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
      fi
    done
  fi
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
