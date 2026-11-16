"""BM25 as a Qdrant sparse vector, with the token rules of the Python server.

The Rust server must make the same tokens and the same weights. Qdrant adds
the IDF part (modifier "idf"), so a document vector holds only the term
frequency part of BM25 Okapi. That part needs the average document length.
"""

from collections import Counter

K1 = 1.5  # rank_bm25 BM25Okapi defaults, used by memory_engine.py
B = 0.75
RRF_K = 60

# Same set as MemoryEngine._BM25_STOPWORDS. A test keeps the two equal.
STOPWORDS = frozenset(
    "a an and are as at be by can could did do does for from had has have how i in is it "
    "of on or our should that the this to was we were what when where which who why will with "
    "would you".split()
)


def doc_tokens(text):
    return text.lower().split()


def query_tokens(query):
    tokens = [t.rstrip("?") for t in query.lower().split()]
    return [t for t in tokens if t and t not in STOPWORDS] or query.lower().split()


def token_id(token):
    """FNV-1a 32-bit hash of the UTF-8 bytes. Qdrant sparse indices are u32."""
    h = 0x811C9DC5
    for byte in token.encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


def _merge(pairs):
    # Two tokens with the same hash share one dimension. Qdrant refuses repeated indices.
    out = {}
    for idx, value in pairs:
        out[idx] = out.get(idx, 0.0) + value
    return out


def doc_vector(tokens, avgdl, k1=K1, b=B):
    """{index: weight} with the term frequency part of BM25 Okapi."""
    dl = len(tokens)
    norm = k1 * (1 - b + b * dl / avgdl) if b else k1
    return _merge((token_id(t), tf * (k1 + 1) / (tf + norm)) for t, tf in Counter(tokens).items())


def query_vector(tokens):
    """{index: count}. rank_bm25 adds a repeated query term two times; the count does the same."""
    return _merge((token_id(t), float(n)) for t, n in Counter(tokens).items())


def qdrant_idf(n_docs, df):
    """The IDF of the Qdrant "idf" modifier."""
    import math

    return math.log((n_docs - df + 0.5) / (df + 0.5) + 1)


def rrf(vector_ids, keyword_ids, vector_weight=0.7, k=RRF_K):
    """The fusion of MemoryEngine.hybrid_search with no recency, feedback or graph weight."""
    scores = {}
    for rank, doc_id in enumerate(vector_ids):
        scores[doc_id] = scores.get(doc_id, 0.0) + vector_weight / (rank + k)
    for rank, doc_id in enumerate(keyword_ids):
        scores[doc_id] = scores.get(doc_id, 0.0) + (1 - vector_weight) / (rank + k)
    return [d for d, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]
