"""Send more read requests to the Python server and to the Rust server on the
same data, and compare the replies with the normalizer and the diff of the
parity kit. The golden captures have few links and one conflict page; these
requests add the other branches of the read routes.

Usage: python parity_reads_ab.py <metadata.json> <vars.json> <python URL> <rust URL>
Exit code 1 if a reply is different.
"""

import json
import sys

import httpx

from parity import kit


def requests(records, keys):
    admin, ro, rw, managed_admin = keys
    linked = [r["id"] for r in records if r.get("links")]
    targets = sorted({link["to_id"] for r in records for link in r.get("links", [])})
    conflict = [r["id"] for r in records if r.get("conflicts_with") is not None]
    out = []
    for key in (admin, ro, rw, managed_admin):
        for mid in linked[:10] + targets[:10]:
            out.append(("GET", f"/memory/{mid}/links?include_incoming=true", key, None))
            out.append(("GET", f"/memory/{mid}/links?type=related_to&include_incoming=1", key, None))
            out.append(("GET", f"/memory/{mid}/links?type=supersedes", key, None))
        out += [
            ("GET", "/memories?pinned=false&limit=50", key, None),
            ("GET", "/memories?pinned=true&limit=5000", key, None),
            ("GET", "/memories?offset=200&limit=100", key, None),
            ("GET", "/memories?source=wip/&limit=5000", key, None),
            ("GET", "/memories?source=claude-code/&source_boundary=true&limit=7&offset=3", key, None),
            ("GET", "/memories?source=&limit=3", key, None),
            ("GET", "/memories?source_boundary=maybe", key, None),
            ("GET", "/memories?offset=-1&limit=6000", key, None),
            ("GET", "/memories/count?source=claude-code/voltis&source_boundary=true", key, None),
            ("GET", "/memories/count?source=wip/", key, None),
            ("GET", "/memories/count?source=" + "x" * 501, key, None),
            ("GET", "/memory/conflicts", key, None),
            ("GET", "/memory/conflicts?limit=2&offset=1", key, None),
            ("GET", "/memory/conflicts?limit=0&offset=-5", key, None),
            ("GET", "/memory/conflicts?limit=abc", key, None),
            ("GET", "/folders", key, None),
            ("GET", "/stats", key, None),
            ("GET", "/memory/abc", key, None),
            ("GET", "/memory/-1", key, None),
            ("GET", "/memory/5.0", key, None),
            ("GET", "/memory/%205", key, None),
            ("GET", "/memory/1_0", key, None),
            ("GET", "/memory/-007", key, None),
            ("GET", "/memory/99999999999999999999999", key, None),
            ("GET", "/memory/5.5/links", key, None),
            ("GET", "/memory/99999999999999999999999/links", key, None),
            ("GET", f"/memory/{records[-1]['id']}", key, None),
            ("GET", "/memory/abc/links?include_incoming=x", key, None),
            ("POST", "/memory/get-batch", key, {"ids": [r["id"] for r in records[:40]] + [10**6, "7"]}),
            ("POST", "/memory/get-batch", key, {"ids": conflict + [3.5, "x"]}),
            ("POST", "/memory/get-batch", key, {"ids": []}),
            ("POST", "/memory/get-batch", key, {}),
        ]
    out.append(("GET", "/health", None, None))
    out.append(("GET", "/health", ro, None))
    return out


def main(meta_path, vars_path, py_url, rs_url):
    with open(meta_path, encoding="utf-8") as f:
        records = json.load(f)
    with open(vars_path, encoding="utf-8") as f:
        saved = json.load(f)
    failed = 0
    with httpx.Client(base_url=py_url, timeout=60) as py, httpx.Client(base_url=rs_url, timeout=60) as rs:
        # A managed admin key has no prefixes: Python lists no record for it.
        made = py.post(
            "/api/keys",
            headers={"X-API-Key": kit.ADMIN_KEY_DEFAULT},
            json={"name": "parity-admin-managed", "role": "admin"},
        )
        made.raise_for_status()
        keys = (kit.ADMIN_KEY_DEFAULT, saved["key:ro"], saved["key:rw"], made.json()["key"])
        reqs = requests(records, keys)
        for method, path, key, body in reqs:
            headers = {"X-API-Key": key} if key else {}
            replies = []
            for c in (py, rs):
                r = c.request(method, path, headers=headers, json=body)
                b = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
                if path == "/health" or path == "/stats":
                    b = {k: v for k, v in b.items() if k != "version"} if isinstance(b, dict) else b
                replies.append((r.status_code, kit.normalize(b)))
            problems = kit.diff_values(replies[0], replies[1])
            if problems:
                failed += 1
                who = dict(zip(keys, ("admin", "ro", "rw", "managed-admin"))).get(key, "none")
                print(json.dumps({"request": f"{method} {path[:120]}", "key": who, "diff": problems[:5]}))
    print(f"{failed} of {len(reqs)} request(s) differ", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
