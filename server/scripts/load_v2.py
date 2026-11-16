"""Copy the records of a Python metadata.json into a new Qdrant collection for a
parity test of the read routes. The collection has the vectors of the Rust
store. The dense vector of each point is a fixed unit vector, so do not use
the result for a search test.

shortcut: the parity scripts use this until `memories-server migrate` (P1-4)
is on rust-rewrite; then use migrate.

Usage: python load_v2.py <metadata.json> <qdrant REST URL> <collection> <dimension>
"""

import json
import sys

import httpx


def main(meta_path, url, collection, dim):
    with open(meta_path, encoding="utf-8") as f:
        records = json.load(f)
    c = httpx.Client(base_url=url, timeout=60)
    r = c.put(f"/collections/{collection}", json={
        "vectors": {"dense": {"size": dim, "distance": "Cosine"}},
        "sparse_vectors": {"keyword": {"modifier": "idf"}},
    })
    r.raise_for_status()
    unit = [1.0] + [0.0] * (dim - 1)
    for i in range(0, len(records), 500):
        points = [{
            "id": rec["id"],
            "vector": {"dense": unit, "keyword": {"indices": [], "values": []}},
            "payload": {k: v for k, v in rec.items() if k != "id"},
        } for rec in records[i:i + 500]]
        c.put(f"/collections/{collection}/points?wait=true", json={"points": points}).raise_for_status()
    print(f"loaded {len(records)} records into {collection}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]))
