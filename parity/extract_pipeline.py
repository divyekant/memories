"""Exit check of task P4-3: the extraction pipeline with recorded LLM replies.

The driver starts a fake OpenAI chat server that gives the scripted replies of
each case in order and records the system and user prompt of each call. Then it
sends each case to a Memories server, waits for the job, and records the
prompts, the job result and the stored records.

Start the server with these settings (server/scripts/parity_pipeline.sh does it):
EXTRACT_PROVIDER=openai, OPENAI_API_KEY=sk-parity,
OPENAI_BASE_URL=http://127.0.0.1:<provider port>/v1, EXTRACT_MAX_INFLIGHT=1,
EXTRACT_FALLBACK_ADD=true. The OpenAI provider is used because the parity
environment has the openai package and not the anthropic package; the
pipeline is the same for each provider.

Usage:
  python -m parity.extract_pipeline --base-url URL --provider-port N --out FILE
  python -m parity.extract_pipeline --base-url URL --provider-port N --check
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from parity.kit import diff_values, normalize

GOLDEN = Path(__file__).parent / "golden" / "extract" / "pipeline.ndjson"
ADMIN = "parity-admin-key"
S = "claude-code/p43"
# Masked on top of the kit rules: the random extraction job ID, and the
# provider error texts, which come from different HTTP clients.
EXTRA_VOLATILE = ("extraction_job_id", "error_message", "primary_error")
RECORD_FIELDS = (
    "id", "text", "source", "archived", "pinned", "category", "supersedes",
    "conflicts_with", "is_latest", "extract_source", "extraction_job_id",
    "extraction_mode", "context", "document_at",
)


# -- fake provider ----------------------------------------------------------------

class Script:
    def __init__(self):
        self.lock = threading.Lock()
        self.replies: list = []
        self.calls: list = []

    def load(self, replies):
        with self.lock:
            self.replies = list(replies)
            self.calls = []

    def next(self, system, user):
        with self.lock:
            if system == "Reply with OK":
                return 200, "OK"
            self.calls.append({"system": system, "user": user})
            if not self.replies:
                return 400, "no scripted reply"
            reply = self.replies.pop(0)
            # A callable reply is a function of the prompt (parity/extract_corpus.py).
            return reply(system, user) if callable(reply) else reply


SCRIPT = Script()


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        msgs = {m["role"]: m["content"] for m in body["messages"]}
        system, user = msgs.get("system", ""), msgs.get("user", "")
        status, text = SCRIPT.next(system, user)
        if status == 200:
            usage = {"prompt_tokens": 11 + len(user) % 7, "completion_tokens": 5}
            usage["total_tokens"] = usage["prompt_tokens"] + 5
            out = {
                "id": "chatcmpl-parity", "object": "chat.completion", "created": 0,
                "model": body.get("model"), "usage": usage,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
            }
        else:
            out = {"error": {"message": text, "type": "invalid_request_error", "code": None}}
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# -- cases --------------------------------------------------------------------------

def ok(text):
    return (200, text if isinstance(text, str) else json.dumps(text))


ERR = (400, "scripted failure")

SEED = [
    ("We use Redis for caching API responses with a ten minute TTL.", S),
    ("The database is PostgreSQL 15 running on the main droplet.", S),
    ("Deploys happen only on Fridays after the weekly review.", S),
    ("The test framework is pytest with the xdist plugin.", S),
    ("The API server listens on port 8900 behind Caddy.", S),
    ("The frontend is built with Svelte and Vite.", S),
    ("Backups run every night at 02:00 UTC to an S3 bucket.", S),
    ("Logs ship to Loki through the Grafana agent.", "codex/p43"),
]

FACTS_1 = [
    {"category": "DECISION", "text": "We chose uv over pip for Python packaging because it is faster."},
    {"category": "learning", "text": "The database is PostgreSQL 16 after the upgrade on the main droplet."},
    {"category": "detail", "text": "Deploys no longer happen on Fridays."},
    {"category": "detail", "text": "The test framework is pytest with xdist."},
    {"category": "decision", "text": "Redis caching uses a five minute TTL for API responses."},
    {"category": "other", "text": "The API server port moved to 9000."},
]


def case_list(ids):
    """The cases. `ids` maps a seed index to its memory ID."""
    return [
        {
            "name": "audn-all-actions",
            "body": {"messages": "User: we moved to uv and Postgres 16.\nAssistant: noted, and Fridays are out.",
                     "source": S, "document_at": "2026-11-01T00:00:00Z"},
            "replies": [
                ok("Here are the facts:\n```json\n" + json.dumps(FACTS_1) + "\n```"),
                ok([
                    {"action": "add", "fact_index": 0},
                    {"action": "UPDATE", "fact_index": 1, "old_id": ids[1],
                     "new_text": "The database is PostgreSQL 16 (upgraded from 15) on the main droplet."},
                    {"action": "DELETE", "fact_index": 2, "old_id": ids[2]},
                    {"action": "NOOP", "fact_index": 3, "existing_id": ids[3]},
                    {"action": "CONFLICT", "fact_index": 4, "old_id": ids[0]},
                    {"action": "UPDATE", "fact_index": 5, "old_id": ids[4], "new_text": "The API port is 9000."},
                    {"fact_index": 0},
                    {"action": "MERGE", "fact_index": 0},
                ]),
            ],
        },
        {
            "name": "novelty-gate",
            "body": {"messages": "User: remember the frontend stack.", "source": S},
            "replies": [ok([{"category": "detail", "text": SEED[5][0]}]),
                        ok([{"action": "ADD", "fact_index": 0}])],
        },
        {
            "name": "pre-compact-hygiene-secrets",
            "body": {"messages": (
                "<system-reminder>\nRecalled: We use Redis.\n</system-reminder>\n"
                "User: the token is sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 do not store it\n"
                "Assistant: AWS key AKIAIOSFODNN7EXAMPLE noted.\n"
                "## Relevant Memories\n- old memory line\n\nUser: thanks"),
                "source": S, "context": "pre_compact"},
            "replies": [ok("[]")],
        },
        {
            "name": "empty-after-hygiene",
            "body": {"messages": "<system-reminder>\nonly a reminder\n</system-reminder>", "source": S},
            "replies": [],
        },
        {
            "name": "extract-error-runtime-fallback",
            "body": {"messages": "User: We decided to use ruff for linting in every Python repo.\nAssistant: ok?",
                     "source": S},
            "replies": [ERR],
        },
        {
            "name": "audn-error-fallback-add",
            "body": {"messages": "User: two notes.", "source": S},
            "replies": [ok([{"category": "detail", "text": "Grafana dashboards live in the ops repo."},
                            "The staging droplet runs in the fra1 region."]),
                        ERR],
        },
        {
            "name": "audn-prose-reply",
            "body": {"messages": "User: one note.", "source": S},
            "replies": [ok([{"category": "learning", "text": "Caddy needs a reload after a certificate rotation."}]),
                        ok('Decisions follow. [{"action": "add", "fact_index": 0}] That is all.')],
        },
        {
            "name": "dry-run",
            "body": {"messages": "User: plan only.", "source": S, "dry_run": True},
            "replies": [ok([{"category": "decision", "text": "Use Tailscale for SSH to every droplet."},
                            {"category": "detail", "text": "The ops runbook is in docs/ops.md."}]),
                        ok([{"action": "ADD", "fact_index": 0}, {"action": "ADD", "fact_index": 1},
                            {"action": "DELETE", "fact_index": 1, "old_id": ids[6]}])],
            "commit": lambda result: {
                "source": S,
                "actions": [dict(a, approved=(i != 1)) for i, a in enumerate(result["actions"])],
            },
        },
        {
            "name": "debug-trace",
            "body": {"messages": "User: debug this.", "source": S, "debug": True},
            "replies": [ok([{"category": "detail", "text": "Nightly backups go to an S3 bucket at 02:00 UTC."},
                            {"category": "detail", "text": "Vite builds the Svelte frontend."}]),
                        ok([{"action": "NOOP", "fact_index": 0, "existing_id": ids[6]},
                            {"action": "ADD", "fact_index": 1}])],
        },
        {
            "name": "profile-single-call",
            "setup": [("PUT", "/extraction/profiles/claude-code/p43-single/",
                       {"single_call": True, "max_facts": 3,
                        "rules": {"always_remember": ["ports"], "never_remember": ["jokes"]}})],
            "body": {"messages": "User: single call please.", "source": "claude-code/p43-single/app"},
            "replies": [ok([
                {"action": "ADD", "category": "detail", "text": "The single-call app uses port 7001."},
                {"action": "UPDATE", "text": "The single-call app logs to stdout."},
                {"action": "NOOP", "text": "Python is a language."},
                {"action": "ADD", "text": "This fourth fact is cut by max_facts."},
            ])],
        },
        {
            "name": "profile-aggressive-rules",
            "setup": [("PUT", "/extraction/profiles/claude-code/p43-aggr/",
                       {"mode": "aggressive", "max_facts": 2, "max_fact_chars": 60,
                        "rules": {"always_remember": ["error messages"], "custom_instructions": "Be brief."}})],
            "body": {"messages": "User: aggressive profile.", "source": "claude-code/p43-aggr/x"},
            "replies": [ok([
                {"category": "learning", "text": "The importer fails with 'invalid UTF-8 sequence' on Latin-1 files from the old system."},
                {"category": "detail", "text": "The importer reads CSV files."},
                {"category": "detail", "text": "This third fact is cut."},
            ]), ok([{"action": "ADD", "fact_index": 0}, {"action": "ADD", "fact_index": 1}])],
        },
        {
            "name": "single-call-bad-item",
            "body": {"messages": "User: bad single call.", "source": "claude-code/p43-single/bad"},
            "replies": [ok(["a plain string", {"action": "ADD", "text": "x"}])],
        },
        {
            "name": "project-policy",
            "body": {"messages": "User: policy.", "source": "project/"},
            "replies": [ok([{"category": "detail", "text": "A fact for a bad project source."}]),
                        ok([{"action": "ADD", "fact_index": 0}])],
        },
        {
            "name": "scoped-key-permissions",
            "key": "scoped",
            "body": {"messages": "User: scoped.", "source": "claude-code/p43-scoped/app"},
            "replies": [ok([{"category": "detail", "text": "Scoped fact one about the scoped app."},
                            {"category": "detail", "text": "Scoped fact two about the scoped app."}]),
                        ok([{"action": "UPDATE", "fact_index": 0, "old_id": ids[5], "new_text": "x"},
                            {"action": "DELETE", "fact_index": 1, "old_id": ids[7]},
                            {"action": "ADD", "fact_index": 1}])],
        },
    ]


PROFILE_CALLS = [
    ("GET", "/extraction/profiles", None),
    ("GET", "/extraction/profiles/claude-code/p43-aggr/", None),
    ("GET", "/extraction/profiles/nope/", None),
    ("DELETE", "/extraction/profiles/claude-code/p43-aggr/", None),
    ("DELETE", "/extraction/profiles/claude-code/p43-aggr/", None),
]


# -- runner ---------------------------------------------------------------------------

def call(client, method, path, key, body=None):
    r = client.request(method, path, headers={"X-API-Key": key}, json=body)
    try:
        data = r.json()
    except ValueError:
        data = r.text
    return [r.status_code, data]


def wait_job(client, job_id):
    deadline = time.time() + 90
    while time.time() < deadline:
        _, job = call(client, "GET", f"/memory/extract/{job_id}", ADMIN)
        if job.get("status") in ("completed", "failed"):
            return job
        time.sleep(0.1)
    raise SystemExit(f"job {job_id} did not finish")


def records(client):
    _, body = call(client, "POST", "/search", ADMIN,
                   {"query": "memory", "k": 100, "hybrid": False, "include_archived": True})
    out = []
    for r in body["results"]:
        rec = {k: r[k] for k in RECORD_FIELDS if k in r}
        links = r.get("links") or []
        rec["links"] = sorted([lk["type"], lk["to_id"]] for lk in links)
        out.append(rec)
    return sorted(out, key=lambda r: r["id"])


def run(base_url, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = []
    with httpx.Client(base_url=base_url, timeout=60) as client:
        status, seeded = call(client, "POST", "/memory/add-batch", ADMIN,
                              {"memories": [{"text": t, "source": s} for t, s in SEED]})
        assert status == 200, seeded
        ids = seeded["ids"]
        call(client, "PATCH", f"/memory/{ids[4]}", ADMIN, {"pinned": True})
        _, scoped = call(client, "POST", "/api/keys", ADMIN,
                         {"name": "p43-scoped", "role": "read-write", "prefixes": ["claude-code/p43-scoped/"]})
        keys = {"admin": ADMIN, "scoped": scoped["key"]}
        for case in case_list(ids):
            rec = {"name": case["name"], "setup": []}
            for method, path, body in case.get("setup", []):
                rec["setup"].append(call(client, method, path, ADMIN, body))
            SCRIPT.load(case["replies"])
            status, reply = call(client, "POST", "/memory/extract", keys[case.get("key", "admin")], case["body"])
            rec["enqueue"] = [status, reply]
            job = wait_job(client, reply["job_id"]) if status == 202 else {}
            rec["job"] = {k: job.get(k) for k in ("status", "error", "result")}
            with SCRIPT.lock:
                rec["prompts"] = list(SCRIPT.calls)
                rec["unused_replies"] = len(SCRIPT.replies)
            if "commit" in case and job.get("result"):
                rec["commit"] = call(client, "POST", "/memory/extract/commit", ADMIN,
                                     case["commit"](job["result"]))
            out.append(rec)
        out.append({"name": "profile-routes", "calls": [call(client, m, p, ADMIN, b) for m, p, b in PROFILE_CALLS]})
        out.append({"name": "records", "records": records(client)})
    server.shutdown()
    return out


def check(got, golden):
    problems = []
    by_name = {c["name"]: c for c in golden}
    for case in got:
        want = by_name.get(case["name"])
        if want is None:
            problems.append(f"{case['name']}: not in the golden file")
            continue
        # The prompts must be equal byte for byte.
        if case.get("prompts") != want.get("prompts"):
            problems.append(f"{case['name']}: the prompts differ")
        a = normalize({k: v for k, v in want.items() if k != "prompts"}, extra=EXTRA_VOLATILE)
        b = normalize({k: v for k, v in case.items() if k != "prompts"}, extra=EXTRA_VOLATILE)
        problems += [f"{case['name']}: {d}" for d in diff_values(a, b)]
    missing = by_name.keys() - {c["name"] for c in got}
    problems += [f"{n}: missing" for n in sorted(missing)]
    return problems


def main(argv=None, run_fn=None, golden_path=None, check_fn=None):
    run_fn = run_fn or run
    check_fn = check_fn or check
    golden_path = golden_path or GOLDEN
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--provider-port", type=int, required=True)
    p.add_argument("--out")
    p.add_argument("--check", action="store_true")
    p.add_argument("--record", action="store_true", help="write the golden file")
    args = p.parse_args(argv)
    if args.record:
        args.out = str(golden_path)
    got = run_fn(args.base_url, args.provider_port)
    if args.out:
        with open(args.out, "w") as f:
            for c in got:
                f.write(json.dumps(c, sort_keys=True) + "\n")
    if args.check:
        golden = [json.loads(line) for line in Path(golden_path).read_text().splitlines()]
        problems = check_fn(got, golden)
        for line in problems:
            print(line)
        print(f"{len(got)} cases, {len(problems)} differences")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
