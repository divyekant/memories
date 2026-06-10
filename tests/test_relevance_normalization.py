"""Tests for per-result-set relative score normalization.

Hybrid search fuses rankers with Reciprocal Rank Fusion: each signal adds
``weight * 1/(rank + 60)``, so absolute rrf_score values are bounded near
1/60 (~0.0167) and render as a useless 0-2% when shown as percentages.
RRF values are rank-fusion scores — only meaningful relative to each other
within a single result set.

``relative_score`` exposes that set-relative strength as ``score / max(score)``
in (0, 1]. Raw ``rrf_score``/``similarity`` are preserved untouched so the
``threshold`` request param and existing consumers keep working unchanged.
Normalization by a positive constant is strictly monotone, so ranking order
is provably unchanged.
"""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory_engine import MemoryEngine, annotate_relative_scores


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------

class TestAnnotateRelativeScores:
    def test_empty_list_returns_empty(self):
        assert annotate_relative_scores([]) == []

    def test_single_result_gets_full_relative_score(self):
        results = [{"id": 1, "rrf_score": 0.0123}]
        annotate_relative_scores(results)
        assert results[0]["relative_score"] == 1.0

    def test_top_result_anchored_at_one_with_ratio_spread(self):
        results = [
            {"id": 1, "rrf_score": 0.0167},
            {"id": 2, "rrf_score": 0.0102},
            {"id": 3, "rrf_score": 0.0033},
        ]
        annotate_relative_scores(results)
        assert results[0]["relative_score"] == 1.0
        assert results[1]["relative_score"] == pytest.approx(0.0102 / 0.0167, abs=1e-4)
        assert results[2]["relative_score"] == pytest.approx(0.0033 / 0.0167, abs=1e-4)

    def test_raw_scores_unchanged(self):
        results = [
            {"id": 1, "rrf_score": 0.0167},
            {"id": 2, "rrf_score": 0.0033},
        ]
        annotate_relative_scores(results)
        assert results[0]["rrf_score"] == 0.0167
        assert results[1]["rrf_score"] == 0.0033

    def test_similarity_results_normalized(self):
        results = [
            {"id": 1, "similarity": 0.91},
            {"id": 2, "similarity": 0.455},
        ]
        annotate_relative_scores(results)
        assert results[0]["relative_score"] == 1.0
        assert results[1]["relative_score"] == pytest.approx(0.5, abs=1e-4)
        assert results[0]["similarity"] == 0.91

    def test_all_equal_scores_all_get_one(self):
        results = [{"id": i, "rrf_score": 0.01} for i in range(4)]
        annotate_relative_scores(results)
        assert all(r["relative_score"] == 1.0 for r in results)

    def test_missing_or_invalid_scores_get_zero(self):
        results = [
            {"id": 1, "rrf_score": 0.01},
            {"id": 2},
            {"id": 3, "rrf_score": "bogus"},
        ]
        annotate_relative_scores(results)
        assert results[0]["relative_score"] == 1.0
        assert results[1]["relative_score"] == 0.0
        assert results[2]["relative_score"] == 0.0

    def test_non_positive_max_yields_zero_for_all(self):
        results = [{"id": 1, "rrf_score": 0.0}, {"id": 2, "rrf_score": 0.0}]
        annotate_relative_scores(results)
        assert all(r["relative_score"] == 0.0 for r in results)

    def test_returns_the_same_list_for_chaining(self):
        results = [{"id": 1, "rrf_score": 0.01}]
        assert annotate_relative_scores(results) is results

    def test_order_by_relative_score_matches_order_by_raw_score(self):
        raw = [0.0167, 0.0166, 0.0150, 0.0150, 0.0091, 0.0033, 0.0001]
        results = [{"id": i, "rrf_score": s} for i, s in enumerate(raw)]
        annotate_relative_scores(results)
        by_raw = sorted(results, key=lambda r: -r["rrf_score"])
        by_relative = sorted(results, key=lambda r: -r["relative_score"])
        assert [r["id"] for r in by_raw] == [r["id"] for r in by_relative]
        # ties in raw stay ties in relative
        assert results[2]["relative_score"] == results[3]["relative_score"]


# ---------------------------------------------------------------------------
# Synthetic corpus: ranking order is unchanged by normalization
# ---------------------------------------------------------------------------

@pytest.fixture
def corpus_engine(tmp_path):
    engine = MemoryEngine(data_dir=str(tmp_path))
    engine.add_memories(
        texts=[
            "Python is a great programming language for data science",
            "JavaScript runs in the browser and on Node.js",
            "Docker containers package applications with their dependencies",
            "FastAPI is a modern Python web framework",
            "Qdrant powers efficient vector similarity search",
            "PostgreSQL is a reliable relational database",
            "Redis caches hot data in memory",
            "Kubernetes orchestrates container deployments",
            "Python type hints improve code readability",
            "The deployment target for the API is fly.io",
            "Grafana dashboards visualize Prometheus metrics",
            "Terraform manages infrastructure as code",
        ],
        sources=[f"test/doc{i}.md" for i in range(12)],
    )
    return engine


class TestRankingOrderInvariance:
    def test_hybrid_search_order_invariant(self, corpus_engine):
        results = corpus_engine.hybrid_search("Python web framework", k=8)
        assert len(results) >= 3
        original_ids = [r["id"] for r in results]
        raw_scores = [r["rrf_score"] for r in results]

        annotate_relative_scores(results)

        assert [r["id"] for r in sorted(results, key=lambda r: -r["relative_score"])] == original_ids
        assert [r["rrf_score"] for r in results] == raw_scores
        assert results[0]["relative_score"] == 1.0
        assert all(0.0 < r["relative_score"] <= 1.0 for r in results)

    def test_hybrid_search_with_bonus_signals_order_invariant(self, corpus_engine):
        feedback = {r["id"]: 2 for r in corpus_engine.hybrid_search("Python", k=3)}
        results = corpus_engine.hybrid_search(
            "Python framework deployment",
            k=8,
            recency_weight=0.2,
            confidence_weight=0.1,
            feedback_weight=0.1,
            feedback_scores=feedback,
            graph_weight=0.1,
        )
        assert len(results) >= 3
        original_ids = [r["id"] for r in results]
        annotate_relative_scores(results)
        assert [r["id"] for r in sorted(results, key=lambda r: -r["relative_score"])] == original_ids

    def test_vector_search_order_invariant(self, corpus_engine):
        results = corpus_engine.search("container deployments", k=6)
        assert len(results) >= 3
        original_ids = [r["id"] for r in results]
        raw = [r["similarity"] for r in results]
        annotate_relative_scores(results)
        assert [r["id"] for r in sorted(results, key=lambda r: -r["relative_score"])] == original_ids
        assert [r["similarity"] for r in results] == raw


# ---------------------------------------------------------------------------
# API layer: /search, /search/batch, /search/evidence, /search/explain
# ---------------------------------------------------------------------------

HYBRID_RESULTS = [
    {"id": 1, "text": "top hit", "source": "test/a", "rrf_score": 0.0167},
    {"id": 2, "text": "middle hit", "source": "test/b", "rrf_score": 0.0102},
    {"id": 3, "text": "weak hit", "source": "test/c", "rrf_score": 0.0033},
]

VECTOR_RESULTS = [
    {"id": 4, "text": "vec top", "source": "test/d", "similarity": 0.91},
    {"id": 5, "text": "vec low", "source": "test/e", "similarity": 0.455},
]


@pytest.fixture
def api_client():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5, "dimension": 384, "model": "all-MiniLM-L6-v2"}
        mock_engine.hybrid_search.return_value = [dict(r) for r in HYBRID_RESULTS]
        mock_engine.search.return_value = [dict(r) for r in VECTOR_RESULTS]
        mock_engine.hybrid_search_explain.return_value = {
            "results": [dict(r) for r in HYBRID_RESULTS],
            "explain": {"rrf_k": 60},
        }
        app_module.memory = mock_engine
        yield TestClient(app_module.app), mock_engine


HEADERS = {"X-API-Key": "test-key"}


class TestSearchEndpointRelativeScore:
    def test_hybrid_results_carry_relative_score_and_raw_score(self, api_client):
        test_client, _ = api_client
        response = test_client.post("/search", json={"query": "q", "hybrid": True}, headers=HEADERS)
        assert response.status_code == 200
        results = response.json()["results"]
        assert [r["id"] for r in results] == [1, 2, 3]
        assert results[0]["relative_score"] == 1.0
        assert results[1]["relative_score"] == pytest.approx(0.0102 / 0.0167, abs=1e-4)
        assert results[2]["relative_score"] == pytest.approx(0.0033 / 0.0167, abs=1e-4)
        # raw scores preserved for back-compat
        assert [r["rrf_score"] for r in results] == [0.0167, 0.0102, 0.0033]

    def test_vector_results_carry_relative_score(self, api_client):
        test_client, _ = api_client
        response = test_client.post("/search", json={"query": "q", "hybrid": False}, headers=HEADERS)
        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["relative_score"] == 1.0
        assert results[1]["relative_score"] == pytest.approx(0.5, abs=1e-4)
        assert results[0]["similarity"] == 0.91

    def test_threshold_param_forwarded_unchanged(self, api_client):
        test_client, mock_engine = api_client
        response = test_client.post(
            "/search", json={"query": "q", "hybrid": True, "threshold": 0.42}, headers=HEADERS
        )
        assert response.status_code == 200
        assert mock_engine.hybrid_search.call_args.kwargs["threshold"] == 0.42

    def test_batch_normalizes_each_result_set_independently(self, api_client):
        test_client, mock_engine = api_client
        low_scale = [
            {"id": 7, "text": "x", "source": "test/x", "rrf_score": 0.008},
            {"id": 8, "text": "y", "source": "test/y", "rrf_score": 0.002},
        ]
        mock_engine.hybrid_search.side_effect = [
            [dict(r) for r in HYBRID_RESULTS],
            low_scale,
        ]
        response = test_client.post(
            "/search/batch",
            json={"queries": [{"query": "a"}, {"query": "b"}]},
            headers=HEADERS,
        )
        assert response.status_code == 200
        outputs = response.json()["results"]
        first, second = outputs[0]["results"], outputs[1]["results"]
        # each set anchored to its own top result
        assert first[0]["relative_score"] == 1.0
        assert second[0]["relative_score"] == 1.0
        assert second[1]["relative_score"] == pytest.approx(0.25, abs=1e-4)

    def test_evidence_results_carry_relative_score(self, api_client):
        test_client, _ = api_client
        response = test_client.post("/search/evidence", json={"query": "q"}, headers=HEADERS)
        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["relative_score"] == 1.0

    def test_explain_results_carry_relative_score(self, api_client):
        test_client, _ = api_client
        response = test_client.post("/search/explain", json={"query": "q"}, headers=HEADERS)
        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["relative_score"] == 1.0
        assert results[0]["rrf_score"] == 0.0167
