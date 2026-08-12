#!/bin/bash
# memory-recall.sh — SessionStart hook
# Loads project-relevant memory pointers into Claude Code context.
# Also syncs MEMORY.md with pointers from MCP rather than full memory text,
# so auto-memory cannot become a passive answer source.
# Sync hook: blocks until done, injects additionalContext.

MEMORIES_HOOK_NAME="memory-recall"

set -euo pipefail

# Load from dedicated env file — avoids requiring shell profile changes
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _resolve_primary_backend_url() { printf '%s' "${MEMORIES_URL:-http://localhost:8900}"; }
  _default_source_prefixes() { echo 'claude-code/{project},codex/{project},learning/{project},wip/{project}'; }
  _hook_deadline_init() { :; }
  _hook_deadline_exhausted() { printf 'false'; }
  _hook_call_budget() { printf '%s' "$1"; }
fi

_exit_if_disabled 2>/dev/null || true

# End-to-end deadline for every backend call this hook makes (see
# _hook_deadline_init in _lib.sh): individually-capped per-call timeouts
# cannot bound the WHOLE hook, since several sequential searches can sum
# past hooks.json's own 5s SessionStart budget even with nothing failing
# (PR #85 review, round 7). Init as early as possible, before any call.
_hook_deadline_init

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

PROJECT=$(_memories_resolve_project "$CWD" 2>/dev/null || basename "$CWD")
PROJECT_CONTEXT_JSON=$(_memories_project_context "$CWD" 2>/dev/null || printf '{"active":false}')
PROJECT_CONTEXT_ACTIVE=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.active // false' 2>/dev/null || printf 'false')
PROJECT_CONTEXT_ID=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.project_id // empty' 2>/dev/null || true)
PROJECT_CONTEXT_PRINCIPAL=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.principal_id // empty' 2>/dev/null || true)
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  PROJECT="$PROJECT_CONTEXT_ID"
  MEMORIES_SOURCE_PREFIXES=$(_memories_project_recall_prefixes "$PROJECT_CONTEXT_ID" "$PROJECT_CONTEXT_PRINCIPAL" "$MEMORIES_SOURCE_PREFIXES" | tr '\n' ',' | sed 's/,$//')
fi
PROJECT_SHARING_GUIDANCE=""
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  PROJECT_SHARING_GUIDANCE="Collaborative project memory: apply the durable-sharing test (another contributor will need this fact without the current session). Use memory_add exactly once with project/$PROJECT_CONTEXT_ID/<decisions|knowledge|state|operations> for deliberate shared facts. Automatic extraction remains private in person/$PROJECT_CONTEXT_PRINCIPAL/$PROJECT_CONTEXT_ID/knowledge; never infer project/... ."
fi
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  exit 0
fi

_log_info "Session start for project=$PROJECT cwd=$CWD"

# Health check — probes the ROUTED search backend set (routing.search, or
# every backend when there's no explicit routing — see _get_backends_for_op),
# not just backend #1 in raw declaration order, and warns only when ALL of
# them are unreachable. A single dead backend among several routed ones is
# not the whole service being down (PR #85 review, third pass).
HEALTH_WARNING=""
if ! _health_check; then
  _log_warn "Service unreachable: $MEMORIES_HEALTH_DOWN_NAMES"
  HEALTH_WARNING=$(cat <<HWEOF
## Memories Service Warning

Memories service is not reachable ($MEMORIES_HEALTH_DOWN_NAMES). Memory recall and extraction are unavailable this session. If this is a cloud session, add its host to the allowed domains for this environment; otherwise check that the service is running.
HWEOF
)
fi

# Backend version check — skip if service already unreachable, or if the
# deadline doesn't have room for it (this check is purely informational, so
# it never competes with search budget). Uses the routed primary backend
# (_resolve_primary_backend_url), not the bare MEMORIES_URL default.
TARGET_URL=$(_resolve_primary_backend_url)
EXPECTED_VERSION_FILE="$(dirname "${BASH_SOURCE[0]}")/../assets/BACKEND_VERSION"
VERSION_CHECK_BUDGET=""
if [ -z "$HEALTH_WARNING" ] && [ -f "$EXPECTED_VERSION_FILE" ]; then
  VERSION_CHECK_BUDGET=$(_hook_call_budget 2) || VERSION_CHECK_BUDGET=""
fi
if [ -n "$VERSION_CHECK_BUDGET" ]; then
  EXPECTED_VERSION=$(cat "$EXPECTED_VERSION_FILE" | tr -d '[:space:]')
  RUNNING_VERSION=$(curl -sf --max-time "$VERSION_CHECK_BUDGET" "$TARGET_URL/health" 2>/dev/null | jq -r '.version // empty') || RUNNING_VERSION=""
  if [ -n "$RUNNING_VERSION" ] && [ -n "$EXPECTED_VERSION" ] && [ "$RUNNING_VERSION" != "$EXPECTED_VERSION" ]; then
    _log_warn "Backend version mismatch: running=$RUNNING_VERSION expected=$EXPECTED_VERSION"
    VERSION_WARNING=$(printf '## Memories Backend Update Available\n\nRunning v%s, latest is v%s. Run `/memories:setup` to update, or: `cd ~/.config/memories && docker compose pull && docker compose up -d`' "$RUNNING_VERSION" "$EXPECTED_VERSION")
    if [ -n "$HEALTH_WARNING" ]; then
      HEALTH_WARNING=$(printf '%s\n\n%s' "$HEALTH_WARNING" "$VERSION_WARNING")
    else
      HEALTH_WARNING="$VERSION_WARNING"
    fi
  fi
fi

search_memories() {
  _search_memories_multi "$@"
}

# /health is unauthenticated and can't see a bad API key, so credential
# failures are detected from the /search calls this hook already makes
# (_search_memories_multi tags a 401 response with auth_failed:true). Search
# responses are consumed via command substitution (a subshell), so a plain
# variable set inside search_memories_multi can't propagate back here — check
# the JSON itself instead.
AUTH_FAILED="false"
_note_auth_status() {
  local resp="$1"
  local flag
  flag=$(printf '%s' "$resp" | jq -r '.auth_failed // false' 2>/dev/null) || flag="false"
  [ "$flag" = "true" ] && AUTH_FAILED="true"
  return 0
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
prefix_idx=0
for raw_prefix in "${prefix_templates[@]}"; do
  prefix_idx=$((prefix_idx + 1))
  raw_prefix=$(echo "$raw_prefix" | xargs)
  [ -z "$raw_prefix" ] && continue

  # End-to-end deadline: several sequential searches can sum past the
  # hook's own budget even with nothing failing. Once there isn't enough
  # left to justify another call, stop issuing them — partial context
  # delivered on time beats complete context discarded when hooks.json
  # kills the whole process at its own timeout.
  if [ "$(_hook_deadline_exhausted)" = "true" ]; then
    SKIPPED_PREFIXES=$(IFS=,; echo "${prefix_templates[*]:$((prefix_idx - 1))}")
    _log_warn "Hook budget exhausted — skipping remaining prefix searches: $SKIPPED_PREFIXES"
    break
  fi

  prefix=$(printf '%s' "$raw_prefix" | sed "s/{project}/$PROJECT/g")
  query=$(query_for_prefix "$prefix")
  limit=3
  case "$prefix" in
    claude-code/*|codex/*) limit=4 ;;
    learning/*|wip/*) limit=2 ;;
  esac

  response=$(search_memories "$query" "$prefix" "$limit" "$MEMORIES_RECALL_SCOPED_THRESHOLD")
  _note_auth_status "$response"
  if [ -n "$response" ]; then
    RAW_RESPONSES=$(printf '%s\n%s' "$RAW_RESPONSES" "$response")
  fi

  if [ -n "$SCOPED_PREFIX_LIST" ]; then
    SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST, "
  fi
  SCOPED_PREFIX_LIST="$SCOPED_PREFIX_LIST$prefix"
done

if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | _memories_merge_search_results true "$RECALL_LIMIT" 2>/dev/null) || RESULTS_JSON="[]"
else
  RESULTS_JSON=$(printf '%s\n' "$RAW_RESPONSES" | jq -sr --argjson limit "$RECALL_LIMIT" '
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

if [ "$RESULTS_JSON" = "[]" ]; then
  if [ "$(_hook_deadline_exhausted)" = "true" ]; then
    _log_warn "Hook budget exhausted — skipping the unscoped fallback search"
  else
    FALLBACK_RESPONSE=$(search_memories "project $PROJECT conventions decisions patterns" "" 6 "$MEMORIES_RECALL_FALLBACK_THRESHOLD")
    _note_auth_status "$FALLBACK_RESPONSE"
    RESULTS_JSON=$(printf '%s' "$FALLBACK_RESPONSE" | jq -c '.results // []' 2>/dev/null) || RESULTS_JSON="[]"
  fi
fi

CONTEXT_RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map(("- [\(.source)]" + (if (.provenance_label // "") != "" then " " + .provenance_label else "" end) + " candidate memory id=\(.id // .memory_id // "unknown") found at session start; call memory_search with this source prefix before using it.")) | join("\n")
  end
' 2>/dev/null) || true

_log_info "Recalled $(printf '%s' "$RESULTS_JSON" | jq -r 'length' 2>/dev/null || echo 0) memories for $PROJECT"

# Dedicated deferred-work surfacing
WIP_QUERY="deferred incomplete blocked todo revisit wip"
if [ "$(_hook_deadline_exhausted)" = "true" ]; then
  _log_warn "Hook budget exhausted — skipping the deferred-work (WIP) search"
  WIP_RESULTS='{"results":[],"count":0}'
else
  WIP_RESULTS=$(search_memories "$WIP_QUERY" "wip/$PROJECT" 5 0.3)
  _note_auth_status "$WIP_RESULTS"
fi
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

ACTIVE SEARCH ACTION for applicable prompts: Load the memory search tool if
needed with ToolSearch("+memory_search"), then call
memory_search before answering.

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
if [ -n "$PROJECT_SHARING_GUIDANCE" ]; then
  PLAYBOOK="$PLAYBOOK

$PROJECT_SHARING_GUIDANCE"
fi

# --- Sync auto-memory MEMORY.md pointers ---
# Claude Code's auto-memory loads MEMORY.md into every conversation (first 200 lines).
# We sync pointers instead of full memory text so models must call memory_search to
# inspect stored facts, while preserving manually-pinned content above the marker.
SYNC_MARKER="<!-- SYNCED-FROM-MEMORIES-MCP -->"
ENCODED_CWD=$(echo "$CWD" | tr '/' '-')
MEMORY_DIR="$HOME/.claude/projects/${ENCODED_CWD}/memory"
MEMORY_FILE="$MEMORY_DIR/MEMORY.md"

# Create memory dir if it doesn't exist (enables auto-memory for all projects)
mkdir -p "$MEMORY_DIR" 2>/dev/null || true

MEMORY_RESULTS=$(printf '%s' "$RESULTS_JSON" | jq -r '
  if length == 0 then
    empty
  else
    map("- [\(.source)] candidate memory id=\(.id // .memory_id // "unknown"); call memory_search with this source prefix before using it.") | join("\n")
  end
' 2>/dev/null) || true

if [ -n "$MEMORY_RESULTS" ] && [ "$MEMORY_RESULTS" != "null" ]; then
  MANUAL_SECTION=""
  if [ -f "$MEMORY_FILE" ]; then
    # Preserve everything above the sync marker (manual/pinned content)
    MARKER_LINE=$(grep -Fn "$SYNC_MARKER" "$MEMORY_FILE" 2>/dev/null | head -1 | cut -d: -f1) || true
    if [ -n "$MARKER_LINE" ]; then
      if [ "$MARKER_LINE" -gt 1 ]; then
        MANUAL_SECTION=$(head -n $((MARKER_LINE - 1)) "$MEMORY_FILE")
      else
        MANUAL_SECTION=""
      fi
    else
      MANUAL_SECTION=$(cat "$MEMORY_FILE")
    fi
  fi

  # Write: manual section (preserved) + sync marker + fresh memories
  {
    if [ -n "$MANUAL_SECTION" ]; then
      printf '%s\n' "$MANUAL_SECTION"
      echo ""
    fi
    echo "$SYNC_MARKER"
    echo "## Synced from Memories"
    echo "$MEMORY_RESULTS"
  } > "$MEMORY_FILE"
fi

# Credential diagnostic: /health is unauthenticated, so a wrong/missing
# MEMORIES_API_KEY would otherwise fail silently — recall returns nothing,
# forever, with no warning. Detected from the /search 401s tallied above.
CREDENTIAL_WARNING=""
if [ "$AUTH_FAILED" = "true" ]; then
  _log_warn "Backend rejected the API key (401) at $TARGET_URL"
  CREDENTIAL_WARNING=$(cat <<CWEOF
## Memories Credential Warning

Memories reached $TARGET_URL but it rejected the API key. Set MEMORIES_API_KEY (memory recall and extraction are disabled this session).
CWEOF
)
fi

# --- Output context for Claude Code ---
jq -n --arg memories "$CONTEXT_RESULTS" --arg playbook "$PLAYBOOK" --arg health_warning "$HEALTH_WARNING" --arg credential_warning "$CREDENTIAL_WARNING" --arg deferred "$DEFERRED_SECTION" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: (
      (if ($health_warning | length) > 0 then $health_warning + "\n\n" else "" end) +
      (if ($credential_warning | length) > 0 then $credential_warning + "\n\n" else "" end) +
      (if ($memories | length) > 0 then "## Relevant Memories\n\n" + $memories + "\n\n" else "" end) +
      (if ($deferred | length) > 0 then $deferred + "\n" else "" end) +
      $playbook
    )
  }
}'
