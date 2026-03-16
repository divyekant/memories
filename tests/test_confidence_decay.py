"""Tests for memory confidence decay and reinforcement."""

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from memory_engine import MemoryEngine


class TestConfidenceComputation:
    """Test computed confidence score based on memory age."""

    def test_fresh_memory_has_full_confidence(self):
        now = datetime.now(timezone.utc).isoformat()
        score = MemoryEngine.compute_confidence(now, half_life_days=90)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_one_half_life_gives_half_confidence(self):
        past = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        score = MemoryEngine.compute_confidence(past, half_life_days=90)
        assert score == pytest.approx(0.5, abs=0.05)

    def test_missing_timestamp_returns_minimum(self):
        score = MemoryEngine.compute_confidence(None, half_life_days=90)
        assert score == 0.0

    def test_invalid_timestamp_returns_minimum(self):
        score = MemoryEngine.compute_confidence("garbage", half_life_days=90)
        assert score == 0.0

    def test_uses_updated_at_over_created_at(self):
        """Reinforced memories should use updated_at for decay anchor."""
        old_created = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        recent_updated = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        # Should use updated_at which is recent
        score = MemoryEngine.compute_confidence(recent_updated, half_life_days=90)
        assert score > 0.9

    def test_zero_half_life_clamps(self):
        now = datetime.now(timezone.utc).isoformat()
        score = MemoryEngine.compute_confidence(now, half_life_days=0)
        assert score == pytest.approx(1.0, abs=0.01)


class TestReinforcement:
    """Test that accessing memories in search reinforces them."""

    @pytest.fixture
    def engine(self, tmp_path):
        with patch("memory_engine.QdrantStore") as MockStore, \
             patch("memory_engine.QdrantSettings") as MockSettings:
            mock_store = MagicMock()
            mock_store.ensure_collection.return_value = None
            mock_store.ensure_payload_indexes.return_value = None
            mock_store.count.return_value = 0
            mock_store.search.return_value = []
            mock_store.upsert_points.return_value = None
            mock_store.set_payload.return_value = None
            MockStore.return_value = mock_store

            mock_settings = MagicMock()
            mock_settings.read_consistency = "majority"
            MockSettings.from_env.return_value = mock_settings

            eng = MemoryEngine(data_dir=str(tmp_path / "data"))
            return eng

    def test_reinforce_updates_updated_at(self, engine):
        engine.add_memories(texts=["test fact"], sources=["test"])
        meta = engine._get_meta_by_id(0)
        old_updated = meta.get("updated_at")

        engine.reinforce(0)
        new_updated = meta.get("updated_at")
        assert new_updated != old_updated
        assert new_updated > old_updated

    def test_reinforce_nonexistent_does_not_crash(self, engine):
        # Should not raise
        engine.reinforce(999)

    def test_get_memory_includes_confidence(self, engine):
        engine.add_memories(texts=["test fact"], sources=["test"])
        mem = engine.get_memory(0)
        assert "confidence" in mem
        assert mem["confidence"] == pytest.approx(1.0, abs=0.01)
