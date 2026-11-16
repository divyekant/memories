#!/usr/bin/env bash
# Exit check of task P2-3: with 300,000 synthetic records, the memory of the
# Rust server does not increase after 1,000 list requests.
#
# Usage, from the repository root: server/scripts/list_memory_check.sh <rust binary> [count]
# Needs the Python environment in .venv and a Qdrant server at QDRANT_URL
# (default http://127.0.0.1:6333, gRPC on port 6334).
set -euo pipefail
rust_bin="$(realpath "$1")"
count="${2:-300000}"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="memory-check-key"
port=18131
qdrant_url="${QDRANT_URL:-http://127.0.0.1:6333}"
collection="list_memory_$$"
pid=""
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  curl -s -X DELETE "$qdrant_url/collections/${collection}_v2" > /dev/null || true
  if [ "$rc" -ne 0 ]; then tail -n 50 "$work/rust.log" >&2 || true; fi
  rm -rf "$work"
}
trap cleanup EXIT

.venv/bin/python server/scripts/list_memory_check.py load "$qdrant_url" "${collection}_v2" "$count" 384
API_KEY="$key" DATA_DIR="$work/data" QDRANT_URL="$qdrant_url" QDRANT_COLLECTION="$collection" \
  RUST_LOG=warn "$rust_bin" serve --port "$port" > "$work/rust.log" 2>&1 &
pid=$!
for _ in $(seq 1 600); do
  curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
  sleep 1
done
.venv/bin/python server/scripts/list_memory_check.py measure "http://127.0.0.1:$port" "$pid" "$key" "$count"
