#!/bin/bash
# memory-extract.sh — Stop hook
# Extracts facts from the last assistant message and stores via extraction pipeline.
# CC Stop hook provides: session_id, transcript_path, cwd, last_assistant_message
# (NOT a "messages" field — that was a bug in the original version)

set -euo pipefail

# Load from dedicated env file
[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

INPUT=$(cat)

# CC sends last_assistant_message on Stop events
LAST_MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty')
if [ -z "$LAST_MSG" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

# POST to extraction endpoint (fire-and-forget)
curl -sf -X POST "$MEMORIES_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "{\"messages\": $(echo "$LAST_MSG" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"stop\"}" \
  > /dev/null 2>&1 || true
