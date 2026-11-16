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


def test_verdict_applies_the_gate_conditions():
    good = {
        "corpus": {"vector_top10_same": 0.995, "hybrid_top1_same": 0.92, "hybrid_top5_overlap": 0.95},
        "graph": {"python": {"graph_on": 0.80, "graph_off": 0.70}, "rust": {"graph_on": 0.795, "graph_off": 0.70}},
        "longmemeval": {"python": {"r5": 0.95}, "rust": {"r5": 0.941}},
    }
    v = g3.verdict(good)
    assert v["pass"], v
    bad = {**good, "longmemeval": {"python": {"r5": 0.95}, "rust": {"r5": 0.939}}}
    assert not g3.verdict(bad)["pass"]
    bad = {**good, "corpus": {**good["corpus"], "vector_top10_same": 0.98}}
    assert not g3.verdict(bad)["pass"]
    # A part that did not run does not fail the gate, but the verdict names it.
    v = g3.verdict({"corpus": good["corpus"]})
    assert v["pass"] and v["not_run"] == ["graph", "longmemeval"]


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
