#!/bin/bash
# memory-flush.sh — PreCompact hook
# Aggressive extraction before context compaction.
# CC PreCompact provides: session_id, transcript_path, cwd
# Reads recent messages from JSONL transcript before they're compacted away.

MEMORIES_HOOK_NAME="memory-flush"

set -euo pipefail

# Load from dedicated env file
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
  _default_extract_source() { echo 'claude-code/{project}'; }
fi

_exit_if_disabled 2>/dev/null || true

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

# Configurable thresholds (Task 1.2)
TAIL_LINES="${MEMORIES_FLUSH_TAIL_LINES:-1000}"
MSG_PAIRS="${MEMORIES_FLUSH_MSG_PAIRS:-20}"
MSG_CAP="${MEMORIES_FLUSH_MSG_CAP:-12000}"
# Tool calls and output never reach the Stop hook, so the flush is the only
# place where one-time results (exact errors, totals, versions) can be kept.
TOOL_ITEMS="${MEMORIES_FLUSH_TOOL_ITEMS:-30}"
TOOL_CHARS="${MEMORIES_FLUSH_TOOL_CHARS:-500}"

INPUT=$(cat)

TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
PROJECT=$(_memories_resolve_project "$CWD" 2>/dev/null || basename "$CWD")

# Expand tilde if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Build extraction source — supports {project} placeholder (Task 1.3)
_DEFAULT_SRC="$(_default_extract_source)"
_EXTRACT_SRC="${MEMORIES_EXTRACT_SOURCE:-$_DEFAULT_SRC}"
SOURCE="${_EXTRACT_SRC//\{project\}/$PROJECT}"

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

# Pre-compact: read aggressively to capture context about to be lost
MESSAGES=$(tail -"$TAIL_LINES" "$TRANSCRIPT_PATH" 2>/dev/null | jq -sr --argjson pairs "$MSG_PAIRS" \
  --argjson tool_items "$TOOL_ITEMS" --argjson tool_chars "$TOOL_CHARS" "${_MEMORIES_TURN_TEXT_JQ:-}"'
  # Built-in tools only: memory and messaging tools return recalled or
  # Claude-written text, which must not be extracted again.
  def kept_tool: test("^(Bash|Read|Grep|Glob|Edit|MultiEdit|Write|WebFetch)$");
  def blocks: (.message.content // []) | if type == "array" then .[] else empty end;
  # Keep the head and the tail: errors and totals are usually at the end.
  def clip($n): if length > $n then .[0:($n / 4 | floor)] + " ... " + .[-($n * 3 / 4 | floor):] else . end;
  def result_text:
    (if type == "string" then . elif type == "array" then [.[] | .text? // empty] | join(" ") else "" end)
    | gsub("<system-reminder>[\\s\\S]*?</system-reminder>"; "");
  (reduce (.[] | blocks | select(.type == "tool_use")) as $t ({}; .[$t.id // ""] = ($t.name // ""))) as $names
  | [
      .[]
      | select(.type == "user" or .type == "assistant")
      | ({role: .type, text: turn_text} | select(.text != "" and (.text | length) > 10)),
        (blocks
         | if .type == "tool_use" and ((.name // "") | kept_tool) then
             {role: "tool call", text: (.name + ": " + ((.input.command // .input.file_path // .input.pattern // .input.url // (.input | tostring)) | tostring | .[0:300]))}
           elif .type == "tool_result" and (($names[.tool_use_id // ""] // "") | kept_tool) then
             {role: "tool output", text: (.content | result_text)} | select(.text | test("\\S"))
           else empty end)
    ]
  | ([to_entries[] | select(.value.role == "user" or .value.role == "assistant") | .key]) as $text_idx
  | if ($text_idx | length) == 0 then "" else
      .[(if ($text_idx | length) > $pairs then $text_idx[-$pairs] else $text_idx[0] end):]
      | ([to_entries[] | select(.value.role | startswith("tool")) | .key]) as $tool_idx
      | (if $tool_items > 0 then $tool_idx[:-$tool_items] else $tool_idx end) as $drop
      | (reduce $drop[] as $k ({}; .[$k | tostring] = true)) as $dropped
      | [to_entries[] | select(($dropped[.key | tostring] // false) | not) | .value]
      | map(.role + ": " + (if .role == "tool output" then (.text | clip($tool_chars)) elif .role == "tool call" then (.text | .[0:$tool_chars]) else (.text | .[0:2000]) end))
      | join("\n\n")
    end
' 2>/dev/null) || { _log_warn "Transcript parse failed for $TRANSCRIPT_PATH"; true; }

if [ -z "$MESSAGES" ] || [ "$MESSAGES" = "null" ]; then
  exit 0
fi

# Truncate to MSG_CAP chars of text, plus room for the clipped tool items
MESSAGES="${MESSAGES:0:$((MSG_CAP + TOOL_ITEMS * (TOOL_CHARS + 16)))}"

_log_info "Flush-extracting from $PROJECT (${#MESSAGES} chars, source=$SOURCE)"

_extract_multi "$MESSAGES" "$SOURCE" "pre_compact"
