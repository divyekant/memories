#!/usr/bin/env bash
# Gate G3 of task P2-4: the same search on the Python server v5.19.0 and on
# the Rust server, with the same records.
#
# 1. The Python server gets the G3 data through the HTTP API (parity/g3.py seed).
# 2. `memories-server migrate` copies a copy of that metadata.json into Qdrant.
# 3. The two servers run at the same time. parity/g3.py compare sends each
#    query to the two servers and applies the gate conditions.
#
# Usage, from the repository root:
#   QDRANT_URL=http://127.0.0.1:6333 server/scripts/gate_g3.sh <rust binary> [results.json]
# G3_PARTS selects the parts (default corpus,graph; add longmemeval for the
# full gate). G3_LME_EVERY selects each Nth LongMemEval question (default 5).
# G3_PORT sets the Python port (default 18131); the Rust port is the next one.
# Needs .venv (uv sync --frozen), a Qdrant server with no collections
# memories and memories_v2, and ORT_DYLIB_PATH.
set -euo pipefail
rust_bin="$(realpath "$1")"
out="${2:-}"
[ -n "$out" ] && out="$(realpath -m "$out")"
: "${QDRANT_URL:?set QDRANT_URL}"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port="${G3_PORT:-18131}"
rs_port=$((py_port + 1))
pids=()
cleanup() {
  local rc=$?
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 50 "$log"; done >&2
  fi
  rm -rf "$work"
}
trap cleanup EXIT

wait_up() {
  for _ in $(seq 1 300); do
    curl -sf "http://127.0.0.1:$1/health" > /dev/null && return 0
    sleep 1
  done
  echo "The server on port $1 did not start" >&2
  return 1
}

# The Python server uses the same Qdrant server (collection memories), as in
# production. The local mode of qdrant-client gives tied hits in another order.
start_python() {
  PARITY_PYTHON_QDRANT_URL="$QDRANT_URL" PYTHON=.venv/bin/python parity/run_python_server.sh "$work/python" "$py_port" >> "$work/python.log" 2>&1 &
  pids+=($!)
  wait_up "$py_port"
}

# 1. The G3 data on the Python server.
start_python
.venv/bin/python -m parity.g3 seed --base-url "http://127.0.0.1:$py_port" \
  --parts "${G3_PARTS:-corpus,graph}" --lme-every "${G3_LME_EVERY:-5}" --plan "$work/plan.json"
kill "${pids[0]}"
wait "${pids[0]}" || true
pids=()

# 2. The same records into Qdrant for the Rust server.
cp -a "$work/python" "$work/rust"
DATA_DIR="$work/rust" "$rust_bin" migrate > "$work/migrate.log" 2>&1

# 3. The two servers, and the comparison.
start_python
API_KEY="$key" DATA_DIR="$work/rust" "$rust_bin" serve --port "$rs_port" >> "$work/rust.log" 2>&1 &
pids+=($!)
wait_up "$rs_port"
.venv/bin/python -m parity.g3 compare --python "http://127.0.0.1:$py_port" \
  --rust "http://127.0.0.1:$rs_port" --plan "$work/plan.json" ${out:+--out "$out"}
