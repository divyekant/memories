"""Steps of the rollback drill (task P7-1). `rollback_drill.sh` runs them.

Commands (URL is a server, QDRANT is the Qdrant REST address):
  seed URL                         Add the seed corpus of the parity kit.
  load QDRANT NAME SRC DATA        Load a copy of production data in place of the seed:
                                   SRC holds metadata.json, config.json and
                                   points.ndjson.gz (Qdrant points with vectors).
  changes URL N SEED OUT           Make N changes; write the changed IDs to OUT.
  jobs URL N OUT                   Queue N extract jobs; write their bodies to OUT.
  collection QDRANT NAME OUT       Write each record of a Qdrant collection to OUT.
  search URL OUT                   Run the vector search cases; write the replies to OUT.
  check-python URL EXPECTED        Each record of EXPECTED is in the Python server, and
                                   no other record. Each read case answers 2xx.
  diff A B [ann]                   The two search capture files are equal. With ann,
                                   a reply can have one other ID (production size).
  python-writes URL TOUCHED RUST OUT
                                   Case 2: writes on the Python server; write them to OUT.
  check-merge QDRANT NAME RUST PY  Case 2: each record of the two sides is in NAME.
"""

import gzip
import json
import random
import shutil
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from parity import kit, seed  # noqa: E402
from parity.scenario import c  # noqa: E402

KEY = "parity-admin-key"
H = {"X-API-Key": KEY}
PREFIXES = ["claude-code/drill", "codex/drill", "learning/drill/notes", "wip/drill"]
WORDS = ("rate limit deploy rollback cache index billing engine queue retry token budget owner "
         "incident postmortem schema migration latency vector keyword graph").split()
QUERIES = ["How is the fleet-api rate limit configured?", "incident root cause", "drill rollback cache",
           "who owns billing-engine", "design decision"]


def client(url):
    return httpx.Client(base_url=url, headers=H, timeout=120)


def ok(r):
    if r.status_code >= 300:
        raise SystemExit(f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:300]}")
    return r.json()


def text(rnd, n):
    return f"Drill note {n}: " + " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(6, 14))) + "."


def cmd_seed(url):
    out = kit.run(seed.build(), url, admin_key=KEY)
    bad = [r["name"] for r in out if r["response"]["status"] >= 300]
    if bad:
        raise SystemExit(f"seed cases failed: {bad}")


def cmd_load(qdrant, name, src, data_dir):
    src, data = Path(src), Path(data_dir)
    meta = json.loads((src / "metadata.json").read_text())
    ids = {m["id"] for m in meta}
    with gzip.open(src / "points.ndjson.gz", "rt") as f:
        points = [p for p in map(json.loads, f) if p["id"] in ids]
    # The copy reads metadata.json, then Qdrant, from a live server: a point of a
    # newer add is left out, and a record with no point makes the copy unusable.
    if len(points) != len(ids):
        raise SystemExit(f"{len(ids) - len(points)} records of metadata.json have no point: copy again")
    data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "metadata.json", data / "metadata.json")
    # The Python server writes its QDRANT_COLLECTION here; export-legacy reads it.
    config = json.loads((src / "config.json").read_text())
    config["qdrant_collection"] = name
    (data / "config.json").write_text(json.dumps(config, indent=2))
    dim = len(points[0]["vector"])
    with httpx.Client(base_url=qdrant, timeout=120) as q:
        q.put(f"/collections/{name}", json={"vectors": {"size": dim, "distance": "Cosine"}}).raise_for_status()
        for i in range(0, len(points), 1000):
            batch = [{k: p[k] for k in ("id", "vector", "payload")} for p in points[i:i + 1000]]
            q.put(f"/collections/{name}/points?wait=true", json={"points": batch}).raise_for_status()
    print(f"loaded {len(points)} records into {name}")


def all_memories(cl):
    """Each record of GET /memories, in pages of 5,000 (the Python maximum)."""
    out = []
    while True:
        page = ok(cl.get("/memories", params={"offset": len(out), "limit": 5000}))["memories"]
        out.extend(page)
        if len(page) < 5000:
            return out


def ids_of(cl):
    return [m["id"] for m in all_memories(cl)]


def qdrant_records(qdrant, name):
    out, offset = {}, None
    with httpx.Client(base_url=qdrant, timeout=60) as q:
        while True:
            body = {"limit": 1000, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            res = q.post(f"/collections/{name}/points/scroll", json=body)
            res.raise_for_status()
            res = res.json()["result"]
            for p in res["points"]:
                out[p["id"]] = {"id": p["id"], **p["payload"]}
            offset = res.get("next_page_offset")
            if offset is None:
                return out


def cmd_changes(url, n, rnd_seed, out_path, qdrant, name):
    """N write requests through the Rust server: adds, batch adds, edits,
    deletes, links and supersedes, in a fixed random order."""
    rnd = random.Random(int(rnd_seed))
    ids = sorted(qdrant_records(qdrant, name))
    touched = set()
    with client(url) as cl:
        for i in range(int(n)):
            op = rnd.random()
            if op < 0.40 or len(ids) < 10:
                new = ok(cl.post("/memory/add", json={"text": text(rnd, i), "source": rnd.choice(PREFIXES)}))["id"]
                ids.append(new)
                touched.add(new)
            elif op < 0.45:
                body = {"memories": [{"text": text(rnd, f"{i}.{j}"), "source": rnd.choice(PREFIXES)}
                                     for j in range(3)]}
                new = ok(cl.post("/memory/add-batch", json=body))["ids"]
                ids.extend(new)
                touched.update(new)
            elif op < 0.70:
                mid = rnd.choice(ids)
                field = rnd.choice(["text", "pinned", "archived", "source"])
                value = {"text": text(rnd, i), "pinned": rnd.random() < 0.5,
                         "archived": rnd.random() < 0.5, "source": rnd.choice(PREFIXES)}[field]
                ok(cl.patch(f"/memory/{mid}", json={field: value}))
                touched.add(mid)
            elif op < 0.80:
                mid = ids.pop(rnd.randrange(len(ids)))
                r = cl.delete(f"/memory/{mid}")
                if r.status_code == 409:  # a pinned record
                    ids.append(mid)
                    continue
                ok(r)
                touched.add(mid)
            elif op < 0.92:
                a, b = rnd.sample(ids, 2)
                ok(cl.post(f"/memory/{a}/link", json={"to_id": b, "type": rnd.choice(["related_to", "reinforces"])}))
                touched.add(a)
            else:
                mid = rnd.choice(ids)
                r = cl.post(f"/memory/{mid}/supersede", json={"text": text(rnd, i)})
                if r.status_code == 409:  # a pinned record
                    continue
                new = ok(r)
                touched.add(mid)
                if new.get("new_id") is not None:
                    ids.append(new["new_id"])
                    touched.add(new["new_id"])
    Path(out_path).write_text(json.dumps(sorted(touched)))
    print(f"{n} changes, {len(touched)} records touched")


def cmd_jobs(url, n, out_path):
    bodies = [{"messages": f"User: drill job {i}. Assistant: noted.", "source": "claude-code/drill",
               "context": "stop"} for i in range(int(n))]
    with client(url) as cl:
        for b in bodies:
            ok(cl.post("/memory/extract", json=b))
    Path(out_path).write_text(json.dumps(bodies))


def cmd_collection(qdrant, name, out_path):
    Path(out_path).write_text(json.dumps(qdrant_records(qdrant, name), sort_keys=True))


def search_cases():
    cases = [c(f"search-vector-{i}", "POST", "/search", json={"query": q, "k": 8, "hybrid": False})
             for i, q in enumerate(QUERIES)]
    cases.append(c("search-vector-prefix", "POST", "/search",
                   json={"query": QUERIES[0], "k": 8, "hybrid": False, "source_prefix": "claude-code/"}))
    cases.append(c("is-novel", "POST", "/memory/is-novel", json={"text": QUERIES[1], "threshold": 0.9}))
    return cases


def cmd_search(url, out_path):
    kit.dump(kit.run(search_cases(), url, admin_key=KEY, allow_nonempty=True), out_path)


def cmd_diff(a, b, mode="exact"):
    left, right = kit.load(a), kit.load(b)
    diffs = kit.diff_captures(left, right)
    if mode == "ann":
        # At production size Qdrant searches the HNSW graph, which is approximate,
        # and the two collections have different graphs. Accept one other ID in
        # a reply (the G3 tolerance); check-python checks each record exactly.
        def results(case):
            return (case["response"].get("body") or {}).get("results", [])

        def ids(case):
            return [r.get("id") for r in results(case)]

        def same_shared(x, y):
            # relative_score depends on the other results of the reply.
            def strip(r):
                return {k: v for k, v in r.items() if k != "relative_score"}
            by_id = {r.get("id"): strip(r) for r in results(y)}
            return all(by_id[r.get("id")] == strip(r) for r in results(x) if r.get("id") in by_id)
        cases = {c["name"]: (c, d) for c, d in zip(left, right)}
        kept = []
        for d in diffs:
            pair = cases.get(d["name"])
            other = len(set(ids(pair[0])) ^ set(ids(pair[1]))) // 2 if pair else None
            if pair and ids(pair[0]) and len(ids(pair[0])) == len(ids(pair[1])) and other <= 1 \
                    and same_shared(*pair):
                print(f"{d['name']}: {other} other ID (HNSW tolerance): {ids(pair[0])} / {ids(pair[1])}")
            else:
                kept.append(d)
        diffs = kept
    for d in diffs:
        print(d)
    if diffs:
        raise SystemExit(f"{len(diffs)} difference(s)")
    print("no differences")


def python_records(url):
    with client(url) as cl:
        mems = all_memories(cl)
    return {m["id"]: {k: v for k, v in m.items() if k != "confidence"} for m in mems}


def read_cases(ids, linked):
    a, b = ids[0], ids[len(ids) // 2]
    return [
        c("count-all", "GET", "/memories/count"),
        c("count-source", "GET", "/memories/count", query="source=claude-code/"),
        c("list-page", "GET", "/memories", query="offset=10&limit=15"),
        c("list-pinned", "GET", "/memories", query="pinned=true&limit=50"),
        c("folders", "GET", "/folders"),
        c("stats", "GET", "/stats"),
        c("memory-get", "GET", f"/memory/{a}"),
        c("memory-get-batch", "POST", "/memory/get-batch", json={"ids": [a, b]}),
        c("links-in", "GET", f"/memory/{linked}/links", query="include_incoming=true"),
        c("conflicts", "GET", "/memory/conflicts"),
        c("search-hybrid", "POST", "/search", json={"query": QUERIES[0], "k": 8, "hybrid": True}),
        c("search-batch", "POST", "/search/batch", json={"queries": [{"query": q, "k": 3} for q in QUERIES]}),
        c("export-all", "GET", "/export"),
        c("health-ready", "GET", "/health/ready"),
    ]


def cmd_check_python(url, expected_path):
    want = {int(k): v for k, v in json.loads(Path(expected_path).read_text()).items()}
    got = python_records(url)
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    changed = sorted(i for i in set(want) & set(got) if want[i] != got[i])
    for i in changed[:5]:
        print(f"record {i}: want {want[i]}\n           got  {got[i]}")
    if missing or extra or changed:
        raise SystemExit(f"python records: {len(missing)} missing {missing[:10]}, {len(extra)} extra "
                         f"{extra[:10]}, {len(changed)} changed {changed[:10]}")
    linked = next((i for i, r in sorted(want.items()) if r.get("links")), sorted(want)[0])
    out = kit.run(read_cases(sorted(want), linked), url, admin_key=KEY, allow_nonempty=True)
    bad = [(r["name"], r["response"]["status"]) for r in out if not 200 <= r["response"]["status"] < 300]
    count = next(r for r in out if r["name"] == "count-all")["response"]["body"]["count"]
    if bad or count != len(want):
        raise SystemExit(f"read checks failed: {bad}, count {count} for {len(want)} records")
    print(f"python has each of the {len(want)} records; {len(out)} read checks pass")


def cmd_python_writes(url, touched_path, rust_path, out_path):
    """Case 2: writes on the Python server after a return through an old image
    tag. The change and the delete go to records that the Rust server did not
    touch, so the merge must take them. The pin goes to a record that the Rust
    server changed, so the merge must keep the two changes. The searches write
    last_reinforced_at, which is not a change."""
    rnd = random.Random(7)
    touched = set(json.loads(Path(touched_path).read_text()))
    rust = {int(k): v for k, v in json.loads(Path(rust_path).read_text()).items()}
    with client(url) as cl:
        for q in QUERIES:
            ok(cl.post("/search", json={"query": q, "k": 8, "hybrid": False}))
        before = python_records(url)
        ids = [i for i in ids_of(cl) if i not in touched]
        both = [i for i in sorted(touched) if i in before and i in rust and not before[i].get("pinned")]
        pinned = next((i for i in both if before[i].get("text") != rust[i].get("text")), both[0])
        ok(cl.patch(f"/memory/{pinned}", json={"pinned": True}))
        added = [ok(cl.post("/memory/add", json={"text": text(rnd, f"py{i}"), "source": "codex/drill/python"}))["id"]
                 for i in range(5)]
        patched = ids[3]
        ok(cl.patch(f"/memory/{patched}", json={"text": "Changed by the Python server in case 2."}))
        deleted = ids[4]
        ok(cl.delete(f"/memory/{deleted}"))
        final = python_records(url)
    Path(out_path).write_text(json.dumps({"added": added, "patched": patched, "deleted": deleted,
                                          "pinned": pinned, "records": final}))


def cmd_check_merge(qdrant, name, rust_path, py_path):
    """Case 2: after `migrate --merge`, memories_v2 holds each record that the
    Rust server wrote, each record that the Python server added (maybe with a
    new ID) one time, the Python change, the Python pin and the Python delete,
    and no other record."""
    rust = json.loads(Path(rust_path).read_text())
    py = json.loads(Path(py_path).read_text())
    got = qdrant_records(qdrant, name)
    texts = [r.get("text") for r in got.values()]
    errors = []
    for i, r in rust.items():
        if int(i) in (py["deleted"], py["patched"]):
            continue
        if got.get(int(i), {}).get("text") != r.get("text"):
            errors.append(f"rust record {i} lost or changed")
    for i in py["added"]:
        n = texts.count(py["records"][str(i)]["text"])
        if n != 1:
            errors.append(f"python record {i} is in memories_v2 {n} times")
    if got.get(py["patched"], {}).get("text") != "Changed by the Python server in case 2.":
        errors.append(f"python change of {py['patched']} lost")
    pin = got.get(py["pinned"], {})
    if pin.get("pinned") is not True or pin.get("text") != rust[str(py["pinned"])].get("text"):
        errors.append(f"record {py['pinned']}: want the python pin and the rust text, got {pin}")
    if py["deleted"] in got:
        errors.append(f"python delete of {py['deleted']} lost")
    want = len(rust) - 1 + len(py["added"])
    if len(got) != want:
        errors.append(f"memories_v2 has {len(got)} records, want {want}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"merge kept the records of the two sides: {len(got)} records")


def main(argv):
    cmd, args = argv[0], argv[1:]
    fn = {"seed": cmd_seed, "load": cmd_load, "changes": cmd_changes, "jobs": cmd_jobs, "collection": cmd_collection,
          "search": cmd_search, "diff": cmd_diff, "check-python": cmd_check_python,
          "python-writes": cmd_python_writes, "check-merge": cmd_check_merge}[cmd]
    fn(*args)


if __name__ == "__main__":
    main(sys.argv[1:])
