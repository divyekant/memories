"""Compare rank_bm25 with a Qdrant sparse vector (Rust rewrite, task P0-2).

Usage:
  python -m parity.keyword_index.run --qdrant 1.15.4=http://127.0.0.1:6333 \
      [--qdrant 1.20.1=http://127.0.0.1:6433] [--dataset seed|longmemeval|all] \
      [--questions N] [--cache DIR] [--out results.json]

The script makes the collection "p0_2_keyword" on each Qdrant server (it
deletes a collection of that name first) and deletes it after each corpus.
Do not point it at a Qdrant server that holds data of an install.
"""

import argparse
import json
import urllib.request
import random
import statistics
import time
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi

from parity.keyword_index import bm25_sparse as bs

COLLECTION = "p0_2_keyword"
# avgdl rules that the Rust server can use. "exact" is what rank_bm25 does.
VARIANTS = {
    "exact": lambda avgdl: (avgdl, bs.B),
    "avgdl_x0.5": lambda avgdl: (avgdl * 0.5, bs.B),
    "avgdl_x2": lambda avgdl: (avgdl * 2, bs.B),
    "b0": lambda avgdl: (avgdl, 0.0),
}
TOP = 10
LME_K = 50  # eval/longmemeval.py asks for k=50; hybrid_search oversamples 3 times.


def python_rank(bm25, tokens, limit):
    """The order of MemoryEngine._bm25_hits plus the sort in hybrid_search."""
    scores = bm25.get_scores(tokens)
    hits = [(int(p), float(scores[p])) for p in np.flatnonzero(scores > 0)]
    return [p for p, _ in sorted(hits, key=lambda x: x[1], reverse=True)[:limit]]


def collisions(docs):
    seen = {}
    for tokens in docs:
        for t in set(tokens):
            seen.setdefault(bs.token_id(t), set()).add(t)
    vocab = sum(len(v) for v in seen.values())
    return vocab, sum(len(v) - 1 for v in seen.values() if len(v) > 1)


class Sparse:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.client = QdrantClient(url=url, timeout=120, check_compatibility=False)
        version = self._rest("GET", "/")["version"]
        # BM25 over a full-text payload index came after 1.15; the client of the venv has no model for it.
        self.text_bm25 = tuple(int(x) for x in version.split(".")[:2]) >= (1, 20)
        self.variants = list(VARIANTS) + (["text_bm25"] if self.text_bm25 else [])

    def _rest(self, method, path, body=None):
        req = urllib.request.Request(
            self.url + path, method=method, headers={"content-type": "application/json"},
            data=None if body is None else json.dumps(body).encode(),
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)

    def load(self, docs):
        if self.client.collection_exists(COLLECTION):
            self.client.delete_collection(COLLECTION)
        self.client.create_collection(
            COLLECTION,
            vectors_config={},
            sparse_vectors_config={n: models.SparseVectorParams(modifier=models.Modifier.IDF) for n in VARIANTS},
        )
        if self.text_bm25:
            self._rest("PUT", f"/collections/{COLLECTION}/index?wait=true", {"field_name": "text", "field_schema": {
                "type": "text", "tokenizer": "whitespace", "lowercase": True, "scoring": {"type": "bm25"}}})
        avgdl = sum(map(len, docs)) / len(docs)
        points = []
        for i, tokens in enumerate(docs):
            vectors = {}
            for name, rule in VARIANTS.items():
                a, b = rule(avgdl)
                vec = bs.doc_vector(tokens, a, b=b)
                vectors[name] = models.SparseVector(indices=list(vec), values=list(vec.values()))
            points.append(models.PointStruct(id=i, vector=vectors, payload={"text": " ".join(tokens)}))
        for start in range(0, len(points), 256):
            self.client.upsert(COLLECTION, points[start:start + 256], wait=True)

    def rank(self, variant, tokens, limit):
        if variant == "text_bm25":
            body = {"query": {"text": {"query": " ".join(tokens), "scoring": {"bm25": {"k": bs.K1, "b": bs.B}}}},
                    "using": "text", "limit": limit}
            return [int(p["id"]) for p in self._rest("POST", f"/collections/{COLLECTION}/points/query", body)["result"]["points"]]
        q = bs.query_vector(tokens)
        res = self.client.query_points(
            COLLECTION,
            query=models.SparseVector(indices=list(q), values=list(q.values())),
            using=variant,
            limit=limit,
        )
        return [int(p.id) for p in res.points]

    def drop(self):
        self.client.delete_collection(COLLECTION)


def overlap(a, b):
    a = a[:TOP]
    return len(set(a) & set(b[:TOP])) / len(a) if a else None


def tie_overlap(scores, a, b):
    """Like overlap, but a document with the same rank_bm25 score as the last one of a counts as in a."""
    a = a[:TOP]
    if not a:
        return None
    floor = scores[a[-1]]
    return sum(1 for d in b[:TOP] if scores[d] >= floor - 1e-9) / len(a)


def tie_top1(scores, a, b):
    return (abs(scores[b[0]] - scores[a[0]]) < 1e-9) if a and b else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(map(float, xs)), 4) if xs else None


def seed_corpus():
    from parity.seed import memories

    texts = [m["text"] for m in memories()]
    rnd = random.Random(20261116)
    queries = []
    for _ in range(200):
        words = [w for w in bs.doc_tokens(rnd.choice(texts)) if w.isalpha() and w not in bs.STOPWORDS]
        n = min(len(words), rnd.randint(1, 4))
        queries.append("what about " + " ".join(rnd.sample(words, n)) + "?")
    return texts, queries


def run_seed(servers, out):
    texts, queries = seed_corpus()
    docs = [bs.doc_tokens(t) for t in texts]
    bm25 = BM25Okapi(docs)
    vocab, coll = collisions(docs)
    report = {"docs": len(docs), "queries": len(queries), "vocab": vocab, "hash_collisions": coll, "servers": {}}
    sc = [bm25.get_scores(bs.query_tokens(q)) for q in queries]
    py = [python_rank(bm25, bs.query_tokens(q), TOP) for q in queries]
    for name, srv in servers.items():
        srv.load(docs)
        res = {}
        for variant in srv.variants:
            qd = [srv.rank(variant, bs.query_tokens(q), TOP) for q in queries]
            res[variant] = {
                "overlap_at_10": mean(overlap(p, r) for p, r in zip(py, qd)),
                "top1_same": mean((p[0] == r[0]) if p and r else None for p, r in zip(py, qd)),
                "tie_overlap_at_10": mean(tie_overlap(s, p, r) for s, p, r in zip(sc, py, qd)),
                "tie_top1_same": mean(tie_top1(s, p, r) for s, p, r in zip(sc, py, qd)),
            }
        srv.drop()
        report["servers"][name] = res
    out["seed"] = report


def embedder():
    from onnx_embedder import OnnxEmbedder

    return OnnxEmbedder()


def run_longmemeval(servers, out, n_questions, cache, hybrid_every):
    from eval.longmemeval import LongMemEvalRunner

    lme = LongMemEvalRunner(client=None)
    data = lme.load_dataset()
    if n_questions:
        data = data[:n_questions]
    emb = None
    cache.mkdir(parents=True, exist_ok=True)
    keys = ["python"] + [f"{n}/{v}" for n, s in servers.items() for v in s.variants]
    acc = {k: {"overlap_at_10": [], "top1_same": [], "tie_overlap_at_10": [], "tie_top1_same": [], "kw_r5": [], "hybrid_r5": []} for k in keys}
    vocab_total = coll_total = 0
    t0 = time.time()
    for qi, q in enumerate(data):
        texts, sessions = [], []
        for s_idx, session in enumerate(q.get("haystack_sessions", [])):
            for chunk in lme._chunk_session(session, max_chars=lme.DEFAULT_MAX_MEMORY_CHARS):
                if chunk.strip():
                    texts.append(chunk)
                    sessions.append(s_idx)
        if not texts:
            continue
        question = str(q.get("question", ""))
        k = min(LME_K, len(texts))
        oversample = min(k * 3, len(texts))
        dense = None
        if qi % hybrid_every == 0:  # the dense side costs about 30 s per question on 4 CPUs
            path = cache / f"{LongMemEvalRunner._question_id(q)}.npy"
            if path.exists():
                vecs = np.load(path)
            else:
                emb = emb or embedder()
                vecs = emb.encode(texts, normalize_embeddings=True).astype(np.float32)
                np.save(path, vecs)
            emb = emb or embedder()
            qvec = emb.encode([question], normalize_embeddings=True)[0]
            dense = [int(i) for i in np.argsort(-(vecs @ qvec), kind="stable")[:oversample]]

        def r5(ids):
            return LongMemEvalRunner.compute_recall_at_k([{"session_index": sessions[i]} for i in ids], q, k=5)["recall_any"]

        docs = [bs.doc_tokens(t) for t in texts]
        tokens = bs.query_tokens(question)
        vocab, coll = collisions(docs)
        vocab_total += vocab
        coll_total += coll
        bm25 = BM25Okapi(docs)
        scores = bm25.get_scores(tokens)
        py = python_rank(bm25, tokens, oversample)
        lists = {"python": py}
        for name, srv in servers.items():
            srv.load(docs)
            for variant in srv.variants:
                lists[f"{name}/{variant}"] = srv.rank(variant, tokens, oversample)
            srv.drop()
        for key, kw in lists.items():
            a = acc[key]
            a["overlap_at_10"].append(overlap(py, kw))
            a["top1_same"].append((py[0] == kw[0]) if py and kw else None)
            a["tie_overlap_at_10"].append(tie_overlap(scores, py, kw))
            a["tie_top1_same"].append(tie_top1(scores, py, kw))
            a["kw_r5"].append(r5(kw))
            a["hybrid_r5"].append(None if dense is None else r5(bs.rrf(dense, kw)[:k]))
        if (qi + 1) % 50 == 0:
            print(f"longmemeval {qi + 1}/{len(data)} {time.time() - t0:.0f}s", flush=True)
    out["longmemeval"] = {
        "questions": len(data),
        "hybrid_questions": len(range(0, len(data), hybrid_every)),
        "vocab_total": vocab_total,
        "hash_collisions_total": coll_total,
        "results": {k: {m: mean(v) for m, v in a.items()} for k, a in acc.items()},
    }


def run_scale(servers, out, n_docs):
    """Latency at production size (about 66k records): one collection, real LongMemEval chunks."""
    from eval.longmemeval import LongMemEvalRunner

    lme = LongMemEvalRunner(client=None)
    data = lme.load_dataset()
    texts, questions = [], []
    for q in data:
        questions.append(str(q.get("question", "")))
        for session in q.get("haystack_sessions", []):
            texts += [c for c in lme._chunk_session(session, max_chars=lme.DEFAULT_MAX_MEMORY_CHARS) if c.strip()]
        if len(texts) >= n_docs:
            break
    docs = [bs.doc_tokens(t) for t in texts[:n_docs]]
    queries = [bs.query_tokens(q) for q in questions[:100]]
    vocab, coll = collisions(docs)

    def timed(fn):
        ms = []
        for tokens in queries:
            t = time.perf_counter()
            fn(tokens)
            ms.append((time.perf_counter() - t) * 1000)
        ms.sort()
        return {"p50_ms": round(ms[len(ms) // 2], 2), "p95_ms": round(ms[int(len(ms) * 0.95)], 2)}

    t = time.perf_counter()
    bm25 = BM25Okapi(docs)
    report = {"docs": len(docs), "vocab": vocab, "hash_collisions": coll,
              "rank_bm25": {"build_s": round(time.perf_counter() - t, 1), **timed(lambda q: python_rank(bm25, q, 150))},
              "servers": {}}
    for name, srv in servers.items():
        t = time.perf_counter()
        srv.load(docs)
        res = {"load_s_all_variants": round(time.perf_counter() - t, 1)}
        for variant in ("exact", "text_bm25"):
            if variant in srv.variants:
                res[variant] = timed(lambda q, v=variant: srv.rank(v, q, 150))
        srv.drop()
        report["servers"][name] = res
    out["scale"] = report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant", action="append", required=True, help="name=url")
    ap.add_argument("--dataset", default="all", choices=["seed", "longmemeval", "all", "scale"])
    ap.add_argument("--scale-docs", type=int, default=66000)
    ap.add_argument("--questions", type=int, default=0, help="0 = all 500")
    ap.add_argument("--hybrid-every", type=int, default=5, help="hybrid R@5 on every Nth question")
    ap.add_argument("--cache", default=".cache/p0_2_embeddings")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    servers = {n: Sparse(u) for n, u in (x.split("=", 1) for x in args.qdrant)}
    out = {"k1": bs.K1, "b": bs.B, "variants": list(VARIANTS)}
    if args.dataset in ("seed", "all"):
        run_seed(servers, out)
    if args.dataset in ("longmemeval", "all"):
        run_longmemeval(servers, out, args.questions, Path(args.cache), args.hybrid_every)
    if args.dataset == "scale":
        run_scale(servers, out, args.scale_docs)
    text = json.dumps(out, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
