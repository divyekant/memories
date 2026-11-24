"""Record the 422 replies of the Python `ExtractRequest` body (v5.19.0).

Each case sends one body to a FastAPI route that takes `app.ExtractRequest`,
as `POST /memory/extract` does. The body and the reply go to
fixtures/extract_validation.json. The Rust test `extract.rs` sends the same
bodies to `POST /memory/extract` and compares the replies.

Run from the repository root:
    .venv/bin/python server/crates/memories-server/tests/record_extract_validation.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent / "fixtures" / "extract_validation.json"

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("EXTRACT_PROVIDER", None)
os.environ.pop("MAX_EXTRACT_MESSAGE_CHARS", None)

import app  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

probe = FastAPI()


@probe.post("/memory/extract")
async def extract(request_body: app.ExtractRequest):
    return request_body.model_dump()


FP = "a" * 64
CASES = [
    ("empty-object", {}),
    ("messages-empty", {"messages": ""}),
    ("messages-number", {"messages": 5}),
    ("messages-null", {"messages": None}),
    ("messages-too-long", {"messages": "x" * 120001}),
    ("messages-max", {"messages": "é" * 120000}),
    ("source-number", {"messages": "m", "source": 1}),
    ("source-null", {"messages": "m", "source": None}),
    ("context-list", {"messages": "m", "context": []}),
    ("debug-string-yes", {"messages": "m", "debug": "yes"}),
    ("debug-string-bad", {"messages": "m", "debug": "maybe"}),
    ("debug-int-1", {"messages": "m", "debug": 1}),
    ("debug-int-2", {"messages": "m", "debug": 2}),
    ("debug-float", {"messages": "m", "debug": 1.0}),
    ("debug-float-half", {"messages": "m", "debug": 0.5}),
    ("debug-null", {"messages": "m", "debug": None}),
    ("dry-run-object", {"messages": "m", "dry_run": {}}),
    ("document-at-number", {"messages": "m", "document_at": 5}),
    ("document-at-null", {"messages": "m", "document_at": None}),
    ("promotion-null", {"messages": "m", "promotion_context": None}),
    ("promotion-string", {"messages": "m", "promotion_context": "x"}),
    ("promotion-empty", {"messages": "m", "promotion_context": {}}),
    ("promotion-ok", {"messages": "m", "promotion_context": {
        "project_id": "proj-1", "mode": "shadow", "declaration_fingerprint": FP}}),
    ("promotion-bad", {"messages": "m", "promotion_context": {
        "project_id": "Bad_Id", "mode": "on", "declaration_fingerprint": "A" * 64, "x": 1}}),
    ("promotion-short", {"messages": "m", "promotion_context": {
        "project_id": "", "mode": 1, "declaration_fingerprint": "ab"}}),
    ("promotion-long", {"messages": "m", "promotion_context": {
        "project_id": "a" * 201, "mode": "off", "declaration_fingerprint": FP + "a"}}),
    ("many-errors", {"messages": 1, "source": 2, "context": 3, "debug": "x", "dry_run": [],
                     "document_at": 4, "extra": "ignored"}),
    ("array-body", ["messages"]),
]


def main():
    client = TestClient(probe)
    out = []
    for name, body in CASES:
        resp = client.post("/memory/extract", json=body)
        if resp.status_code == 200:
            continue
        out.append({"name": name, "body": body, "status": resp.status_code, "reply": resp.json()})
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(out)} cases written to {OUT}")


if __name__ == "__main__":
    main()
