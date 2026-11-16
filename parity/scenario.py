"""The parity scenario: the seed corpus, then cases for each route and each wire rule.

The order is important. Each case runs on the state that the cases before it
made. The cases that report request counts run before the cases that poll.
"""

from parity import seed

# The wire rules of the Plan tab ("Wire rules that the clients depend on").
WIRE_RULES = {
    "W1-health-minimal": "GET /health needs no key and returns status, service and version.",
    "W2-401-wrong-key": "HTTP 401 means up, but the key is wrong.",
    "W3-422-detail-list": "A validation error is HTTP 422 with a detail list, also from /search/batch.",
    "W4-json-error-body": "An error body is JSON {detail} with content type application/json.",
    "W5-extract-202-fast": "POST /memory/extract returns 202 in less than 2 s.",
    "W6-lenient-input": "Unknown JSON fields are ignored, an empty X-API-Key is no key, batch IDs can be strings.",
    "W7-extract-failure-completed": "A finished extract job with a provider failure is completed with result.error.",
    "W8-export-and-ui": "GET /export streams NDJSON with a header line first. The web UI loads from /ui and /ui/static.",
    "W9-usage-headers": "X-Memories-Client, X-Memories-Session-Id and X-Memories-Invocation feed the usage numbers.",
}

KEYS = {"admin": "{{key:admin}}", "ro": "{{key:ro}}", "rw": "{{key:rw}}"}


def c(name, method, path, route=None, *, json=None, body=None, query=None, key="admin",
      headers=None, rules=(), save=None, until=None, max_ms=None, volatile=(), delay_s=None):
    h = {}
    if key is not None:
        h["X-API-Key"] = KEYS.get(key, key)
    if body is not None:
        h["Content-Type"] = "application/x-ndjson"
    h.update(headers or {})
    request = {"method": method, "path": path, "headers": h}
    if query:
        request["query"] = query
    if json is not None:
        request["json"] = json
    if body is not None:
        request["body"] = body
    case = {"name": name, "route": route or f"{method} {path}", "rules": list(rules), "request": request}
    for k, v in (("save", save), ("until", until), ("max_ms", max_ms), ("volatile", list(volatile)),
                 ("delay_s", delay_s)):
        if v:
            case[k] = v
    return case


S0 = "{{seed0.0}}"  # first seed record
S1 = "{{seed0.1}}"
S2 = "{{seed0.2}}"
M = "/memory/{memory_id}"


def _rules_cases():
    return [
        c("health-minimal", "GET", "/health", key=None, rules=["W1-health-minimal"], volatile=["version"]),
        c("health-empty-key", "GET", "/health", key="", rules=["W1-health-minimal", "W6-lenient-input"],
          volatile=["version"]),
        c("health-ready", "GET", "/health/ready", key=None),
        c("auth-wrong-key", "GET", "/stats", key="wrong-key", rules=["W2-401-wrong-key", "W4-json-error-body"]),
        c("auth-no-key", "GET", "/stats", key=None, rules=["W2-401-wrong-key"]),
        c("auth-empty-key", "GET", "/stats", key="", rules=["W6-lenient-input"]),
        c("ui-page", "GET", "/ui", key=None, rules=["W8-export-and-ui"]),
        c("ui-static", "GET", "/ui/static/utils.js", route="GET /ui/static", key=None, rules=["W8-export-and-ui"]),
        c("search-422", "POST", "/search", json={"k": 3}, rules=["W3-422-detail-list", "W4-json-error-body"]),
        c("search-batch-422", "POST", "/search/batch", json={"queries": []}, rules=["W3-422-detail-list"]),
        c("search-batch-422-bad-query", "POST", "/search/batch", json={"queries": [{"query": ""}]},
          rules=["W3-422-detail-list"]),
        c("memory-404", "GET", "/memory/999999", route=f"GET {M}", rules=["W4-json-error-body"]),
        c("method-405", "PUT", "/search", route="POST /search", rules=["W4-json-error-body"]),
    ]


def _key_cases():
    return [
        c("keys-create-ro", "POST", "/api/keys",
          json={"name": "parity-ro", "role": "read-only", "prefixes": ["claude-code/"]},
          save={"key:ro": "key", "ro_id": "id"}),
        c("keys-create-rw", "POST", "/api/keys",
          json={"name": "parity-rw", "role": "read-write", "prefixes": ["codex/"]},
          save={"key:rw": "key", "rw_id": "id"}),
        c("keys-create-422", "POST", "/api/keys", json={"name": "x", "role": "owner"}),
        c("keys-me-admin", "GET", "/api/keys/me"),
        c("keys-me-ro", "GET", "/api/keys/me", key="ro"),
        c("keys-list", "GET", "/api/keys"),
        c("keys-list-ro-403", "GET", "/api/keys", key="ro"),
        c("keys-update", "PATCH", "/api/keys/{{rw_id}}", route="PATCH /api/keys/{key_id}",
          json={"name": "parity-rw-2", "prefixes": ["codex/", "wip/"]}),
    ]


def _read_cases():
    q = "How is the fleet-api rate limit configured?"
    return [
        c("usage-headers-search", "POST", "/search", json={"query": q, "k": 3},
          headers={"X-Memories-Client": "codex", "X-Memories-Session-Id": "parity-session",
                   "X-Memories-Invocation": "parity-inv-1"}, rules=["W9-usage-headers"]),
        c("usage-headers-add", "POST", "/memory/add",
          json={"text": "Parity usage probe record.", "source": "codex/voltis/usage"},
          headers={"X-Memories-Client": "codex", "X-Memories-Session-Id": "parity-session",
                   "X-Memories-Invocation": "parity-inv-2"}, rules=["W9-usage-headers"]),
        c("usage-session", "GET", "/usage", query="period=all&session_id=parity-session", rules=["W9-usage-headers"]),
        c("usage-7d", "GET", "/usage"),
        c("stats", "GET", "/stats", volatile=["backup_count"]),
        c("health-full", "GET", "/health", volatile=["version"]),
        c("count-all", "GET", "/memories/count"),
        c("count-source", "GET", "/memories/count", query="source=claude-code/"),
        c("count-boundary", "GET", "/memories/count", query="source=codex/voltis&source_boundary=true"),
        c("count-ro-scope", "GET", "/memories/count", key="ro"),
        c("list-page", "GET", "/memories", query="offset=10&limit=15"),
        c("list-source", "GET", "/memories", query="source=wip/&limit=50"),
        c("list-pinned", "GET", "/memories", query="pinned=true&limit=50"),
        c("list-ro-scope", "GET", "/memories", query="limit=30", key="ro"),
        c("list-422", "GET", "/memories", query="limit=0"),
        c("folders", "GET", "/folders"),
        c("folders-ro", "GET", "/folders", key="ro"),
        c("memory-get", "GET", f"/memory/{S0}", route=f"GET {M}"),
        c("memory-get-ro-403", "GET", "/memory/{{conflict1}}", route=f"GET {M}", key="rw"),
        c("memory-get-batch-string-ids", "POST", "/memory/get-batch",
          json={"ids": [S0, "{{seed0.1}}", "999999"], "unknown_field": True}, rules=["W6-lenient-input"]),
        c("memory-get-batch-str", "POST", "/memory/get-batch", json={"ids": ["3", "4"]}, rules=["W6-lenient-input"]),
        c("links-out", "GET", "/memory/{{seed0.5}}/links", route="GET /memory/{memory_id}/links"),
        c("links-in", "GET", "/memory/{{seed0.5}}/links", route="GET /memory/{memory_id}/links",
          query="include_incoming=true"),
        c("conflicts", "GET", "/memory/conflicts"),
        c("search-vector", "POST", "/search", json={"query": q, "k": 5, "hybrid": False}),
        c("search-hybrid", "POST", "/search", json={"query": q, "k": 8, "hybrid": True}),
        c("search-unknown-field", "POST", "/search", json={"query": q, "k": 3, "not_a_field": 1},
          rules=["W6-lenient-input"]),
        c("search-prefix", "POST", "/search", json={"query": "incident root cause", "k": 6, "hybrid": True,
                                                    "source_prefix": "claude-code/"}),
        c("search-prefixes-boundary", "POST", "/search",
          json={"query": "design decision", "k": 6, "hybrid": True,
                "source_prefixes": ["codex/voltis", "wip/voltis"], "source_boundary": True}),
        c("search-archived", "POST", "/search", json={"query": q, "k": 10, "hybrid": True,
                                                      "include_archived": True}),
        c("search-threshold", "POST", "/search", json={"query": q, "k": 10, "threshold": 0.4}),
        c("search-weights", "POST", "/search",
          json={"query": "who owns billing-engine", "k": 6, "hybrid": True, "vector_weight": 0.4,
                "graph_weight": 0.3, "feedback_weight": 0.0,
                "reference_date": "2026-06-01T00:00:00Z"}),
        c("search-temporal", "POST", "/search",
          json={"query": "what changed last month in the billing-engine", "k": 5, "hybrid": True,
                "reference_date": "2026-03-15T00:00:00Z"}),
        c("search-since-until", "POST", "/search",
          json={"query": q, "k": 5, "since": "2020-01-01", "until": "2099-01-01", "auto_intent": False}),
        c("search-ro-scope", "POST", "/search", json={"query": q, "k": 5, "hybrid": True}, key="ro"),
        c("search-explain", "POST", "/search/explain", json={"query": q, "k": 5, "hybrid": True}),
        c("search-evidence", "POST", "/search/evidence", json={"query": q, "k": 5, "hybrid": True}),
        c("search-batch", "POST", "/search/batch",
          json={"queries": [{"query": q, "k": 3}, {"query": "postmortem", "k": 2, "hybrid": True,
                                                   "source_prefix": "codex/"}]}),
        c("is-novel-dup", "POST", "/memory/is-novel", json={"text": "{{seed_text}}"}),
        c("is-novel-new", "POST", "/memory/is-novel", json={"text": "Parity: a fact that no record holds.",
                                                            "threshold": 0.9}),
        c("export-all", "GET", "/export", rules=["W8-export-and-ui"]),
        c("export-source", "GET", "/export", query="source=learning/"),
        c("export-ro", "GET", "/export", key="ro"),
        c("events-recent", "GET", "/events/recent", query="limit=20"),
        c("events-stream", "GET", "/events/stream"),
        c("extraction-profiles", "GET", "/extraction/profiles"),
        c("extraction-profile-default", "GET", "/extraction/profiles/claude-code/voltis",
          route="GET /extraction/profiles/{prefix:path}"),
        c("audit-list", "GET", "/audit", query="limit=20"),
        c("audit-filter", "GET", "/audit", query="action=memory.created&limit=5"),
        c("promotions", "GET", "/promotions"),
        c("promotion-get", "GET", "/promotions/1", route="GET /promotions/{candidate_id}"),
        c("backups", "GET", "/backups", volatile=["backups", "count"]),
        c("snapshots", "GET", "/snapshots"),
        c("sync-status", "GET", "/sync/status"),
        c("sync-snapshots", "GET", "/sync/snapshots"),
        c("webhooks-list", "GET", "/webhooks"),
        c("webhooks-list-ro-403", "GET", "/webhooks", key="ro"),
    ]


def _write_cases():
    return [
        c("add-plain", "POST", "/memory/add",
          json={"text": "Parity: the fleet-api uses a token bucket of 500 requests.",
                "source": "claude-code/voltis/parity", "metadata": {"category": "config", "confidence": 0.8}},
          save={"add_id": "id"}),
        c("add-dedup", "POST", "/memory/add",
          json={"text": "Parity: the fleet-api uses a token bucket of 500 requests.",
                "source": "claude-code/voltis/parity", "deduplicate": True}),
        c("add-on-dup-skip", "POST", "/memory/add",
          json={"text": "Parity: the fleet-api uses a token bucket of 500 requests.",
                "source": "claude-code/voltis/parity", "on_duplicate": "skip"}),
        c("add-on-dup-supersede", "POST", "/memory/add",
          json={"text": "Parity: the fleet-api uses a token bucket of 600 requests.",
                "source": "claude-code/voltis/parity", "on_duplicate": "supersede"}),
        c("add-on-dup-add", "POST", "/memory/add",
          json={"text": "Parity: the fleet-api uses a token bucket of 600 requests.",
                "source": "claude-code/voltis/parity", "on_duplicate": "add"}),
        c("add-ro-403", "POST", "/memory/add", json={"text": "x", "source": "claude-code/voltis"}, key="ro"),
        c("add-rw-out-of-scope", "POST", "/memory/add", json={"text": "x", "source": "claude-code/voltis"},
          key="rw"),
        c("add-rw-in-scope", "POST", "/memory/add", json={"text": "Parity scoped write.",
                                                          "source": "codex/voltis/scoped"}, key="rw"),
        c("add-422", "POST", "/memory/add", json={"text": "", "source": "x"}),
        c("add-batch", "POST", "/memory/add-batch",
          json={"memories": [{"text": f"Parity batch fact {i} about the route-optimizer.",
                              "source": "wip/voltis/batch"} for i in range(4)]},
          save={"batch_ids": "ids"}),
        c("upsert", "POST", "/memory/upsert",
          json={"text": "Parity upsert value one.", "source": "learning/voltis/upsert", "key": "parity-k1"}),
        c("upsert-again", "POST", "/memory/upsert",
          json={"text": "Parity upsert value two.", "source": "learning/voltis/upsert", "key": "parity-k1"}),
        c("upsert-batch", "POST", "/memory/upsert-batch",
          json={"memories": [{"text": f"Parity upsert batch {i}.", "source": "learning/voltis/upsert",
                              "key": f"parity-b{i}"} for i in range(3)]}),
        c("patch-text", "PATCH", "/memory/{{add_id}}", route=f"PATCH {M}",
          json={"text": "Parity: the fleet-api uses a token bucket of 700 requests."}),
        c("patch-meta", "PATCH", "/memory/{{batch_ids.0}}", route=f"PATCH {M}",
          json={"metadata_patch": {"category": "decision", "owner": "parity"}, "source": "wip/voltis/moved"}),
        c("patch-empty-400", "PATCH", "/memory/{{batch_ids.0}}", route=f"PATCH {M}", json={}),
        c("archive-batch", "POST", "/memory/archive-batch", json={"ids": ["{{batch_ids.1}}", 999999]}),
        c("supersede-by-id", "POST", "/memory/{{batch_ids.2}}/supersede", route="POST /memory/{memory_id}/supersede",
          json={"text": "Parity batch fact 2, corrected."}, save={"superseded_new": "new_id"}),
        c("supersede-pinned-409", "POST", "/memory/{{seed_pinned}}/supersede",
          route="POST /memory/{memory_id}/supersede", json={"text": "x"}),
        c("supersede-legacy", "POST", "/memory/supersede",
          json={"old_id": "{{batch_ids.3}}", "new_text": "Parity batch fact 3, replaced.",
                "source": "wip/voltis/batch"}),
        c("merge", "POST", "/memory/merge",
          json={"ids": ["{{seed1.0}}", "{{seed1.1}}"], "merged_text": "Parity merged record.",
                "source": "learning/voltis/merged"}),
        c("link-add", "POST", "/memory/{{add_id}}/link", route="POST /memory/{memory_id}/link",
          json={"to_id": S1, "type": "related_to"}),
        c("link-add-bad-type", "POST", "/memory/{{add_id}}/link", route="POST /memory/{memory_id}/link",
          json={"to_id": S1, "type": "likes"}),
        c("link-remove", "DELETE", "/memory/{{add_id}}/link/" + S1, route="DELETE /memory/{memory_id}/link/{target_id}"),
        c("feedback-useful", "POST", "/search/feedback",
          json={"memory_id": S2, "query": "fleet-api limits", "signal": "useful"}),
        c("feedback-not-useful", "POST", "/search/feedback",
          json={"memory_id": S2, "query": "fleet-api limits", "signal": "not_useful", "search_id": "s1"}),
        c("feedback-history", "GET", "/search/feedback/history", query="memory_id=" + S2,
          save={"fb_id": "entries.0.id"}),
        c("feedback-retract", "DELETE", "/search/feedback/{{fb_id}}", route="DELETE /search/feedback/{feedback_id}"),
        c("missed", "POST", "/memory/missed",
          json={"text": "Parity: the search missed the charging-manager owner.", "source": "claude-code/voltis",
                "context": "parity"}),
        c("folder-rename", "POST", "/folders/rename",
          json={"old_name": "learning/voltis/upsert", "new_name": "learning/voltis/renamed"}),
        c("dedup-dry", "POST", "/memory/deduplicate", json={"threshold": 0.9, "dry_run": True}),
        c("conflicts-resolve-dry", "POST", "/memory/conflicts/resolve", json={"dry_run": True}),
        c("conflicts-resolve", "POST", "/memory/conflicts/resolve", json={"dry_run": False}),
        c("profile-put", "PUT", "/extraction/profiles/codex/voltis", route="PUT /extraction/profiles/{prefix:path}",
          json={"mode": "conservative", "max_facts": 5}),
        c("profile-get", "GET", "/extraction/profiles/codex/voltis", route="GET /extraction/profiles/{prefix:path}"),
        c("profile-delete", "DELETE", "/extraction/profiles/codex/voltis",
          route="DELETE /extraction/profiles/{prefix:path}"),
        c("webhook-create", "POST", "/webhooks", json={"url": "http://127.0.0.1:9/hook", "events": ["memory.added"]},
          save={"wh_id": "id"}),
        c("extract-commit", "POST", "/memory/extract/commit",
          json={"source": "claude-code/voltis/parity",
                "actions": [
                    {"action": "add", "approved": True, "fact": {"text": "Parity committed fact.", "category": "decision"}},
                    {"action": "update", "approved": True, "old_id": "{{add_id}}",
                     "fact": {"text": "Parity: the fleet-api token bucket is 800 requests.", "category": "detail"}},
                    {"action": "add", "approved": False, "fact": {"text": "Parity not approved."}}]}),
        c("extract-commit-none", "POST", "/memory/extract/commit",
          json={"source": "claude-code/voltis/commit", "actions": [{"action": "add"}]}),
        c("import", "POST", "/import", query="strategy=add&no_backup=true",
          body='{"_header": true, "version": 1}\n'
               '{"text": "Parity imported one.", "source": "learning/voltis/import"}\n'
               '{"text": "Parity imported two.", "source": "learning/voltis/import", '
               '"custom_fields": {"k": "v"}}\n'
               '{"source": "no-text"}\n'),
        c("import-bad-strategy", "POST", "/import", query="strategy=nope", body='{"_header": true}\n'),
        c("embedder-reload", "POST", "/maintenance/embedder/reload"),
        c("reembed", "POST", "/maintenance/reembed", json={}),
        c("compact", "POST", "/maintenance/compact", json={"threshold": 0.95}),
        c("consolidate-dry", "POST", "/maintenance/consolidate", query="dry_run=true&source_prefix=wip/"),
        c("prune-dry", "POST", "/maintenance/prune", query="dry_run=true"),
        c("enforce-policies-dry", "POST", "/maintenance/enforce-policies", query="dry_run=true"),
        c("promotion-approve", "POST", "/promotions/1/approve", route="POST /promotions/{candidate_id}/approve",
          json={"reason": "parity"}),
        c("promotion-reject", "POST", "/promotions/1/reject", route="POST /promotions/{candidate_id}/reject",
          json={"reason": "parity"}),
        c("extract-status", "GET", "/extract/status"),
    ]


def _metrics_cases():
    return [
        c("metrics", "GET", "/metrics"),
        c("metrics-search-quality", "GET", "/metrics/search-quality", query="period=all"),
        c("metrics-extraction-quality", "GET", "/metrics/extraction-quality", query="period=all"),
        c("metrics-quality-summary", "GET", "/metrics/quality-summary", query="period=all"),
        c("metrics-failures", "GET", "/metrics/failures", query="type=retrieval"),
        c("metrics-problem-queries", "GET", "/metrics/problem-queries", query="min_feedback=1"),
        c("metrics-stale", "GET", "/metrics/stale-memories", query="min_retrievals=1"),
        c("metrics-graph", "GET", "/metrics/graph-search", query="period=all"),
        c("metrics-temporal", "GET", "/metrics/temporal-search", query="period=all"),
        c("usage-final", "GET", "/usage", query="period=all"),
    ]


def _destructive_cases():
    long_messages = "\n".join(
        f"User: note {i}: the route-optimizer cache TTL is {i} minutes.\nAssistant: Noted." for i in range(5))
    return [
        c("extract-202", "POST", "/memory/extract",
          json={"messages": long_messages, "source": "claude-code/voltis/extract", "context": "stop"},
          rules=["W5-extract-202-fast"], max_ms=2000, save={"job": "job_id"}),
        c("extract-job", "GET", "/memory/extract/{{job}}", route="GET /memory/extract/{job_id}",
          rules=["W7-extract-failure-completed"], volatile=["error_message"],
          until={"path": "status", "in": ["completed", "failed"], "timeout_s": 60}),
        c("extract-job-404", "GET", "/memory/extract/nojob", route="GET /memory/extract/{job_id}"),
        c("extract-ro-403", "POST", "/memory/extract", json={"messages": "User: hi"}, key="ro"),
        c("backup-create", "POST", "/backup", query="prefix=parity", save={"backup": "backup_path:basename"}),
        c("backups-after-create", "GET", "/backups", volatile=["backups", "count"]),
        c("restore", "POST", "/restore", json={"backup_name": "{{backup}}"}),
        c("restore-missing", "POST", "/restore", json={"backup_name": "nope"}),
        c("sync-upload", "POST", "/sync/upload"),
        c("sync-download", "POST", "/sync/download", query="confirm=false"),
        c("sync-restore", "POST", "/sync/restore/nope", route="POST /sync/restore/{backup_name}"),
        c("delete-one", "DELETE", f"/memory/{S2}", route=f"DELETE {M}"),
        c("delete-pinned", "DELETE", "/memory/{{seed_pinned}}", route=f"DELETE {M}"),
        c("delete-pinned-force", "DELETE", "/memory/{{seed_pinned}}", route=f"DELETE {M}", query="force=true"),
        c("delete-missing", "DELETE", "/memory/999999", route=f"DELETE {M}"),
        c("delete-batch", "POST", "/memory/delete-batch", json={"ids": ["{{seed2.0}}", "{{seed2.1}}", 999999]}),
        c("delete-by-source-dry", "POST", "/memory/delete-by-source",
          json={"source_pattern": "wip/voltis/batch", "dry_run": True}),
        c("delete-by-source", "POST", "/memory/delete-by-source",
          json={"source_pattern": "wip/voltis/batch", "skip_snapshot": True}),
        c("delete-by-prefix", "POST", "/memory/delete-by-prefix",
          json={"source_prefix": "learning/voltis/import", "skip_snapshot": True}),
        c("delete-memories-query", "DELETE", "/memories", query="source=learning/voltis/merged"),
        c("audit-purge", "POST", "/audit/purge", query="retention_days=30"),
        c("webhook-delete", "DELETE", "/webhooks/{{wh_id}}", route="DELETE /webhooks/{webhook_id}"),
        c("keys-revoke", "DELETE", "/api/keys/{{rw_id}}", route="DELETE /api/keys/{key_id}"),
        c("keys-revoked-401", "GET", "/api/keys/me", key="rw", rules=["W2-401-wrong-key"]),
        c("count-end", "GET", "/memories/count"),
        c("export-end", "GET", "/export"),
        # /index/build deletes all records and loads parity/workspace/ (WORKSPACE_DIR) again.
        c("index-build-traversal", "POST", "/index/build", json={"sources": ["../../etc/passwd"]}),
        c("index-build", "POST", "/index/build", json={}),
        c("export-after-index-build", "GET", "/export"),
        # Last: in embedded Qdrant mode a snapshot restore leaves the local store read-only.
        # Snapshot names have whole seconds. /index/build made a snapshot just before, so wait.
        c("snapshot-create", "POST", "/snapshots", save={"snap": "snapshot"}, delay_s=1.1),
        c("snapshot-restore", "POST", "/snapshots/{{snap}}/restore", route="POST /snapshots/{name}/restore"),
        c("snapshot-restore-missing", "POST", "/snapshots/nope/restore", route="POST /snapshots/{name}/restore"),
    ]


def build():
    cases = (_rules_cases() + seed.build() + _key_cases() + _read_cases()
             + _write_cases() + _metrics_cases() + _destructive_cases())
    pinned = next(x for x in cases if x["name"] == "seed-pin-0")["request"]["path"].split("/")[-1]
    first_text = seed.memories()[0]["text"]
    return [_fill(x, pinned, first_text) for x in cases]


def _fill(case, pinned, text):
    if isinstance(case, dict):
        return {k: _fill(v, pinned, text) for k, v in case.items()}
    if isinstance(case, list):
        return [_fill(v, pinned, text) for v in case]
    if isinstance(case, str):
        return case.replace("{{seed_pinned}}", pinned).replace("{{seed_text}}", text)
    return case
