#!/usr/bin/env python3
"""Sweep the memory corpus for credential-shaped content and redact it.

Stored memories are re-injected verbatim into agent contexts, so a pasted
token becomes a permanent leak. This script scans every memory via the API,
reports matches (dry-run default, secrets never printed — only masked
previews), and with --execute replaces each affected memory:

    1. POST /memory/{id}/supersede with the redacted text (history link)
    2. DELETE /memory/{old_id}?force=true — the archived original still
       contains the secret, so for redactions (unlike normal supersedes)
       the old version is deliberately destroyed.

Usage:
    MEMORIES_URL=... MEMORIES_API_KEY=... python scripts/redact_corpus.py
    python scripts/redact_corpus.py --execute        # apply
    python scripts/redact_corpus.py --limit 500      # scan subset
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transcript_hygiene import redact_secrets  # noqa: E402

BASE = os.environ.get("MEMORIES_URL", "http://localhost:8900").rstrip("/")
KEY = os.environ.get("MEMORIES_API_KEY", "")
PAGE = 200


def _req(path: str, method: str = "GET", body: dict | None = None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", KEY)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _masked_preview(text: str, max_len: int = 80) -> str:
    redacted, _ = redact_secrets(text)
    return (redacted[:max_len] + "…") if len(redacted) > max_len else redacted


def scan(limit: int = 0):
    """Yield (memory, redacted_text, types) for every memory containing secrets."""
    offset = 0
    scanned = 0
    while True:
        page = _req(f"/memories?offset={offset}&limit={PAGE}")
        memories = page.get("memories", [])
        if not memories:
            break
        for m in memories:
            scanned += 1
            redacted, types = redact_secrets(m.get("text", ""))
            if types:
                yield m, redacted, types
            if limit and scanned >= limit:
                return
        offset += PAGE
        if len(memories) < PAGE:
            break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="apply redactions (default: dry-run report)")
    ap.add_argument("--limit", type=int, default=0, help="scan at most N memories (0 = all)")
    args = ap.parse_args()

    hits = 0
    by_type: dict = {}
    for m, redacted, types in scan(args.limit):
        hits += 1
        for t in types:
            by_type[t] = by_type.get(t, 0) + 1
        mid = m.get("id")
        print(f"[{mid}] {','.join(types)} :: {_masked_preview(m.get('text', ''))}")
        if args.execute:
            result = _req(f"/memory/{mid}/supersede", "POST", {"text": redacted})
            old_id = result.get("old_id", mid)
            _req(f"/memory/{old_id}?force=true", "DELETE")
            print(f"    -> redacted as id={result.get('new_id')}; original {old_id} destroyed")

    mode = "EXECUTED" if args.execute else "DRY-RUN (pass --execute to apply)"
    print(f"\n{hits} memorie(s) contain credential-shaped content — {mode}")
    for t, n in sorted(by_type.items()):
        print(f"  {t}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
