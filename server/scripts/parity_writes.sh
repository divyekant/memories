#!/usr/bin/env bash
# Exit check of task P3-1: the core write routes against the golden captures
# of the Python server.
#
# The Rust server gets a new, empty Qdrant collection. The replay sends each
# golden case of the write routes, in the golden order, and the two key cases
# that make the read-only and the read-write keys. No other golden case before
# delete-batch adds a record, so the record IDs are the same as in the golden
# captures. The seed links come from a route that this server does not have
# yet; no reply of these cases shows them.
#
# Usage, from the repository root: server/scripts/parity_writes.sh <rust binary>
# Needs: the Python environment in .venv (uv sync --frozen), QDRANT_URL (a
# Qdrant server) and ORT_DYLIB_PATH (ONNX Runtime).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
: "${QDRANT_URL:?set QDRANT_URL}"
: "${ORT_DYLIB_PATH:?set ORT_DYLIB_PATH}"
work="$(mktemp -d)"
port=18113
collection="parity_writes_$$"
pid=""
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 50 "$log"; done >&2
  fi
  curl -s -X DELETE "$QDRANT_URL/collections/${collection}_v2" > /dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

cases="$(.venv/bin/python - <<'PY'
import json
routes = {
    "POST /memory/add", "POST /memory/add-batch", "POST /memory/upsert",
    "POST /memory/upsert-batch", "PATCH /memory/{memory_id}",
    "DELETE /memory/{memory_id}", "POST /memory/delete-batch",
}
names = []
for line in open("parity/golden/golden.ndjson"):
    case = json.loads(line)
    if case["route"] in routes or case["name"] in ("keys-create-ro", "keys-create-rw"):
        names.append(case["name"])
print(",".join(names))
PY
)"

API_KEY="parity-admin-key" DATA_DIR="$work/data" AUDIT_LOG=true QDRANT_COLLECTION="$collection" \
  "$rust_bin" serve --port "$port" >> "$work/rust.log" 2>&1 &
pid=$!
for _ in $(seq 1 180); do
  curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
  sleep 1
done

.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$port" --allow-nonempty \
  --only "$cases" --out "$work/replay.ndjson"
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/replay.ndjson"
