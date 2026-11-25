#!/usr/bin/env bash
# Gate G6 (task P6-1): run the real clients against a new Python server and a
# new Rust server, and compare the two (parity/clients.py).
#
# Usage, from the repository root:
#   QDRANT_URL=http://127.0.0.1:6333 server/scripts/gate_g6.sh <rust binary> [suites]
# Needs the Python environment in .venv (uv sync --frozen), mcp-server/node_modules
# (npm ci), jq (the hooks use it) and a Qdrant server. Each server uses a new
# collection, and the script deletes the two collections at the end. Set KEEP_WORK=<dir> (a new directory) to keep the records and logs.
set -euo pipefail
rust_bin="$(realpath "$1")"
suites="${2:-mcp,cli,hooks,opencode,installer,probes}"
: "${QDRANT_URL:?set QDRANT_URL}"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18161
rust_port=18162
pids=()
collection="g6_clients_$$"
py_collection="g6_python_$$"
cleanup() {
  local rc=$?
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  for p in "${pids[@]}"; do wait "$p" 2>/dev/null || true; done
  curl -s -X DELETE "$QDRANT_URL/collections/${collection}_v2" > /dev/null || true
  curl -s -X DELETE "$QDRANT_URL/collections/$py_collection" > /dev/null || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 40 "$log"; done >&2
  fi
  if [ -n "${KEEP_WORK:-}" ]; then
    if [ -e "$KEEP_WORK" ]; then echo "KEEP_WORK=$KEEP_WORK exists; the run is not kept" >&2
    else cp -r "$work" "$KEEP_WORK"; fi
  fi
  rm -rf "$work"
}
trap cleanup EXIT

# The Python server uses the Qdrant server too: in the embedded mode, two snapshots
# in the same second have the same name and the second one fails (HTTP 500).
PYTHON=.venv/bin/python PARITY_PYTHON_QDRANT_URL="$QDRANT_URL" QDRANT_COLLECTION="$py_collection" \
  parity/run_python_server.sh "$work/py-data" "$py_port" >> "$work/python.log" 2>&1 &
pids+=($!)
# The same settings as parity/run_python_server.sh, so that the two servers agree.
API_KEY="$key" DATA_DIR="$work/rust-data" QDRANT_COLLECTION="$collection" UI_DIR="$PWD/webui" \
  WORKSPACE_DIR="$PWD/parity/workspace" USAGE_TRACKING=true AUDIT_LOG=true \
  EXTRACT_PROVIDER=ollama OLLAMA_URL=http://127.0.0.1:9 MAINTENANCE_ENABLED=false CLOUD_SYNC_ENABLED=false \
  "$rust_bin" serve --port "$rust_port" >> "$work/rust.log" 2>&1 &
pids+=($!)
for i in 0 1; do
  port=$([ "$i" = 0 ] && echo "$py_port" || echo "$rust_port")
  for _ in $(seq 1 300); do
    curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
    kill -0 "${pids[$i]}" 2>/dev/null || break
    sleep 1
  done
  curl -sf "http://127.0.0.1:$port/health" > /dev/null || { echo "the server on port $port did not start" >&2; exit 1; }
done

.venv/bin/python -m parity.clients --ref "http://127.0.0.1:$py_port" --new "http://127.0.0.1:$rust_port" \
  --key "$key" --only "$suites" --out "$work/records"
