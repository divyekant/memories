#!/usr/bin/env bash
# Exit check of task P5-1: the usage, metrics and feedback routes against the
# golden captures of the Python server, on one usage.db that the two servers
# share.
#
# The golden scenario runs in stints on one DATA_DIR. The Python server runs
# the cases of the routes that the Rust server does not serve yet, and writes
# usage.db. The Rust server runs the cases of this task, and reads and writes
# the same usage.db:
#
# 1. Python: the cases before usage-session.  Then `memories-server migrate`.
# 2. Rust:   usage-session, usage-7d (reads of the rows that Python wrote).
# 3. Python: stats to link-remove.
# 4. Rust:   the four feedback cases (writes, a read and a delete).
# 5. Python: missed to extract-status.
# 6. Rust:   the nine metrics and usage cases after `metrics`, and the key
#            set and types of `GET /metrics` against the golden case.
# 7. Rust writes a search with usage headers. Python reads the rows.
#
# The Rust server serves <QDRANT_COLLECTION>_v2 from the migration of step 1.
# Python changes metadata.json later, so each Rust stint moves metadata.json
# away while it runs (the Rust server refuses a changed metadata.json).
#
# Usage, from the repository root:
#   QDRANT_URL=http://127.0.0.1:6333 server/scripts/parity_usage.sh <rust binary>
# Needs the Python environment in .venv (uv sync --frozen), a Qdrant server
# and ORT_DYLIB_PATH. The Rust server uses a new collection, which the script
# deletes at the end. Set KEEP_WORK=<dir> (a new directory) to keep the
# replies, the logs and usage.db after the run.
set -euo pipefail
rust_bin="$(realpath "$1")"
: "${QDRANT_URL:?set QDRANT_URL}"
cd "$(dirname "$0")/../.."
work="$(mktemp -d)"
data="$work/data"
key="parity-admin-key"
py_port=18131
rs_port=18132
pid=""
export QDRANT_COLLECTION="parity_usage_$$"
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

wait_up() {
  for _ in $(seq 1 300); do
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
  [ -f "$data/metadata.json.py" ] && mv "$data/metadata.json.py" "$data/metadata.json"
  PYTHON=.venv/bin/python parity/run_python_server.sh "$data" "$py_port" >> "$work/python.log" 2>&1 &
  pid=$!
  wait_up "$py_port"
}
start_rust() {
  [ -f "$data/metadata.json" ] && [ -f "$data/migrated" ] && mv "$data/metadata.json" "$data/metadata.json.py"
  API_KEY="$key" DATA_DIR="$data" USAGE_TRACKING=true AUDIT_LOG=true \
    "$rust_bin" serve --port "$rs_port" >> "$work/rust.log" 2>&1 &
  pid=$!
  wait_up "$rs_port"
}

# The golden cases from FIRST to LAST (both included), comma-separated.
cases_between() {
  .venv/bin/python - "$1" "$2" <<'PY'
import json, sys
names = [json.loads(l)["name"] for l in open("parity/golden/golden.ndjson")]
print(",".join(names[names.index(sys.argv[1]):names.index(sys.argv[2]) + 1]))
PY
}

# Runs stint $1 (a number, for the file order) on port $2 with the golden
# cases $3. The replay reply is normalized (keys become <hex>), so the driver
# keeps the raw values of the saved variables in $work/vars.json for the next
# stints.
run_stint() {
  .venv/bin/python - "$work" "$1" "$2" "$3" <<'PY' 2>> "$work/replay.log"
import json, os, sys
sys.path.insert(0, os.getcwd())
from parity import kit
work, stint, port, names = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(",")
path = os.path.join(work, "vars.json")
vars_ = json.load(open(path)) if os.path.exists(path) else {}
send = kit._run_case


def run_case(client, case, v, admin_key):
    resp, elapsed = send(client, case, v, admin_key)
    for name, dotted in (case.get("save") or {}).items():
        try:
            vars_[name] = kit.get_path(resp["body"], dotted)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            pass
    return resp, elapsed


kit._run_case = run_case
cases = kit.select(kit.load(kit.GOLDEN_PATH), names)
out = kit.run(cases, f"http://127.0.0.1:{port}", admin_key="parity-admin-key", allow_nonempty=True,
              log=lambda m: print(m, file=sys.stderr), extra_vars=dict(vars_))
kit.dump(out, os.path.join(work, f"{stint}.out.ndjson"))
json.dump(vars_, open(path, "w"))
PY
}

status=0
check() {
  .venv/bin/python -m parity diff --only "$2" parity/golden/golden.ndjson "$work/$1.out.ndjson" || status=1
}

# 1. Python, then the migration.
start_python
run_stint 1 "$py_port" "$(cases_between health-minimal usage-headers-add)"
stop
DATA_DIR="$data" "$rust_bin" migrate > "$work/migrate.log" 2>&1
touch "$data/migrated"

# 2. Rust: usage reads.
start_rust
c2="usage-session,usage-7d"
run_stint 2 "$rs_port" "$c2"
stop
check 2 "$c2"

# 3. Python.
start_python
run_stint 3 "$py_port" "$(cases_between stats link-remove)"
stop

# 4. Rust: feedback.
start_rust
c4="$(cases_between feedback-useful feedback-retract)"
run_stint 4 "$rs_port" "$c4"
stop
check 4 "$c4"

# 5. Python.
start_python
run_stint 5 "$py_port" "$(cases_between missed extract-status)"
stop

# 6. Rust: metrics and usage.
start_rust
c6="$(cases_between metrics-search-quality usage-final)"
run_stint 6 "$rs_port" "$c6"
check 6 "$c6"
curl -sf "http://127.0.0.1:$rs_port/metrics" -H "X-API-Key: $key" > "$work/metrics.json"
.venv/bin/python - "$work/metrics.json" <<'PY' || status=1
import json, sys
golden = next(json.loads(l) for l in open("parity/golden/golden.ndjson") if json.loads(l)["name"] == "metrics")
want = golden["response"]["body"]
got = json.load(open(sys.argv[1]))
# The promotion part (task P5-6) has no volatile field: it must be equal.
if got.get("promotion") != want["promotion"]:
    print(f"FAIL: GET /metrics promotion\n  python {want['promotion']}\n  rust   {got.get('promotion')}", file=sys.stderr)
    sys.exit(1)

def shape(v, path, skip_keys):
    if isinstance(v, dict):
        if path in skip_keys:
            return "dict"
        return {k: shape(x, f"{path}.{k}", skip_keys) for k, x in v.items()}
    if isinstance(v, str) and v.startswith("<volatile:"):
        return v[len("<volatile:"):-1]
    if isinstance(v, list):
        return "list"
    return type(v).__name__

# routes and decision_reasons hold one key per route or reason seen.
skip = {"$.routes", "$.embedder_reload.auto.decision_reasons"}
a, b = shape(want, "$", skip), shape(got, "$", skip)
# Python sets the manual reload fields after a reload; the Rust server has none.
for k in ("last_completed_at", "last_gc_collected", "last_reload_duration_ms",
          "last_requested_at", "last_trim_reason"):
    a["embedder_reload"]["manual"][k] = b["embedder_reload"]["manual"][k]
a["embedder_reload"]["auto"]["last_reload_duration_ms"] = "float"
a["embedder_reload"]["auto"]["last_rss_kb"] = "int"
if a != b:
    print(f"FAIL: GET /metrics shape\n  python {a}\n  rust   {b}", file=sys.stderr)
    sys.exit(1)
print("ok: GET /metrics has the key set and types of the Python body")
PY

# 7. A Rust search, add, add-batch, delete and delete-batch with usage
# headers; Python reads the rows.
rs() {
  curl -sf -X "$1" "http://127.0.0.1:$rs_port$2" -H "X-API-Key: $key" -H "Content-Type: application/json" \
    -H "X-Memories-Client: rust-check" -H "X-Memories-Session-Id: rust-session" ${3:+-d "$3"}
}
rs POST /search '{"query": "fleet-api rate limit", "k": 3, "hybrid": false}' > /dev/null
one="$(rs POST /memory/add '{"text": "Rust usage check one.", "source": "rust/usage"}' | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
two="$(rs POST /memory/add-batch '{"memories": [{"text": "Rust usage check two.", "source": "rust/usage"}, {"text": "Rust usage check three.", "source": "rust/usage"}]}' |
  .venv/bin/python -c 'import json,sys; print(",".join(map(str, json.load(sys.stdin)["ids"])))')"
rs DELETE "/memory/$one" > /dev/null
rs POST /memory/delete-batch "{\"ids\": [$two]}" > /dev/null
stop
start_python
want="search 1 {'rust-check': 1} {'(unknown)': 1}
add 3 {'rust-check': 3} {'(unknown)': 2, 'rust/usage': 1}
delete 3 {'rust-check': 3} {'(unknown)': 2, 'rust/usage': 1}"
got="$(curl -sf "http://127.0.0.1:$py_port/usage?period=all&session_id=rust-session" -H "X-API-Key: $key" |
  .venv/bin/python -c '
import json, sys
ops = json.load(sys.stdin)["operations"]
for op in ("search", "add", "delete"):
    o = ops.get(op, {})
    print(op, o.get("total"), o.get("by_client"), o.get("by_source"))')"
if [ "$got" != "$want" ]; then
  echo "FAIL: the Python server reads other rows for the Rust calls" >&2
  printf 'want:\n%s\ngot:\n%s\n' "$want" "$got" >&2
  status=1
else
  echo "ok: the Python server reads the usage rows of the Rust calls"
fi
stop
exit "$status"
