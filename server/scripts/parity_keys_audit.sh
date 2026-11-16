#!/usr/bin/env bash
# Exit check of task P5-2: the key admin and audit routes against the golden
# captures of the Python server, and keys that work across the two servers.
#
# 1. The Python server runs the golden cases before `audit-list`, except the
#    key cases. This writes the audit rows that `audit-list` expects.
# 2. The Rust server opens the same DATA_DIR and runs the key and audit cases.
# 3. A key that the Rust server made works on the Python server, a key that
#    the Rust server revoked fails there, and a key that the Python server
#    made works on the Rust server.
#
# Usage, from the repository root: server/scripts/parity_keys_audit.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18111
rs_port=18112
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
stop() {
  kill "$pid"
  wait "$pid" || true
  pid=""
}
start_python() {
  PYTHON=.venv/bin/python parity/run_python_server.sh "$work/data" "$py_port" >> "$work/python.log" 2>&1 &
  pid=$!
  wait_up "$py_port"
}
start_rust() {
  API_KEY="$key" DATA_DIR="$work/data" AUDIT_LOG=true "$rust_bin" serve --port "$rs_port" >> "$work/rust.log" 2>&1 &
  pid=$!
  wait_up "$rs_port"
}
# Prints the HTTP status of GET /api/keys/me with the key $2 on port $1.
me_status() {
  curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1/api/keys/me" -H "X-API-Key: $2"
}
# Prints "<raw key> <id>" of a new key on port $1.
new_key() {
  curl -sf -X POST "http://127.0.0.1:$1/api/keys" -H "X-API-Key: $key" \
    -H "Content-Type: application/json" -d "$2" |
    .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print(d["key"], d["id"])'
}
expect() {
  if [ "$2" != "$3" ]; then
    echo "FAIL: $1: expected $3, got $2" >&2
    exit 1
  fi
  echo "ok: $1"
}

key_cases="keys-create-ro,keys-create-rw,keys-create-422,keys-me-admin,keys-me-ro,keys-list,keys-list-ro-403,keys-update"
cases="$key_cases,audit-list,audit-filter,audit-purge,keys-revoke,keys-revoked-401"

# 1. The cases before audit-list on the Python server, with no managed key.
prefix="$(.venv/bin/python - "$key_cases" <<'PY'
import json, sys
skip = set(sys.argv[1].split(","))
names = []
for line in open("parity/golden/golden.ndjson"):
    name = json.loads(line)["name"]
    if name == "audit-list":
        break
    if name not in skip:
        names.append(name)
print(",".join(names))
PY
)"
start_python
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$py_port" --only "$prefix" \
  --var key:ro=mem_none --var key:rw=mem_none --var ro_id=none --var rw_id=none \
  --out "$work/prefix.ndjson" 2> "$work/prefix.log"
stop

# 2. The key and audit cases on the Rust server.
start_rust
status=0
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --only "$cases" --out "$work/replay.ndjson"
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/replay.ndjson" || status=1

# 3. Keys across the two servers.
read -r rs_key rs_id < <(new_key "$rs_port" '{"name": "made-by-rust", "role": "read-write", "prefixes": ["codex/"]}')
read -r rs_gone rs_gone_id < <(new_key "$rs_port" '{"name": "revoked-by-rust", "role": "read-only", "prefixes": ["wip/"]}')
curl -sf -X DELETE "http://127.0.0.1:$rs_port/api/keys/$rs_gone_id" -H "X-API-Key: $key" > /dev/null
stop
start_python
expect "a Rust key works on the Python server" "$(me_status "$py_port" "$rs_key")" 200
expect "a key that Rust revoked fails on the Python server" "$(me_status "$py_port" "$rs_gone")" 401
curl -sf "http://127.0.0.1:$py_port/api/keys/me" -H "X-API-Key: $rs_key" |
  grep -q "\"id\":\"$rs_id\"" || { echo "FAIL: the Python server shows another id" >&2; exit 1; }
read -r py_key _ < <(new_key "$py_port" '{"name": "made-by-python", "role": "read-only", "prefixes": ["learning/"]}')
stop
start_rust
expect "a Python key works on the Rust server" "$(me_status "$rs_port" "$py_key")" 200
expect "a Rust key still works on the Rust server" "$(me_status "$rs_port" "$rs_key")" 200
exit "$status"
