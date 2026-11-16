#!/usr/bin/env bash
# Exit check of task P5-6: the four /promotions routes and the `promotion`
# part of GET /metrics, in the `off` mode of project promotion.
#
# 1. The Python server gets project records (a managed key with a principal)
#    and the promotion requests of parity_promotions_ab.py.
# 2. `memories-server migrate` copies metadata.json into the Rust collection.
# 3. The Rust server replays the four golden promotion cases, then gets the
#    same requests. Each status and body must be equal.
#
# Usage, from the repository root: server/scripts/parity_promotions.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen) and a Qdrant server
# at QDRANT_URL (default http://127.0.0.1:6333).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18131
rs_port=18132
qdrant_url="${QDRANT_URL:-http://127.0.0.1:6333}"
collection="parity_promotions_$$"
pid=""
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  curl -s -X DELETE "$qdrant_url/collections/${collection}_v2" > /dev/null || true
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
ab=server/scripts/parity_promotions_ab.py
cases="promotions,promotion-get,promotion-approve,promotion-reject"

# 1. Python.
PYTHON=.venv/bin/python parity/run_python_server.sh "$work/data" "$py_port" > "$work/python.log" 2>&1 &
pid=$!
wait_up "$py_port"
.venv/bin/python "$ab" seed "http://127.0.0.1:$py_port" "$key"
.venv/bin/python "$ab" probe "http://127.0.0.1:$py_port" "$key" "$work/python.json"
kill "$pid"
wait "$pid" || true
pid=""

# 2. metadata.json into the collection of the Rust server.
QDRANT_URL="$qdrant_url" QDRANT_COLLECTION="$collection" DATA_DIR="$work/data" \
  "$rust_bin" migrate > "$work/migrate.log" 2>&1

# 3. Rust.
API_KEY="$key" DATA_DIR="$work/data" AUDIT_LOG=true USAGE_TRACKING=true \
  QDRANT_URL="$qdrant_url" QDRANT_COLLECTION="$collection" \
  "$rust_bin" serve --port "$rs_port" > "$work/rust.log" 2>&1 &
pid=$!
wait_up "$rs_port"
status=0
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --only "$cases" --out "$work/replay.ndjson"
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/replay.ndjson" || status=1
.venv/bin/python "$ab" probe "http://127.0.0.1:$rs_port" "$key" "$work/rust.json"
.venv/bin/python "$ab" compare "$work/python.json" "$work/rust.json" || status=1

# 4. The Rust server refuses a promotion mode that it does not have.
if PROJECT_PROMOTION_MODE=shadow API_KEY="$key" DATA_DIR="$work/data" \
  QDRANT_URL="$qdrant_url" QDRANT_COLLECTION="$collection" \
  timeout 20 "$rust_bin" serve --port "$rs_port" > "$work/refuse.log" 2>&1; then
  echo "FAIL: the Rust server started with PROJECT_PROMOTION_MODE=shadow" >&2
  status=1
elif ! grep -q "PROJECT_PROMOTION_MODE=shadow is not supported" "$work/refuse.log"; then
  echo "FAIL: no clear message for PROJECT_PROMOTION_MODE=shadow" >&2
  status=1
else
  echo "ok: PROJECT_PROMOTION_MODE=shadow stops the start"
fi
exit "$status"
