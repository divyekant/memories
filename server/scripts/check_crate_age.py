"""Fail if a crate in Cargo.lock was published less than N days ago.

Reads the publish time (`pubtime`) from the crates.io sparse index.
Usage: python3 check_crate_age.py [Cargo.lock] [--days 2]
"""

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request

INDEX = "https://index.crates.io/"
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"


def registry_packages(lock_text):
    out = []
    for block in lock_text.split("[[package]]")[1:]:
        fields = dict(re.findall(r'^(\w+) = "([^"]*)"', block, re.M))
        if fields.get("source") == CRATES_IO:
            out.append((fields["name"], fields["version"]))
    return out


def index_path(name):
    n = name.lower()
    if len(n) <= 2:
        return f"{len(n)}/{n}"
    if len(n) == 3:
        return f"3/{n[0]}/{n}"
    return f"{n[:2]}/{n[2:4]}/{n}"


def too_new(pubtime, now, days):
    # Index entries from before 2025 have no pubtime. Those releases are old.
    if not pubtime:
        return False
    published = dt.datetime.fromisoformat(pubtime.replace("Z", "+00:00"))
    return now - published < dt.timedelta(days=days)


def pubtimes(name):
    with urllib.request.urlopen(INDEX + index_path(name), timeout=30) as r:
        return {e["vers"]: e.get("pubtime") for e in map(json.loads, r.read().decode().splitlines())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lock", nargs="?", default="Cargo.lock")
    ap.add_argument("--days", type=int, default=2)
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    with open(a.lock) as f:
        pkgs = registry_packages(f.read())
    bad = []
    cache = {}
    for name, vers in pkgs:
        if name not in cache:
            cache[name] = pubtimes(name)
        if vers not in cache[name]:
            bad.append(f"{name} {vers}: not in the crates.io index")
        elif too_new(cache[name][vers], now, a.days):
            bad.append(f"{name} {vers}: published {cache[name][vers]}")
    if bad:
        print(f"Crates published less than {a.days} days ago:", *bad, sep="\n  ")
        return 1
    print(f"OK: {len(pkgs)} crates are {a.days} days old or more.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
