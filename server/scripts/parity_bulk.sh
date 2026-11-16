#!/usr/bin/env bash
# Exit check of task P3-2: the bulk write and relation write routes.
#
# 1. Golden replay. The Rust server gets a new, empty Qdrant collection. The
#    replay sends each golden case of the write routes of P3-1 and P3-2, in the
#    golden order, and the two key cases. It leaves out `delete-by-prefix`: its
#    golden reply counts 2 records that `POST /import` added, and this server
#    does not have that route yet.
# 2. Live run. A new Python server (v5.19.0, MEMORIES_PERSIST_DELAY_SEC=0)
#    runs the bulk scenario (parity/bulk_scenario.py). A new Rust server gets
#    the same requests. The diff compares each reply. After each delete, the
#    case `inv-N` lists the records that stay, so a different set of deleted
#    IDs is a difference.
#
# Usage, from the repository root: server/scripts/parity_bulk.sh <rust binary>
# Needs: the Python environment in .venv (uv sync --frozen), QDRANT_URL (a
# Qdrant server) and ORT_DYLIB_PATH (ONNX Runtime).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
: "${QDRANT_URL:?set QDRANT_URL}"
: "${ORT_DYLIB_PATH:?set ORT_DYLIB_PATH}"
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18114
rs_port=18115
pid=""
collections=()
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 50 "$log"; done >&2
  fi
  for c in "${collections[@]}"; do
    curl -s -X DELETE "$QDRANT_URL/collections/${c}_v2" > /dev/null || true
  done
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
stop() {
  kill "$pid"
  wait "$pid" || true
  pid=""
}
# Starts a Rust server with a new data directory and collection: $1 is the name.
start_rust() {
  local c="parity_bulk_$1_$$"
  collections+=("$c")
  API_KEY="$key" DATA_DIR="$work/rust-$1" AUDIT_LOG=true QDRANT_COLLECTION="$c" \
    "$rust_bin" serve --port "$rs_port" >> "$work/rust-$1.log" 2>&1 &
  pid=$!
  wait_up "$rs_port"
}

echo "== 1. Golden replay of the write routes"
cases="$(.venv/bin/python - <<'PY'
import json
routes = {
    "POST /memory/add", "POST /memory/add-batch", "POST /memory/upsert",
    "POST /memory/upsert-batch", "PATCH /memory/{memory_id}",
    "DELETE /memory/{memory_id}", "POST /memory/delete-batch",
    "POST /memory/delete-by-source", "POST /memory/delete-by-prefix", "DELETE /memories",
    "POST /memory/supersede", "POST /memory/{memory_id}/supersede", "POST /memory/merge",
    "POST /memory/archive-batch", "POST /memory/{memory_id}/link",
    "DELETE /memory/{memory_id}/link/{target_id}", "POST /folders/rename",
    "POST /memory/conflicts/resolve", "POST /memory/deduplicate", "POST /memory/missed",
}
names = []
for line in open("parity/golden/golden.ndjson"):
    case = json.loads(line)
    if case["name"] == "delete-by-prefix":
        continue
    if case["route"] in routes or case["name"] in ("keys-create-ro", "keys-create-rw"):
        names.append(case["name"])
print(",".join(names))
PY
)"
start_rust golden
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --only "$cases" --out "$work/golden-replay.ndjson"
stop
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/golden-replay.ndjson"

echo "== 2. Live run of the bulk scenario: Python, then Rust"
PYTHON=.venv/bin/python parity/run_python_server.sh "$work/python" "$py_port" >> "$work/python.log" 2>&1 &
pid=$!
wait_up "$py_port"
.venv/bin/python -m parity capture --scenario bulk --base-url "http://127.0.0.1:$py_port" \
  --out "$work/python.ndjson"
stop
start_rust live
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --input "$work/python.ndjson" --out "$work/rust.ndjson"
stop
.venv/bin/python -m parity diff --intended "$work/python.ndjson" "$work/rust.ndjson"
