#!/bin/bash
# memory-subagent-recall.sh — SubagentStart hook
# Injects project-scoped memories into subagents at spawn time.
# Gives Plan, Explore, code-reviewer, and general-purpose agents
# the same memory context that the main agent gets via SessionStart.
# Sync hook: blocks until done, injects additionalContext.

MEMORIES_HOOK_NAME="memory-subagent-recall"

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _health_check() { return 0; }
  _default_source_prefixes() { echo 'claude-code/{project},learning/{project},wip/{project}'; }
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
MEMORIES_SOURCE_PREFIXES="${MEMORIES_SOURCE_PREFIXES:-}"
if [ -z "$MEMORIES_SOURCE_PREFIXES" ]; then
  MEMORIES_SOURCE_PREFIXES="$(_default_source_prefixes)"
fi
MEMORIES_SUBAGENT_RECALL_LIMIT="${MEMORIES_SUBAGENT_RECALL_LIMIT:-6}"
MEMORIES_SUBAGENT_THRESHOLD="${MEMORIES_SUBAGENT_THRESHOLD:-0.35}"

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
AGENT_TYPE=$(echo "$INPUT" | jq -r '.agent_type // empty')

if [ -z "$CWD" ]; then
  exit 0
fi

PROJECT=$(basename "$CWD")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi

# Quick health check — don't block subagent spawn if service is down
if ! _health_check; then
  _log_warn "Service unreachable, skipping subagent recall"
  exit 0
fi

_log_info "Subagent recall for project=$PROJECT agent_type=$AGENT_TYPE"

search_memories() {
  _search_memories_multi "$@"
}

# Tailor queries by agent type for better relevance
query_for_agent_type() {
  local agent_type="$1"
  case "$agent_type" in
    Plan)
      printf 'project %s architecture decisions design constraints deferred work' "$PROJECT"
      ;;
    Explore)
      printf 'project %s structure conventions patterns file organization' "$PROJECT"
      ;;
    *code-reviewer*|*review*)
      printf 'project %s conventions code style patterns known issues' "$PROJECT"
      ;;
    *)
      printf 'project %s architecture decisions conventions patterns' "$PROJECT"
      ;;
  esac
}

# Search across scoped prefixes (same as SessionStart recall)
RAW_RESPONSES=""
IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
for raw_prefix in "${prefix_templates[@]}"; do
  raw_prefix=$(echo "$raw_prefix" | xargs)
  [ -z "$raw_prefix" ] && continue

  prefix=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
  query=$(query_for_agent_type "$AGENT_TYPE")
  limit=3
  case "$prefix" in
    claude-code/*|codex/*) limit=3 ;;
    learning/*|wip/*) limit=2 ;;
  esac

  response=$(search_memories "$query" "$prefix" "$limit" "$MEMORIES_SUBAGENT_THRESHOLD")
  if [ -n "$response" ]; then
    RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
  fi
done

RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr --argjson limit "$MEMORIES_SUBAGENT_RECALL_LIMIT" '
  map(select(type == "object") | (.results // []))
  | add
  | unique_by(.id)
  | sort_by(-(.similarity // .rrf_score // 0))
  | .[0:$limit]
' 2>/dev/null) || RESULTS_JSON="[]"

# Fallback to unscoped search if nothing found
if [ "$RESULTS_JSON" = "[]" ]; then
  FALLBACK_QUERY=$(query_for_agent_type "$AGENT_TYPE")
  FALLBACK_RESPONSE=$(search_memories "$FALLBACK_QUERY" "" 5 0.55)
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

_log_info "Injected $(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0) memories into $AGENT_TYPE subagent for $PROJECT"

jq -n --arg memories "$RESULTS" --arg agent_type "$AGENT_TYPE" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: (
      "IMPORTANT: The following memories from prior sessions are relevant to this task. These represent prior decisions and context that MUST be considered. Do not contradict stored decisions without explicitly acknowledging the change.\n\n## Project Memories (" + $agent_type + " context)\n" + $memories
    )
  }
}'
