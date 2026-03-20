"""Tests for extraction profiles — CRUD and cascade resolution."""

import json
import os
import tempfile

import pytest

from extraction_profiles import DEFAULTS, ExtractionProfiles


@pytest.fixture
def profiles_path(tmp_path):
    return str(tmp_path / "profiles.json")


@pytest.fixture
def ep(profiles_path):
    return ExtractionProfiles(profiles_path)


class TestCRUD:
    def test_create_profile_and_list(self, ep):
        ep.put("claude-code/", {"mode": "aggressive"})
        all_profiles = ep.list_all()
        assert len(all_profiles) == 1
        assert all_profiles[0]["source_prefix"] == "claude-code/"

    def test_defaults_applied_on_create(self, ep):
        ep.put("claude-code/", {"mode": "aggressive"})
        profile = ep.get("claude-code/")
        assert profile["max_facts"] == DEFAULTS["max_facts"]
        assert profile["max_fact_chars"] == DEFAULTS["max_fact_chars"]
        assert profile["half_life_days"] == DEFAULTS["half_life_days"]
        assert profile["single_call"] == DEFAULTS["single_call"]
        assert profile["enabled"] == DEFAULTS["enabled"]
        assert profile["rules"] == DEFAULTS["rules"]

    def test_explicit_values_override_defaults(self, ep):
        ep.put("claude-code/", {"mode": "conservative", "max_facts": 10})
        profile = ep.get("claude-code/")
        assert profile["mode"] == "conservative"
        assert profile["max_facts"] == 10

    def test_update_existing_profile(self, ep):
        ep.put("claude-code/", {"mode": "aggressive", "max_facts": 20})
        ep.put("claude-code/", {"max_facts": 50})
        profile = ep.get("claude-code/")
        assert profile["max_facts"] == 50
        assert profile["mode"] == "aggressive"

    def test_delete_profile(self, ep):
        ep.put("claude-code/", {"mode": "aggressive"})
        result = ep.delete("claude-code/")
        assert result is True
        assert ep.get("claude-code/") is None
        assert ep.list_all() == []

    def test_delete_nonexistent_profile_returns_false(self, ep):
        result = ep.delete("nonexistent/")
        assert result is False

    def test_get_nonexistent_profile_returns_none(self, ep):
        assert ep.get("nonexistent/") is None

    def test_list_all_empty(self, ep):
        assert ep.list_all() == []

    def test_list_all_multiple_profiles(self, ep):
        ep.put("claude-code/", {"mode": "aggressive"})
        ep.put("work/", {"mode": "conservative"})
        all_profiles = ep.list_all()
        assert len(all_profiles) == 2
        prefixes = {p["source_prefix"] for p in all_profiles}
        assert prefixes == {"claude-code/", "work/"}

    def test_profile_persists_to_disk(self, profiles_path):
        ep1 = ExtractionProfiles(profiles_path)
        ep1.put("claude-code/", {"mode": "aggressive"})

        ep2 = ExtractionProfiles(profiles_path)
        profile = ep2.get("claude-code/")
        assert profile is not None
        assert profile["mode"] == "aggressive"

    def test_source_prefix_stored_in_profile(self, ep):
        ep.put("claude-code/", {"mode": "standard"})
        profile = ep.get("claude-code/")
        assert profile["source_prefix"] == "claude-code/"

    def test_put_returns_merged_profile(self, ep):
        result = ep.put("claude-code/", {"mode": "aggressive"})
        assert result["mode"] == "aggressive"
        assert result["max_facts"] == DEFAULTS["max_facts"]
        assert result["source_prefix"] == "claude-code/"


class TestCascadeResolution:
    def test_no_profile_returns_defaults(self, ep):
        result = ep.resolve("claude-code/memories/deep")
        for k, v in DEFAULTS.items():
            assert result[k] == v

    def test_exact_prefix_match(self, ep):
        ep.put("claude-code/", {"mode": "aggressive"})
        result = ep.resolve("claude-code/memories")
        assert result["mode"] == "aggressive"

    def test_child_inherits_parent(self, ep):
        ep.put("claude-code/", {"mode": "aggressive", "max_facts": 50})
        result = ep.resolve("claude-code/memories/deep")
        assert result["mode"] == "aggressive"
        assert result["max_facts"] == 50

    def test_child_overrides_parent(self, ep):
        ep.put("claude-code/", {"mode": "aggressive", "max_facts": 50})
        ep.put("claude-code/memories/", {"max_facts": 10})
        result = ep.resolve("claude-code/memories/deep")
        assert result["mode"] == "aggressive"
        assert result["max_facts"] == 10

    def test_most_specific_prefix_wins(self, ep):
        ep.put("claude-code/", {"mode": "aggressive"})
        ep.put("claude-code/memories/", {"mode": "standard"})
        ep.put("claude-code/memories/deep/", {"mode": "conservative"})
        result = ep.resolve("claude-code/memories/deep/nested")
        assert result["mode"] == "conservative"

    def test_deeply_nested_falls_back_to_root_prefix(self, ep):
        ep.put("claude-code/", {"mode": "conservative"})
        result = ep.resolve("claude-code/a/b/c/d/e")
        assert result["mode"] == "conservative"

    def test_resolve_source_without_trailing_slash(self, ep):
        ep.put("claude-code/", {"mode": "aggressive"})
        result = ep.resolve("claude-code/memories")
        assert result["mode"] == "aggressive"

    def test_resolve_unrelated_prefix_returns_defaults(self, ep):
        ep.put("work/", {"mode": "conservative"})
        result = ep.resolve("claude-code/memories")
        assert result["mode"] == DEFAULTS["mode"]

    def test_resolve_includes_source_prefix_field(self, ep):
        result = ep.resolve("claude-code/memories")
        assert "source_prefix" in result


class TestRulesCascade:
    def test_rules_stored_and_retrieved(self, ep):
        rules = {"min_confidence": 0.8, "exclude_pii": True}
        ep.put("claude-code/", {"rules": rules})
        profile = ep.get("claude-code/")
        assert profile["rules"] == rules

    def test_child_rules_replace_parent_rules(self, ep):
        ep.put("claude-code/", {"rules": {"min_confidence": 0.8, "extra": "parent"}})
        ep.put("claude-code/memories/", {"rules": {"min_confidence": 0.5}})
        result = ep.resolve("claude-code/memories/deep")
        assert result["rules"] == {"min_confidence": 0.5}
        assert "extra" not in result["rules"]

    def test_parent_rules_used_when_child_has_none(self, ep):
        parent_rules = {"min_confidence": 0.9}
        ep.put("claude-code/", {"rules": parent_rules})
        ep.put("claude-code/memories/", {"max_facts": 5})
        result = ep.resolve("claude-code/memories/deep")
        assert result["rules"] == parent_rules

    def test_empty_rules_in_child_replaces_parent_rules(self, ep):
        ep.put("claude-code/", {"rules": {"min_confidence": 0.8}})
        ep.put("claude-code/memories/", {"rules": {}})
        result = ep.resolve("claude-code/memories/deep")
        assert result["rules"] == {}

    def test_no_profile_rules_defaults_to_empty(self, ep):
        result = ep.resolve("claude-code/memories")
        assert result["rules"] == {}
