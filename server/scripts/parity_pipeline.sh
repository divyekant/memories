#!/usr/bin/env bash
# Exit check of task P4-3: the extraction pipeline with recorded LLM replies.
# The driver (parity/extract_pipeline.py) runs a fake OpenAI chat server and
# compares the prompts (byte for byte), the job results and the stored records
# with parity/golden/extract/pipeline.ndjson, the capture of Python v5.19.0.
#
# Gate G4 part a (task P4-4) uses the same script with the driver
# extract_corpus (parity/extract_corpus.py, golden file corpus.ndjson).
#
# Usage, from the repository root:
#   server/scripts/parity_pipeline.sh <rust binary>   (needs QDRANT_URL and ORT_DYLIB_PATH)
#   server/scripts/parity_pipeline.sh python          (the Python reference server)
#   server/scripts/parity_pipeline.sh python --record (write the golden file again)
#   server/scripts/parity_pipeline.sh <target> --check extract_corpus
# Needs the Python environment in .venv (uv sync --frozen).
set -euo pipefail
target="$1"
mode="${2:---check}"
driver="${3:-extract_pipeline}"
[ "$target" = python ] || target="$(realpath "$target")"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
port=18131
provider_port=18132
collection="parity_pipeline_$$"
pid=""
cleanup() {
  local rc=$?
  # Wait for the server to stop, so that the next run finds the port free.
  [ -n "$pid" ] && { kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; } || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 80 "$log"; done >&2
  fi
  if [ "$target" != python ]; then
    curl -s -X DELETE "$QDRANT_URL/collections/${collection}_v2" > /dev/null || true
  fi
  rm -rf "$work"
}
trap cleanup EXIT

export API_KEY="parity-admin-key" DATA_DIR="$work/data" AUDIT_LOG=true \
  EXTRACT_PROVIDER=openai OPENAI_API_KEY=sk-parity \
  OPENAI_BASE_URL="http://127.0.0.1:$provider_port/v1" EXTRACT_MODEL=gpt-4o-mini \
  EXTRACT_MAX_INFLIGHT=1 EXTRACT_FALLBACK_ADD=true MEMORIES_PERSIST_DELAY_SEC=0
mkdir -p "$DATA_DIR"
if [ "$target" = python ]; then
  QDRANT_URL="" WORKSPACE_DIR="$PWD/parity/workspace" MAINTENANCE_ENABLED=false \
    CLOUD_SYNC_ENABLED=false PYTHONHASHSEED=0 \
    .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$port" > "$work/server.log" 2>&1 &
else
  : "${QDRANT_URL:?set QDRANT_URL}"
  : "${ORT_DYLIB_PATH:?set ORT_DYLIB_PATH}"
  QDRANT_COLLECTION="$collection" "$target" serve --port "$port" > "$work/server.log" 2>&1 &
fi
pid=$!
for _ in $(seq 1 180); do
  curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
  sleep 1
done

.venv/bin/python -m "parity.$driver" --base-url "http://127.0.0.1:$port" \
  --provider-port "$provider_port" "$mode" "${@:4}"
