#!/bin/bash
# memory-subagent-recall.sh — SubagentStart hook
# Injects project-scoped memories into subagents at spawn time.
# Gives Plan, Explore, code-reviewer, and general-purpose agents
# the same memory context that the main agent gets via SessionStart.
# Sync hook: blocks until done, injects additionalContext.

MEMORIES_HOOK_NAME="memory-subagent-recall"

set -euo pipefail

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
  _health_check() { return 0; }
  _default_source_prefixes() { echo 'claude-code/{project},codex/{project},learning/{project},wip/{project}'; }
  _hook_deadline_init() { :; }
  _hook_deadline_exhausted() { printf 'false'; }
fi

_exit_if_disabled 2>/dev/null || true

# End-to-end deadline for every backend call this hook makes — see
# _hook_deadline_init in _lib.sh (SubagentStart shares SessionStart's 5s
# hooks.json budget and the same "sequential searches can sum past it"
# problem, PR #85 review round 7). Init before any backend call.
_hook_deadline_init

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

PROJECT=$(_memories_resolve_project "$CWD" 2>/dev/null || basename "$CWD")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi
PROJECT_CONTEXT_JSON=$(_memories_project_context "$CWD" 2>/dev/null || printf '{"active":false}')
PROJECT_CONTEXT_ACTIVE=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.active // false' 2>/dev/null || printf 'false')
PROJECT_CONTEXT_ID=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.project_id // empty' 2>/dev/null || true)
PROJECT_CONTEXT_PRINCIPAL=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.principal_id // empty' 2>/dev/null || true)
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  PROJECT="$PROJECT_CONTEXT_ID"
  MEMORIES_SOURCE_PREFIXES=$(_memories_project_recall_prefixes "$PROJECT_CONTEXT_ID" "$PROJECT_CONTEXT_PRINCIPAL" "$MEMORIES_SOURCE_PREFIXES" | tr '\n' ',' | sed 's/,$//')
fi

# Quick health check — don't block subagent spawn if service is down.
# Probes the ROUTED search backend set, not backend #1 in raw declaration
# order, and only skips when ALL of them are unreachable (PR #85 review,
# third pass — see _health_check in _lib.sh).
if ! _health_check; then
  _log_warn "Service unreachable: $MEMORIES_HEALTH_DOWN_NAMES, skipping subagent recall"
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

  # End-to-end deadline (see memory-recall.sh / _hook_deadline_init in
  # _lib.sh): stop issuing further searches once the budget's gone rather
  # than risk hooks.json killing the whole process before it can respond.
  if [ "$(_hook_deadline_exhausted)" = "true" ]; then
    _log_warn "Hook budget exhausted — skipping remaining subagent-recall prefix searches"
    break
  fi

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

if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | _memories_merge_search_results true "$MEMORIES_SUBAGENT_RECALL_LIMIT" 2>/dev/null) || RESULTS_JSON="[]"
else
  RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr --argjson limit "$MEMORIES_SUBAGENT_RECALL_LIMIT" '
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

# Fallback to unscoped search if nothing found
if [ "$PROJECT_CONTEXT_ACTIVE" != "true" ] && [ "$RESULTS_JSON" = "[]" ]; then
  if [ "$(_hook_deadline_exhausted)" = "true" ]; then
    _log_warn "Hook budget exhausted — skipping the unscoped fallback search"
  else
    FALLBACK_QUERY=$(query_for_agent_type "$AGENT_TYPE")
    FALLBACK_RESPONSE=$(search_memories "$FALLBACK_QUERY" "" 5 0.55)
    RESULTS_JSON=$(printf '%s' "$FALLBACK_RESPONSE" | jq -c '.results // []' 2>/dev/null) || RESULTS_JSON="[]"
  fi
fi

RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map(("- [\(.source)]" + (if (.provenance_label // "") != "" then " " + .provenance_label else "" end) + " \(.text)")) | join("\n")
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
