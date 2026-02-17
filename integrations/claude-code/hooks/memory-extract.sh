#!/bin/bash
# memory-extract.sh — Stop hook (async)
# Extracts facts from the last exchange and stores via AUDN pipeline.
# Requires FAISS service with extraction enabled (EXTRACT_PROVIDER set).

set -euo pipefail

FAISS_URL="${FAISS_URL:-http://localhost:8900}"
FAISS_API_KEY="${FAISS_API_KEY:-}"

INPUT=$(cat)
STOP_REASON=$(echo "$INPUT" | jq -r '.stop_reason // "end_turn"')

# Only extract on normal completions
if [ "$STOP_REASON" != "end_turn" ]; then
  exit 0
fi

MESSAGES=$(echo "$INPUT" | jq -r '.messages // empty')
if [ -z "$MESSAGES" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

# POST to extraction endpoint (fire-and-forget, async hook)
curl -sf -X POST "$FAISS_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FAISS_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"stop\"}" \
  > /dev/null 2>&1 || true
