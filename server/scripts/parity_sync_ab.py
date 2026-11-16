"""Requests for the exit check of task P5-7 (server/scripts/parity_sync.sh).

Usage:
  parity_sync_ab.py seed <base url> <admin key> <keys.json>
  parity_sync_ab.py probe <base url> <admin key> <keys.json> <out.json>
  parity_sync_ab.py compare <python.json> <rust.json>

`seed` makes a read-only and a read-write key. `probe` sends the five /sync
routes with each key and keeps the status and the body of each reply.
"""

import json
import sys
import urllib.error
import urllib.request

PROBES = [
    ("GET", "/sync/status"),
    ("POST", "/sync/upload"),
    ("GET", "/sync/snapshots"),
    ("POST", "/sync/download"),
    ("POST", "/sync/download?confirm=true"),
    ("POST", "/sync/download?confirm=yes&backup_name=manual_20260101_000000"),
    ("POST", "/sync/download?confirm=maybe"),
    ("POST", "/sync/download?confirm=1&confirm=x"),
    ("POST", "/sync/download?confirm=%20true"),
    ("POST", "/sync/download?confirm="),
    ("POST", "/sync/restore/b1"),
    ("POST", "/sync/restore/b1?confirm=true"),
    ("POST", "/sync/restore/b1?confirm=off"),
    ("POST", "/sync/restore/b1?confirm=2"),
    ("GET", "/sync/upload"),
    ("POST", "/sync/status"),
]
# Not in PROBES: an encoded slash in backup_name (..%2Fx). Starlette decodes the
# path before it routes (404); the Rust shell routes on the raw path. See
# docs/decisions/2026-11-16-rust-sync-off.md.


def call(base, key, method, path):
    req = urllib.request.Request(base + path, method=method, headers={"X-API-Key": key})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def seed(base, admin, out):
    keys = {"admin": admin, "wrong": "not-a-key"}
    for role in ["read-only", "read-write"]:
        req = urllib.request.Request(
            base + "/api/keys", method="POST",
            headers={"X-API-Key": admin, "Content-Type": "application/json"},
            data=json.dumps({"name": f"sync-{role}", "role": role, "prefixes": ["a/"]}).encode(),
        )
        with urllib.request.urlopen(req) as res:
            keys[role] = json.loads(res.read())["key"]
    json.dump(keys, open(out, "w"))


def probe(base, keys_file, out):
    keys = json.load(open(keys_file))
    replies = []
    for name in ["admin", "read-only", "read-write", "wrong"]:
        for method, path in PROBES:
            status, body = call(base, keys[name], method, path)
            replies.append({"request": f"{name} {method} {path}", "status": status, "body": body})
    json.dump(replies, open(out, "w"), indent=1, sort_keys=True)


def compare(left, right):
    a, b = json.load(open(left)), json.load(open(right))
    bad = 0
    for x, y in zip(a, b, strict=True):
        if (x["status"], x["body"]) != (y["status"], y["body"]):
            bad += 1
            print(f"DIFF {x['request']}\n  python {x['status']} {x['body']}\n  rust   {y['status']} {y['body']}",
                  file=sys.stderr)
    print(f"{len(a) - bad} of {len(a)} sync replies are equal")
    return 1 if bad else 0


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "seed":
        seed(*args)
    elif cmd == "probe":
        probe(args[0], args[2], args[3])
    else:
        sys.exit(compare(*args))
