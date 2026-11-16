"""Requests for the exit check of task P5-6 (server/scripts/parity_promotions.sh).

Usage:
  parity_promotions_ab.py seed <base url> <admin key>
  parity_promotions_ab.py probe <base url> <admin key> <out.json>
  parity_promotions_ab.py compare <python.json> <rust.json>

`seed` adds project records through a managed key with a principal, and
archives one of them. `probe` sends the promotion requests and keeps the
status and the body of each reply. For GET /metrics it keeps only the
`promotion` part.
"""

import json
import sys
import urllib.error
import urllib.request

SLUG_BAD = "Bad_"
PROBES = [
    ("GET", "/promotions", None),
    ("GET", "/promotions?state=candidate&limit=7&offset=3&project_id=alpha&owner=dk", None),
    ("GET", "/promotions?since=1700000000&until=2026-01-01T10:00:00%2B05:30", None),
    ("GET", "/promotions?state=nope", None),
    ("GET", "/promotions?state=it%27s", None),
    ("GET", f"/promotions?project_id={SLUG_BAD}&owner=-x&since=abc&until=2026-13-01&limit=0&offset=-1&state=zz", None),
    ("GET", "/promotions?project_id=a&project_id=B_", None),
    ("GET", "/promotions?limit=501&offset=x", None),
    ("GET", "/promotions?since=2026-01-01T25:00", None),
    ("GET", "/promotions?since=2026-02-30", None),
    ("GET", "/promotions?until=%202026-01-01", None),
    ("GET", "/promotions?until=2026-01-01%2010:00", None),
    ("GET", "/promotions?since=2026-01-01T10:00:00.123456789Z", None),
    ("GET", "/promotions?since=2026-01-01%2010:00:00.1234567", None),
    ("GET", "/promotions?until=1700000000.1234567", None),
    ("GET", "/promotions?since=1.5e9", None),
    ("GET", "/promotions/1", None),
    ("GET", "/promotions/2", None),
    ("GET", "/promotions/-5", None),
    ("GET", "/promotions/99999999999999999999999", None),
    ("GET", "/promotions/abc", None),
    ("POST", "/promotions/1/approve", '{"reason": "parity"}'),
    ("POST", "/promotions/1/approve", '{"reason": "ok", "shared_text": null}'),
    ("POST", "/promotions/1/approve", '{"reason": "   "}'),
    ("POST", "/promotions/1/approve", '{"reason": "", "shared_text": ""}'),
    ("POST", "/promotions/1/approve", '{"reason": "ok", "shared_text": "x", "zz": 1}'),
    ("POST", "/promotions/1/approve", "[1]"),
    ("POST", "/promotions/x/approve", '{"zz": 1}'),
    ("POST", "/promotions/x/approve", None),
    ("POST", "/promotions/1/reject", '{"reason": "parity"}'),
    ("POST", "/promotions/1/reject", '{"reason": "\\u001c"}'),
    ("POST", "/promotions/1/reject", '{"reason": null}'),
    ("POST", "/promotions/1/reject", '{"reason": "ok", "shared_text": "x"}'),
    ("POST", "/promotions/1/reject", '{"reason": "' + "r" * 2001 + '"}'),
    ("POST", "/promotions/x/reject", None),
    ("GET", "/metrics", None),
]


def call(base, key, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, headers={"X-API-Key": key})
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = body.encode()
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def seed(base, admin):
    status, body = call(base, admin, "POST", "/api/keys", json.dumps({
        "name": "promotion-parity", "role": "read-write",
        "prefixes": ["project/", "person/"], "principal_id": "dk",
    }))
    assert status == 200, body
    key = body["key"]
    ids = []
    for source in ["project/alpha/knowledge", "project/alpha/decisions",
                   "project/beta/knowledge", "project/beta/state", "person/dk/alpha/knowledge"]:
        status, body = call(base, key, "POST", "/memory/add", json.dumps(
            {"text": f"Promotion parity fact for {source}.", "source": source}))
        assert status == 200 and body.get("success"), body
        ids.append(body["id"])
    status, body = call(base, admin, "POST", "/memory/archive-batch", json.dumps({"ids": [ids[3]]}))
    assert status == 200, body


def probe(base, admin, out):
    replies = []
    for method, path, body in PROBES:
        status, reply = call(base, admin, method, path, body)
        if path == "/metrics":
            reply = reply.get("promotion")
        replies.append({"request": f"{method} {path} {(body or '')[:60]}", "status": status, "body": reply})
    json.dump(replies, open(out, "w"), indent=1, sort_keys=True)


def compare(left, right):
    a, b = json.load(open(left)), json.load(open(right))
    bad = 0
    for x, y in zip(a, b, strict=True):
        if (x["status"], x["body"]) != (y["status"], y["body"]):
            bad += 1
            print(f"DIFF {x['request']}\n  python {x['status']} {x['body']}\n  rust   {y['status']} {y['body']}",
                  file=sys.stderr)
    print(f"{len(a) - bad} of {len(a)} promotion replies are equal")
    return 1 if bad else 0


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "seed":
        seed(*args)
    elif cmd == "probe":
        probe(*args)
    else:
        sys.exit(compare(*args))
