#!/usr/bin/env python3
"""Send the extract jobs that export-legacy moved to a file (task P7-1).

After a return to the Python server, run this script once. It sends each job
of the file to POST /memory/extract of the Python server. The IDs of the sent
jobs go to <file>.sent, so a second run sends only the jobs that failed.

Usage: resend_extract_jobs.py <file> [--base-url URL]
The admin key comes from the environment variable MEMORIES_API_KEY or API_KEY.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

FIELDS = ("messages", "source", "context", "debug", "dry_run", "document_at", "promotion_context")
TRIES = 5


def body_of(request):
    """The body of POST /memory/extract: the stored request without null values."""
    return {k: request[k] for k in FIELDS if request.get(k) is not None}


def read_jobs(path):
    jobs, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                job = json.loads(line)
                # A second export can write a job again.
                if job["job_id"] not in seen:
                    seen.add(job["job_id"])
                    jobs.append(job)
    return jobs


def send(base_url, key, body, sleep=time.sleep):
    """Send one job. Returns the reply. Retries after HTTP 429 and 503."""
    data = json.dumps(body).encode()
    for attempt in range(TRIES):
        req = urllib.request.Request(
            base_url.rstrip("/") + "/memory/extract",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == TRIES - 1:
                raise
            sleep(float(e.headers.get("Retry-After") or 2))
    raise AssertionError("unreachable")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("file")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = p.parse_args(argv)
    key = os.environ.get("MEMORIES_API_KEY") or os.environ.get("API_KEY") or ""
    sent_path = args.file + ".sent"
    sent = set()
    if os.path.exists(sent_path):
        with open(sent_path, encoding="utf-8") as f:
            sent = {line.strip() for line in f if line.strip()}
    failed = 0
    jobs = [j for j in read_jobs(args.file) if j["job_id"] not in sent]
    with open(sent_path, "a", encoding="utf-8") as out:
        for job in jobs:
            try:
                reply = send(args.base_url, key, body_of(job["request"]))
            except (urllib.error.URLError, OSError) as e:
                failed += 1
                print(f"{job['job_id']}: not sent ({e})", file=sys.stderr)
                continue
            out.write(job["job_id"] + "\n")
            out.flush()
            print(json.dumps({"old_job_id": job["job_id"], "new_job_id": reply.get("job_id")}))
    print(f"sent {len(jobs) - failed}, failed {failed}, sent before {len(sent)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
