#!/bin/bash
# memory-recall.sh — SessionStart hook (Codex)
# Loads project-relevant memory pointers into Codex context.
# Sync hook: blocks until done, injects additionalContext.

MEMORIES_HOOK_NAME="memory-recall"

set -euo pipefail

# Load from dedicated env file — avoids requiring shell profile changes
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_source_prefixes() { echo 'codex/{project},claude-code/{project},learning/{project},wip/{project}'; }
fi

_exit_if_disabled 2>/dev/null || true

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

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // .workspace_roots[0] // empty')
if [ -z "$CWD" ]; then
  exit 0
fi

PROJECT=$(basename "$CWD")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi

# Parse Codex session source (startup, resume, clear)
SESSION_SOURCE=$(echo "$INPUT" | jq -r '.source // "unknown"')

_log_info "Session start for project=$PROJECT cwd=$CWD source=$SESSION_SOURCE"

# Health check — warn if service unreachable
HEALTH_WARNING=""
if ! _health_check; then
  _log_warn "Service unreachable at $MEMORIES_URL"
  HEALTH_WARNING=$(cat <<HWEOF
## Memories Service Warning

Memories service is not reachable at $MEMORIES_URL. Memory recall and extraction are unavailable this session. Check that the service is running.
HWEOF
)
fi

search_memories() {
  _search_memories_multi "$@"
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
IFS=',' read -r -a prefix_templates <<< "$MEMORIES_SOURCE_PREFIXES"
for raw_prefix in "${prefix_templates[@]}"; do
  raw_prefix=$(echo "$raw_prefix" | xargs)
  [ -z "$raw_prefix" ] && continue

  prefix=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
  query=$(query_for_prefix "$prefix")
  limit=3
  case "$prefix" in
    claude-code/*|codex/*) limit=4 ;;
    learning/*|wip/*) limit=2 ;;
  esac

  response=$(search_memories "$query" "$prefix" "$limit" "$MEMORIES_RECALL_SCOPED_THRESHOLD")
  if [ -n "$response" ]; then
    RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
  fi

  if [ -n "$SCOPED_PREFIX_LIST" ]; then
    SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST, "
  fi
  SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST$prefix"
done

RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr --argjson limit "$RECALL_LIMIT" '
  map(select(type == "object") | (.results // []))
  | add
  | unique_by(.id)
  | sort_by(-(.similarity // .rrf_score // 0))
  | .[0:$limit]
' 2>/dev/null) || RESULTS_JSON="[]"

if [ "$RESULTS_JSON" = "[]" ]; then
  FALLBACK_RESPONSE=$(search_memories "project $PROJECT conventions decisions patterns" "" 6 "$MEMORIES_RECALL_FALLBACK_THRESHOLD")
  RESULTS_JSON=$(printf '%s' "$FALLBACK_RESPONSE" | jq -c '.results // []' 2>/dev/null) || RESULTS_JSON="[]"
fi

CONTEXT_RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map("- [\(.source)] candidate memory id=\(.id // .memory_id // "unknown") found at session start; call memory_search with this source prefix before using it.") | join("\n")
  end
' 2>/dev/null) || true

_log_info "Recalled $(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0) memories for $PROJECT"

# Dedicated deferred-work surfacing
WIP_QUERY="deferred incomplete blocked todo revisit wip"
WIP_RESULTS=$(search_memories "$WIP_QUERY" "wip/$PROJECT" 5 0.3)
WIP_COUNT=$(echo "$WIP_RESULTS" | jq -r '.count // 0')
DEFERRED_SECTION=""
if [ "$WIP_COUNT" -gt 0 ] && [ "$WIP_COUNT" != "null" ]; then
  DEFERRED_ITEMS=$(echo "$WIP_RESULTS" | jq -r '.results[:5][] | "- [\(.source)] deferred candidate memory id=\(.id // .memory_id // "unknown"); call memory_search with this source prefix before answering deferred-work questions."')
  DEFERRED_SECTION="\n## Deferred Work\n$DEFERRED_ITEMS\n"
fi

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

# --- Output context for Codex ---
jq -n --arg memories "$CONTEXT_RESULTS" --arg playbook "$PLAYBOOK" --arg health_warning "$HEALTH_WARNING" --arg deferred "$DEFERRED_SECTION" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: (
      (if ($health_warning | length) > 0 then $health_warning + "\n\n" else "" end) +
      (if ($memories | length) > 0 then "## Relevant Memories\n\n" + $memories + "\n\n" else "" end) +
      (if ($deferred | length) > 0 then $deferred + "\n" else "" end) +
      $playbook
    )
  }
}'
