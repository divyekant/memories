"""Tests for lifecycle policy fields in extraction profiles and policy enforcement."""

import pytest
from extraction_profiles import ExtractionProfiles


@pytest.fixture
def profiles(tmp_path):
    return ExtractionProfiles(str(tmp_path / "profiles.json"))


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
