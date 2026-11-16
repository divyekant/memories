"""Gate G6 (task P6-1): run the real clients against two servers and compare them.

The reference server is the Python server. The new server is the Rust server.
Each server must be new and empty. The script runs the same client steps
against each server, with no change to a client:

- mcp: the stdio MCP server (`mcp-server/index.js`), each of its 18 tools
  (`server/scripts/g6_mcp.mjs`).
- cli: the Python CLI (`memories`), each command that talks to the server.
- hooks: each hook of the Claude Code plugin and of the Codex integration,
  with the time limit of its `hooks.json`.
- opencode: the OpenCode plugin (`integrations/opencode/plugin/memories.js`).
- installer: `memories doctor` of the npm installer (the health check).
- probes: one HTTP request for each (route, status) pair that a Python test
  asserts and that no golden case of the parity kit has (PROBES). The test list
  is `docs/rust-rewrite/gates/G6-tests.tsv` (`python -m parity.sort_tests`).

The script masks the values that change on each run, then compares the output
of each step. A step passes if the two outputs are the same, or if KNOWN has a
reason for the difference. On the new server, each step must also exit as on
the reference server, and each hook must end inside its time limit.

Usage, from the repository root:
  .venv/bin/python -m parity.clients --ref http://127.0.0.1:18001 \
      --new http://127.0.0.1:18002 --key parity-admin-key [--only mcp,cli]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from parity import kit

ROOT = Path(__file__).resolve().parents[1]
PY_BIN = Path(sys.executable).parent
SUITES = ("mcp", "cli", "hooks", "opencode", "installer", "probes")
PROJECT = "g6proj"

# Masks for text output, after the masks of the parity kit.
TEXT_MASKS = [
    (re.compile(r"Index size: \d+KB"), "Index size: <n>KB"),
    (re.compile(r"\b\d+\.\d{3,}\b"), lambda m: f"{float(m.group()):.2f}"),
    (re.compile(r"\b\d+(\.\d+)?\s?ms\b"), "<ms>"),
    (re.compile(r"/tmp/[^\s\"']+"), "<tmp>"),
    (re.compile(r"(\d{4}-\d{2}-\d{2})"), "<date>"),
    (re.compile(r"127\.0\.0\.1:\d+"), "<server>"),
]

# Steps whose output depends on the request timing (for example, how many polls
# a job needed). Only the keys and the value types compare.
SHAPE_ONLY = {("cli", "metrics")}

# Text that the output of a step must hold on the new server, so that a step
# that finds nothing on the two servers does not pass. Key: (suite, step suffix).
MUST = {
    ("mcp", "search"): "blue cluster",
    ("hooks", ":memory-recall.sh"): "g6proj",
    ("hooks", ":memory-query.sh"): "g6proj",
    ("hooks", ":memory-subagent-recall.sh"): "Postgres 17",
    ("opencode", "system-transform"): "Postgres 17",
    ("installer", "doctor"): "healthy",
}

# Keys whose text comes from the HTTP client of the LLM provider
# (docs/decisions/2026-11-16-rust-extract-pipeline.md, row "Provider error text").
PROVIDER_ERROR_KEYS = {"error_message", "primary_error"}

# Queue numbers depend on the jobs of the earlier steps that wait for a retry.
QUEUE_KEYS = {"queue_depth"}

# Probes: (name, key, method, path, body). The name ends in the status that the
# Python test asserts. Keys: admin, none, wrong, revoked (a revoked key), ro (read-only,
# claude-code/), scoped (read-write, claude-code/g6scope). {other} is a record of
# codex/g6other, {mine} a record of claude-code/g6scope, {job} an extract job of codex/g6other.
PROBES = [
    ("folders-401", "none", "GET", "/folders", None),
    ("folders-rename-401", "none", "POST", "/folders/rename", {"old_name": "a", "new_name": "b"}),
    ("folders-rename-404", "admin", "POST", "/folders/rename", {"old_name": "g6/none", "new_name": "g6/new"}),
    ("folders-rename-422", "admin", "POST", "/folders/rename", {"old_name": "", "new_name": ""}),
    ("extraction-quality-401", "none", "GET", "/metrics/extraction-quality", None),
    ("extraction-quality-403", "ro", "GET", "/metrics/extraction-quality", None),
    ("enforce-policies-401", "none", "POST", "/maintenance/enforce-policies", None),
    ("enforce-policies-403", "ro", "POST", "/maintenance/enforce-policies", None),
    ("compact-401", "none", "POST", "/maintenance/compact", {}),
    ("compact-403", "ro", "POST", "/maintenance/compact", {}),
    ("reembed-401", "none", "POST", "/maintenance/reembed", {}),
    ("reembed-403", "ro", "POST", "/maintenance/reembed", {}),
    ("quality-summary-401", "none", "GET", "/metrics/quality-summary", None),
    ("quality-summary-403", "ro", "GET", "/metrics/quality-summary", None),
    ("quality-summary-422", "admin", "GET", "/metrics/quality-summary?period=bogus", None),
    ("failures-401", "none", "GET", "/metrics/failures", None),
    ("failures-403", "ro", "GET", "/metrics/failures", None),
    ("failures-422", "admin", "GET", "/metrics/failures?type=bogus", None),
    ("search-401-none", "none", "POST", "/search", {"query": "g6"}),
    ("search-401-wrong", "wrong", "POST", "/search", {"query": "g6"}),
    ("search-401-revoked", "revoked", "POST", "/search", {"query": "g6"}),
    ("search-403-prefix", "scoped", "POST", "/search", {"query": "g6", "source_prefixes": ["claude-code/g6scope", "codex/"]}),
    ("is-novel-401", "none", "POST", "/memory/is-novel", {"text": "g6"}),
    ("feedback-401", "none", "POST", "/search/feedback", {"memory_id": "{mine}", "signal": "useful"}),
    ("feedback-403", "scoped", "POST", "/search/feedback", {"memory_id": "{other}", "signal": "useful"}),
    ("feedback-422", "admin", "POST", "/search/feedback", {"memory_id": "{mine}", "signal": "bogus"}),
    ("explain-403", "scoped", "POST", "/search/explain", {"query": "g6"}),
    ("audit-403", "ro", "GET", "/audit", None),
    ("usage-403", "scoped", "GET", "/usage", None),
    ("backups-403", "scoped", "GET", "/backups", None),
    ("count-403", "scoped", "GET", "/memories/count?source=codex/", None),
    ("list-403", "scoped", "GET", "/memories?source=codex/", None),
    ("delete-403-ro", "ro", "DELETE", "/memory/{mine}", None),
    ("delete-403-source", "scoped", "DELETE", "/memory/{other}", None),
    ("add-batch-403", "scoped", "POST", "/memory/add-batch",
     {"memories": [{"text": "g6 ok", "source": "claude-code/g6scope"}, {"text": "g6 no", "source": "codex/g6other"}]}),
    ("upsert-403", "scoped", "POST", "/memory/upsert", {"text": "g6", "source": "codex/g6other", "key": "g6-k"}),
    ("supersede-403", "scoped", "POST", "/memory/supersede", {"old_id": "{other}", "new_text": "g6 new"}),
    ("supersede-404", "admin", "POST", "/memory/supersede", {"old_id": 999999, "new_text": "g6 new"}),
    ("supersede-id-404", "admin", "POST", "/memory/999999/supersede", {"text": "g6 new"}),
    ("keys-create-403", "ro", "POST", "/api/keys", {"name": "g6-x", "role": "read-only", "prefixes": ["claude-code/"]}),
    ("keys-create-400", "admin", "POST", "/api/keys", {"name": "g6-x", "role": "read-only", "prefixes": []}),
    ("keys-update-422", "admin", "PATCH", "/api/keys/00000000-0000-0000-0000-000000000000", {"principal_id": "Bad Principal"}),
    ("keys-revoke-404", "admin", "DELETE", "/api/keys/00000000-0000-0000-0000-000000000000", None),
    ("snapshot-restore-403", "ro", "POST", "/snapshots/g6-none/restore", None),
    ("restore-400", "admin", "POST", "/restore", {"backup_name": "../etc"}),
    ("merge-422-one", "admin", "POST", "/memory/merge", {"ids": ["{mine}"], "merged_text": "g6", "source": "claude-code/g6scope"}),
    ("merge-422-empty", "admin", "POST", "/memory/merge", {"ids": [], "merged_text": "g6", "source": "claude-code/g6scope"}),
    # The real Python engine gives 500 here; the 400 of the Python test needs a mock.
    ("merge-500-missing", "admin", "POST", "/memory/merge", {"ids": ["{mine}", 999999], "merged_text": "g6", "source": "claude-code/g6scope"}),
    ("extract-422-size", "admin", "POST", "/memory/extract", {"messages": "x" * 600000, "source": "claude-code/g6scope"}),
    ("extract-400-scoped", "scoped", "POST", "/memory/extract", {"messages": "user: g6"}),
    ("extract-dry-run-202", "admin", "POST", "/memory/extract", {"messages": "user: g6", "source": "claude-code/g6scope", "dry_run": True}),
    ("extract-job-403", "scoped", "GET", "/memory/extract/{job}", None),
    ("extract-commit-422", "admin", "POST", "/memory/extract/commit", {"actions": []}),
    ("promotion-get-401", "none", "GET", "/promotions/1", None),
    ("promotion-reject-422-spoofed", "admin", "POST", "/promotions/1/reject", {"reason": "dismiss", "owner": "bob"}),
    ("promotion-reject-422-long", "admin", "POST", "/promotions/1/reject", {"reason": "x" * 2001}),
    ("profile-delete-404", "admin", "DELETE", "/extraction/profiles/g6-none/", None),
    ("sync-restore-404-traversal", "admin", "POST", "/sync/restore/../../../etc?confirm=true", None),
    ("missed-422-policy", "admin", "POST", "/memory/missed", {"text": "g6 decision", "source": "project/demo/decisions"}),
    ("commit-422-policy", "admin", "POST", "/memory/extract/commit", {"source": "project/demo/decisions", "actions": [
        {"approved": True, "action": "add", "fact": {"text": "a g6 decision", "category": "decision"}, "fact_index": 0}]}),
    # The real servers report a policy error for each line; the 422 of the Python test needs a mock.
    ("import-200-policy-line", "admin", "POST", "/import?strategy=add",
     '{"_header": true}\n{"text": "g6 decision", "source": "project/demo/decisions"}'),
]

# Known differences: (suite, step) -> reason. Each reason names the record
# that accepts the difference. Add an entry only with such a record.
KNOWN = {
    ("cli", "stats"): "backup_count and created_at: no metadata.json copy and no metadata.json "
                      "(docs/decisions/2026-11-16-rust-core-writes.md).",
    ("cli", "health"): "version 6.0.0 (G6.md, finding 1), and two more keys for an admin key "
                       "(docs/decisions/2026-11-16-rust-extract-queue.md).",
    ("cli", "backup-list"): "the Rust server makes no metadata.json copy before a write, so only the "
                            "backups that a client asks for are in the list "
                            "(docs/decisions/2026-11-16-rust-core-writes.md, row `_backup`).",
    ("cli", "extract-status"): "queue_max is 10,000 on the Rust server: the jobs are on disk "
                               "(server/crates/memories-server/src/extract.rs, ExtractConfig).",
    ("installer", "doctor"): "deployed version 6.0.0 differs from BACKEND_VERSION 5.19.0 until "
                             "the release bump of task P7-2 (G6.md, finding 1).",
}


def mask(value):
    if isinstance(value, str):
        value = kit._text(value)
        for pattern, repl in TEXT_MASKS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: "<provider error>" if k in PROVIDER_ERROR_KEYS and v else
                "<queue>" if k in QUEUE_KEYS else mask(v)
                for k, v in kit.normalize(value).items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def shape(value):
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else kit._type_name(value)


def parse(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


class Run:
    """The client steps against one server. Each step adds one record."""

    def __init__(self, base, key, work):
        self.base, self.key, self.work = base.rstrip("/"), key, Path(work)
        self.home = self.work / "home"
        self.proj = self.work / PROJECT
        self.home.mkdir(parents=True)
        self.proj.mkdir()
        self.records = []
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "MEMORIES_URL": self.base,
            "MEMORIES_API_KEY": self.key,
            "MEMORIES_ENV_FILE": str(self.work / "no-env-file"),
            "MEMORIES_BACKENDS_FILE": "__g6_single_backend__",
            "NO_COLOR": "1",
        }

    def add(self, suite, step, rc, out, seconds=0.0, limit=None, expect_rc=None):
        self.records.append({"suite": suite, "step": step, "rc": rc, "out": out,
                             "seconds": round(seconds, 2), "limit": limit, "expect_rc": expect_rc})

    def api(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-API-Key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")

    def sh(self, cmd, stdin=None, cwd=None, timeout=120):
        t = time.monotonic()
        p = subprocess.run(cmd, check=False, input=stdin, text=True, capture_output=True, env=self.env,
                           cwd=cwd or ROOT, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, time.monotonic() - t

    # -- mcp ---------------------------------------------------------------

    def mcp(self):
        rc, out, err, _ = self.sh(["node", "server/scripts/g6_mcp.mjs", self.base, self.key], timeout=300)
        if rc != 0:
            self.add("mcp", "driver", rc, err[-2000:])
        for line in out.splitlines():
            r = json.loads(line)
            self.add("mcp", r["step"], int(r["isError"]), r["text"])

    # -- cli ---------------------------------------------------------------

    def cli(self, step, *args, stdin=None, expect_rc=0):
        rc, out, err, sec = self.sh([str(PY_BIN / "memories"), "--json", *args], stdin=stdin)
        value = parse(out)
        self.add("cli", step, rc, value if out.strip() else err.strip()[-500:], sec, expect_rc=expect_rc)
        return value

    def cli_suite(self):
        src = f"claude-code/{PROJECT}"
        f = self.work / "files"
        f.mkdir()
        a = self.cli("add", "add", "-s", src, "The g6 cache lives in /var/cache/g6 on each node.")
        b = self.cli("add-stdin", "add", "-s", f"codex/{PROJECT}", "-",
                     stdin="Codex runs the g6 test suite with nextest before each push.")
        self.cli("add-learning", "add", "-s", f"learning/{PROJECT}", "The g6 port is 8900 by default.")
        a_id, b_id = a["data"]["id"], b["data"]["id"]
        self.cli("upsert-1", "upsert", "-s", f"wip/{PROJECT}", "-k", "g6-state", "g6 work is at step one.")
        self.cli("upsert-2", "upsert", "-s", f"wip/{PROJECT}", "-k", "g6-state", "g6 work is at step two.")
        self.cli("get", "get", str(a_id))
        self.cli("get-missing", "get", "999999", expect_rc=None)
        self.cli("count", "count", "--source", src)
        self.cli("count-all", "count")
        self.cli("list", "list", "--source", "claude-code/")
        self.cli("list-page", "list", "--offset", "1", "--limit", "2")
        self.cli("search", "search", "where is the g6 cache", "--source", src)
        self.cli("search-vector", "search", "where is the g6 cache", "--no-hybrid", "-k", "2")
        self.cli("search-recency", "search", "g6 tests", "--recency", "0.3", "--half-life", "7")
        # The recency boost uses the age of each record at the time of the search, which
        # changes between the two runs. The order of the results compares; the scores do not.
        last = self.records[-1]
        if isinstance(last["out"], dict):
            last["out"] = [[r.get("id"), r.get("match_type")] for r in last["out"].get("data", {}).get("results", [])]
        self.cli("is-novel-no", "is-novel", "The g6 cache lives in /var/cache/g6 on each node.")
        self.cli("is-novel-yes", "is-novel", "Owls of the g6 forest hunt at dawn.")
        self.cli("folders", "folders")
        self.cli("links-add", "links", "add", str(a_id), str(b_id), "--type", "related_to")
        self.cli("links-list", "links", "list", str(a_id))
        self.cli("links-remove", "links", "remove", str(a_id), str(b_id), "--type", "related_to", expect_rc=None)
        (f / "batch.json").write_text(json.dumps([
            {"text": "g6 batch record about the build cache.", "source": f"{src}/batch"},
            {"text": "g6 batch record about the release tag.", "source": f"{src}/batch"},
        ]))
        added = self.cli("batch-add", "batch", "add", str(f / "batch.json"))
        ids = [str(i) for i in added["data"]["ids"]]
        self.cli("batch-get", "batch", "get", *ids)
        (f / "queries.json").write_text(json.dumps([{"query": "g6 build cache", "k": 2},
                                                    {"query": "g6 release tag", "k": 2}]))
        self.cli("batch-search", "batch", "search", str(f / "queries.json"))
        (f / "upsert.json").write_text(json.dumps([
            {"text": "g6 upsert batch one.", "source": f"{src}/batch", "key": "g6-u1"},
            {"text": "g6 upsert batch two.", "source": f"{src}/batch", "key": "g6-u2"},
        ]))
        self.cli("batch-upsert", "batch", "upsert", str(f / "upsert.json"))
        self.cli("batch-delete", "batch", "delete", *ids)
        self.cli("export", "export", "-o", str(f / "export.ndjson"), "--source", src)
        exported = (f / "export.ndjson").read_text() if (f / "export.ndjson").exists() else ""
        self.add("cli", "export-file", 0, [mask(parse(x)) for x in exported.splitlines() if x.strip()])
        self.cli("import", "import", str(f / "export.ndjson"), "--strategy", "add",
                 "--source-remap", f"{src}=claude-code/{PROJECT}-copy")
        self.cli("import-smart", "import", str(f / "export.ndjson"), "--strategy", "smart", "--no-backup")
        self.cli("health", "admin", "health")
        self.cli("stats", "admin", "stats")
        self.cli("metrics", "admin", "metrics", expect_rc=None)
        self.cli("usage", "admin", "usage", expect_rc=None)
        self.cli("conflicts", "admin", "conflicts")
        self.cli("deduplicate", "admin", "deduplicate", "--dry-run")
        self.cli("consolidate", "admin", "consolidate", expect_rc=None)
        self.cli("prune", "admin", "prune", expect_rc=None)
        self.cli("reload-embedder", "admin", "reload-embedder", expect_rc=None)
        self.cli("auth-status", "auth", "status", expect_rc=None)
        backup = self.cli("backup-create", "backup", "create")
        self.cli("backup-list", "backup", "list")
        name = (backup.get("data") or {}).get("backup_name") if isinstance(backup, dict) else None
        if name:
            self.cli("backup-restore", "backup", "restore", name, "-y", expect_rc=None)
        (f / "transcript.txt").write_text("user: The g6 deploy runs at noon.\nassistant: Noted.\n")
        job = self.cli("extract-submit", "extract", "submit", str(f / "transcript.txt"), "-s", src)
        job_id = (job.get("data") or {}).get("job_id") if isinstance(job, dict) else None
        if job_id:
            self.cli("extract-poll", "extract", "poll", job_id, "--wait", "--timeout", "60",
                     expect_rc=None)
        self.cli("extract-status", "extract", "status")
        self.cli("sync-status", "sync", "status", expect_rc=None)
        self.cli("sync-snapshots", "sync", "snapshots", expect_rc=None)
        # See add-anchor in server/scripts/g6_mcp.mjs: this record keeps the highest ID.
        self.cli("add-anchor", "add", "-s", f"{src}/anchor", "Kestrels nest on the north tower; this CLI record stays.")
        self.cli("delete", "delete", str(b_id))
        self.cli("delete-by-prefix", "delete-by", "prefix", f"claude-code/{PROJECT}-copy", "-y")
        self.cli("delete-by-source", "delete-by", "source", "g6proj/batch", "-y")
        self.cli("count-end", "count")

    # -- hooks -------------------------------------------------------------

    def seed_hooks(self):
        for client in ("claude-code", "codex", "learning"):
            self.api("POST", "/memory/add", {"text": f"The {PROJECT} database is Postgres 17, chosen for "
                                             f"row-level security ({client}).", "source": f"{client}/{PROJECT}"})

    def hooks_suite(self):
        transcript = self.work / "transcript.jsonl"
        transcript.write_text("\n".join(json.dumps(x) for x in [
            {"type": "user", "message": {"role": "user", "content": f"Which database does {PROJECT} use?"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": f"{PROJECT} uses Postgres 17. We decided on it for row-level security."}]}},
        ]) + "\n")
        payload = {
            "session_id": "g6-session", "cwd": str(self.proj), "transcript_path": str(transcript),
            "agent_transcript_path": str(transcript), "agent_type": "general-purpose", "agent_id": "g6-agent",
            "prompt": f"what did we decide about the {PROJECT} database?", "source": "startup",
            "tool_name": "mcp__memories__memory_search",
            "tool_input": {"query": f"{PROJECT} database", "source_prefix": f"claude-code/{PROJECT}",
                           "file_path": str(self.proj / "notes.md")},
            "tool_response": {"content": [{"type": "text", "text": "Found 1 memory"}]},
            "last_assistant_message": f"{PROJECT} uses Postgres 17 for row-level security.",
            "stop_hook_active": False, "trigger": "auto",
        }
        before = self.api("GET", "/extract/status").get("jobs_tracked", 0)
        for client, hook_dir in (("claude-code", ROOT / "plugin/hooks"), ("codex", ROOT / "integrations/codex/hooks")):
            spec = json.loads((hook_dir / "hooks.json").read_text())["hooks"]
            for event, groups in spec.items():
                for group in groups:
                    for hook in group["hooks"]:
                        script = hook_dir / Path(hook["command"].split()[-1].strip("\"'")).name
                        body = dict(payload, hook_event_name=event)
                        rc, out, _err, sec = self.sh([str(script)], stdin=json.dumps(body), cwd=self.proj, timeout=90)
                        self.add("hooks", f"{client}:{event}:{script.name}", rc,
                                 parse(out) if out.strip() else "", sec, hook.get("timeout"), expect_rc=0)
        # The extract hooks send their jobs in the background; wait for the jobs to arrive.
        deadline = time.monotonic() + 30
        after = before
        while time.monotonic() < deadline:
            after = self.api("GET", "/extract/status").get("jobs_tracked", 0)
            time.sleep(2)
            if self.api("GET", "/extract/status").get("jobs_tracked", 0) == after:
                break
        self.add("hooks", "extract-jobs-sent", 0, after - before)

    # -- opencode ----------------------------------------------------------

    def opencode_suite(self):
        script = f"""
const plugin = require({json.dumps(str(ROOT / "integrations/opencode/plugin/memories.js"))});
(async () => {{
  const hooks = plugin.server({{ directory: {json.dumps(str(self.proj))}, worktree: {json.dumps(str(self.proj))} }},
    {{ fetchTimeoutMs: 5000 }});
  const out = {{}};
  await hooks["experimental.chat.system.transform"]({{ sessionID: "g6", messages: [
    {{ info: {{ role: "user" }}, parts: [{{ type: "text", text: "what did we decide about the {PROJECT} database?" }}] }}] }}, out);
  await hooks["tool.execute.after"]({{ tool: "memories_memory_search", sessionID: "g6",
    args: {{ query: "{PROJECT} database", source_prefix: "opencode/{PROJECT}" }} }});
  console.log(JSON.stringify(out.system));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
"""
        rc, out, err, sec = self.sh(["node", "-e", script], cwd=self.proj)
        self.add("opencode", "system-transform", rc, parse(out) if out.strip() else err[-1000:], sec, expect_rc=0)

    # -- installer ---------------------------------------------------------

    def installer_suite(self):
        rc, out, err, sec = self.sh(["node", str(ROOT / "mcp-server/cli/index.mjs"), "doctor", "--url", self.base],
                                    cwd=self.proj)
        self.add("installer", "doctor", rc, out + err, sec, expect_rc=0)

    # -- probes ------------------------------------------------------------

    def probe_suite(self):
        a = lambda path, body=None, method="POST": self.api(method, path, body)
        other = a("/memory/add", {"text": "g6 probe record of another source.", "source": "codex/g6other"})["id"]
        mine = a("/memory/add", {"text": "g6 probe record of the scoped source.", "source": "claude-code/g6scope"})["id"]
        keys = {"admin": self.key, "none": None, "wrong": "g6-wrong-key"}
        for name, role, prefixes in (("ro", "read-only", ["claude-code/"]), ("scoped", "read-write", ["claude-code/g6scope"]),
                                     ("revoked", "read-write", ["codex/"])):
            k = a("/api/keys", {"name": f"g6-{name}", "role": role, "prefixes": prefixes})
            keys[name] = k["key"]
            if name == "revoked":
                a(f"/api/keys/{k['id']}", method="DELETE")
        job = a("/memory/extract", {"messages": "user: g6 probe job.", "source": "codex/g6other"})["job_id"]
        vars_ = {"other": other, "mine": mine, "job": job}
        for name, key, method, path, body in PROBES:
            path = path.format(**vars_)
            if isinstance(body, str):  # a raw body, for example NDJSON
                data = body.encode()
            else:
                body = json.loads(json.dumps(body).replace('"{other}"', str(other)).replace('"{mine}"', str(mine)))
                data = None if body is None else json.dumps(body).encode()
            headers = {"Content-Type": "application/json"}
            if keys[key]:
                headers["X-API-Key"] = keys[key]
            req = urllib.request.Request(self.base + path, method=method, headers=headers, data=data)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    status, raw = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
            self.add("probes", name, status, parse(raw.decode() or "null"))

    def run(self, only):
        if "mcp" in only:
            self.mcp()
        if "cli" in only:
            self.cli_suite()
        if "hooks" in only or "opencode" in only:
            self.seed_hooks()
        if "hooks" in only:
            self.hooks_suite()
        if "opencode" in only:
            self.opencode_suite()
        if "installer" in only:
            self.installer_suite()
        if "probes" in only:
            self.probe_suite()
        return self.records


def compare(ref, new):
    """Return (rows, failures). A row: suite, step, verdict, detail."""
    rows, failures = [], 0
    by_key = {(r["suite"], r["step"]): r for r in ref}
    for n in new:
        key = (n["suite"], n["step"])
        r = by_key.pop(key, None)
        problems, known = [], False
        if r is None:
            problems.append("step is missing on the reference server")
        else:
            left, right = mask(r["out"]), mask(n["out"])
            if key in SHAPE_ONLY:
                left, right = shape(left), shape(right)
            diffs = kit.diff_values(left, right)
            if r["rc"] != n["rc"]:
                diffs.insert(0, f"exit {r['rc']} != {n['rc']}")
            if diffs:
                if key in KNOWN:
                    known = True
                    rows.append((*key, "known", KNOWN[key] + " " + "; ".join(diffs)[:300]))
                else:
                    problems += diffs
        for (suite, suffix), text in MUST.items():
            if n["suite"] == suite and n["step"].endswith(suffix) and text not in json.dumps(n["out"]):
                problems.append(f"the output does not hold {text!r}")
        if n["step"] == "extract-jobs-sent" and not n["out"]:
            problems.append("the hooks sent no extract job")
        want = re.search(r"-(\d{3})(-|$)", n["step"]) if n["suite"] == "probes" else None
        if want and {n["rc"], (r or n)["rc"]} != {int(want.group(1))}:
            problems.append(f"status {(r or n)['rc']} and {n['rc']}, expected {want.group(1)}")
        if n["expect_rc"] is not None and n["rc"] != n["expect_rc"]:
            problems.append(f"exit {n['rc']}, expected {n['expect_rc']}")
        if n["limit"] is not None and n["seconds"] > n["limit"]:
            problems.append(f"{n['seconds']} s, over the time limit of {n['limit']} s")
        if problems:
            failures += 1
            rows.append((*key, "FAIL", "; ".join(problems)[:1500]))
        elif not known:
            rows.append((*key, "same", f"{n['seconds']} s" + (f" (limit {n['limit']} s)" if n["limit"] else "")))
    for key in by_key:
        failures += 1
        rows.append((*key, "FAIL", "step is missing on the new server"))
    return rows, failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref", required=True, help="base URL of the reference (Python) server")
    ap.add_argument("--new", required=True, help="base URL of the new (Rust) server")
    ap.add_argument("--key", required=True, help="admin API key of the two servers")
    ap.add_argument("--only", default=",".join(SUITES), help="comma-separated suites")
    ap.add_argument("--out", help="write the records of the two servers to this directory")
    args = ap.parse_args(argv)
    only = set(args.only.split(","))
    unknown = only - set(SUITES)
    if unknown:
        ap.error(f"unknown suites: {sorted(unknown)}")
    results = {}
    for name, base in (("ref", args.ref), ("new", args.new)):
        with tempfile.TemporaryDirectory(prefix=f"g6-{name}-") as work:
            results[name] = Run(base, args.key, work).run(only)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, recs in results.items():
            kit.dump(recs, out / f"{name}.ndjson")
    rows, failures = compare(results["ref"], results["new"])
    for suite, step, verdict, detail in rows:
        print(f"{verdict:5} {suite:9} {step:45} {detail}")
    counts = {v: sum(1 for r in rows if r[2] == v) for v in ("same", "known", "FAIL")}
    print(f"G6 clients: {len(rows)} steps, {counts['same']} same, {counts['known']} known differences, "
          f"{counts['FAIL']} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
