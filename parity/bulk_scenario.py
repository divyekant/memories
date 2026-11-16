"""The bulk scenario (task P3-2): error replies of the bulk and relation write routes,
then each delete route, with the list of the records that stay after each delete.

The case `inv-N` lists the IDs of each record that is not pinned (a dry run of
`delete-by-source` with "/", which each seed source holds). Thus the diff of two
captures shows each difference in the set of deleted IDs.
"""

from parity import scenario, seed
from parity.scenario import c

SEED1 = "{{seed1.0}}"
SEED2 = "{{seed2.0}}"


def _inventory(n):
    return c(f"inv-{n}", "POST", "/memory/delete-by-source", json={"source_pattern": "/", "dry_run": True})


def _error_cases():
    m = "/memory/{memory_id}"
    return [
        c("missing-source-422", "DELETE", "/memories", query="x=1"),
        c("empty-source-422", "DELETE", "/memories", query="source="),
        c("dbs-422", "POST", "/memory/delete-by-source", json={"source_pattern": "", "dry_run": "maybe"}),
        c("dbp-ro-403", "POST", "/memory/delete-by-prefix", json={"source_prefix": "codex/"}, key="ro"),
        c("supersede-legacy-422", "POST", "/memory/supersede", json={"new_text": 5}),
        c("supersede-legacy-ro-403", "POST", "/memory/supersede", json={"old_id": SEED1, "new_text": "x"}, key="ro"),
        c("supersede-legacy-404", "POST", "/memory/supersede", json={"old_id": 999999, "new_text": "x"}),
        c("supersede-legacy-rw-missing", "POST", "/memory/supersede",
          json={"old_id": 999999, "new_text": "x", "source": "codex/voltis/x"}, key="rw"),
        c("supersede-id-422", "POST", f"{m}/supersede".replace("{memory_id}", "abc"),
          route="POST /memory/{memory_id}/supersede", json={"text": ""}),
        c("supersede-id-ro-403", "POST", f"/memory/{SEED1}/supersede", route="POST /memory/{memory_id}/supersede",
          json={"text": "x"}, key="ro"),
        c("supersede-id-404", "POST", "/memory/999999/supersede", route="POST /memory/{memory_id}/supersede",
          json={"text": "x"}),
        c("supersede-id-rw", "POST", f"/memory/{SEED1}/supersede", route="POST /memory/{memory_id}/supersede",
          json={"text": "x"}, key="rw"),
        c("merge-422", "POST", "/memory/merge", json={"ids": [1], "merged_text": "x", "source": "s"}),
        c("merge-missing-500", "POST", "/memory/merge",
          json={"ids": [SEED1, 999999], "merged_text": "x", "source": "codex/x"}),
        c("merge-dup-400", "POST", "/memory/merge",
          json={"ids": ["{{seed1.3}}", "{{seed1.3}}"], "merged_text": "Parity duplicate merge.",
                "source": "learning/voltis/merged"}),
        # Python v5.19.0 adds the merged record before the 400. This delete removes it, so the
        # two servers hold the same records again (Python then reuses the ID of the record).
        c("merge-dup-cleanup", "POST", "/memory/delete-by-source",
          json={"source_pattern": "learning/voltis/merged", "skip_snapshot": True}),
        c("merge-cross-422", "POST", "/memory/merge",
          json={"ids": ["{{seed1.4}}", "{{seed1.5}}"], "merged_text": "x", "source": "project/p1/notes"}),
        c("link-422", "POST", "/memory/abc/link", route="POST /memory/{memory_id}/link", json={"type": 5}),
        c("link-missing-400", "POST", "/memory/999999/link", route="POST /memory/{memory_id}/link",
          json={"to_id": SEED1, "type": "related_to"}),
        c("link-to-missing-400", "POST", f"/memory/{SEED1}/link", route="POST /memory/{memory_id}/link",
          json={"to_id": 999999, "type": "related_to"}),
        c("link-self-400", "POST", f"/memory/{SEED1}/link", route="POST /memory/{memory_id}/link",
          json={"to_id": SEED1, "type": "related_to"}),
        c("link-rw", "POST", f"/memory/{SEED1}/link", route="POST /memory/{memory_id}/link",
          json={"to_id": SEED2, "type": "related_to"}, key="rw"),
        c("link-add", "POST", f"/memory/{SEED1}/link", route="POST /memory/{memory_id}/link",
          json={"to_id": SEED2, "type": "caused_by"}),
        c("link-dup-400", "POST", f"/memory/{SEED1}/link", route="POST /memory/{memory_id}/link",
          json={"to_id": SEED2, "type": "caused_by"}),
        c("patch-rw-out-of-scope", "PATCH", f"/memory/{SEED1}", route="PATCH /memory/{memory_id}",
          json={"text": "x"}, key="rw"),
        c("keys-update-principal-404", "PATCH", "/api/keys/no-principal_id-key", route="PATCH /api/keys/{key_id}",
          json={"name": "x"}),
        c("search-intent-overflow", "POST", "/search",
          json={"query": "what happened 1000000000 days ago", "source_prefix": "nothing/here/"}),
        c("unlink-422", "DELETE", "/memory/1/link/abc", route="DELETE /memory/{memory_id}/link/{target_id}"),
        c("unlink-missing-500", "DELETE", "/memory/999999/link/1",
          route="DELETE /memory/{memory_id}/link/{target_id}"),
        c("unlink-wrong-type", "DELETE", f"/memory/{SEED1}/link/{SEED2}",
          route="DELETE /memory/{memory_id}/link/{target_id}", query="type=supersedes"),
        c("unlink", "DELETE", f"/memory/{SEED1}/link/{SEED2}",
          route="DELETE /memory/{memory_id}/link/{target_id}", query="type=caused_by"),
        c("unlink-ro-403", "DELETE", f"/memory/{SEED1}/link/{SEED2}",
          route="DELETE /memory/{memory_id}/link/{target_id}", key="ro"),
        c("archive-422", "POST", "/memory/archive-batch", json={"ids": []}),
        c("archive-ro-missing", "POST", "/memory/archive-batch", json={"ids": [999999]}, key="ro"),
        c("archive-ro-403", "POST", "/memory/archive-batch", json={"ids": [SEED1]}, key="ro"),
        c("rename-ro-403", "POST", "/folders/rename", json={"old_name": "a", "new_name": "b"}, key="ro"),
        c("rename-404", "POST", "/folders/rename", json={"old_name": "nothing/here", "new_name": "b"}),
        c("rename-cross-422", "POST", "/folders/rename",
          json={"old_name": "learning/voltis/vendors", "new_name": "project/p1/vendors"}),
        c("rename-422", "POST", "/folders/rename", json={"old_name": ""}),
        c("resolve-422", "POST", "/memory/conflicts/resolve", json={"max": -1}),
        c("resolve-rw-403", "POST", "/memory/conflicts/resolve", json={}, key="rw"),
        c("resolve-ro-403", "POST", "/memory/conflicts/resolve", json={}, key="ro"),
        c("resolve-max-1", "POST", "/memory/conflicts/resolve", json={"max": 1}),
        c("dedup-422", "POST", "/memory/deduplicate", json={"threshold": 0.3}),
        c("dedup-ro-403", "POST", "/memory/deduplicate", json={}, key="ro"),
        c("missed-422", "POST", "/memory/missed", json={"text": "x"}),
        c("missed-ro-403", "POST", "/memory/missed", json={"text": "x", "source": "claude-code/x"}, key="ro"),
        c("missed-1", "POST", "/memory/missed", json={"text": "Parity missed one.", "source": "codex/voltis/missed"},
          key="rw"),
        c("missed-2", "POST", "/memory/missed", json={"text": "Parity missed two.", "source": "codex/voltis/missed",
                                                      "context": ""}),
    ]


def _delete_cases():
    return [
        _inventory(0),
        # A substring that is not a prefix of any source.
        c("dbs-mid-dry", "POST", "/memory/delete-by-source", json={"source_pattern": "voltis/meet", "dry_run": True}),
        c("dbs-mid", "POST", "/memory/delete-by-source", json={"source_pattern": "voltis/meet", "skip_snapshot": True}),
        _inventory(1),
        # A prefix with no "/" boundary: "admin" also matches "admin-dashboard".
        c("dbp-dry", "POST", "/memory/delete-by-prefix",
          json={"source_prefix": "eval/graph/synth/config/a", "dry_run": True}),
        c("dbp", "POST", "/memory/delete-by-prefix", json={"source_prefix": "eval/graph/synth/config/a"}),
        _inventory(2),
        # A substring of sources that is not their prefix deletes nothing.
        c("dbp-none", "POST", "/memory/delete-by-prefix", json={"source_prefix": "voltis", "skip_snapshot": True}),
        # Python names a local snapshot by the second: wait after the snapshot of "dbp".
        c("delete-query", "DELETE", "/memories", query="source=learning/voltis/arch", delay_s=1.1),
        _inventory(3),
        # A scoped key (codex/ and wip/): more than 10 records, so a snapshot first.
        c("dbs-scoped-dry", "POST", "/memory/delete-by-source", json={"source_pattern": "wip/", "dry_run": True},
          key="rw"),
        c("dbs-scoped", "POST", "/memory/delete-by-source", json={"source_pattern": "wip/"}, key="rw",
          delay_s=1.1),
        c("dbs-scoped-none", "POST", "/memory/delete-by-source", json={"source_pattern": "wip/"}, key="rw"),
        _inventory(4),
        c("folder-rename", "POST", "/folders/rename",
          json={"old_name": "claude-code/voltis/arch", "new_name": "claude-code/voltis/design"}),
        c("dbp-renamed-dry", "POST", "/memory/delete-by-prefix",
          json={"source_prefix": "claude-code/voltis/design", "dry_run": True}),
        c("dedup-dry", "POST", "/memory/deduplicate", json={"threshold": 0.95, "dry_run": True}),
        # Python v5.19.0 deletes, then replies 500 (its audit call fails). See parity/intended.json.
        c("dedup", "POST", "/memory/deduplicate", json={"threshold": 0.95, "dry_run": False}),
        _inventory(5),
    ]


def build():
    keys = [x for x in scenario._key_cases() if x["name"] in ("keys-create-ro", "keys-create-rw", "keys-update")]
    return scenario.fill(seed.build() + keys + _error_cases() + _delete_cases())
