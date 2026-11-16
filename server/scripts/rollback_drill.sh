#!/usr/bin/env bash
# Rollback drill of task P7-1, on the seed corpus of the parity kit.
#
# Case 1 (return through the rollback image): a Python server (v5.19.0,
#   MEMORIES_PERSIST_DELAY_SEC=0) writes the seed corpus. The Rust server
#   migrates it and gets 1,000 changes and 3 extract jobs. The entrypoint of the
#   rollback image runs export-legacy and starts the Python server. The Python
#   server must hold each record of memories_v2 and no other record, answer the
#   read checks, and give the same vector search replies as the Rust server.
#   The extract jobs go to the Python server with resend_extract_jobs.py. After
#   a Python write, a restart of the rollback image must keep that write.
# Case 2 (return through an old image tag): the Rust server migrates and gets
#   200 changes. A plain Python server adds, changes and deletes records. The
#   Rust server then refuses to start, `migrate --merge` runs, and memories_v2
#   must hold the records of the two sides.
#
# Usage, from the repository root: server/scripts/rollback_drill.sh <rust binary>
# Needs: the Python environment in .venv (uv sync --frozen), QDRANT_URL (a
# Qdrant server) and ORT_DYLIB_PATH (ONNX Runtime).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
: "${QDRANT_URL:?set QDRANT_URL}"
: "${ORT_DYLIB_PATH:?set ORT_DYLIB_PATH}"
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18120
rs_port=18121
pid=""
blackhole=""
collections=()
drill() { .venv/bin/python server/scripts/rollback_drill.py "$@"; }
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  [ -n "$blackhole" ] && kill "$blackhole" 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 60 "$log"; done >&2
  fi
  for c in "${collections[@]}"; do
    curl -s -X DELETE "$QDRANT_URL/collections/$c" > /dev/null || true
    curl -s -X DELETE "$QDRANT_URL/collections/${c}_v2" > /dev/null || true
  done
  rm -rf "$work"
}
trap cleanup EXIT

# The environment of the two servers: $1 is the case name.
env_of() {
  echo API_KEY="$key" DATA_DIR="$work/$1" QDRANT_URL="$QDRANT_URL" QDRANT_COLLECTION="drill_$1_$$" \
    MEMORIES_PERSIST_DELAY_SEC=0 AUDIT_LOG=true USAGE_TRACKING=true WORKSPACE_DIR="$PWD/parity/workspace" \
    EXTRACT_PROVIDER=ollama OLLAMA_URL=http://127.0.0.1:9 MAINTENANCE_ENABLED=false \
    CLOUD_SYNC_ENABLED=false PYTHONHASHSEED=0
}
python_cmd=(.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$py_port")

wait_up() {
  for _ in $(seq 1 240); do
    curl -sf "http://127.0.0.1:$1/health" > /dev/null && return 0
    kill -0 "$pid" 2>/dev/null || { echo "The server on port $1 stopped" >&2; return 1; }
    sleep 1
  done
  echo "The server on port $1 did not start" >&2
  return 1
}
# The Rust server answers 503 until the first-start migration ends. A search
# here would write last_reinforced_at of a record, so the probe is a read.
wait_ready() {
  for _ in $(seq 1 240); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $key" "http://127.0.0.1:$rs_port/health/ready")"
    [ "$code" = 200 ] && return 0
    kill -0 "$pid" 2>/dev/null || { echo "The Rust server stopped" >&2; return 1; }
    sleep 1
  done
  echo "The Rust server is not ready" >&2
  return 1
}
stop() {
  kill "$pid"
  wait "$pid" || true
  pid=""
}
start_python() {
  # shellcheck disable=SC2046
  env $(env_of "$1") "${python_cmd[@]}" >> "$work/python-$1.log" 2>&1 &
  pid=$!
  wait_up "$py_port"
}
start_rollback_image() {
  # The entrypoint of the rollback image, with the local binaries.
  # shellcheck disable=SC2046
  env $(env_of "$1") MEMORIES_SERVER_BIN="$rust_bin" server/rollback/entrypoint.sh "${python_cmd[@]}" \
    >> "$work/rollback-$1.log" 2>&1 &
  pid=$!
  wait_up "$py_port"
}
start_rust() {
  # shellcheck disable=SC2046
  env $(env_of "$1") ${RUST_ENV:-} "$rust_bin" serve --port "$rs_port" >> "$work/rust-$1.log" 2>&1 &
  pid=$!
  wait_up "$rs_port"
  wait_ready
}
run_rust() {
  # shellcheck disable=SC2046
  env $(env_of "$1") "$rust_bin" "${@:2}"
}

echo "== Case 1: return through the rollback image"
collections+=("drill_case1_$$")
py="http://127.0.0.1:$py_port"
rs="http://127.0.0.1:$rs_port"
start_python case1
drill seed "$py"
stop
# The provider of the Rust server accepts connections and never answers, so the
# extract jobs still wait (running or queued) at the stop.
.venv/bin/python -c "import socket, time; s = socket.socket(); s.bind(('127.0.0.1', 18122)); s.listen(64); time.sleep(3600)" &
blackhole=$!
RUST_ENV="OLLAMA_URL=http://127.0.0.1:18122" start_rust case1
drill changes "$rs" 1000 1 "$work/touched1.json" "$QDRANT_URL" "drill_case1_$$_v2"
drill jobs "$rs" 3 "$work/jobs1.json"
# A search writes last_reinforced_at of its results (2 s later). The second
# capture shows the field, as the Python search after the export does.
drill search "$rs" "$work/search-rust.ndjson"
sleep 3
drill search "$rs" "$work/search-rust.ndjson"
stop
kill "$blackhole"
blackhole=""
drill collection "$QDRANT_URL" "drill_case1_$$_v2" "$work/expected1.json"
start_rollback_image case1
grep -q '"status": "exported"' "$work/rollback-case1.log"
drill check-python "$py" "$work/expected1.json"
drill search "$py" "$work/search-python.ndjson"
drill diff "$work/search-rust.ndjson" "$work/search-python.ndjson"
jobs_file="$work/case1/rollback-extract-jobs.ndjson"
[ "$(wc -l < "$jobs_file")" -eq 3 ] || { echo "want 3 extract jobs in $jobs_file" >&2; exit 1; }
MEMORIES_API_KEY="$key" .venv/bin/python server/scripts/resend_extract_jobs.py "$jobs_file" --base-url "$py"
[ "$(wc -l < "$jobs_file.sent")" -eq 3 ]
# A Python write, then a restart of the rollback image: the export must skip.
added="$(curl -sf -H "X-API-Key: $key" -H 'Content-Type: application/json' \
  -d '{"text":"Written by the Python server after the rollback.","source":"codex/drill"}' "$py/memory/add" \
  | .venv/bin/python -c 'import json, sys; print(json.load(sys.stdin)["id"])')"
stop
start_rollback_image case1
grep -q '"status": "skipped"' "$work/rollback-case1.log"
curl -sf -H "X-API-Key: $key" "$py/memory/$added" | grep -q "Written by the Python server after the rollback."
stop

echo "== Case 2: return through an old image tag, then migrate --merge"
collections+=("drill_case2_$$")
start_python case2
drill seed "$py"
stop
start_rust case2
drill changes "$rs" 200 2 "$work/touched2.json" "$QDRANT_URL" "drill_case2_$$_v2"
# Searches on the two sides write last_reinforced_at; the merge must not count
# that as a change.
drill search "$rs" "$work/search-rust2.ndjson"
sleep 3
stop
drill collection "$QDRANT_URL" "drill_case2_$$_v2" "$work/rust2.json"
start_python case2
drill python-writes "$py" "$work/touched2.json" "$work/rust2.json" "$work/python2.json"
stop
if run_rust case2 serve --port "$rs_port" > "$work/refused.log" 2>&1; then
  echo "The Rust server started on changed Python data" >&2
  exit 1
fi
grep -q "migrate --merge" "$work/refused.log"
run_rust case2 migrate --merge | tee "$work/merge.json"
grep -q '"status": "merged"' "$work/merge.json"
start_rust case2
stop
drill check-merge "$QDRANT_URL" "drill_case2_$$_v2" "$work/rust2.json" "$work/python2.json"
echo "== The rollback drill passed"
