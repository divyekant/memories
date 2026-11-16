"""Gate G4, part a (task P4-4): the eval corpus through the extraction pipeline.

The transcripts are the texts of the extraction evals: the 20 texts of
`eval/run_extraction_eval.py` (CORPUS), then the scenario memories that
`eval/run_scenario_extraction_eval.py` sends to `/memory/extract`. The store
grows from empty, so later transcripts find similar records and get UPDATE,
DELETE, NOOP and CONFLICT decisions on real records.

The fake LLM of `parity/extract_pipeline.py` gives replies that are a function
of the prompt and of the highest record ID that earlier jobs returned. The check compares the prompts, the job results and all
stored records with `parity/golden/extract/corpus.ndjson`, the capture of
Python v5.19.0.

The similar records in the decision prompt come from hybrid search. The Rust
keyword index is a Qdrant sparse vector, and its scores are close to the
rank_bm25 scores but not equal (docs/decisions/2026-11-16-rust-keyword-index.md).
Thus the check has two parts:

- Strict, byte for byte or value for value: the fact prompts, the decision
  prompts outside the similar list, the JSON format of the similar list, the
  text of each record that is in both similar lists, the actions and counts
  of each job, the links that each job made, the compaction candidates
  without their IDs and scores, and all stored records.
- With the gate G3 tolerance for hybrid search: the IDs and order of the
  similar lists, and the IDs of the compaction candidates. 90% or more of the
  similar lists must be equal, and the mean overlap of the IDs must be 0.90
  or more. Not compared: the shown relevance, the RRF scores, and the input
  tokens of the decision call of a case whose similar lists differ.

The fake LLM picks the target of a decision with a tie rule (the lowest ID of
the records with the highest shown relevance), so that a small change of order
in the similar list does not change the decision.

Usage (server/scripts/parity_pipeline.sh starts the server):
  python -m parity.extract_corpus --base-url URL --provider-port N --check
  python -m parity.extract_corpus --base-url URL --provider-port N --record
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import yaml

from parity.extract_pipeline import (
    ADMIN, EXTRA_VOLATILE, RECORD_FIELDS, SCRIPT, Fake, call, main, wait_job)
from parity.kit import diff_values, normalize

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).parent / "golden" / "extract" / "corpus.ndjson"
CONTEXTS = ("stop", "pre_compact", "session_end")
CATEGORIES = ("DECISION", "learning", "DETAIL", "detail", "LEARNING", "decision")
# The highest record ID so far. Python gives the ID of a deleted highest record
# again at the next add; Rust does not (docs/decisions/2026-11-16-rust-record-store.md).
# The fake LLM never deletes this record, so that the IDs of the two servers stay equal.
STATE = {"max_id": -1}


def eval_corpus():
    """CORPUS of eval/run_extraction_eval.py, read without an import of the module."""
    tree = ast.parse((ROOT / "eval" / "run_extraction_eval.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CORPUS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("CORPUS not found in eval/run_extraction_eval.py")


def scenario_memories():
    """The memories of each scenario, in the order of eval/run_scenario_extraction_eval.py."""
    out = []
    for path in sorted((ROOT / "eval" / "scenarios").rglob("*.yaml")):
        s = yaml.safe_load(path.read_text())
        if not (s and "memories" in s and "prompt" in s):
            continue
        prefix = f"eval/{s.get('id', path.stem)}"
        for i, mem in enumerate(s["memories"]):
            out.append({"id": f"{path.stem}-{i}", "text": mem["text"],
                        "source": mem.get("source", f"{prefix}/mem-{i}")})
    return out


def h(text):
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")


# -- replies: a function of the prompt and STATE ---------------------------------

def sentences(text):
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(User|Assistant|Human|tool output|tool call)\s*:\s*", "", line).strip(" -*#")
        for part in re.split(r"(?<=[.!?])\s+", line):
            if 30 <= len(part) <= 400:
                out.append(part)
    return out


def fact_reply(user):
    """Stage 1: pick up to 4 sentences of the transcript in the prompt."""
    picked = [s for s in sentences(user) if h(s) % 3 != 0][:4]
    facts = [{"category": CATEGORIES[h(s) % len(CATEGORIES)], "text": s} for s in picked]
    body = json.dumps(facts)
    style = h(user) % 3
    if style == 1:
        body = "```json\n" + body + "\n```"
    elif style == 2:
        body = "Here are the facts:\n" + body
    return 200, body


SIMILAR_START = "higher is more relevant):\n"
SIMILAR_END = "\n\nOutput a JSON array"


def section(user, start, end):
    i = user.index(start) + len(start)
    return json.loads(user[i:user.index(end, i)])


def target(similar):
    """The lowest ID of the records with the highest shown relevance."""
    if not similar:
        return None
    best = max(m["relevance"] for m in similar)
    return min((m for m in similar if m["relevance"] == best), key=lambda m: m["id"])


def audn_reply(user):
    """Stage 2: a decision for each fact from its first similar record."""
    facts = section(user, "New facts:\n", "\n\nExisting similar memories")
    similar = section(user, SIMILAR_START, SIMILAR_END)
    out = []
    for fact in facts:
        i, text = fact["index"], fact["text"]
        top = target(similar.get(str(i)))
        pick = h("audn:" + text) % 6
        if top is None or pick in (0, 1):
            out.append({"action": "ADD", "fact_index": i})
        elif pick == 2:
            out.append({"action": "UPDATE", "fact_index": i, "old_id": top["id"],
                        "new_text": f"{text} Before: {top['text'][:80]}"})
        elif pick == 3:
            out.append({"action": "NOOP", "fact_index": i, "existing_id": top["id"]})
        elif pick == 4:
            out.append({"action": "CONFLICT", "fact_index": i, "old_id": top["id"]})
        elif top["id"] >= STATE["max_id"]:
            out.append({"action": "NOOP", "fact_index": i, "existing_id": top["id"]})
        else:
            out.append({"action": "DELETE", "fact_index": i, "old_id": top["id"]})
    return 200, json.dumps(out)


def reply(system, user):
    if "Existing similar memories" in user:
        return audn_reply(user)
    return fact_reply(user)


# -- runner -----------------------------------------------------------------------

def cases():
    out = []
    for i, c in enumerate(eval_corpus()):
        out.append({"name": f"corpus/{c['id']}",
                    "body": {"messages": c["text"], "source": "eval/g4",
                             "context": CONTEXTS[i % len(CONTEXTS)]}})
    for m in scenario_memories():
        # One source for most, so that the decisions find records they may change.
        # A quarter keep the scenario source: their cross-source decisions give error actions.
        source = m["source"] if h(m["id"]) % 4 == 0 else "eval/g4"
        out.append({"name": f"scenario/{m['id']}",
                    "body": {"messages": m["text"], "source": source, "context": "stop"}})
    return out


def all_records(client):
    out, offset = [], 0
    while True:
        status, body = call(client, "GET", f"/memories?offset={offset}&limit=500", ADMIN)
        assert status == 200, body
        for r in body["memories"]:
            rec = {k: r[k] for k in RECORD_FIELDS if k in r}
            rec["links"] = sorted([lk["type"], lk["to_id"]] for lk in r.get("links") or [])
            out.append(rec)
        offset += len(body["memories"])
        if not body["memories"] or offset >= body["total"]:
            return sorted(out, key=lambda r: r["id"])


def run(base_url, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = []
    STATE["max_id"] = -1
    with httpx.Client(base_url=base_url, timeout=60) as client:
        for case in cases():
            # Two calls at most: stage 1 and stage 2.
            SCRIPT.load([reply, reply])
            status, enq = call(client, "POST", "/memory/extract", ADMIN, case["body"])
            job = wait_job(client, enq["job_id"]) if status == 202 else {}
            for a in (job.get("result") or {}).get("actions", []):
                for key in ("id", "new_id"):
                    if isinstance(a.get(key), int):
                        STATE["max_id"] = max(STATE["max_id"], a[key])
            with SCRIPT.lock:
                prompts = list(SCRIPT.calls)
            out.append({"name": case["name"], "enqueue": [status, enq],
                        "job": {k: job.get(k) for k in ("status", "error", "result")},
                        "prompts": prompts})
        out.append({"name": "records", "records": all_records(client)})
    server.shutdown()
    return out


# -- check ------------------------------------------------------------------------

def split_prompt(user):
    """(the prompt without the similar list, the similar list or None).

    The marker records whether the list has the Python format (compact JSON,
    ASCII escapes), so that the strict compare finds a format change.
    """
    if SIMILAR_START not in user:
        return user, None
    i = user.index(SIMILAR_START) + len(SIMILAR_START)
    j = user.index(SIMILAR_END, i)
    raw = user[i:j]
    similar = json.loads(raw)
    fmt = "python" if raw == json.dumps(similar, separators=(",", ":")) else "other"
    return f"{user[:i]}<similar:{fmt}>{user[j:]}", similar


def overlap(a, b):
    a, b = set(a), set(b)
    return 1.0 if not a and not b else len(a & b) / max(len(a), len(b))


def similar_lists(case):
    """The similar list of each fact of each decision prompt: {key: [entries]}."""
    out = {}
    for n, p in enumerate(case.get("prompts") or []):
        _, similar = split_prompt(p["user"])
        for fact, mems in (similar or {}).items():
            out[f"prompt{n}/fact{fact}"] = mems
    return out


def candidates(case):
    return {c["fact_index"]: c for c in
            ((case.get("job") or {}).get("result") or {}).get("compaction_candidates") or []}


def strict_part(case, lists_equal):
    case = json.loads(json.dumps(case))
    case["prompts"] = [{"system": p["system"], "user": split_prompt(p["user"])[0]}
                       for p in case.get("prompts") or []]
    result = (case.get("job") or {}).get("result") or {}
    for c in result.get("compaction_candidates") or []:
        c.pop("memory_ids", None)
        c.pop("avg_rrf_score", None)
    if not lists_equal:
        # The fake LLM counts input tokens from the prompt length, which includes the similar list.
        (result.get("tokens") or {}).get("audn", {}).pop("input", None)
    for link in result.get("links_created") or []:
        link.pop("rrf_score", None)
    return case


def check(got, golden, min_equal=0.90, min_overlap=0.90):
    problems, lists, equal, overlaps = [], 0, 0, []
    by_name = {c["name"]: c for c in golden}
    for case in got:
        name = case["name"]
        want = by_name.get(name)
        if want is None:
            problems.append(f"{name}: not in the golden file")
            continue
        want_lists, got_lists = similar_lists(want), similar_lists(case)
        case_equal = True
        for key in sorted(want_lists.keys() | got_lists.keys()):
            x, y = want_lists.get(key, []), got_lists.get(key, [])
            ids_x, ids_y = [m["id"] for m in x], [m["id"] for m in y]
            lists += 1
            equal += ids_x == ids_y
            case_equal &= x == y
            overlaps.append(overlap(ids_x, ids_y))
            if ids_x != ids_y:
                print(f"note: {name} {key}: {ids_x} != {ids_y}")
            # A record in both lists must show the same text.
            text_x, text_y = {m["id"]: m["text"] for m in x}, {m["id"]: m["text"] for m in y}
            for mid in text_x.keys() & text_y.keys():
                if text_x[mid] != text_y[mid]:
                    problems.append(f"{name} {key}: record {mid} has a different text")
        want_c, got_c = candidates(want), candidates(case)
        for fact in want_c.keys() & got_c.keys():
            ids_x, ids_y = want_c[fact]["memory_ids"], got_c[fact]["memory_ids"]
            overlaps.append(overlap(ids_x, ids_y))
            if ids_x != ids_y:
                print(f"note: {name} compaction/fact{fact}: {ids_x} != {ids_y}")
        a, b = strict_part(want, case_equal), strict_part(case, case_equal)
        if a.get("prompts") != b.get("prompts"):
            problems.append(f"{name}: the prompts differ outside the similar lists")
        a = normalize({k: v for k, v in a.items() if k != "prompts"}, extra=EXTRA_VOLATILE)
        b = normalize({k: v for k, v in b.items() if k != "prompts"}, extra=EXTRA_VOLATILE)
        problems += [f"{name}: {d}" for d in diff_values(a, b)]
    missing = by_name.keys() - {c["name"] for c in got}
    problems += [f"{n}: missing" for n in sorted(missing)]
    share = equal / lists if lists else 1.0
    mean = sum(overlaps) / len(overlaps) if overlaps else 1.0
    print(f"similar lists: {equal} of {lists} equal ({share:.3f}); "
          f"mean ID overlap with the compaction candidates {mean:.3f}")
    if share < min_equal or mean < min_overlap:
        problems.append(f"hybrid search lists below the G3 tolerance: {share:.3f} equal, {mean:.3f} overlap")
    return problems


if __name__ == "__main__":
    sys.exit(main(run_fn=run, golden_path=GOLDEN, check_fn=check))
