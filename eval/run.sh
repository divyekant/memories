#!/usr/bin/env bash
# Run the Memories efficacy eval harness.
# Usage: ./eval/run.sh [--scenario coding-001] [--category coding] [-v]
set -euo pipefail

cd "$(dirname "$0")/.."

# Source env files for API key and other config
[[ -f .env ]] && set -a && source .env && set +a
[[ -f ~/.config/memories/env ]] && set -a && source ~/.config/memories/env && set +a

# Map common env var names
export MEMORIES_API_KEY="${MEMORIES_API_KEY:-${API_KEY:-}}"

# Resolve MCP server path if not set
if [[ -z "${EVAL_MCP_SERVER_PATH:-}" ]]; then
  MCP_PATH="$(pwd)/mcp-server/index.js"
  [[ -f "$MCP_PATH" ]] && export EVAL_MCP_SERVER_PATH="$MCP_PATH"
fi

# Use project venv
PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Error: .venv not found. Run: python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

# Check Memories service
EVAL_PORT="${MEMORIES_PORT:-8901}"
if ! curl -sf "http://localhost:${EVAL_PORT}/health" > /dev/null 2>&1; then
  echo "Error: Memories service not reachable at http://localhost:${EVAL_PORT}" >&2
  exit 1
fi

# Check claude CLI
if ! command -v claude > /dev/null 2>&1; then
  echo "Error: claude CLI not found in PATH" >&2
  exit 1
fi

echo "=== Memories Efficacy Eval ==="
echo "Memories: http://localhost:${EVAL_PORT} (healthy)"
if [[ -n "${MEMORIES_API_KEY:-}" ]]; then echo "API Key:  [set]"; else echo "API Key:  NOT SET"; fi
echo "MCP Path: ${EVAL_MCP_SERVER_PATH:-not set}"
echo "Args:     $*"
echo ""

exec "$PYTHON" -m eval "$@"
