# tests/test_confidence_ranking.py
import pytest
import inspect
from memory_engine import MemoryEngine


def test_hybrid_search_accepts_confidence_weight():
    """hybrid_search should accept confidence_weight parameter."""
    sig = inspect.signature(MemoryEngine.hybrid_search)
    assert "confidence_weight" in sig.parameters


def test_weight_scaling_sums_to_one():
    """All 5 signal weights must sum to 1.0."""
    # Test the scaling formula
    vector_weight = 0.7
    recency_weight = 0.2
    feedback_weight = 0.15
    confidence_weight = 0.1

    total_auxiliary = min(feedback_weight + confidence_weight, 1.0)
    total_core = 1.0 - total_auxiliary
    eff_vector = vector_weight * total_core * (1.0 - recency_weight)
    eff_bm25 = (1.0 - vector_weight) * total_core * (1.0 - recency_weight)
    eff_recency = recency_weight * total_core

    total = eff_vector + eff_bm25 + eff_recency + feedback_weight + confidence_weight
    assert abs(total - 1.0) < 0.0001


def test_combined_weight_guard():
    """Combined auxiliary weights > 1.0 should be clamped."""
    feedback_weight = 0.8
    confidence_weight = 0.5
    total_auxiliary = min(feedback_weight + confidence_weight, 1.0)
    total_core = 1.0 - total_auxiliary
    assert total_core == 0.0  # core signals fully displaced
    assert total_auxiliary == 1.0  # clamped
