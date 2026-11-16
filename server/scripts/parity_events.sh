#!/usr/bin/env bash
# Exit check of task P5-5: the event, webhook and web UI routes against the
# golden captures of the Python server, then a browser check of each page of
# the web UI on the Rust server.
#
# The event history is in the memory of the server process, so the Rust server
# itself must do the writes before GET /events/recent. The script starts the
# Rust server on an empty DATA_DIR and replays the golden writes of the seed
# corpus and the cases that save the read-only key. The seed link cases are
# left out: they are not in the last 20 events of the golden case.
# Then it replays the cases of this task and compares them with the golden
# file. The browser check (server/scripts/webui_check.cjs) uses the same
# server and data.
#
# Usage, from the repository root:
#   QDRANT_URL=http://127.0.0.1:6333 server/scripts/parity_events.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen), a Qdrant server,
# ORT_DYLIB_PATH, and the `playwright` Node package with its Chromium
# (NODE_PATH). The script uses a new collection and deletes it at the end.
# Set KEEP_WORK=<dir> (a new directory) to keep the replies and the logs.
set -euo pipefail
rust_bin="$(realpath "$1")"
: "${QDRANT_URL:?set QDRANT_URL}"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
key="parity-admin-key"
port=18141
pid=""
export QDRANT_COLLECTION="parity_events_$$"
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  curl -s -X DELETE "$QDRANT_URL/collections/${QDRANT_COLLECTION}_v2" > /dev/null || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 50 "$log"; done >&2
  fi
  if [ -n "${KEEP_WORK:-}" ]; then
    if [ -e "$KEEP_WORK" ]; then echo "KEEP_WORK=$KEEP_WORK exists; the run is not kept" >&2
    else cp -r "$work" "$KEEP_WORK"; fi
  fi
  rm -rf "$work"
}
trap cleanup EXIT

API_KEY="$key" DATA_DIR="$work/data" UI_DIR="$PWD/webui" \
  "$rust_bin" serve --port "$port" >> "$work/rust.log" 2>&1 &
pid=$!
for _ in $(seq 1 300); do
  curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
  sleep 1
done

mine="events-recent,events-stream,webhooks-list,webhooks-list-ro-403,webhook-create,webhook-delete,ui-page,ui-static"
.venv/bin/python - "$work/out.ndjson" "$port" "$mine" <<'PY' 2>> "$work/replay.log"
import os, sys
sys.path.insert(0, os.getcwd())
from parity import kit
out_path, port, mine = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
cases = kit.load(kit.GOLDEN_PATH)
names = [c["name"] for c in cases]
seed = names[names.index("seed-add-batch-0"):names.index("seed-conflict-5") + 1]
writes = [n for n in seed if not n.startswith("seed-link-")]
writes += ["keys-create-ro", "keys-create-rw", "usage-headers-add"]
out = kit.run(kit.select(cases, writes + mine), f"http://127.0.0.1:{port}",
              admin_key="parity-admin-key", allow_nonempty=True,
              log=lambda m: print(m, file=sys.stderr))
bad = [r["name"] for r in out if r["name"] in writes and r["response"]["status"] != 200]
if bad:
    sys.exit(f"the setup cases failed: {bad}")
kit.dump(out, out_path)
PY

status=0
.venv/bin/python -m parity diff --only "$mine" parity/golden/golden.ndjson "$work/out.ndjson" || status=1
node server/scripts/webui_check.cjs "http://127.0.0.1:$port" "$key" || status=1
exit "$status"
