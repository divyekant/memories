#!/bin/bash
# memory-flush.sh — PreCompact hook (async)
# Aggressive extraction before context compaction.
# Same as memory-extract.sh but with context=pre_compact.

set -euo pipefail

FAISS_URL="${FAISS_URL:-http://localhost:8900}"
FAISS_API_KEY="${FAISS_API_KEY:-}"

INPUT=$(cat)
MESSAGES=$(echo "$INPUT" | jq -r '.messages // empty')
if [ -z "$MESSAGES" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(basename "$CWD")

curl -sf -X POST "$FAISS_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FAISS_API_KEY" \
  -d "{\"messages\": $(echo "$MESSAGES" | jq -Rs), \"source\": \"claude-code/$PROJECT\", \"context\": \"pre_compact\"}" \
  > /dev/null 2>&1 || true
