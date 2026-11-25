#!/usr/bin/env bash
# Gate G4 part b (task P4-4): 24 dry-run probes with a live model on a Python
# server and a Rust server (parity/extract_live.py). Both servers use the same
# provider and model from the environment. The key goes only to the provider.
#
# Usage, from the repository root:
#   EXTRACT_PROVIDER=openai EXTRACT_MODEL=gpt-4o-mini OPENAI_API_KEY=... \
#   QDRANT_URL=... \
#     server/scripts/gate_g4_live.sh <rust binary> [--rounds N] [--out FILE]
# Needs the Python environment in .venv (uv sync --frozen).
set -euo pipefail
rust="$(realpath "$1")"
shift
cd "$(dirname "$0")/../.."
: "${EXTRACT_PROVIDER:?set EXTRACT_PROVIDER}"
: "${EXTRACT_MODEL:?set EXTRACT_MODEL}"
: "${QDRANT_URL:?set QDRANT_URL}"
work="$(mktemp -d)"
collection="gate_g4_live_$$"
pids=()
cleanup() {
  local rc=$?
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; done
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 60 "$log"; done >&2
  fi
  curl -s -X DELETE "$QDRANT_URL/collections/${collection}_v2" > /dev/null || true
  rm -rf "$work"
  exit "$rc"
}
trap cleanup EXIT

export API_KEY="parity-admin-key" EXTRACT_MAX_INFLIGHT=1 MEMORIES_PERSIST_DELAY_SEC=0
mkdir -p "$work/py" "$work/rs"
DATA_DIR="$work/py" QDRANT_URL="" WORKSPACE_DIR="$PWD/parity/workspace" MAINTENANCE_ENABLED=false \
  CLOUD_SYNC_ENABLED=false PYTHONHASHSEED=0 \
  .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 18161 > "$work/python.log" 2>&1 &
pids+=($!)
DATA_DIR="$work/rs" QDRANT_COLLECTION="$collection" \
  "$rust" serve --port 18171 > "$work/rust.log" 2>&1 &
pids+=($!)
for port in 18161 18171; do
  for _ in $(seq 1 180); do
    curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
    sleep 1
  done
done
.venv/bin/python -m parity.extract_live --python-url http://127.0.0.1:18161 \
  --rust-url http://127.0.0.1:18171 "$@"
