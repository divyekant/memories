#!/usr/bin/env bash
# memory-rehydrate.sh — PostCompact hook
# Fires after context compaction. Uses compact_summary as a targeted
# search query to re-inject relevant memories into the new context.
# Sync hook: blocks until done, injects additionalContext.

set -euo pipefail

MEMORIES_HOOK_NAME="memory-rehydrate"

# Source env and shared lib
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"
_LIB="$(dirname "$0")/_lib.sh"
if [ -f "$_LIB" ]; then
  source "$_LIB"
else
  _log_info() { :; }; _log_error() { :; }; _log_warn() { :; }
  _rotate_log() { :; }; _health_check() { return 0; }
fi

INPUT=$(cat)

# Extract compact summary
SUMMARY=$(echo "$INPUT" | jq -r '.compact_summary // empty')
[ -z "$SUMMARY" ] && { _log_warn "No compact_summary in input"; exit 0; }

# Extract CWD and project for scoped search
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
PROJECT=$(basename "${CWD:-unknown}")

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

# Truncate summary for use as query (max 500 chars)
QUERY="${SUMMARY:0:500}"

# Build source prefixes
PREFIXES="${MEMORIES_SOURCE_PREFIXES:-claude-code/{project},learning/{project},wip/{project}}"

# Search with the compact summary as query
RESULTS=""
for tpl in $(echo "$PREFIXES" | tr ',' ' '); do
  prefix="${tpl//\{project\}/$PROJECT}"
  BATCH=$(curl -sf --max-time 4 \
    -X POST "$MEMORIES_URL/search" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $MEMORIES_API_KEY" \
    -d "$(jq -n --arg q "$QUERY" --arg p "$prefix" '{query: $q, source_prefix: $p, k: 3, hybrid: true, threshold: 0.35}')" \
    2>/dev/null) || { _log_error "Search failed for prefix $prefix"; continue; }

  BATCH_RESULTS=$(echo "$BATCH" | jq -r '.results // []')
  if [ -n "$RESULTS" ]; then
    RESULTS=$(echo "$RESULTS $BATCH_RESULTS" | jq -s 'add | unique_by(.id) | sort_by(-.similarity // -.rrf_score) | .[0:6]')
  else
    RESULTS="$BATCH_RESULTS"
  fi
done

# Format output
if [ -n "$RESULTS" ] && [ "$RESULTS" != "[]" ] && [ "$RESULTS" != "null" ]; then
  FORMATTED=$(echo "$RESULTS" | jq -r '.[] | "- [\(.source // "unknown")] \(.text)"' 2>/dev/null)
  if [ -n "$FORMATTED" ]; then
    _log_info "Rehydrated $(echo "$RESULTS" | jq 'length') memories after compaction"
    jq -n --arg ctx "## Re-Hydrated Memories (Post-Compaction)

$FORMATTED" '{"additionalContext": $ctx}'
    exit 0
  fi
fi

_log_info "No memories matched compact summary"
exit 0
