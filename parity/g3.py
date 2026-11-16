"""Gate G3 of the Rust rewrite (task P2-4): search on the two servers.

`seed` writes the G3 data into the Python server v5.19.0 through the HTTP API
and saves the query plan. Then `memories-server migrate` copies the same
records into Qdrant. `compare` sends each query to the two servers and applies
the gate conditions. `server/scripts/gate_g3.sh` runs the full sequence.

Parts:
- corpus: 2,000 synthetic records and 600 queries, vector and hybrid search.
- graph: the scenarios of eval/run_graph_eval.py, graph weight 0 and 0.1.
- longmemeval: session recall at 5 (no judge) of eval/longmemeval.py, with
  each question in its own source prefix of one store.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import httpx

SEED = 20261116
CORPUS_SIZE = 2000
QUERIES = 600
PREFIXES = ["claude-code/voltis", "codex/voltis", "learning/voltis", "wip/voltis", "eval/graph/synth"]
GRAPH_PREFIX = "eval/graph/scenarios"
LME_PREFIX = "eval/longmemeval"
STOPWORDS = frozenset(
    "a an and are as at be by can could did do does for from had has have how i in is it "
    "of on or our should that the this to was we were what when where which who why will with "
    "would you".split()
)

# The pass conditions of G3 in the plan (proposals that dk approved).
VECTOR_TOP10 = 0.99
HYBRID_TOP1 = 0.90
HYBRID_TOP5 = 0.90
MAX_DROP = 0.01


def top_same(a, b, n):
    return set(a[:n]) == set(b[:n])


def tie_top(py, rs, n):
    """Same first n results, where a result with the similarity of the Python
    n-th result can take the place of another one with that similarity."""
    p, r = py[:n], rs[:n]
    if len(p) != len(r):
        return False
    if not p:
        return True
    last = p[-1]["similarity"]
    want = {x["id"] for x in p if x["similarity"] > last + 1e-6}
    got = {x["id"] for x in r}
    return want <= got and all(x["id"] in {y["id"] for y in p} or abs(x["similarity"] - last) <= 1e-6 for x in r)


def top1_same(a, b):
    return a[:1] == b[:1]


def overlap(py, rs, n):
    """The part of the first n Python results that is in the first n Rust results."""
    want = py[:n]
    if not want:
        return 1.0 if not rs else 0.0
    return len(set(want) & set(rs[:n])) / len(want)


def mean(xs):
    xs = list(xs)
    return round(sum(map(float, xs)) / len(xs), 4) if xs else None


def corpus():
    from eval.generate_synthetic_memories import generate_all

    # Unique texts only: two equal texts have equal scores, and Qdrant can put
    # either one first. That measures the Qdrant tie order, not the server.
    rnd = random.Random(SEED)
    out, seen = [], set()
    for m in generate_all(CORPUS_SIZE + 200):
        if m["text"] in seen:
            continue
        seen.add(m["text"])
        tail = m["source"].split("eval/graph/synth/", 1)[-1].strip("/") or "misc"
        out.append({"text": m["text"], "source": f"g3/{rnd.choice(PREFIXES)}/{tail}"})
    return out[:CORPUS_SIZE]


def corpus_queries(texts):
    """Half keyword queries ("what about w1 w2?"), half the start of a record."""
    rnd = random.Random(SEED)
    seen, out = set(), []
    while len(out) < QUERIES:
        words = [w for w in rnd.choice(texts).lower().split() if w.isalpha() and w not in STOPWORDS]
        if not words:
            continue
        if len(out) % 2 == 0:
            q = "what about " + " ".join(rnd.sample(words, min(len(words), rnd.randint(1, 4)))) + "?"
        else:
            q = " ".join(words[: rnd.randint(3, 8)])
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def verdict(results):
    checks, not_run = {}, []
    c = results.get("corpus")
    if c:
        checks["vector_top10_same"] = c["vector_top10_same"] >= VECTOR_TOP10
        checks["hybrid_top1_same"] = c["hybrid_top1_same"] >= HYBRID_TOP1
        checks["hybrid_top5_overlap"] = c["hybrid_top5_overlap"] >= HYBRID_TOP5
    else:
        not_run.append("corpus")
    g = results.get("graph")
    if g:
        for key in ("graph_off", "graph_on"):
            checks[f"graph_{key.split('_')[1]}"] = g["rust"][key] >= g["python"][key] - MAX_DROP - 1e-9
    else:
        not_run.append("graph")
    lme = results.get("longmemeval")
    if lme:
        checks["longmemeval_r5"] = lme["rust"]["r5"] >= lme["python"]["r5"] - MAX_DROP - 1e-9
    else:
        not_run.append("longmemeval")
    return {"pass": all(checks.values()), "checks": checks, "not_run": not_run}


def client(url, key):
    return httpx.Client(base_url=url, headers={"X-API-Key": key}, timeout=300.0)


def lme_questions(every):
    from eval.longmemeval import LongMemEvalRunner

    data = LongMemEvalRunner(client=None).load_dataset()
    return [q for i, q in enumerate(data) if i % every == 0]


# --- seed ---------------------------------------------------------------


def seed(args):
    from eval.memories_client import MemoriesClient
    from eval.run_graph_eval import load_scenarios

    plan = {"parts": args.parts}
    mc = MemoriesClient(url=args.base_url, api_key=args.key)
    mc.MAX_BATCH_MEMORIES = 500  # the AddBatchRequest limit of app.py v5.19.0
    http = client(args.base_url, args.key)
    if "corpus" in args.parts:
        records = corpus()
        mc.add_batch(records, deduplicate=False)
        plan["corpus_queries"] = corpus_queries([r["text"] for r in records])
        print(f"corpus: {len(records)} records", flush=True)
    if "graph" in args.parts:
        searches = []
        for sc in load_scenarios("eval/scenarios/graph"):
            prefix = f"{GRAPH_PREFIX}/{sc['id']}"
            ids = {}
            for m in sc["memories"]:
                r = http.post("/memory/add", json={"text": m["text"], "source": f"{prefix}/{m['source']}", "deduplicate": False})
                r.raise_for_status()
                ids[m["id"]] = r.json()["id"]
            for link in sc.get("links", []):
                a, b = ids.get(link["from"]), ids.get(link["to"])
                if a and b:
                    http.post(f"/memory/{a}/link", json={"to_id": b, "type": link.get("type", "related_to")}).raise_for_status()
            scope = f"{prefix}/{sc['source_prefix']}" if "source_prefix" in sc else prefix
            searches.append({"id": sc["id"], "query": sc["prompt"], "source_prefix": scope, "expected": sc["expected"]})
        plan["graph"] = searches
        print(f"graph: {len(searches)} scenarios", flush=True)
    if "longmemeval" in args.parts:
        from eval.longmemeval import LongMemEvalRunner

        lme = LongMemEvalRunner(client=mc)
        qs = lme_questions(args.lme_every)
        t0 = time.time()
        for i, q in enumerate(qs):
            lme.seed_question(q, source_prefix=LME_PREFIX)
            if (i + 1) % 10 == 0:
                print(f"longmemeval: {i + 1}/{len(qs)} questions, {time.time() - t0:.0f} s", flush=True)
        plan["longmemeval_every"] = args.lme_every
    Path(args.plan).write_text(json.dumps(plan))


# --- compare ------------------------------------------------------------


def search(http, body):
    r = http.post("/search", json=body)
    r.raise_for_status()
    return r.json()["results"]


def ids(results):
    return [r["id"] for r in results]


def compare_corpus(servers, queries):
    lists = {name: {"vector": [], "hybrid": []} for name in servers}
    for q in queries:
        for name, http in servers.items():
            lists[name]["vector"].append(search(http, {"query": q, "k": 10, "hybrid": False}))
            lists[name]["hybrid"].append(search(http, {"query": q, "k": 10, "hybrid": True}))
    py, rs = lists["python"], lists["rust"]
    tie_top1 = []
    for p, r in zip(py["hybrid"], rs["hybrid"]):
        # Tie-aware: the Rust first result has the Python first score in the Python list.
        scores = {x["id"]: x["rrf_score"] for x in p}
        tie_top1.append(bool(p) == bool(r) and (not p or abs(scores.get(r[0]["id"], -1) - p[0]["rrf_score"]) < 1e-9))
    # The first differing vector queries, with (id, similarity), to find the cause.
    diffs = [
        {"query": q, "python": [(x["id"], x["similarity"]) for x in p], "rust": [(x["id"], x["similarity"]) for x in r]}
        for q, p, r in zip(queries, py["vector"], rs["vector"]) if not top_same(ids(p), ids(r), 10)
    ][:5]
    return {
        "vector_differences": diffs,
        "vector_top10_same_tie_aware": mean(tie_top(p, r, 10) for p, r in zip(py["vector"], rs["vector"])),
        "queries": len(queries),
        "vector_top10_same": mean(top_same(ids(p), ids(r), 10) for p, r in zip(py["vector"], rs["vector"])),
        "vector_top10_same_order": mean(ids(p) == ids(r) for p, r in zip(py["vector"], rs["vector"])),
        "hybrid_top1_same": mean(top1_same(ids(p), ids(r)) for p, r in zip(py["hybrid"], rs["hybrid"])),
        "hybrid_top1_same_tie_aware": mean(tie_top1),
        "hybrid_top5_overlap": mean(overlap(ids(p), ids(r), 5) for p, r in zip(py["hybrid"], rs["hybrid"])),
        "hybrid_top10_overlap": mean(overlap(ids(p), ids(r), 10) for p, r in zip(py["hybrid"], rs["hybrid"])),
    }


def compare_graph(servers, searches):
    from eval.run_graph_eval import score_result

    out = {}
    for name, http in servers.items():
        scores = {"graph_off": [], "graph_on": []}
        for s in searches:
            for key, weight in (("graph_off", 0.0), ("graph_on", 0.1)):
                body = {"query": s["query"], "k": 5, "hybrid": True, "graph_weight": weight, "source_prefix": s["source_prefix"]}
                scores[key].append(score_result(search(http, body), s["expected"]))
        out[name] = {k: mean(v) for k, v in scores.items()}
    out["scenarios"] = len(searches)
    return out


def compare_lme(servers, every):
    from eval.longmemeval import LongMemEvalRunner, _normalize_longmemeval_date

    lme = LongMemEvalRunner(client=None)
    qs = lme_questions(every)
    r5 = {name: [] for name in servers}
    top5 = []
    for q in qs:
        body = {"query": str(q.get("question", "")), "k": 50, "hybrid": True,
                "source_prefix": lme._question_prefix(q, LME_PREFIX)}
        if q.get("question_date"):
            body["reference_date"] = _normalize_longmemeval_date(q["question_date"])
        got = {}
        for name, http in servers.items():
            got[name] = search(http, body)
            r5[name].append(lme.compute_recall_at_k(got[name], q, k=5)["recall_any"])
        top5.append(overlap(ids(got["python"]), ids(got["rust"]), 5))
    out = {name: {"r5": mean(v)} for name, v in r5.items()}
    out.update({"questions": len(qs), "every": every, "hybrid_top5_overlap": mean(top5)})
    return out


def compare(args):
    plan = json.loads(Path(args.plan).read_text())
    servers = {"python": client(args.python, args.key), "rust": client(args.rust, args.key)}
    results = {}
    t0 = time.time()
    if "corpus_queries" in plan:
        results["corpus"] = compare_corpus(servers, plan["corpus_queries"])
    if "graph" in plan:
        results["graph"] = compare_graph(servers, plan["graph"])
    if "longmemeval_every" in plan:
        results["longmemeval"] = compare_lme(servers, plan["longmemeval_every"])
    results["seconds"] = round(time.time() - t0)
    results["verdict"] = verdict(results)
    text = json.dumps(results, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    return 0 if results["verdict"]["pass"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m parity.g3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--base-url", required=True)
    s.add_argument("--parts", default="corpus,graph")
    s.add_argument("--lme-every", type=int, default=5, help="each Nth LongMemEval question")
    s.add_argument("--plan", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--python", required=True)
    c.add_argument("--rust", required=True)
    c.add_argument("--plan", required=True)
    c.add_argument("--out", default="")
    for p in (s, c):
        p.add_argument("--key", default="parity-admin-key")
    args = ap.parse_args(argv)
    if args.cmd == "seed":
        args.parts = args.parts.split(",")
        seed(args)
        return 0
    return compare(args)


if __name__ == "__main__":
    sys.exit(main())
