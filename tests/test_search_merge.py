# tests/test_search_merge.py
"""Test search result merging across multiple backends."""
import pytest


def _merge_results(results_by_backend):
    """Simulate the merge logic from MCP server / hooks."""
    all_results = []
    for backend, results in results_by_backend.items():
        for r in results:
            all_results.append({**r, "_backend": backend})

    # Sort by score FIRST so dedup (first-seen wins) keeps the highest-scoring result
    all_results.sort(key=lambda x: x.get("similarity", x.get("rrf_score", 0)), reverse=True)

    # Dedup by exact text
    seen = set()
    deduped = []
    for r in all_results:
        key = r.get("text", "")
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped


class TestSearchMerge:
    def test_merge_two_backends(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "fact A", "similarity": 0.9, "source": "test/a"}],
            "prod": [{"id": 2, "text": "fact B", "similarity": 0.8, "source": "test/b"}],
        })
        assert len(results) == 2
        assert results[0]["text"] == "fact A"  # higher score first
        assert results[0]["_backend"] == "local"

    def test_dedup_exact_text_match(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "same fact", "similarity": 0.9}],
            "prod": [{"id": 99, "text": "same fact", "similarity": 0.85}],
        })
        assert len(results) == 1
        assert results[0]["_backend"] == "local"  # highest score wins
        assert results[0]["similarity"] == 0.9

    def test_dedup_keeps_highest_score_not_first_seen(self):
        """When a lower-scoring backend is listed first, dedup still keeps the higher score."""
        results = _merge_results({
            "prod": [{"id": 99, "text": "same fact", "similarity": 0.7}],
            "local": [{"id": 1, "text": "same fact", "similarity": 0.95}],
        })
        assert len(results) == 1
        assert results[0]["_backend"] == "local"  # highest score, not first backend
        assert results[0]["similarity"] == 0.95

    def test_different_text_not_deduped(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "fact A", "similarity": 0.9}],
            "prod": [{"id": 2, "text": "fact A extended version", "similarity": 0.85}],
        })
        assert len(results) == 2  # different text, both kept

    def test_single_backend_passthrough(self):
        results = _merge_results({
            "only": [{"id": 1, "text": "solo", "similarity": 0.9}],
        })
        assert len(results) == 1
        assert results[0]["_backend"] == "only"

    def test_backend_tags_preserved(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "A", "similarity": 0.9}],
            "prod": [{"id": 2, "text": "B", "similarity": 0.8}],
        })
        backends = {r["_backend"] for r in results}
        assert backends == {"local", "prod"}

    def test_empty_backend_handled(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "A", "similarity": 0.9}],
            "prod": [],
        })
        assert len(results) == 1
