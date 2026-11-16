#!/usr/bin/env bash
# Exit check of task P5-7: the five /sync routes and the embedder setting, in
# the configuration of production (no cloud sync, EMBED_PROVIDER=onnx).
#
# 1. The Python server makes two managed keys, then gets the sync requests
#    of parity_sync_ab.py with the admin key, each managed key and a wrong key.
# 2. The Rust server reads the same keys.db and gets the same requests. Each
#    status and body must be equal.
# 3. The Rust server refuses EMBED_PROVIDER=openai at start.
#
# Usage, from the repository root: server/scripts/parity_sync.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen). No Qdrant server.
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18141
rs_port=18142
pid=""
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 50 "$log"; done >&2
  fi
  rm -rf "$work"
}
trap cleanup EXIT

wait_up() {
  for _ in $(seq 1 180); do
    curl -sf "http://127.0.0.1:$1/health" > /dev/null && return 0
    sleep 1
  done
  echo "The server on port $1 did not start" >&2
  return 1
}
ab=server/scripts/parity_sync_ab.py

# 1. Python.
PYTHON=.venv/bin/python parity/run_python_server.sh "$work/data" "$py_port" > "$work/python.log" 2>&1 &
pid=$!
wait_up "$py_port"
.venv/bin/python "$ab" seed "http://127.0.0.1:$py_port" "$key" "$work/keys.json"
.venv/bin/python "$ab" probe "http://127.0.0.1:$py_port" "$key" "$work/keys.json" "$work/python.json"
kill "$pid"
wait "$pid" || true
pid=""

# 2. Rust, with the keys.db of the Python run.
API_KEY="$key" DATA_DIR="$work/data" EMBED_PROVIDER=onnx QDRANT_URL= \
  "$rust_bin" serve --port "$rs_port" > "$work/rust.log" 2>&1 &
pid=$!
wait_up "$rs_port"
status=0
.venv/bin/python "$ab" probe "http://127.0.0.1:$rs_port" "$key" "$work/keys.json" "$work/rust.json"
.venv/bin/python "$ab" compare "$work/python.json" "$work/rust.json" || status=1
kill "$pid"
wait "$pid" || true
pid=""

# 3. The Rust server refuses an embedder that it does not have.
if EMBED_PROVIDER=openai API_KEY="$key" DATA_DIR="$work/data" QDRANT_URL= \
  timeout 20 "$rust_bin" serve --port 18143 > "$work/refuse.log" 2>&1; then
  echo "FAIL: the Rust server started with EMBED_PROVIDER=openai" >&2
  status=1
elif ! grep -q "EMBED_PROVIDER=openai is not supported" "$work/refuse.log"; then
  echo "FAIL: no clear message for EMBED_PROVIDER=openai" >&2
  status=1
else
  echo "ok: EMBED_PROVIDER=openai stops the start"
fi
exit "$status"
