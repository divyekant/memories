"""Tests for lifecycle policy fields in extraction profiles and policy enforcement."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
from extraction_profiles import ExtractionProfiles
from memory_engine import MemoryEngine


@pytest.fixture
def profiles(tmp_path):
    return ExtractionProfiles(str(tmp_path / "profiles.json"))


@pytest.fixture
def engine(tmp_path, profiles):
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
        eng._profiles = profiles
        yield eng


def test_profile_supports_ttl_days(profiles):
    """Profile should accept and resolve ttl_days field."""
    profiles.put("wip/", {"ttl_days": 30})
    resolved = profiles.resolve("wip/test-project")
    assert resolved["ttl_days"] == 30


def test_profile_supports_confidence_threshold(profiles):
    """Profile should accept confidence_threshold and min_age_days."""
    profiles.put("claude-code/", {"confidence_threshold": 0.1, "min_age_days": 90})
    resolved = profiles.resolve("claude-code/test")
    assert resolved["confidence_threshold"] == 0.1
    assert resolved["min_age_days"] == 90


def test_profile_supports_confidence_half_life(profiles):
    """Profile should accept confidence_half_life_days."""
    profiles.put("claude-code/", {"confidence_half_life_days": 60})
    resolved = profiles.resolve("claude-code/test")
    assert resolved["confidence_half_life_days"] == 60


def test_profile_defaults_lifecycle_to_none(profiles):
    """Lifecycle fields should default to None (no policy)."""
    resolved = profiles.resolve("anything/test")
    assert resolved.get("ttl_days") is None
    assert resolved.get("confidence_threshold") is None
    assert resolved.get("min_age_days") is None
    assert resolved.get("confidence_half_life_days") is None


def test_child_profile_overrides_parent_ttl(profiles):
    """Child profile with explicit ttl_days=None should override parent's TTL."""
    profiles.put("wip/", {"ttl_days": 30})
    profiles.put("wip/important/", {"ttl_days": None})
    resolved = profiles.resolve("wip/important/test")
    assert resolved["ttl_days"] is None


# -- Task 2: _policy_ namespace protection --


def test_policy_metadata_protected_from_patch(engine):
    """PATCH metadata_patch should not overwrite _policy_ fields."""
    mem_id = engine.add_memories(texts=["test"], sources=["test/policy"])[0]
    # Simulate policy setting evidence
    meta = engine._get_meta_by_id(mem_id)
    meta["_policy_archived_reason"] = "ttl"
    engine.save()

    # Try to overwrite via update_memory
    engine.update_memory(mem_id, metadata_patch={"_policy_archived_reason": "hacked"})
    updated = engine.get_memory(mem_id)
    assert updated.get("_policy_archived_reason") == "ttl"  # unchanged
