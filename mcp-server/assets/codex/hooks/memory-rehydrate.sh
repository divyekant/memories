#!/usr/bin/env bash
# memory-rehydrate.sh — PostCompact hook (Codex)
# Re-query project-scoped memories and inject pointers into the compacted turn.

MEMORIES_HOOK_NAME="memory-rehydrate"

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
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
MEMORIES_USAGE_CLIENT=$(_memory_client_prefix 2>/dev/null || echo "codex")
MEMORIES_USAGE_SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty')
MEMORIES_USAGE_INVOCATION="$MEMORIES_HOOK_NAME"
MEMORIES_USAGE_SOURCE="hook:$MEMORIES_USAGE_CLIENT:$MEMORIES_HOOK_NAME"
_hook_deadline_init

PROJECT=$(_memories_resolve_project "${CWD:-unknown}" 2>/dev/null || basename "${CWD:-unknown}")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi

TRIGGER=$(printf '%s' "$INPUT" | jq -r '.trigger // empty')
LAST_ASSISTANT_MESSAGE=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty')
QUERY=$(printf '%s %s' "$TRIGGER" "$LAST_ASSISTANT_MESSAGE" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | sed 's/^ //;s/ $//' | head -c 500)
[ -n "$QUERY" ] || QUERY="project $PROJECT compaction context decisions conventions"

RAW_RESPONSES=""
IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
for raw_prefix in "${prefix_templates[@]}"; do
  raw_prefix=$(printf '%s' "$raw_prefix" | xargs)
  [ -n "$raw_prefix" ] || continue
  [ "$(_hook_deadline_exhausted)" = "true" ] && break
  PREFIX=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
  RESPONSE=$(_search_memories_multi "$QUERY" "$PREFIX" 3 0.35) || true
  [ -n "$RESPONSE" ] && RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$RESPONSE")
done

RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr '
  map(select(type == "object") | (.results // []))
  | add
  | unique_by(.id // .memory_id // .text)
  | sort_by(-(.similarity // .rrf_score // 0))
  | .[0:6]
' 2>/dev/null) || RESULTS_JSON="[]"
RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then empty else map("- [\(.source // "unknown")] candidate memory id=\(.id // .memory_id // "unknown"): \(.text // "")") | join("\n") end
' 2>/dev/null) || RESULTS=""
[ -n "$RESULTS" ] || exit 0

_log_info "Rehydrated $(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0) Codex memories for $PROJECT"
jq -n --arg memories "$RESULTS" '{
  hookSpecificOutput: {
    hookEventName: "PostCompact",
    additionalContext: ("## Memories after compaction\n\n" + $memories)
  }
}'
