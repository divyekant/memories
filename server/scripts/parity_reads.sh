#!/usr/bin/env bash
# Exit check of task P2-3: the read routes against the golden captures of the
# Python server.
#
# 1. The Python server runs the golden cases before `stats` (the seed corpus,
#    the keys and the usage cases) and saves the variables of the run.
# 2. A loader copies metadata.json of the Python server into the collection of
#    the Rust server.
# 3. The Rust server opens the same DATA_DIR (keys.db, config.json) and runs
#    the read cases.
# 4. The Python server starts again on the same data. Both servers get more
#    read requests (parity_reads_ab.py), and the replies must be equal.
#
# Usage, from the repository root: server/scripts/parity_reads.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen) and a Qdrant server
# at QDRANT_URL (default http://127.0.0.1:6333).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18121
rs_port=18122
qdrant_url="${QDRANT_URL:-http://127.0.0.1:6333}"
collection="parity_reads_$$"
pid=""
py_pid=""
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  [ -n "$py_pid" ] && kill "$py_pid" 2>/dev/null || true
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

cases="memory-404,stats,health-full,count-all,count-source,count-boundary,count-ro-scope,list-page,list-source"
cases="$cases,list-pinned,list-ro-scope,list-422,folders,folders-ro,memory-get,memory-get-ro-403"
cases="$cases,memory-get-batch-string-ids,memory-get-batch-str,links-out,links-in,conflicts"

# 1. The cases before `stats` on the Python server.
prefix="$(.venv/bin/python - <<'PY'
import json
names = []
for line in open("parity/golden/golden.ndjson"):
    name = json.loads(line)["name"]
    if name == "stats":
        break
    names.append(name)
print(",".join(names))
PY
)"
PYTHON=.venv/bin/python parity/run_python_server.sh "$work/data" "$py_port" > "$work/python.log" 2>&1 &
pid=$!
wait_up "$py_port"
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$py_port" --only "$prefix" \
  --save-vars "$work/vars.json" --out "$work/prefix.ndjson" 2> "$work/prefix.log"
kill "$pid"
wait "$pid" || true
pid=""

# 2. metadata.json into the collection of the Rust server.
.venv/bin/python server/scripts/load_v2.py "$work/data/metadata.json" "$qdrant_url" "${collection}_v2" 384

# 3. The read cases on the Rust server.
API_KEY="$key" DATA_DIR="$work/data" AUDIT_LOG=true USAGE_TRACKING=true \
  QDRANT_URL="$qdrant_url" QDRANT_COLLECTION="$collection" \
  "$rust_bin" serve --port "$rs_port" > "$work/rust.log" 2>&1 &
pid=$!
wait_up "$rs_port"
status=0
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --only "$cases" --vars-file "$work/vars.json" --out "$work/replay.ndjson"
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/replay.ndjson" || status=1

# 4. More read requests to both servers on the same data.
PYTHON=.venv/bin/python parity/run_python_server.sh "$work/data" "$py_port" >> "$work/python.log" 2>&1 &
py_pid=$!
wait_up "$py_port"
.venv/bin/python server/scripts/parity_reads_ab.py "$work/data/metadata.json" "$work/vars.json" \
  "http://127.0.0.1:$py_port" "http://127.0.0.1:$rs_port" || status=1
exit "$status"
