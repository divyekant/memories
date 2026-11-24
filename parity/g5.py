"""Gate G5 helpers (task P6-3): the synthetic corpus, an LLM stub, the extract job check and an RSS sampler.

Usage:
    python -m parity.g5 corpus <data dir> --count 1000000
    python -m parity.g5 fake-llm <port>
    python -m parity.g5 jobs-submit <url> <key> <count> <out file>
    python -m parity.g5 jobs-check <url> <key> <job file> [--timeout 900]
    python -m parity.g5 rss <pid> <out file> [--interval 0.2]
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

SIGNATURE = "onnx:all-MiniLM-L6-v2:384d"
PREFIXES = ["claude-code", "codex", "learning", "wip", "project"]
TERMINAL = {"completed", "failed"}


def _vocab(rng, n=6000):
    letters = "abcdefghijklmnopqrstuvwxyz"
    words = set()
    while len(words) < n:
        words.add("".join(rng.choice(letters) for _ in range(rng.randint(3, 10))))
    return sorted(words)


def corpus(data_dir, count, seed, long_share, qdrant_url=None, collection=None):
    """metadata.json and config.json in the format of Python v5.19.0.

    Record lengths: most records have 10 to 80 words (production has about
    800 bytes for each row). `long_share` of the records have 300 to 500 words,
    more than the 256-token cut of the embedder, so a batch of them gives the
    bulk peak of the embedder.
    """
    rng = random.Random(seed)
    vocab = _vocab(rng)
    # Zipf-like word choice, so that the keyword IDF spreads as in real text.
    cum, total = [], 0.0
    for i in range(len(vocab)):
        total += 1.0 / (i + 1)
        cum.append(total)
    sources = [f"{p}/proj{i}" for p in PREFIXES for i in range(40)]
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / "metadata.json", "w") as f:
        f.write("[")
        for i in range(1, count + 1):
            n = rng.randint(300, 500) if rng.random() < long_share else rng.randint(10, 80)
            # The ID makes each text unique: equal texts tie in search (G3 finding 1).
            text = f"r{i} " + " ".join(rng.choices(vocab, cum_weights=cum, k=n))
            day = 1 + i % 28
            ts = f"2026-{1 + i % 10:02d}-{day:02d}T{i % 24:02d}:00:00+00:00"
            rec = {
                "id": i,
                "text": text,
                "source": f"{rng.choice(sources)}/s{i % 7}",
                "timestamp": ts,
                "created_at": ts,
                "updated_at": ts,
                "category": rng.choice(["decision", "learning", "detail"]),
            }
            f.write(("," if i > 1 else "") + json.dumps(rec))
        f.write("]")
    (data_dir / "config.json").write_text(json.dumps({
        "storage_backend": "qdrant", "embed_provider": "onnx", "dimension": 384,
        "embedding_signature": SIGNATURE,
    }))
    if qdrant_url:
        old_collection(qdrant_url, collection, count, seed)


def old_collection(qdrant_url, name, count, seed, pool_size=2000):
    """The old collection of Python (one unnamed dense vector), so that the
    migration copies the vectors as it does in production. Each vector is a
    real embedding of a pool text with a small change, so the vectors spread
    as real records do (the method of benches/write_load.rs)."""
    import numpy as np
    from qdrant_client import QdrantClient, models

    from onnx_embedder import OnnxEmbedder

    rng = random.Random(seed + 1)
    vocab = _vocab(rng, 3000)
    pool_texts = [" ".join(rng.choices(vocab, k=rng.randint(8, 40))) for _ in range(pool_size)]
    pool = np.asarray(OnnxEmbedder().encode(pool_texts), dtype=np.float32)
    client = QdrantClient(url=qdrant_url, timeout=300)
    client.create_collection(name, vectors_config=models.VectorParams(size=pool.shape[1], distance=models.Distance.COSINE))
    gen = np.random.default_rng(seed)
    step = 10_000
    for start in range(1, count + 1, step):
        ids = list(range(start, min(start + step, count + 1)))
        v = pool[gen.integers(0, pool_size, len(ids))] + 0.02 * gen.uniform(-1, 1, (len(ids), pool.shape[1])).astype(np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        client.upload_collection(name, vectors=v, ids=ids, batch_size=1000, wait=True)


class _LLM(BaseHTTPRequestHandler):
    delay = float(os.environ.get("G5_LLM_DELAY", "0.1"))
    counter = 0
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"models": [{"name": "stub"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        prompt = f"{body.get('system', '')}\n{body.get('prompt', '')}"
        time.sleep(self.delay)
        if "Output a JSON array of decisions" in prompt:
            n = len(re.findall(r'\{"index":', prompt))
            reply = json.dumps([{"action": "ADD", "fact_index": i} for i in range(n)])
        else:
            with self.lock:
                _LLM.counter += 1
                c = _LLM.counter
            reply = json.dumps([
                {"category": "DECISION", "text": f"Load test decision {c}: use the queue for job {c}."},
                {"category": "LEARNING", "text": f"Load test learning {c}: the worker {c} retries twice."},
            ])
        self._send({"response": reply, "prompt_eval_count": 100, "eval_count": 50, "done": True})


def fake_llm(port):
    ThreadingHTTPServer(("127.0.0.1", port), _LLM).serve_forever()


def jobs_submit(url, key, count, out):
    ids = []
    with httpx.Client(timeout=30) as c:
        for i in range(count):
            r = c.post(f"{url}/memory/extract", headers={"X-API-Key": key}, json={
                "messages": f"User: decide the queue policy {i}.\nAssistant: we use a durable queue {i}.",
                "source": "g5/jobs", "context": "stop",
            })
            r.raise_for_status()
            ids.append(r.json()["job_id"])
    Path(out).write_text("\n".join(ids) + "\n")
    print(json.dumps({"submitted": len(ids)}))


def jobs_check(url, key, job_file, timeout):
    """Exit 1 if a job is lost (404) or does not end before the timeout."""
    ids = Path(job_file).read_text().split()
    end = time.time() + timeout
    status = {}
    with httpx.Client(timeout=30) as c:
        while True:
            for j in ids:
                if status.get(j) in TERMINAL:
                    continue
                r = c.get(f"{url}/memory/extract/{j}", headers={"X-API-Key": key})
                status[j] = r.json().get("status") if r.status_code == 200 else f"http_{r.status_code}"
            open_ = [j for j in ids if status[j] not in TERMINAL and not status[j].startswith("http_")]
            if not open_ or time.time() > end:
                break
            time.sleep(2)
    counts = {}
    for s in status.values():
        counts[s] = counts.get(s, 0) + 1
    ok = counts.get("completed", 0) == len(ids)
    print(json.dumps({"jobs": len(ids), "by_status": counts, "pass": ok}))
    return 0 if ok else 1


def rss(pid, out, interval):
    """Samples VmRSS until the process ends; writes the peak, VmHWM and the series (MB)."""
    series, hwm, t0 = [], 0, time.time()
    while True:
        try:
            text = Path(f"/proc/{pid}/status").read_text()
        except (FileNotFoundError, ProcessLookupError):
            break
        if "\nState:\tZ" in text:  # ended, not reaped yet
            break
        kb = {k: int(v.split()[0]) for k, v in (ln.split(":", 1) for ln in text.splitlines()) if k in ("VmRSS", "VmHWM")}
        series.append([round(time.time() - t0, 1), kb.get("VmRSS", 0) // 1024])
        hwm = max(hwm, kb.get("VmHWM", 0) // 1024)
        Path(out).write_text(json.dumps({
            "pid": pid, "peak_rss_mb": max(s[1] for s in series), "vmhwm_mb": hwm,
            "last_rss_mb": series[-1][1], "samples": series[::max(1, len(series) // 2000)],
        }))
        time.sleep(interval)


def main(argv=None):
    p = argparse.ArgumentParser(prog="parity.g5")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("corpus")
    a.add_argument("data_dir", type=Path)
    a.add_argument("--count", type=int, default=1_000_000)
    a.add_argument("--seed", type=int, default=5)
    a.add_argument("--long-share", type=float, default=0.05)
    a.add_argument("--qdrant-url", help="also write the old collection with vectors")
    a.add_argument("--collection", default="g5")
    a = sub.add_parser("fake-llm")
    a.add_argument("port", type=int)
    a = sub.add_parser("jobs-submit")
    a.add_argument("url")
    a.add_argument("key")
    a.add_argument("count", type=int)
    a.add_argument("out")
    a = sub.add_parser("jobs-check")
    a.add_argument("url")
    a.add_argument("key")
    a.add_argument("job_file")
    a.add_argument("--timeout", type=float, default=900)
    a = sub.add_parser("rss")
    a.add_argument("pid", type=int)
    a.add_argument("out")
    a.add_argument("--interval", type=float, default=0.2)
    args = p.parse_args(argv)
    if args.cmd == "corpus":
        corpus(args.data_dir, args.count, args.seed, args.long_share, args.qdrant_url, args.collection)
    elif args.cmd == "fake-llm":
        fake_llm(args.port)
    elif args.cmd == "jobs-submit":
        jobs_submit(args.url, args.key, args.count, args.out)
    elif args.cmd == "jobs-check":
        return jobs_check(args.url, args.key, args.job_file, args.timeout)
    elif args.cmd == "rss":
        rss(args.pid, args.out, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
