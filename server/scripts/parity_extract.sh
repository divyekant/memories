#!/usr/bin/env bash
# Exit check of task P4-1: the extract job routes against the golden captures
# of the Python server, with the parity settings (an Ollama provider on a
# closed port, so that each job ends with a provider failure, rule W7).
#
# EXTRACT_QUEUE_MAX=40 is the Python default for 2 workers, the value in the
# golden captures. The default of the Rust server is 10,000.
#
# Usage, from the repository root: server/scripts/parity_extract.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
port=18121
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

API_KEY="$key" DATA_DIR="$work/data" AUDIT_LOG=true EXTRACT_PROVIDER=ollama \
  OLLAMA_URL=http://127.0.0.1:9 EXTRACT_QUEUE_MAX=40 \
  "$rust_bin" serve --port "$port" > "$work/rust.log" 2>&1 &
pid=$!
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
  sleep 1
done

ro_key="$(curl -sf -X POST "http://127.0.0.1:$port/api/keys" -H "X-API-Key: $key" \
  -H "Content-Type: application/json" \
  -d '{"name": "parity-ro", "role": "read-only", "prefixes": ["claude-code/"]}' |
  .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["key"])')"

cases="extract-status,extract-202,extract-job,extract-job-404,extract-ro-403"
status=0
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$port" --allow-nonempty \
  --only "$cases" --var "key:ro=$ro_key" --out "$work/replay.ndjson"
.venv/bin/python -m parity diff --only "$cases" parity/golden/golden.ndjson "$work/replay.ndjson" || status=1
exit "$status"
