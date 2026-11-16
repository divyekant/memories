"""Write the golden cases of the search routes (task P2-2) that need no server:

- parity/golden/search/validation.ndjson: the 422 bodies (or the parsed values)
  of the request models of app.py (v5.19.0). The script takes the class code
  of SearchRequest, SearchBatchRequest and IsNovelRequest from app.py and runs
  it with pydantic.
- parity/golden/search/evidence.ndjson: `evidence_packet.build_evidence_packet`
  for fixed and random result lists.

The Rust tests `search_golden` check each case.

Usage (from the repository root):
  .venv/bin/python -m parity.search_golden [--check]
"""

import ast
import json
import sys
from pathlib import Path
from typing import List, Optional

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "parity" / "golden" / "search"
MODELS = ("SearchRequest", "SearchBatchRequest", "IsNovelRequest")


def load_models():
    tree = ast.parse((ROOT / "app.py").read_text())
    code = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in MODELS]
    ns = {"BaseModel": BaseModel, "Field": Field, "Optional": Optional, "List": List}
    exec(compile(ast.Module(body=code, type_ignores=[]), "app.py", "exec"), ns)
    return {name: ns[name] for name in MODELS}


Q = "q"
SEARCH_CASES = [
    {"k": 3},
    {"query": ""},
    {"query": "x" * 10001},
    {"query": 5},
    {"query": None},
    {"query": Q, "k": 0},
    {"query": Q, "k": 101},
    {"query": Q, "k": "7"},
    {"query": Q, "k": "seven"},
    {"query": Q, "k": 2.5},
    {"query": Q, "k": 3.0},
    {"query": Q, "k": True},
    {"query": Q, "k": None},
    {"query": Q, "threshold": 1.5},
    {"query": Q, "threshold": -0.1},
    {"query": Q, "threshold": "0.5"},
    {"query": Q, "threshold": "x"},
    {"query": Q, "threshold": None},
    {"query": Q, "hybrid": "yes"},
    {"query": Q, "hybrid": "maybe"},
    {"query": Q, "hybrid": 1},
    {"query": Q, "hybrid": 2},
    {"query": Q, "hybrid": None},
    {"query": Q, "vector_weight": 2},
    {"query": Q, "recency_half_life_days": 0},
    {"query": Q, "recency_half_life_days": 366},
    {"query": Q, "feedback_weight": -1},
    {"query": Q, "confidence_weight": 1.01},
    {"query": Q, "graph_weight": "high"},
    {"query": Q, "source_prefix": "p" * 501},
    {"query": Q, "source_prefix": 3},
    {"query": Q, "source_prefixes": "codex/"},
    {"query": Q, "source_prefixes": ["codex/", 4]},
    {"query": Q, "source_boundary": "no"},
    {"query": Q, "since": "2" * 51},
    {"query": Q, "until": 2026},
    {"query": Q, "source": None},
    {"query": Q, "source": "s" * 501},
    {"query": Q, "include_archived": "true"},
    {"query": Q, "auto_intent": 0},
    {"query": Q, "reference_date": "r" * 51},
    {"query": "", "k": 0, "threshold": 2, "hybrid": "x"},
    {"query": Q, "k": " 7 "},
    {"query": Q, "k": "1_0"},
    {"query": Q, "k": "+3"},
    {"query": Q, "k": "7.0"},
    {"query": Q, "k": "1e2"},
    {"query": Q, "k": 1e20},
    {"query": Q, "threshold": True},
    {"query": Q, "threshold": " 1e-2 "},
    {"query": Q, "vector_weight": "0x1"},
    {"query": Q, "hybrid": 1.0},
    {"query": Q, "hybrid": 0.5},
    {"query": Q, "hybrid": "TRUE"},
    {"query": Q, "hybrid": "t"},
    {"query": Q, "hybrid": ""},
    {"query": Q, "hybrid": " yes "},
    {"query": Q, "include_archived": "off"},
    {"query": Q, "source_prefixes": None},
    {"query": Q, "source_prefixes": []},
    {"query": Q, "source_prefix": None},
    {"query": Q, "unknown": [1, 2]},
]
BATCH_CASES = [
    {},
    {"queries": []},
    {"queries": "x"},
    {"queries": [{"query": ""}]},
    {"queries": [{"query": Q}, {"k": 2}, 3]},
    {"queries": [{"query": Q}] * 201},
]
NOVEL_CASES = [
    {},
    {"text": ""},
    {"text": "t" * 50001},
    {"text": "t", "threshold": 1.2},
    {"text": "t", "threshold": None},
    {"text": ["t"]},
]


def validate(model, body):
    """(detail, value): the 422 detail list, or the parsed model as JSON."""
    try:
        parsed = model.model_validate(body)
    except ValidationError as e:
        items = e.errors(include_url=False)
        for item in items:
            item["loc"] = ["body", *item["loc"]]
        return jsonable_encoder(items), None
    return None, jsonable_encoder(parsed)


def build_validation():
    models = load_models()
    groups = (("search", "SearchRequest", SEARCH_CASES), ("batch", "SearchBatchRequest", BATCH_CASES),
              ("is_novel", "IsNovelRequest", NOVEL_CASES))
    out = []
    for key, name, cases in groups:
        for body in cases:
            detail, value = validate(models[name], body)
            out.append({"model": key, "body": body, "detail": detail, "value": value})
    return out


DATES = [None, "", "2026-01-05", "2026-01-05T10:00:00Z", "2026-01-05T10:00:00+05:00",
         "2025-12-31T23:59:59.5", "2026-03-01T00:00:00+00:00", "not a date", 7]
QUERIES = ["", "  what   is the latest rate limit ", "Latest config", "what changed about x",
           "current owner", "rate limit", "TODAY'S plan", "nowhere", "recent\tchanges"]


def _memory(rng, i):
    m = {"id": i, "source": rng.choice(["codex/a", "wip/b", "", 3]), "text": f"text {i}"}
    for key in ("document_at", "updated_at", "created_at", "timestamp"):
        d = rng.choice(DATES)
        if d is not None:
            m[key] = d
    score_key = rng.choice(["similarity", "rrf_score", None, "both"])
    score = rng.choice([0.5, 0.25, 0.75, 0.0, "0.6", None, "x", 1])
    if score_key == "both":
        m["similarity"], m["rrf_score"] = score, 0.9
    elif score_key:
        m[score_key] = score
    for key in ("is_latest", "archived"):
        v = rng.choice([None, True, False, 1, "", "yes"])
        if v is not None:
            m[key] = v
    return m


def build_evidence():
    import random

    from evidence_packet import build_evidence_packet

    rng = random.Random(22)
    cases = [{"query": q, "results": []} for q in QUERIES[:3]]
    for _ in range(60):
        results = [_memory(rng, i) for i in range(rng.randint(1, 9))]
        cases.append({"query": rng.choice(QUERIES), "results": results})
    for c in cases:
        c["packet"] = build_evidence_packet(c["query"], [dict(r) for r in c["results"]])
    return cases


def main(argv):
    status = 0
    for name, build in (("validation.ndjson", build_validation), ("evidence.ndjson", build_evidence)):
        path = DIR / name
        text = "\n".join(json.dumps(c, sort_keys=True) for c in build()) + "\n"
        if "--check" in argv:
            if path.read_text() != text:
                print(f"{path} is out of date; run python -m parity.search_golden", file=sys.stderr)
                status = 1
            continue
        path.write_text(text)
        print(f"wrote {path}")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
