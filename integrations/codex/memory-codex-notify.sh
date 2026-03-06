#!/bin/bash
# memory-codex-notify.sh — Codex notify hook (after-agent)
# Receives Codex notify payload as argv[1] JSON and enqueues extraction.

set -euo pipefail

[ -f "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}" ] && . "${MEMORIES_ENV_FILE:-$HOME/.config/memories/env}"

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
MEMORIES_SOURCE="${MEMORIES_SOURCE:-}"
MEMORIES_SOURCE_PREFIX="${MEMORIES_SOURCE_PREFIX:-codex}"

PAYLOAD="${1:-}"
if [ -z "$PAYLOAD" ]; then
  PAYLOAD="$(cat 2>/dev/null || true)"
fi

if [ -z "$PAYLOAD" ]; then
  exit 0
fi

if ! echo "$PAYLOAD" | jq -e . >/dev/null 2>&1; then
  exit 0
fi

EVENT_TYPE=$(echo "$PAYLOAD" | jq -r '.type // .event // ""')
if [ -n "$EVENT_TYPE" ] && [ "$EVENT_TYPE" != "agent-turn-complete" ]; then
  exit 0
fi

CWD=$(echo "$PAYLOAD" | jq -r '.cwd // .workspace_roots[0] // .workspaceRoots[0] // .workspace.root // .workspaceRoot // "unknown"')
PROJECT=$(basename "$CWD")
if [ -z "$PROJECT" ] || [ "$PROJECT" = "/" ] || [ "$PROJECT" = "." ]; then
  PROJECT="unknown"
fi

TRANSCRIPT_PATH=$(echo "$PAYLOAD" | jq -r '.transcript_path // .transcriptPath // empty')
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Shared jq function: recursively extract plain text from string/array/object content.
JQ_TEXTIFY='def textify:
  if . == null then ""
  elif type == "string" then .
  elif type == "number" or type == "boolean" then tostring
  elif type == "array" then
    [
      .[]
      | if type == "object" then
          if ((.type // "") | tostring) == "text" and ((.text // null) | type) == "string" then .text
          elif ((.text // null) | type) == "string" then .text
          elif ((.content // null) | type) == "string" then .content
          elif (.content // null) != null then (.content | textify)
          elif ((.message // null) | type) == "string" then .message
          else "" end
        elif type == "string" then .
        else "" end
    ]
    | map(select(length > 0))
    | join(" ")
  elif type == "object" then
    if (.content // null) != null then (.content | textify)
    elif ((.text // null) | type) == "string" then .text
    elif ((.message // null) | type) == "string" then .message
    else "" end
  else "" end;'

MESSAGES=""
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  MESSAGES=$(tail -300 "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr "${JQ_TEXTIFY}
    [
      .[]
      | {
          role: ((.type // .message.role // \"\") | tostring),
          text: ((.message.content // .content // .text // .message // null) | textify)
        }
      | select((.role == \"user\" or .role == \"assistant\") and ((.text | length) > 10))
    ]
    | .[-2:]
    | map(
        (if .role == \"user\" then \"User: \" else \"Assistant: \" end) +
        (
          .text
          | gsub(\"[\\\\r\\\\n]+\"; \" \")
          | gsub(\"\\\\s+\"; \" \")
          | gsub(\"^\\\\s+|\\\\s+\$\"; \"\")
          | .[0:2000]
        )
      )
    | join(\"\\n\")
  " 2>/dev/null) || true
fi

if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  USER_LINES=$(echo "$PAYLOAD" | jq -r "${JQ_TEXTIFY}
    (.\"input-messages\" // .input_messages // .inputMessages // .messages // [])
    | if type == \"array\" then . else [.] end
    | map(
        textify
        | gsub(\"[\\\\r\\\\n]+\"; \" \")
        | gsub(\"\\\\s+\"; \" \")
        | gsub(\"^\\\\s+|\\\\s+\$\"; \"\")
      )
    | map(select(length > 0))
    | map(\"User: \" + (.[0:2000]))
    | join(\"\\n\")
  ")
  ASSISTANT_LINE=$(echo "$PAYLOAD" | jq -r "${JQ_TEXTIFY}
    (.\"last-assistant-message\" // .last_assistant_message // .lastAssistantMessage // .assistant // .response // \"\")
    | textify
    | gsub(\"[\\\\r\\\\n]+\"; \" \")
    | gsub(\"\\\\s+\"; \" \")
    | gsub(\"^\\\\s+|\\\\s+\$\"; \"\")
    | .[0:2000]
  ")

  MESSAGES="$USER_LINES"
  if [ -n "$ASSISTANT_LINE" ]; then
    if [ -n "$MESSAGES" ]; then
      MESSAGES="$MESSAGES"$'\n'
    fi
    MESSAGES="$MESSAGES""Assistant: $ASSISTANT_LINE"
  fi
fi

if [ -z "$MESSAGES" ]; then
  exit 0
fi
MESSAGES="${MESSAGES:0:4000}"

SOURCE="$MEMORIES_SOURCE"
if [ -z "$SOURCE" ]; then
  SOURCE_PREFIX="${MEMORIES_SOURCE_PREFIX%/}"
  if [ -z "$SOURCE_PREFIX" ]; then
    SOURCE_PREFIX="codex"
  fi
  SOURCE="$SOURCE_PREFIX/$PROJECT"
fi

BODY=$(jq -nc --arg msgs "$MESSAGES" --arg src "$SOURCE" '{"messages": $msgs, "source": $src, "context": "after_agent"}')
curl -sf --connect-timeout 1 --max-time 2 -X POST "$MEMORIES_URL/memory/extract" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $MEMORIES_API_KEY" \
  -d "$BODY" \
  > /dev/null 2>&1 || true
