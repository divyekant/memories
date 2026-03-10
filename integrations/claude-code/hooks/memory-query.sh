#!/bin/bash
# memory-query.sh — UserPromptSubmit hook
# Searches Memories for memories relevant to the current prompt.
# Prefers project-scoped sources first and uses recent transcript context
# so short follow-up prompts still retrieve the right memories.
# Sync hook: blocks until done, injects additionalContext.

set -euo pipefail

# Load from dedicated env file — avoids requiring shell profile changes
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

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
  local primary_memory="$2"
  local primary_example="$3"

  case "$prompt_lower" in
    *"we're still on "*|*"we are still on "*|*"i don't want "*|*"i do not want "*)
      cat <<EOF
## Context Continuation Hint

- The user is resuming an existing decision or constraint.
- Start by restating the remembered choice or boundary in concrete terms before asking what to do next.
- Keep the concrete implementation choice and the boundary condition together in the same sentence.
$(if [ -n "$primary_example" ]; then printf -- "- Suggested first sentence: %s\n" "$primary_example"; elif [ -n "$primary_memory" ]; then printf -- "- Most relevant context to restate: %s\n" "$primary_memory"; fi)
EOF
      ;;
    *"does that still apply"*|*"should we keep it"*|*"can we keep it simple"*)
      cat <<EOF
## Follow-up Response Hint

- This is a short confirmation follow-up.
- Answer directly in the first sentence instead of asking the user to reconfirm the decision.
- Reuse the remembered decision and its boundary condition in the reply.
- Keep qualifiers like `until`, `unless`, `because`, or `blocked on` if they appear in the retrieved memory.
- Restate the concrete choice and the exact condition together, not separately.
$(if [ -n "$primary_example" ]; then printf -- "- If the memory already answers the question, use this answer shape: Yes — %s\n" "$primary_example"; elif [ -n "$primary_memory" ]; then printf -- "- Most relevant decision to preserve: %s\n" "$primary_memory"; fi)
EOF
      ;;
    *"what about"*|*"what about retries"*|*"and retries"*|*"retry"*|*"retries"*)
      cat <<EOF
## Follow-up Response Hint

- This follow-up is asking for the missing piece of the earlier topic.
- If the retrieved memory says the work is deferred or blocked, say that explicitly in sentence one.
- Keep the blocker or dependency in the answer instead of widening into unrelated design details.
$(if [ -n "$primary_example" ]; then printf -- "- If the memory already answers the question, use this answer shape: Not yet — %s\n" "$primary_example"; elif [ -n "$primary_memory" ]; then printf -- "- Most relevant blocker to preserve: %s\n" "$primary_memory"; fi)
EOF
      ;;
    *)
      ;;
  esac
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
  ' 2>/dev/null || true
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
    2>/dev/null || true
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

PRIMARY_MEMORY=$(printf '%s' "$RESULTS_JSON" | jq -r '.[0].text // empty' 2>/dev/null) || PRIMARY_MEMORY=""
PRIMARY_MEMORY_EXAMPLE=$(printf '%s' "$PRIMARY_MEMORY" | sed -E 's/^[^:]+ decision:[[:space:]]*//; s/^[^:]+:[[:space:]]*//')
RESPONSE_HINT=$(build_response_hint "$PROMPT_LOWER" "$PRIMARY_MEMORY" "$PRIMARY_MEMORY_EXAMPLE")

jq -n --arg memories "$RESULTS" --arg response_hint "$RESPONSE_HINT" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: (
      "## Retrieved Memories\n" + $memories +
      (if ($response_hint | length) > 0 then "\n\n" + $response_hint else "" end)
    )
  }
}'
