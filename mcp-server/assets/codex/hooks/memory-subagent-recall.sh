#!/usr/bin/env bash
# memory-subagent-recall.sh — SubagentStart hook (Codex)
# Inject project-scoped memories into a newly spawned subagent.

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
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _exit_if_disabled() { return 0; }
  _memories_resolve_project() { basename "${1:-unknown}"; }
  _default_source_prefixes() { echo 'codex/{project},claude-code/{project},learning/{project},wip/{project}'; }
  _search_memories_multi() { echo '{"results":[],"count":0}'; }
  _hook_deadline_init() { :; }
  _hook_deadline_exhausted() { printf 'false'; }
fi

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // empty')
_exit_if_disabled "$CWD" 2>/dev/null || true

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
MEMORIES_SOURCE_PREFIXES="${MEMORIES_SOURCE_PREFIXES:-$(_default_source_prefixes)}"
MEMORIES_SUBAGENT_THRESHOLD="${MEMORIES_SUBAGENT_THRESHOLD:-0.35}"
MEMORIES_SUBAGENT_RECALL_LIMIT="${MEMORIES_SUBAGENT_RECALL_LIMIT:-6}"
MEMORIES_USAGE_CLIENT=$(_memory_client_prefix 2>/dev/null || echo "codex")
MEMORIES_USAGE_SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty')
MEMORIES_USAGE_INVOCATION="$MEMORIES_HOOK_NAME"
MEMORIES_USAGE_SOURCE="hook:$MEMORIES_USAGE_CLIENT:$MEMORIES_HOOK_NAME"
_hook_deadline_init

AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty')
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
PROJECT=$(_memories_resolve_project "${CWD:-unknown}" 2>/dev/null || basename "${CWD:-unknown}")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi

case "$AGENT_TYPE" in
  Plan|plan) QUERY="project $PROJECT architecture decisions design constraints deferred work" ;;
  Explore|explore) QUERY="project $PROJECT structure conventions patterns file organization" ;;
  *) QUERY="project $PROJECT architecture decisions conventions patterns" ;;
esac

RAW_RESPONSES=""
IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
for raw_prefix in "${prefix_templates[@]}"; do
  raw_prefix=$(printf '%s' "$raw_prefix" | xargs)
  [ -n "$raw_prefix" ] || continue
  [ "$(_hook_deadline_exhausted)" = "true" ] && break
  PREFIX=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
  RESPONSE=$(_search_memories_multi "$QUERY" "$PREFIX" 3 "$MEMORIES_SUBAGENT_THRESHOLD") || true
  [ -n "$RESPONSE" ] && RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$RESPONSE")
done

RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr --argjson limit "$MEMORIES_SUBAGENT_RECALL_LIMIT" '
  map(select(type == "object") | (.results // []))
  | add
  | unique_by(.id // .memory_id // .text)
  | sort_by(-(.similarity // .rrf_score // 0))
  | .[0:$limit]
' 2>/dev/null) || RESULTS_JSON="[]"
RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then empty else map("- [\(.source // "unknown")] candidate memory id=\(.id // .memory_id // "unknown"): \(.text // "")") | join("\n") end
' 2>/dev/null) || RESULTS=""
[ -n "$RESULTS" ] || exit 0

_log_info "Injected $(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0) Codex memories into $AGENT_TYPE subagent ($AGENT_ID)"
jq -n --arg memories "$RESULTS" --arg agent_type "$AGENT_TYPE" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: ("## Project Memories (" + $agent_type + " context)\n\n" + $memories)
  }
}'
