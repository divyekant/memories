"""Gate G4, part b (task P4-4): 24 dry-run probes with a live model on two servers.

The two servers must use the same provider and the same model. The driver
seeds each server with the same records (the scenario memories of the
extraction eval, source `eval/g4`), then sends 24 probes with `dry_run: true`:
the 20 texts of `CORPUS` in `eval/run_extraction_eval.py` and 4 scenario
memories. A dry run writes nothing, so each probe sees the same store.

The action of a probe is the sorted list of the action types of its decisions.
The probes run `--rounds` times on each server, in turns. The report gives the
mean agreement of each Python run with each Rust run, and the mean agreement of
the Python runs with each other: the noise of the live model.

Pass condition (plan): the same action for 90% or more of the probes.

Usage (server/scripts/gate_g4_live.sh starts the servers):
  python -m parity.extract_live --python-url URL --rust-url URL [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from parity.extract_corpus import eval_corpus, scenario_memories
from parity.extract_pipeline import ADMIN, call, wait_job

SOURCE = "eval/g4"
PASS_SHARE = 0.90


def probes():
    out = [{"name": f"corpus/{c['id']}", "messages": c["text"]} for c in eval_corpus()]
    mems = scenario_memories()
    # 4 scenario memories spread over the list; their records are in the store.
    for i in (0, len(mems) // 3, 2 * len(mems) // 3, len(mems) - 1):
        out.append({"name": f"scenario/{mems[i]['id']}", "messages": mems[i]["text"]})
    return out


def seed(client):
    mems = [{"text": m["text"], "source": SOURCE} for m in scenario_memories()]
    status, body = call(client, "POST", "/memory/add-batch", ADMIN, {"memories": mems})
    assert status == 200, body


def action(job):
    if job.get("status") != "completed":
        return [f"job {job.get('status')}"]
    return sorted(str(a.get("action", "")).lower() for a in (job.get("result") or {}).get("actions") or [])


def run_probes(client):
    out = {}
    for p in probes():
        status, enq = call(client, "POST", "/memory/extract", ADMIN,
                           {"messages": p["messages"], "source": SOURCE, "dry_run": True})
        assert status == 202, enq
        job = wait_job(client, enq["job_id"])
        out[p["name"]] = {"action": action(job), "result": job.get("result"), "error": job.get("error")}
    return out


def agree(a, b):
    return sum(a[n]["action"] == b[n]["action"] for n in a) / len(a)


def mean(values):
    return sum(values) / len(values) if values else float("nan")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--python-url", required=True)
    p.add_argument("--rust-url", required=True)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--out")
    args = p.parse_args(argv)
    py_runs, rust_runs = [], []
    with httpx.Client(base_url=args.python_url, timeout=120) as py, \
            httpx.Client(base_url=args.rust_url, timeout=120) as rs:
        seed(py)
        seed(rs)
        for _ in range(args.rounds):
            py_runs.append(run_probes(py))
            rust_runs.append(run_probes(rs))
    cross = mean([agree(a, b) for a in py_runs for b in rust_runs])
    py_self = mean([agree(a, b) for i, a in enumerate(py_runs) for b in py_runs[i + 1:]])
    rust_self = mean([agree(a, b) for i, a in enumerate(rust_runs) for b in rust_runs[i + 1:]])
    for name in py_runs[0]:
        print(f"{name}: python {[r[name]['action'] for r in py_runs]} rust {[r[name]['action'] for r in rust_runs]}")
    print(f"python with rust: {cross:.3f}; python with python: {py_self:.3f}; rust with rust: {rust_self:.3f}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"python": py_runs, "rust": rust_runs, "python_with_rust": cross,
                       "python_with_python": py_self, "rust_with_rust": rust_self},
                      f, indent=1, sort_keys=True)
    return 0 if cross >= PASS_SHARE else 1


if __name__ == "__main__":
    sys.exit(main())
