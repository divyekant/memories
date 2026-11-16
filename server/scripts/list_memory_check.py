"""Exit check of task P2-3: with 300,000 synthetic records, the memory of the
Rust server process does not increase after 1,000 list requests.

1. `load`: write N synthetic records into a new collection (gRPC, fixed vector).
2. `measure`: send a warm-up of list requests, read VmRSS of the server
   process, send 1,000 list requests, read VmRSS again. The check fails if
   the second value is more than the first value plus the tolerance.

Usage:
  python list_memory_check.py load <qdrant URL> <collection> <count> <dimension>
  python list_memory_check.py measure <server URL> <pid> <admin key> <count>
"""

import random
import sys

import httpx

# Allocator pages and the HTTP buffers can move by some MB; a leak of one
# record page per request is more than 1 GB after 1,000 requests.
TOLERANCE_KB = 16 * 1024
SOURCES = [f"{a}/proj{b}/{c}" for a in ("claude-code", "codex", "learning", "wip", "eval")
           for b in range(10) for c in ("config", "decisions", "notes")]


def load(url, collection, count, dim):
    from qdrant_client import QdrantClient, models

    host = url.split("://", 1)[1].split(":")[0]
    c = QdrantClient(host=host, grpc_port=6334, prefer_grpc=True, timeout=120)
    c.create_collection(
        collection,
        vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        sparse_vectors_config={"keyword": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    vec = [1.0] + [0.0] * (dim - 1)
    rng = random.Random(7)
    batch = 2000
    for start in range(0, count, batch):
        points = []
        for i in range(start, min(start + batch, count)):
            points.append(models.PointStruct(
                id=i,
                vector={"dense": vec, "keyword": models.SparseVector(indices=[], values=[])},
                payload={
                    "text": f"Synthetic record {i}: service {rng.randrange(500)} uses pool size "
                            f"{rng.randrange(5, 50)} and a timeout of {rng.randrange(1, 60)} s.",
                    "source": SOURCES[i % len(SOURCES)],
                    "created_at": "2026-11-01T00:00:00+00:00",
                    "pinned": i % 97 == 0,
                },
            ))
        c.upsert(collection, points, wait=True)
    print(f"loaded {count} records into {collection}", file=sys.stderr)


def rss_kb(pid):
    with open(f"/proc/{pid}/status", encoding="ascii") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    raise RuntimeError("no VmRSS")


def requests(rng, count, n):
    for _ in range(n):
        q = {"offset": rng.randrange(count), "limit": rng.choice([20, 100, 1000, 5000])}
        if rng.random() < 0.3:
            q["source"] = rng.choice(["claude-code/", "codex/proj3", "wip/proj1/notes"])
        if rng.random() < 0.1:
            q["pinned"] = "true"
        yield q


def measure(url, pid, key, count):
    pid, count = int(pid), int(count)
    rng = random.Random(11)
    with httpx.Client(base_url=url, timeout=120, headers={"X-API-Key": key}) as c:
        def run(n):
            for q in requests(rng, count, n):
                r = c.get("/memories", params=q)
                r.raise_for_status()
                assert len(r.json()["memories"]) <= q["limit"]
        run(200)
        before = rss_kb(pid)
        run(1000)
        after = rss_kb(pid)
        total = c.get("/memories/count").json()["count"]
    print(f"records {total}; VmRSS after warm-up {before} kB; after 1000 list requests {after} kB; "
          f"change {after - before:+d} kB (tolerance {TOLERANCE_KB} kB)")
    if total != count:
        print(f"FAIL: the server has {total} records, not {count}", file=sys.stderr)
        return 1
    if after > before + TOLERANCE_KB:
        print("FAIL: the memory of the process increased", file=sys.stderr)
        return 1
    print("OK: the memory of the process did not increase")
    return 0


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "load":
        load(args[0], args[1], int(args[2]), int(args[3]))
    else:
        sys.exit(measure(*args))
