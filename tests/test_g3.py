"""Offline checks of the gate G3 metrics (Rust rewrite, task P2-4)."""

from parity import g3


def test_top_n_same_compares_sets():
    assert g3.top_same([1, 2, 3], [3, 2, 1], 10)
    assert not g3.top_same([1, 2, 3], [1, 2, 4], 10)
    assert g3.top_same([1, 2, 3, 9], [1, 2, 3, 8], 3)
    assert g3.top_same([], [], 10)


def test_overlap_at_uses_the_python_list_size():
    assert g3.overlap([1, 2, 3, 4, 5], [1, 2, 3, 9, 8], 5) == 0.6
    assert g3.overlap([1, 2], [2, 1, 7], 5) == 1.0
    assert g3.overlap([], [], 5) == 1.0
    assert g3.overlap([], [1], 5) == 0.0


def test_top1_same():
    assert g3.top1_same([4, 1], [4, 2])
    assert not g3.top1_same([4], [5])
    assert g3.top1_same([], [])
    assert not g3.top1_same([1], [])


def test_queries_are_fixed_and_enough():
    texts = [m["text"] for m in g3.corpus()]
    q1, q2 = g3.corpus_queries(texts), g3.corpus_queries(texts)
    assert q1 == q2
    assert len(q1) >= 500
    assert len(set(q1)) == len(q1)


GOOD = {
    "corpus": {
        "vector_top10_same": 0.995,
        "by_kind": {
            "keyword": {"hybrid_top1_same": 0.91, "hybrid_top5_overlap": 0.92},
            "record_start": {"hybrid_top1_same": 0.99, "hybrid_top5_overlap": 0.97},
        },
    },
    "graph": {
        "python": {"graph_on": 0.80, "graph_off": 0.70},
        "rust": {"graph_on": 0.795, "graph_off": 0.70},
        "graph_on_top1_same": 1.0,
        "graph_on_top5_overlap": 0.96,
    },
    "longmemeval": {"python": {"r5": 0.95}, "rust": {"r5": 0.941}},
}


def test_verdict_applies_the_gate_conditions():
    v = g3.verdict(GOOD)
    assert v["pass"] is True, v
    bad = {**GOOD, "longmemeval": {"python": {"r5": 0.95}, "rust": {"r5": 0.939}}}
    assert g3.verdict(bad)["pass"] is False
    bad = {**GOOD, "corpus": {**GOOD["corpus"], "vector_top10_same": 0.98}}
    assert g3.verdict(bad)["pass"] is False


def test_each_query_kind_must_pass():
    by_kind = {**GOOD["corpus"]["by_kind"], "keyword": {"hybrid_top1_same": 0.85, "hybrid_top5_overlap": 0.92}}
    bad = {**GOOD, "corpus": {**GOOD["corpus"], "by_kind": by_kind}}
    assert g3.verdict(bad)["pass"] is False


def test_graph_lists_must_agree_when_scores_are_at_the_maximum():
    bad = {**GOOD, "graph": {**GOOD["graph"], "graph_on_top5_overlap": 0.6}}
    assert g3.verdict(bad)["pass"] is False


def test_a_part_that_did_not_run_gives_partial():
    v = g3.verdict({"corpus": GOOD["corpus"]})
    assert v["pass"] == "partial" and v["not_run"] == ["graph", "longmemeval"]
    assert g3.verdict({})["pass"] is False


def test_query_kinds_alternate():
    texts = [m["text"] for m in g3.corpus()]
    qs = g3.corpus_queries(texts)
    assert all(qs[i].startswith("what about ") for i in range(0, len(qs), 2))
    assert [g3.kind(i) for i in range(3)] == ["keyword", "record_start", "keyword"]


def test_tie_top_allows_swaps_at_the_cut():
    def hits(*pairs):
        return [{"id": i, "similarity": s} for i, s in pairs]

    py = hits((1, 0.9), (2, 0.5), (3, 0.5))
    assert g3.tie_top(py, hits((1, 0.9), (3, 0.5), (4, 0.5)), 3)
    assert not g3.tie_top(py, hits((1, 0.9), (3, 0.5), (4, 0.4)), 3)
    assert not g3.tie_top(py, hits((5, 0.9), (2, 0.5), (3, 0.5)), 3)
    assert g3.tie_top([], [], 3)


def test_corpus_has_unique_texts():
    texts = [m["text"] for m in g3.corpus()]
    assert len(texts) == g3.CORPUS_SIZE == len(set(texts))
