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
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
  _default_source_prefixes() { echo 'claude-code/{project},codex/{project},learning/{project},wip/{project}'; }
fi

_exit_if_disabled 2>/dev/null || true

INPUT=$(cat)

# Extract compact summary
SUMMARY=$(echo "$INPUT" | jq -r '.compact_summary // empty')
[ -z "$SUMMARY" ] && { _log_warn "No compact_summary in input"; exit 0; }

# Extract CWD and project for scoped search
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
PROJECT=$(basename "${CWD:-unknown}")
[ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ] || [ -z "$PROJECT" ] && exit 0

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

# Truncate summary for use as query (max 500 chars)
QUERY="${SUMMARY:0:500}"

# Build source prefixes
PREFIXES="${MEMORIES_SOURCE_PREFIXES:-$(_default_source_prefixes)}"

# Search with the compact summary as query
RESULTS=""
for tpl in $(echo "$PREFIXES" | tr ',' ' '); do
  prefix="${tpl//\{project\}/$PROJECT}"
  BATCH=$(_search_memories_multi "$QUERY" "$prefix" 3 0.35) || { _log_error "Search failed for prefix $prefix"; continue; }

  BATCH_RESULTS=$(echo "$BATCH" | jq -r '.results // []')
  if [ -n "$RESULTS" ]; then
    RESULTS=$(echo "$RESULTS $BATCH_RESULTS" | jq -s 'add | unique_by(.id) | sort_by(-.similarity // -.rrf_score) | .[0:6]')
  else
    RESULTS="$BATCH_RESULTS"
  fi
done

# Sync MEMORY.md with post-compaction pointers (same sync-marker approach as recall)
if [ -n "$RESULTS" ] && [ "$RESULTS" != "[]" ] && [ "$RESULTS" != "null" ]; then
  FORMATTED=$(echo "$RESULTS" | jq -r '.[] | "- [\(.source // "unknown")] candidate memory id=\(.id // .memory_id // "unknown"); call memory_search with this source prefix before using it."' 2>/dev/null)
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
