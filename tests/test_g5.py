"""Tests of the gate G5 helpers (parity/g5.py)."""

import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import httpx

from parity import g5


def test_corpus_is_python_metadata(tmp_path):
    g5.corpus(tmp_path, 200, seed=1, long_share=0.1)
    recs = json.loads((tmp_path / "metadata.json").read_text())
    assert [r["id"] for r in recs] == list(range(1, 201))
    assert len({r["text"] for r in recs}) == 200
    lengths = [len(r["text"].split()) for r in recs]
    assert any(n > 300 for n in lengths) and any(n < 90 for n in lengths)
    assert json.loads((tmp_path / "config.json").read_text())["embedding_signature"] == g5.SIGNATURE


def test_fake_llm_replies(monkeypatch):
    monkeypatch.setattr(g5._LLM, "delay", 0)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), g5._LLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        facts = json.loads(httpx.post(f"{url}/api/generate", json={"system": "x", "prompt": "y"}).json()["response"])
        assert len(facts) == 2 and all("text" in f for f in facts)
        audn = 'New facts:\n[{"index":0,"text":"a"},{"index":1,"text":"b"}]\nOutput a JSON array of decisions'
        decisions = json.loads(httpx.post(f"{url}/api/generate", json={"system": audn}).json()["response"])
        assert decisions == [{"action": "ADD", "fact_index": 0}, {"action": "ADD", "fact_index": 1}]
        assert httpx.get(f"{url}/api/tags").status_code == 200
    finally:
        srv.shutdown()


def test_rss_sampler(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    out = tmp_path / "rss.json"
    g5.rss(p.pid, out, 0.05)
    p.wait()
    r = json.loads(out.read_text())
    assert r["peak_rss_mb"] > 0 and r["vmhwm_mb"] >= r["peak_rss_mb"] - 1 and r["samples"]
