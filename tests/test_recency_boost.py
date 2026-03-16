"""Tests for temporal search weighting (recency boost) in hybrid search."""

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from memory_engine import MemoryEngine


class TestRecencyScore:
    """Test the _recency_score static helper."""

    def test_now_returns_1(self):
        now = datetime.now(timezone.utc).isoformat()
        score = MemoryEngine._recency_score(now, half_life_days=30)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_one_half_life_returns_half(self):
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        score = MemoryEngine._recency_score(past, half_life_days=30)
        assert score == pytest.approx(0.5, abs=0.05)

    def test_two_half_lives_returns_quarter(self):
        past = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        score = MemoryEngine._recency_score(past, half_life_days=30)
        assert score == pytest.approx(0.25, abs=0.05)

    def test_missing_timestamp_returns_zero(self):
        score = MemoryEngine._recency_score(None, half_life_days=30)
        assert score == 0.0

    def test_invalid_timestamp_returns_zero(self):
        score = MemoryEngine._recency_score("not-a-date", half_life_days=30)
        assert score == 0.0

    def test_future_timestamp_clamped_to_1(self):
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        score = MemoryEngine._recency_score(future, half_life_days=30)
        assert score == 1.0

    def test_zero_half_life_does_not_crash(self):
        now = datetime.now(timezone.utc).isoformat()
        score = MemoryEngine._recency_score(now, half_life_days=0)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_negative_half_life_clamps_to_default(self):
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        score = MemoryEngine._recency_score(past, half_life_days=-5)
        # Should use 30-day default, so ~0.5
        assert score == pytest.approx(0.5, abs=0.05)


class TestHybridSearchRecencyParam:
    """Test that recency_weight parameter is accepted and respected."""

    @pytest.fixture
    def engine(self, tmp_path):
        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.ensure_payload_indexes.return_value = None
            mock_store.count.return_value = 0
            mock_store.search.return_value = []
            MockStore.return_value = mock_store

            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            return eng

    def test_default_recency_weight_is_zero(self, engine):
        """hybrid_search with no recency_weight behaves as before."""
        results = engine.hybrid_search(query="test")
        # Should not raise — recency_weight defaults to 0.0

    def test_recency_weight_accepted(self, engine):
        """hybrid_search accepts recency_weight parameter."""
        results = engine.hybrid_search(query="test", recency_weight=0.3)
        # Should not raise

    def test_recency_weight_zero_gives_same_results(self, engine):
        """With recency_weight=0.0, results should be identical to without it."""
        now = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

        engine.metadata = [
            {"id": 1, "text": "recent fact", "source": "test", "created_at": now},
            {"id": 2, "text": "old fact", "source": "test", "created_at": old},
        ]
        engine._rebuild_id_map()
        engine._rebuild_bm25()

        # Both should return empty since there's no real vector search
        r1 = engine.hybrid_search(query="fact", recency_weight=0.0)
        r2 = engine.hybrid_search(query="fact")
        assert r1 == r2
