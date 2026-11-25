#!/usr/bin/env bash
# Exit check of task P5-3: the maintenance jobs.
#
# 1. Live run. A new Python server (v5.19.0, MEMORIES_PERSIST_DELAY_SEC=0)
#    writes the seed corpus (scenario maintenance-seed). The script stops it,
#    sets old dates on records 1 to 90, writes extraction_profiles.json, starts
#    it again and runs the maintenance scenario. A new Rust server gets the
#    same requests, the same old dates (in Qdrant) and the same profiles. The
#    diff compares each reply: the archived, merged and deleted records must
#    be the same. Both servers get their LLM replies from parity/fake_llm.py,
#    and the script compares the prompts that the two servers sent.
# 2. Golden replay of `embedder-reload`.
# 3. Search p95 on the Rust server while maintenance jobs run. It must be
#    500 ms or less.
#
# Usage, from the repository root: server/scripts/parity_maintenance.sh <rust binary>
# Needs: the Python environment in .venv (uv sync --frozen) and QDRANT_URL (a
# Qdrant server).
set -euo pipefail
rust_bin="$(realpath "$1")"
cd "$(dirname "$0")/../.."
: "${QDRANT_URL:?set QDRANT_URL}"
work="$(mktemp -d)"
key="parity-admin-key"
py_port=18131
rs_port=18132
llm_port=18133
pid=""
llm=""
collection="parity_maint_$$"
cleanup() {
  local rc=$?
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  [ -n "$llm" ] && kill "$llm" 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    for log in "$work"/*.log; do echo "== $log"; tail -n 50 "$log"; done >&2
  fi
  curl -s -X DELETE "$QDRANT_URL/collections/${collection}_v2" > /dev/null || true
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
start_llm() {
  [ -n "$llm" ] && { kill "$llm"; wait "$llm" || true; }
  .venv/bin/python parity/fake_llm.py "$llm_port" "$1" &
  llm=$!
  sleep 1
}
start_python() {
  PYTHON=.venv/bin/python PARITY_OLLAMA_URL="http://127.0.0.1:$llm_port" \
    parity/run_python_server.sh "$work/python" "$py_port" >> "$work/python.log" 2>&1 &
  pid=$!
  wait_up "$py_port"
}
start_rust() {
  API_KEY="$key" DATA_DIR="$work/rust" AUDIT_LOG=true USAGE_TRACKING=true \
    QDRANT_COLLECTION="$collection" EXTRACT_PROVIDER=ollama OLLAMA_URL="http://127.0.0.1:$llm_port" \
    "$rust_bin" serve --port "$rs_port" >> "$work/rust.log" 2>&1 &
  pid=$!
  wait_up "$rs_port"
}

# Old dates: created and updated on 2025-01-01, for the records 1 to 90.
old_ids="$(seq -s, 1 90)"
old='{"created_at": "2025-01-01T00:00:00+00:00", "updated_at": "2025-01-01T00:00:00+00:00"}'
profiles='{
  "wip/": {"source_prefix": "wip/", "ttl_days": 30, "_explicit_fields": ["source_prefix", "ttl_days"]},
  "learning/": {"source_prefix": "learning/", "confidence_threshold": 0.5, "min_age_days": 100,
                "_explicit_fields": ["confidence_threshold", "min_age_days", "source_prefix"]}
}'

echo "== 1. Live run of the maintenance scenario: Python, then Rust"
start_llm "$work/llm-python.ndjson"
start_python
.venv/bin/python -m parity capture --scenario maintenance-seed --base-url "http://127.0.0.1:$py_port" \
  --out "$work/python-seed.ndjson"
stop
.venv/bin/python - "$work/python/metadata.json" "$old_ids" "$old" <<'PY'
import json, sys
path, ids, fields = sys.argv[1], {int(i) for i in sys.argv[2].split(",")}, json.loads(sys.argv[3])
meta = json.load(open(path))
for m in meta:
    if m and m["id"] in ids:
        m.update(fields)
json.dump(meta, open(path, "w"))
PY
echo "$profiles" > "$work/python/extraction_profiles.json"
start_python
.venv/bin/python -m parity capture --scenario maintenance --allow-nonempty \
  --base-url "http://127.0.0.1:$py_port" --out "$work/python.ndjson"
stop

start_llm "$work/llm-rust.ndjson"
mkdir -p "$work/rust"
echo "$profiles" > "$work/rust/extraction_profiles.json"
start_rust
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --input "$work/python-seed.ndjson" --out "$work/rust-seed.ndjson"
curl -sf -X POST "$QDRANT_URL/collections/${collection}_v2/points/payload?wait=true" \
  -H "Content-Type: application/json" -d "{\"payload\": $old, \"points\": [$old_ids]}" > /dev/null
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --input "$work/python.ndjson" --out "$work/rust.ndjson"
# What the jobs changed in the Python run (the diff checks that Rust did the same).
.venv/bin/python - "$work/python.ndjson" <<'PY'
import json, sys
r = {c["name"]: c["response"]["body"] for c in map(json.loads, open(sys.argv[1]))}
res = r["consolidate"]["results"]
print(f"enforce archived {r['enforce']['summary']['archived']}, pruned {r['prune']['pruned']}, "
      f"consolidated {sum(1 for x in res if x['merged_count'])} of {len(res)} clusters "
      f"({sum(1 for x in res if 'error' in x)} with an LLM reply that is not JSON), "
      f"records {len(r['inv-0']['would_delete'])} -> {len(r['inv-3']['would_delete'])}")
PY
status=0
.venv/bin/python -m parity diff "$work/python-seed.ndjson" "$work/rust-seed.ndjson" || status=1
.venv/bin/python -m parity diff "$work/python.ndjson" "$work/rust.ndjson" || status=1
if cmp -s "$work/llm-python.ndjson" "$work/llm-rust.ndjson"; then
  echo "LLM prompts: same ($(wc -l < "$work/llm-rust.ndjson") prompts)"
else
  echo "LLM prompts: different" >&2
  diff "$work/llm-python.ndjson" "$work/llm-rust.ndjson" | head -n 20 >&2 || true
  status=1
fi

echo "== 2. Golden replay of embedder-reload"
.venv/bin/python -m parity replay --base-url "http://127.0.0.1:$rs_port" --allow-nonempty \
  --only embedder-reload --out "$work/golden-replay.ndjson"
.venv/bin/python -m parity diff --only embedder-reload parity/golden/golden.ndjson \
  "$work/golden-replay.ndjson" || status=1

echo "== 3. Search p95 while maintenance jobs run"
.venv/bin/python - "$rs_port" "$key" <<'PY' || status=1
import json, sys, threading, time, urllib.request
port, key = sys.argv[1], sys.argv[2]
def call(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"X-API-Key": key, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=300).read()
jobs = [("/maintenance/reembed", {}), ("/maintenance/compact", {"threshold": 0.85}),
        ("/maintenance/consolidate?dry_run=true", {}), ("/maintenance/enforce-policies", {}),
        ("/maintenance/prune", {})]
done = threading.Event()
errors = []
def run_jobs():
    try:
        for _ in range(3):
            for path, body in jobs:
                call(path, body)
    except Exception as e:  # stop the search loop on a failed job
        errors.append(f"{path}: {e}")
    finally:
        done.set()
queries = ["How is the fleet-api rate limit configured?", "release", "incident root cause",
           "design decision", "database migration"]
for i in range(10):
    call("/search", {"query": queries[i % 5], "k": 10, "hybrid": False})
t = threading.Thread(target=run_jobs)
t.start()
times = []
while not done.is_set():
    s = time.perf_counter()
    call("/search", {"query": queries[len(times) % 5], "k": 10, "hybrid": False})
    times.append((time.perf_counter() - s) * 1000)
t.join()
if errors or not times:
    print(f"maintenance job failed: {errors or 'no searches ran'}", file=sys.stderr)
    sys.exit(1)
times.sort()
p95 = times[int(len(times) * 0.95) - 1] if len(times) >= 20 else times[-1]
print(f"search during jobs: {len(times)} searches, p50 {times[len(times) // 2]:.1f} ms, "
      f"p95 {p95:.1f} ms, max {times[-1]:.1f} ms (target: p95 500 ms or less)")
sys.exit(0 if p95 <= 500 else 1)
PY
stop
exit "$status"
