"""Offline checks of the BM25 sparse vector rules (Rust rewrite, task P0-2)."""

import numpy as np
import pytest
from rank_bm25 import BM25Okapi

from memory_engine import MemoryEngine
from parity.keyword_index import bm25_sparse as bs


def test_stopwords_match_engine():
    assert bs.STOPWORDS == MemoryEngine._BM25_STOPWORDS


@pytest.mark.parametrize("query", [
    "What did we decide about Qdrant?",
    "what is the?",
    "Rust  rewrite?? plan",
    "??",
    "",
    "Ünïcode TOKENS here",
])
def test_query_tokens_match_engine(query):
    assert bs.query_tokens(query) == MemoryEngine._bm25_query_tokens(query)


def test_doc_tokens_match_engine_rule():
    assert bs.doc_tokens("Hello, World!\tfoo\nBar") == ["hello,", "world!", "foo", "bar"]


def test_token_id_is_fnv1a_32():
    assert bs.token_id("") == 0x811C9DC5
    assert bs.token_id("a") == 0xE40C292C
    assert bs.token_id("foobar") == 0xBF9CF968


def test_sparse_dot_product_equals_rank_bm25():
    # No term is in more than half of the documents, so rank_bm25 uses no IDF floor.
    corpus = [
        "rust server writes one record",
        "python server rewrites the full metadata file",
        "qdrant sparse vector with idf modifier",
        "tantivy index on disk is the fallback",
        "the keyword index test compares ranks",
        "dense vector search uses onnx embeddings",
    ]
    docs = [bs.doc_tokens(t) for t in corpus]
    bm25 = BM25Okapi(docs)
    avgdl = sum(map(len, docs)) / len(docs)
    query = bs.query_tokens("rust index index vector")
    q = bs.query_vector(query)
    for i, tokens in enumerate(docs):
        d = bs.doc_vector(tokens, avgdl)
        idf = {bs.token_id(t): bm25.idf.get(t, 0.0) for t in query}
        ours = sum(q[j] * d.get(j, 0.0) * idf[j] for j in q)
        assert ours == pytest.approx(bm25.get_scores(query)[i])


def test_b0_drops_length_part():
    assert bs.doc_vector(["x"] * 3, avgdl=1.0, b=0) == bs.doc_vector(["x"] * 3, avgdl=99.0, b=0)


def test_qdrant_idf_is_positive_for_common_terms():
    assert bs.qdrant_idf(10, 10) > 0
    assert bs.qdrant_idf(10, 1) == pytest.approx(np.log(1 + 9.5 / 1.5))


def test_rrf_matches_engine_weights():
    fused = bs.rrf([1, 2, 3], [3, 4])
    # 3 is in both lists, so it wins; 1 is first on the 0.7 side.
    assert fused[:2] == [3, 1]
    assert set(fused) == {1, 2, 3, 4}
