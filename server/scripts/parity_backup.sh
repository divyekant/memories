#!/usr/bin/env bash
# Exit check of task P5-4: backup, export and import against the Python
# reference server (v5.19.0, MEMORIES_PERSIST_DELAY_SEC=0).
#
# 1. The golden cases of these routes that do not need the full scenario
#    (an empty server, the error replies) replay against a new Rust server.
# 2. An export from the Python server (golden seed corpus, with links)
#    imports into the Rust server, and verify passes (compare_exports.py).
# 3. The opposite direction: the Rust export imports into a new Python
#    server, and verify passes.
# 4. The same import body with a scoped key gives the same reply on the two
#    servers (line errors, key errors, counts).
# 5. A backup and a restore on the Rust server: the counts are equal, the
#    links are there, and a record added after the backup is gone.
#
# Usage, from the repository root: server/scripts/parity_backup.sh <rust binary>
# Needs: the Python environment in .venv (uv sync --frozen) and QDRANT_URL (a
# Qdrant server).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
: "${QDRANT_URL:?set QDRANT_URL}"
work="$(mktemp -d)"
key="parity-admin-key"
py_a=18131; py_b=18132; rs=18133; rs_golden=18134
collection="parity_backup_$$"
pids=()
cleanup() {
  local rc=$?
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  # Wait, so that the next script cannot get /health from a server that stops.
  for p in "${pids[@]}"; do wait "$p" 2>/dev/null || true; done
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 40 "$log"; done >&2
  fi
  for c in "$collection" "${collection}_golden"; do
    curl -s -X DELETE "$QDRANT_URL/collections/${c}_v2" > /dev/null || true
  done
  rm -rf "$work"
}
trap cleanup EXIT

wait_up() {
  for _ in $(seq 1 240); do
    curl -sf "http://127.0.0.1:$1/health" > /dev/null && return 0
    sleep 1
  done
  echo "server on port $1 did not start" >&2
  return 1
}
start_python() {
  parity/run_python_server.sh "$work/$2" "$1" >> "$work/$2.log" 2>&1 &
  pids+=($!)
  wait_up "$1"
}
start_rust() {
  API_KEY="$key" DATA_DIR="$work/$2" AUDIT_LOG=true QDRANT_COLLECTION="$3" \
    "$rust_bin" serve --port "$1" >> "$work/$2.log" 2>&1 &
  pids+=($!)
  wait_up "$1"
}
get() { curl -sSf -H "X-API-Key: ${3:-$key}" "http://127.0.0.1:$1$2"; }
post_file() {
  curl -sS -H "X-API-Key: ${4:-$key}" -H "Content-Type: application/x-ndjson" \
    --data-binary "@$3" "http://127.0.0.1:$1$2"
}
post_json() {
  curl -sSf -H "X-API-Key: $key" -H "Content-Type: application/json" -d "$3" "http://127.0.0.1:$1$2"
}
field() { .venv/bin/python -c "import json,sys; print(json.load(sys.stdin)$1)"; }

echo "== 1. golden cases on a new Rust server"
start_rust "$rs_golden" rust_golden "${collection}_golden"
cases="backups,snapshots,import,import-bad-strategy,restore-missing,snapshot-restore-missing"
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_golden" --allow-nonempty \
  --only "$cases" --out "$work/replay.ndjson"
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/replay.ndjson"

echo "== 2. Python export into Rust"
start_python "$py_a" python_a
seed="$(.venv/bin/python -c "
import json
print(','.join(c['name'] for c in map(json.loads, open('parity/golden/golden.ndjson')) if c['name'].startswith('seed')))")"
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$py_a" --only "$seed" \
  --out "$work/seed.ndjson" > /dev/null
get "$py_a" /export > "$work/a.ndjson"
start_rust "$rs" rust "$collection"
reply="$(post_file "$rs" "/import?no_backup=true" "$work/a.ndjson")"
echo "$reply"
[ "$(echo "$reply" | field '["errors"]')" = "[]" ]
get "$rs" /export > "$work/r.ndjson"
.venv/bin/python server/scripts/compare_exports.py "$work/a.ndjson" "$work/r.ndjson"

echo "== 3. Rust export into Python"
start_python "$py_b" python_b
post_file "$py_b" "/import?no_backup=true" "$work/r.ndjson"; echo
get "$py_b" /export > "$work/b.ndjson"
.venv/bin/python server/scripts/compare_exports.py "$work/r.ndjson" "$work/b.ndjson"

echo "== 4. one import body with a scoped key on the two servers"
body='{"name":"rw","role":"read-write","prefixes":["learning/"]}'
py_rw="$(post_json "$py_b" /api/keys "$body" | field '["key"]')"
rs_rw="$(post_json "$rs" /api/keys "$body" | field '["key"]')"
printf '%s\n' '{"_header": 1}' \
  '{"text": "Remapped one.", "source": "codex/p54"}' \
  '{"text": "Out of scope.", "source": "claude-code/p54"}' \
  '{"source": "learning/no-text"}' 'not json' \
  '{"text": "Custom.", "source": "learning/p54", "custom_fields": {"k": [1, {"x": "é"}]}}' \
  > "$work/body.ndjson"
q="/import?strategy=add&source_remap=codex/%3Dlearning/"
post_file "$py_b" "$q" "$work/body.ndjson" "$py_rw" | field '' > "$work/py_reply.txt"
post_file "$rs" "$q" "$work/body.ndjson" "$rs_rw" | field '' > "$work/rs_reply.txt"
.venv/bin/python - "$work/py_reply.txt" "$work/rs_reply.txt" <<'PY'
import ast, sys
a, b = (ast.literal_eval(open(p).read()) for p in sys.argv[1:])
for r in (a, b):
    assert r["backup"], r
    r["backup"] = r["backup"].split("_")[0]
assert a == b, (a, b)
print("same import reply:", a)
PY

echo "== 5. backup and restore on the Rust server"
get "$rs" /export > "$work/before.ndjson"
name="$(post_json "$rs" "/backup?prefix=exit" '{}' | field '["backup_path"]' | xargs basename)"
post_json "$rs" /memory/add '{"text": "Added after the backup.", "source": "learning/p54-late"}' > /dev/null
post_json "$rs" /restore "{\"backup_name\": \"$name\"}"; echo
get "$rs" /export > "$work/after.ndjson"
.venv/bin/python server/scripts/compare_exports.py "$work/before.ndjson" "$work/after.ndjson" --exact
echo "P5-4 exit check passed"
