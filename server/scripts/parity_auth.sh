#!/usr/bin/env bash
# Exit check of task P1-1: the health, ready and keys/me routes, and the wire
# rules about errors and auth, against the golden captures of the Python server.
# The Python server writes keys.db first, then the Rust server reads it.
#
# Usage, from the repository root: server/scripts/parity_auth.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18101
rs_port=18102
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true; rm -rf "$work"' EXIT

wait_up() {
  for _ in $(seq 1 180); do
    curl -sf "http://127.0.0.1:$1/health" > /dev/null && return 0
    sleep 1
  done
  echo "The server on port $1 did not start" >&2
  return 1
}

PYTHON=.venv/bin/python parity/run_python_server.sh "$work/data" "$py_port" > "$work/python.log" 2>&1 &
pids+=($!)
wait_up "$py_port"

new_key() {
  curl -sf -X POST "http://127.0.0.1:$py_port/api/keys" -H "X-API-Key: $key" \
    -H "Content-Type: application/json" -d "$1" |
    .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print(d["key"], d["id"])'
}
read -r ro_key _ < <(new_key '{"name": "parity-ro", "role": "read-only", "prefixes": ["claude-code/"]}')
read -r rw_key rw_id < <(new_key '{"name": "parity-rw", "role": "read-write", "prefixes": ["codex/"]}')
curl -sf -X DELETE "http://127.0.0.1:$py_port/api/keys/$rw_id" -H "X-API-Key: $key" > /dev/null
kill "${pids[0]}"
wait "${pids[0]}" || true

API_KEY="$key" DATA_DIR="$work/data" "$rust_bin" serve --port "$rs_port" > "$work/rust.log" 2>&1 &
pids+=($!)
wait_up "$rs_port"

cases="health-minimal,health-empty-key,health-ready,auth-wrong-key,auth-no-key,auth-empty-key,method-405,keys-me-admin,keys-me-ro,keys-revoked-401"
status=0
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --only "$cases" --var "key:ro=$ro_key" --var "key:rw=$rw_key" --out "$work/replay.ndjson"
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/replay.ndjson" || status=1
if [ "$status" -ne 0 ]; then
  tail -n 50 "$work/rust.log" >&2
fi
exit "$status"
