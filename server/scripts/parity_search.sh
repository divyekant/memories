#!/usr/bin/env bash
# Exit check of task P2-2: the five search routes against the golden captures
# of the Python server, with hybrid set to false, and the search p95.
#
# 1. The Python server runs the golden cases before `usage-headers-search`,
#    and `usage-headers-add`. This writes the seed corpus to metadata.json.
# 2. The test loader copies metadata.json into Qdrant (dense vectors from the
#    Rust embedder, and keyword vectors).
# 3. The Rust server opens the same DATA_DIR and Qdrant, and replays
#    parity/golden/search/routes.ndjson.
# 4. The script prints the p95 of POST /search on the seed corpus.
#
# Usage, from the repository root:
#   QDRANT_URL=http://127.0.0.1:6333 server/scripts/parity_search.sh <rust binary> <load_metadata binary>
# Needs the Python environment in .venv (uv sync --frozen), a Qdrant server
# and ORT_DYLIB_PATH. The loader deletes the collection memories_v2 first.
set -euo pipefail
rust_bin="$(realpath "$1")"
loader_bin="$(realpath "$2")"
: "${QDRANT_URL:?set QDRANT_URL}"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18121
rs_port=18122
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
  for _ in $(seq 1 300); do
    curl -sf "http://127.0.0.1:$1/health" > /dev/null && return 0
    sleep 1
  done
  echo "The server on port $1 did not start" >&2
  return 1
}

# 1. The seed corpus on the Python server.
prefix="$(.venv/bin/python - <<'PY'
import json
names = []
for line in open("parity/golden/golden.ndjson"):
    name = json.loads(line)["name"]
    if name == "usage-headers-search":
        break
    names.append(name)
print(",".join(names + ["usage-headers-add"]))
PY
)"
PYTHON=.venv/bin/python parity/run_python_server.sh "$work/data" "$py_port" >> "$work/python.log" 2>&1 &
pid=$!
wait_up "$py_port"
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$py_port" --only "$prefix" \
  --out "$work/prefix.ndjson" 2> "$work/prefix.log"
kill "$pid"
wait "$pid" || true
pid=""

# 2. The records into Qdrant.
"$loader_bin" "$work/data/metadata.json" > "$work/loader.log" 2>&1

# 3. The search cases on the Rust server.
API_KEY="$key" DATA_DIR="$work/data" "$rust_bin" serve --port "$rs_port" >> "$work/rust.log" 2>&1 &
pid=$!
wait_up "$rs_port"
status=0
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --input parity/golden/search/routes.ndjson --out "$work/replay.ndjson"
.venv/bin/python -m parity diff parity/golden/search/routes.ndjson "$work/replay.ndjson" || status=1

# 4. The search p95 (200 searches, k=10, hybrid false, after 20 to warm up).
.venv/bin/python - "$rs_port" "$key" <<'PY'
import json, sys, time, urllib.request
port, key = sys.argv[1], sys.argv[2]
queries = ["How is the fleet-api rate limit configured?", "release", "incident root cause",
           "design decision", "database migration"]
def one(q):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/search", method="POST",
        data=json.dumps({"query": q, "k": 10, "hybrid": False}).encode(),
        headers={"X-API-Key": key, "Content-Type": "application/json"})
    t = time.perf_counter()
    urllib.request.urlopen(req).read()
    return (time.perf_counter() - t) * 1000
for i in range(20):
    one(queries[i % len(queries)])
times = sorted(one(queries[i % len(queries)]) for i in range(200))
print(f"search p50 {times[99]:.1f} ms, p95 {times[189]:.1f} ms, max {times[-1]:.1f} ms (200 searches)")
PY
exit "$status"
