#!/usr/bin/env bash
# Gate G5 of task P6-3, synthetic part: the load test of the Rust server on a
# synthetic corpus. Results: docs/rust-rewrite/gates/G5.md.
#
# 1. parity/g5.py writes a metadata.json of G5_COUNT records, and the old
#    Python collection g5 with a vector for each record.
# 2. `memories-server migrate` copies the records and vectors to g5_v2, as in
#    production (the embedding signature is the same).
# 3. The embed path: a second corpus of G5_EMBED_COUNT records with no old
#    collection, so the migration embeds each text in batches of 64. This
#    step measures the bulk peak of the embedder.
# 4. The server runs with an Ollama stub as the LLM. benchmarks/load_test.py
#    runs search, add, burst and extract for G5_SHORT seconds each. Then
#    /memory/add-batch gets 5 batches of 64 long texts, and mixed runs for
#    G5_MIXED seconds.
# 5. Forced stop: 200 extract jobs wait in the queue during a mixed load, then
#    kill -9. After the restart, each job must complete.
# 6. `memories-server verify` after the stop: no record of the corpus is
#    missing or changed. Records that the load added count as extra.
# Each process has an RSS sample (rss-*.json, MB).
#
# Usage, from the repository root:
#   QDRANT_URL=http://127.0.0.1:6333 ORT_DYLIB_PATH=... \
#     server/scripts/gate_g5.sh <rust binary> <out dir>
# G5_COUNT (default 1000000), G5_EMBED_COUNT (default 10000), G5_SHORT
# (default 60), G5_MIXED (default 1800), G5_CONC (workers, default 5),
# G5_PORT (default 18151; the stub uses the next port).
# Needs .venv (uv sync --frozen). The script stops if one of the collections
# g5, g5_v2, g5e or g5e_v2 exists, and deletes them at the end.
#
# Copied data in place of the synthetic corpus (production data, task P6-3):
#   G5_DATA=<dir with metadata.json and config.json> G5_COLLECTION=<old collection>
# The old collection must exist in QDRANT_URL, and config.json must name it in
# qdrant_collection. The script copies the directory, and deletes the old
# collection and <old collection>_v2 at the end. G5_EMBED_COUNT=0 skips step 3.
set -euo pipefail
rust_bin="$(realpath "$1")"
out="$(realpath -m "$2")"
: "${QDRANT_URL:?set QDRANT_URL}"
cd "$(dirname "$0")/../.."
count="${G5_COUNT:-1000000}"
embed_count="${G5_EMBED_COUNT:-10000}"
short="${G5_SHORT:-60}"
mixed="${G5_MIXED:-1800}"
conc="${G5_CONC:-5}"
port="${G5_PORT:-18151}"
llm_port=$((port + 1))
url="http://127.0.0.1:$port"
key="g5-admin-key"
py=.venv/bin/python
src="${G5_DATA:-}"
coll="${G5_COLLECTION:-g5}"
collections="$coll ${coll}_v2 g5e g5e_v2"
work="$(mktemp -d)"
mkdir -p "$out"
pids=()
srv=""
own=""
cleanup() {
  local rc=$?
  for p in "${pids[@]}" $srv; do kill "$p" 2>/dev/null || true; done
  if [ -n "$own" ]; then
    for c in $collections; do curl -s -X DELETE "$QDRANT_URL/collections/$c" > /dev/null || true; done
  fi
  if [ "$rc" -ne 0 ]; then
    for log in "$out"/*.log; do echo "== $log"; tail -n 30 "$log"; done >&2
  fi
  rm -rf "$work"
}
trap cleanup EXIT

for c in $collections; do
  [ -n "$src" ] && [ "$c" = "$coll" ] && continue
  if curl -sf "$QDRANT_URL/collections/$c" > /dev/null; then
    echo "Collection $c exists. Delete it first." >&2
    exit 1
  fi
done
own=1
export API_KEY="$key" QDRANT_URL EXTRACT_PROVIDER=ollama OLLAMA_URL="http://127.0.0.1:$llm_port"
# Keep each finished job, so that the job check can read all of them.
export EXTRACT_JOBS_MAX=100000

step() { echo "$*" | tee -a "$out/steps.txt"; }

# run_sampled <name> <cmd...>: the command with an RSS sample. The JSON report
# goes to <name>.json, the log to <name>.log.
run_sampled() {
  local name="$1"; shift
  local t0=$SECONDS rc=0
  "$@" > "$work/$name.out" 2>&1 &
  local p=$!
  $py -m parity.g5 rss "$p" "$out/rss-$name.json" &
  local s=$!
  wait "$p" || rc=$?
  wait "$s" || true
  sed -n '/^{/,$p' "$work/$name.out" > "$out/$name.json"
  sed '/^{/,$d' "$work/$name.out" > "$out/$name.log"
  step "$name: exit $rc, $((SECONDS - t0)) s"
  return "$rc"
}

start_server() {
  "$rust_bin" serve --port "$port" >> "$out/serve-$1.log" 2>&1 &
  srv=$!
  $py -m parity.g5 rss "$srv" "$out/rss-serve-$1.json" &
  pids+=($!)
  local t0=$SECONDS
  for _ in $(seq 1 900); do
    if curl -sf "$url/health" > /dev/null; then
      step "serve-$1: up in $((SECONDS - t0)) s"
      return 0
    fi
    sleep 1
  done
  echo "The server did not start" >&2
  return 1
}

stop_server() {
  kill "$srv" 2>/dev/null || true
  wait "$srv" 2>/dev/null || true
  srv=""
}

load() {
  $py benchmarks/load_test.py --url "$url" --key "$key" --scenario "$1" \
    --concurrency "$conc" --duration "$2" --json | sed -n '/^{/,$p' > "$out/load-$3.json"
  step "load $3: $1 for $2 s"
}

t0=$SECONDS
: > "$out/steps.txt"
# The embed path first: a small corpus with no old collection.
if [ "$embed_count" -gt 0 ]; then
  $py -m parity.g5 corpus "$work/embed" --count "$embed_count"
  DATA_DIR="$work/embed" QDRANT_COLLECTION=g5e run_sampled migrate-embed "$rust_bin" migrate --sample 200
  curl -s -X DELETE "$QDRANT_URL/collections/g5e_v2" > /dev/null
  rm -rf "$work/embed"
fi

export DATA_DIR="$work/data" QDRANT_COLLECTION="$coll"
t=$SECONDS
if [ -n "$src" ]; then
  cp -r "$src" "$DATA_DIR"
else
  $py -m parity.g5 corpus "$DATA_DIR" --count "$count" --qdrant-url "$QDRANT_URL" --collection "$coll"
fi
step "data: $($py -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' "$DATA_DIR/metadata.json") records, $(du -m "$DATA_DIR/metadata.json" | cut -f1) MB metadata.json, $((SECONDS - t)) s"
run_sampled migrate "$rust_bin" migrate --sample 1000

$py -m parity.g5 fake-llm "$llm_port" &
pids+=($!)
start_server 1
for s in search add burst extract; do load "$s" "$short" "$s"; done
# The bulk peak in the server: batches of 64 texts over the 256-token cut.
$py - "$url" "$key" > "$out/add-batch.json" <<'EOF'
import json, sys, time, httpx
url, key = sys.argv[1], sys.argv[2]
words = "deploy server cache token release branch review client query index vector backup".split()
lat = []
with httpx.Client(timeout=300) as c:
    for b in range(5):
        items = [{"text": f"g5 batch {b} item {i} " + " ".join(words[(i + j) % len(words)] for j in range(400)),
                  "source": "g5/batch"} for i in range(64)]
        t = time.perf_counter()
        r = c.post(f"{url}/memory/add-batch", headers={"X-API-Key": key}, json={"memories": items})
        lat.append(round((time.perf_counter() - t) * 1000, 1))
        r.raise_for_status()
print(json.dumps({"batches": len(lat), "latency_ms": lat}))
EOF
step "add-batch: 5 batches of 64 long texts"
load mixed "$mixed" mixed
stop_server

# Forced stop with jobs in the queue.
start_server 2
load mixed 60 crash-mixed &
lp=$!
$py -m parity.g5 jobs-submit "$url" "$key" 200 "$work/jobs.txt" > "$out/jobs-submit.json"
queue="$(curl -s "$url/extract/status" -H "X-API-Key: $key")"
kill -9 "$srv"
srv=""
step "kill -9 with this queue: $queue"
wait "$lp" || true
start_server 3
rc=0
$py -m parity.g5 jobs-check "$url" "$key" "$work/jobs.txt" --timeout 1800 > "$out/jobs-check.json" || rc=1
step "jobs-check: $(cat "$out/jobs-check.json")"
stop_server
run_sampled verify "$rust_bin" verify --sample 1000 || true
# Search hits get last_reinforced_at (as in Python), so verify lists them as
# field mismatches. The check reads the listed records and allows only that field.
$py - "$out/verify.json" "$DATA_DIR/metadata.json" "$QDRANT_URL/collections/${coll}_v2" <<'EOF' | tee -a "$out/steps.txt" || rc=1
import json, sys, httpx
v = json.load(open(sys.argv[1]))
ids = set(v.get("field_mismatch_ids") or [])
legacy = {r["id"]: r for r in json.load(open(sys.argv[2])) if r["id"] in ids}
pts = httpx.post(f"{sys.argv[3]}/points", json={"ids": sorted(ids), "with_payload": True}).json()["result"]
fields = set()
for p in pts:
    old, new = legacy[p["id"]], dict(p["payload"], id=p["id"])
    fields |= {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
bad = {k: v[k] for k in ("missing_count", "vector_mismatch_count") if v.get(k)}
if fields - {"last_reinforced_at"}:
    bad["changed_fields"] = sorted(fields)
print(json.dumps({"verify_ok": v.get("ok"), "extra": v.get("extra_count"),
                  "field_mismatch": v.get("field_mismatch_count"), "fields_in_listed": sorted(fields), "problems": bad}))
sys.exit(1 if bad else 0)
EOF
step "total: $((SECONDS - t0)) s, rc $rc"
exit "$rc"
