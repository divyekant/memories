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


# -- Task 3: Per-prefix confidence half-life --


def test_per_prefix_confidence_half_life(engine, profiles):
    """Confidence should use per-prefix half-life when configured."""
    profiles.put("fast-decay/", {"confidence_half_life_days": 30})
    # Add a memory 60 days old to fast-decay prefix
    mem_id = engine.add_memories(texts=["old memory"], sources=["fast-decay/test"])[0]
    # Manually set created_at to 60 days ago
    old_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)).isoformat()
    meta = engine._get_meta_by_id(mem_id)
    meta["created_at"] = old_date
    meta["updated_at"] = old_date
    engine.save()

    mem = engine.get_memory(mem_id)
    # With 30-day half-life and 60 days old: confidence = 0.5^(60/30) = 0.25
    assert 0.20 <= mem["confidence"] <= 0.30


# -- Task 4: enforce_policies engine method --


def _set_age(engine, mem_id, days):
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    meta = engine._get_meta_by_id(mem_id)
    meta["created_at"] = old
    meta["updated_at"] = old
    engine.save()


def test_enforce_ttl_dry_run(engine, profiles):
    """TTL policy should identify expired memories in dry-run."""
    profiles.put("wip/", {"ttl_days": 30})
    # Add old memory
    mem_id = engine.add_memories(texts=["old wip"], sources=["wip/test"])[0]
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=True)
    assert result["summary"]["would_archive"] >= 1
    assert any(a["memory_id"] == mem_id and a["action"] == "would_archive" for a in result["actions"])


def test_enforce_ttl_execute(engine, profiles):
    """TTL policy should archive expired memories when dry_run=False."""
    profiles.put("wip/", {"ttl_days": 30})
    mem_id = engine.add_memories(texts=["old wip"], sources=["wip/test"])[0]
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=False)
    assert result["summary"]["archived"] >= 1
    mem = engine.get_memory(mem_id)
    assert mem.get("archived") is True
    assert mem.get("_policy_archived_reason") == "ttl"


def test_enforce_confidence_threshold(engine, profiles):
    """Low-confidence memories should be archived with evidence."""
    profiles.put("claude-code/", {"confidence_threshold": 0.1, "min_age_days": 90})
    mem_id = engine.add_memories(texts=["ancient"], sources=["claude-code/test"])[0]
    _set_age(engine, mem_id, days=365)  # very old, confidence near 0

    result = engine.enforce_policies(dry_run=False)
    assert any(a["memory_id"] == mem_id for a in result["actions"])
    mem = engine.get_memory(mem_id)
    assert mem.get("_policy_archived_reason") == "confidence"
    assert "_policy_archived_confidence" in mem


def test_enforce_excludes_pinned(engine, profiles):
    """Pinned memories should never be archived by policy."""
    profiles.put("wip/", {"ttl_days": 30})
    mem_id = engine.add_memories(texts=["pinned wip"], sources=["wip/test"])[0]
    engine.update_memory(mem_id, pinned=True)
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=True)
    assert not any(a["memory_id"] == mem_id for a in result["actions"])
    assert result["summary"]["excluded_pinned"] >= 1


def test_enforce_excludes_already_archived(engine, profiles):
    """Already archived memories should be skipped."""
    profiles.put("wip/", {"ttl_days": 30})
    mem_id = engine.add_memories(texts=["archived wip"], sources=["wip/test"])[0]
    engine.update_memory(mem_id, archived=True)
    _set_age(engine, mem_id, days=45)

    result = engine.enforce_policies(dry_run=True)
    assert not any(a["memory_id"] == mem_id for a in result["actions"])


def test_enforce_ttl_takes_precedence_over_confidence(engine, profiles):
    """When both TTL and confidence match, TTL is the primary reason."""
    profiles.put("wip/", {"ttl_days": 30, "confidence_threshold": 0.8, "min_age_days": 7})
    mem_id = engine.add_memories(texts=["both match"], sources=["wip/test"])[0]
    _set_age(engine, mem_id, days=45)  # confidence ~0.71, below 0.8 threshold

    result = engine.enforce_policies(dry_run=True)
    action = next(a for a in result["actions"] if a["memory_id"] == mem_id)
    assert action["reasons"][0]["rule"] == "ttl"
    assert len(action["reasons"]) == 2  # both reasons reported


# -- Task 5: POST /maintenance/enforce-policies endpoint --

import importlib
import os
import tempfile

from starlette.testclient import TestClient


class TestEnforcePoliciesEndpoint:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "API_KEY": "admin-key",
                "EXTRACT_PROVIDER": "",
                "DATA_DIR": tmpdir,
            }
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)

                mock_engine = MagicMock()
                mock_engine.enforce_policies.return_value = {
                    "dry_run": True,
                    "actions": [],
                    "summary": {
                        "candidates_scanned": 0,
                        "would_archive": 0,
                        "by_rule": {"ttl": 0, "confidence": 0},
                        "excluded_pinned": 0,
                        "excluded_already_archived": 0,
                    },
                }
                app_module.memory = mock_engine

                yield TestClient(app_module.app), app_module, mock_engine

    def test_enforce_policies_endpoint_dry_run(self, client):
        """POST /maintenance/enforce-policies should default to dry_run=true."""
        tc, mod, mock_engine = client
        resp = tc.post(
            "/maintenance/enforce-policies",
            headers={"X-API-Key": "admin-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        mock_engine.enforce_policies.assert_called_once_with(dry_run=True)

    def test_enforce_policies_requires_admin(self, client):
        """enforce-policies should reject unauthenticated requests."""
        tc, mod, mock_engine = client
        resp = tc.post(
            "/maintenance/enforce-policies",
            headers={"X-API-Key": "wrong-key"},
        )
        # Wrong key returns 401 (auth middleware rejects before admin check)
        assert resp.status_code in (401, 403)
