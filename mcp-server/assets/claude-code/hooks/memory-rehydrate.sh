#!/usr/bin/env bash
# memory-rehydrate.sh — PostCompact hook
# Fires after context compaction. Uses compact_summary as a targeted
# search query to refresh the MEMORY.md sync section with the most
# relevant memory pointers for the post-compaction context.
#
# PostCompact does not support additionalContext injection, so this hook
# updates the synced MEMORY.md section instead (same mechanism as
# memory-recall.sh SessionStart hydration).

set -euo pipefail

MEMORIES_HOOK_NAME="memory-rehydrate"

# Source env and shared lib
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
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_source_prefixes() { echo 'claude-code/{project},codex/{project},learning/{project},wip/{project}'; }
  _hook_deadline_init() { :; }
fi

_exit_if_disabled 2>/dev/null || true

# PostCompact has one five-second budget for principal resolution, scoped
# searches, result assembly, and the MEMORY.md write.  Initialize the shared
# deadline before any backend call so every search is capped by the time left
# after authenticated project-context lookup.
_hook_deadline_init

INPUT=$(cat)

# Extract compact summary
SUMMARY=$(echo "$INPUT" | jq -r '.compact_summary // empty')
[ -z "$SUMMARY" ] && { _log_warn "No compact_summary in input"; exit 0; }

# Extract CWD and project for scoped search
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
PROJECT=$(_memories_resolve_project "${CWD:-unknown}" 2>/dev/null || basename "${CWD:-unknown}")
[ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ] || [ -z "$PROJECT" ] && exit 0
PREFIXES="${MEMORIES_SOURCE_PREFIXES:-$(_default_source_prefixes)}"
PROJECT_CONTEXT_JSON=$(_memories_project_context "${CWD:-}" 2>/dev/null || printf '{"active":false}')
PROJECT_CONTEXT_ACTIVE=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.active // false' 2>/dev/null || printf 'false')
if [ "$PROJECT_CONTEXT_ACTIVE" != "true" ] && declare -F _memories_project_declared >/dev/null && _memories_project_declared "${CWD:-}"; then
  _log_warn "Collaborative project identity unavailable; skipping memory rehydration"
  exit 0
fi
PROJECT_CONTEXT_ID=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.project_id // empty' 2>/dev/null || true)
PROJECT_CONTEXT_PRINCIPAL=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '.principal_id // empty' 2>/dev/null || true)
PROJECT_CONTEXT_LEGACY_PREFIXES=$(printf '%s' "$PROJECT_CONTEXT_JSON" | jq -r '(.legacy_source_prefixes // []) | join(",")' 2>/dev/null || true)
if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
  PROJECT="$PROJECT_CONTEXT_ID"
  PREFIXES=$(_memories_project_recall_prefixes "$PROJECT_CONTEXT_ID" "$PROJECT_CONTEXT_PRINCIPAL" "$PROJECT_CONTEXT_LEGACY_PREFIXES" | tr '\n' ',' | sed 's/,$//')
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

# Truncate summary for use as query (max 500 chars)
QUERY="${SUMMARY:0:500}"

# Search with the compact summary as query
RESULTS=""
SEARCH_TMPDIR=$(mktemp -d 2>/dev/null || mktemp -d -t memories-rehydrate)
SEARCH_JOBS=()
SEARCH_INDEX=0
for tpl in $(echo "$PREFIXES" | tr ',' ' '); do
  prefix="${tpl//\{project\}/$PROJECT}"
  outfile="$SEARCH_TMPDIR/result_${SEARCH_INDEX}.json"
  SEARCH_INDEX=$((SEARCH_INDEX + 1))
  (
    BATCH=$(_search_memories_multi "$QUERY" "$prefix" 3 0.35) || {
      _log_error "Search failed for prefix $prefix"
      BATCH='{"results":[],"count":0}'
    }
    if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
      BATCH=$(printf '%s' "$BATCH" | _memories_filter_search_response_for_prefix "$prefix")
    fi
    printf '%s' "$BATCH" > "$outfile"
  ) &
  SEARCH_JOBS+=("$!")
done

if [ "${#SEARCH_JOBS[@]}" -gt 0 ]; then
  for job in "${SEARCH_JOBS[@]}"; do
    wait "$job" || true
  done
fi

result_index=0
while [ "$result_index" -lt "$SEARCH_INDEX" ]; do
  result_file="$SEARCH_TMPDIR/result_${result_index}.json"
  BATCH=$(cat "$result_file" 2>/dev/null || printf '{"results":[],"count":0}')
  BATCH_RESULTS=$(echo "$BATCH" | jq -r '.results // []')
  if [ -n "$RESULTS" ]; then
    # Negate the resolved score, not each candidate: `-.similarity // -.rrf_score`
    # negates before the alternative is considered, so a hybrid-search result
    # (rrf_score, no similarity) aborts the merge instead of falling through.
    if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ]; then
      RESULTS=$(printf '%s\n%s\n' "{\"results\":$RESULTS}" "{\"results\":$BATCH_RESULTS}" | _memories_merge_search_results true 6)
    else
      RESULTS=$(echo "$RESULTS $BATCH_RESULTS" | jq -s 'add | unique_by(.id) | sort_by(-(.similarity // .rrf_score // 0)) | .[0:6]')
    fi
  else
    RESULTS="$BATCH_RESULTS"
  fi
  result_index=$((result_index + 1))
done
rm -rf "$SEARCH_TMPDIR"

if [ "$PROJECT_CONTEXT_ACTIVE" = "true" ] && [ -n "$RESULTS" ] && [ "$RESULTS" != "[]" ]; then
  RESULTS=$(printf '%s' "$RESULTS" | _memories_label_project_results "$PROJECT_CONTEXT_ID" 2>/dev/null) || RESULTS="[]"
fi

# Sync MEMORY.md with post-compaction pointers (same sync-marker approach as recall)
if [ -n "$RESULTS" ] && [ "$RESULTS" != "[]" ] && [ "$RESULTS" != "null" ]; then
  FORMATTED=$(echo "$RESULTS" | jq -r '.[] | ("- [\(.source // "unknown")]" + (if (.provenance_label // "") != "" then " " + .provenance_label else "" end) + " candidate memory id=\(.id // .memory_id // "unknown"); call memory_search with this source prefix before using it.")' 2>/dev/null)
  if [ -n "$FORMATTED" ]; then
    SYNC_MARKER="<!-- SYNCED-FROM-MEMORIES-MCP -->"
    ENCODED_CWD=$(echo "$CWD" | sed 's|/|-|g; s|^-||')
    MEMORY_DIR="$HOME/.claude/projects/${ENCODED_CWD}/memory"
    MEMORY_FILE="$MEMORY_DIR/MEMORY.md"

    if [ -d "$MEMORY_DIR" ] && [ -f "$MEMORY_FILE" ]; then
      # Preserve manual content above sync marker, replace synced section
      MANUAL_SECTION=$(sed "/$SYNC_MARKER/,\$d" "$MEMORY_FILE" 2>/dev/null || true)
      {
        [ -n "$MANUAL_SECTION" ] && printf '%s\n' "$MANUAL_SECTION"
        echo "$SYNC_MARKER"
        echo "## Synced from Memories (post-compaction)"
        echo "$FORMATTED"
      } > "$MEMORY_FILE"
      _log_info "Rehydrated MEMORY.md with $(echo "$RESULTS" | jq 'length') memories after compaction"
    else
      _log_warn "MEMORY.md directory not found at $MEMORY_DIR"
    fi
  fi
fi

exit 0
