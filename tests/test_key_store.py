"""Tests for key_store module — API key generation, hashing, and CRUD."""
import hashlib
import os
import tempfile
import time

import pytest

from key_store import KeyStore


class TestKeyGeneration:
    def test_key_has_mem_prefix_and_36_chars(self):
        raw = KeyStore.generate_raw_key()
        assert raw.startswith("mem_")
        assert len(raw) == 36

    def test_hash_is_deterministic(self):
        raw = KeyStore.generate_raw_key()
        assert KeyStore.hash_key(raw) == KeyStore.hash_key(raw)

    def test_hash_matches_hashlib_sha256(self):
        raw = "mem_abcdef1234567890abcdef1234567890"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert KeyStore.hash_key(raw) == expected


class TestCreateKey:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "keys.db")
        self.ks = KeyStore(self.db_path)

    def test_returns_key_with_all_fields(self):
        result = self.ks.create_key("test-key", "read-only", ["proj/"])
        assert "id" in result
        assert "key" in result
        assert result["key"].startswith("mem_")
        assert len(result["key"]) == 36
        assert result["key_prefix"] == result["key"][:8]
        assert result["name"] == "test-key"
        assert result["role"] == "read-only"
        assert result["prefixes"] == ["proj/"]
        assert "created_at" in result

    def test_admin_ignores_prefixes(self):
        result = self.ks.create_key("admin-key", "admin", ["should/", "be/", "ignored/"])
        assert result["role"] == "admin"
        assert result["prefixes"] == []

    def test_rejects_invalid_role(self):
        with pytest.raises(ValueError, match="Invalid role"):
            self.ks.create_key("bad-key", "superadmin", [])


class TestLookupKey:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "keys.db")
        self.ks = KeyStore(self.db_path)

    def test_finds_existing_key(self):
        created = self.ks.create_key("lookup-test", "read-write", ["a/"])
        raw_key = created["key"]
        found = self.ks.lookup(raw_key)
        assert found is not None
        assert found["id"] == created["id"]
        assert found["name"] == "lookup-test"
        assert found["role"] == "read-write"
        assert found["prefixes"] == ["a/"]

    def test_returns_none_for_nonexistent(self):
        assert self.ks.lookup("mem_0000000000000000000000000000dead") is None

    def test_returns_none_for_revoked(self):
        created = self.ks.create_key("revoke-me", "read-only", [])
        self.ks.revoke(created["id"])
        assert self.ks.lookup(created["key"]) is None

    def test_increments_usage_count_and_updates_last_used_at(self):
        created = self.ks.create_key("counter", "read-only", [])
        raw_key = created["key"]

        self.ks.lookup(raw_key)
        self.ks.lookup(raw_key)
        result = self.ks.lookup(raw_key)

        assert result["usage_count"] == 3
        assert result["last_used_at"] is not None


class TestUpdateKey:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "keys.db")
        self.ks = KeyStore(self.db_path)

    def test_updates_name(self):
        created = self.ks.create_key("old-name", "read-only", [])
        self.ks.update_key(created["id"], name="new-name")
        keys = self.ks.list_keys()
        match = [k for k in keys if k["id"] == created["id"]][0]
        assert match["name"] == "new-name"

    def test_updates_role(self):
        created = self.ks.create_key("role-test", "read-only", [])
        self.ks.update_key(created["id"], role="read-write")
        keys = self.ks.list_keys()
        match = [k for k in keys if k["id"] == created["id"]][0]
        assert match["role"] == "read-write"

    def test_updates_prefixes(self):
        created = self.ks.create_key("prefix-test", "read-write", ["old/"])
        self.ks.update_key(created["id"], prefixes=["new/", "other/"])
        keys = self.ks.list_keys()
        match = [k for k in keys if k["id"] == created["id"]][0]
        assert match["prefixes"] == ["new/", "other/"]

    def test_rejects_invalid_role(self):
        created = self.ks.create_key("role-test", "read-only", [])
        with pytest.raises(ValueError, match="Invalid role"):
            self.ks.update_key(created["id"], role="superadmin")

    def test_raises_for_missing_key(self):
        with pytest.raises(ValueError, match="not found"):
            self.ks.update_key("nonexistent-id", name="nope")

    def test_raises_for_revoked_key(self):
        created = self.ks.create_key("revoked", "read-only", [])
        self.ks.revoke(created["id"])
        with pytest.raises(ValueError, match="revoked"):
            self.ks.update_key(created["id"], name="nope")


class TestRevokeKey:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "keys.db")
        self.ks = KeyStore(self.db_path)

    def test_sets_revoked(self):
        created = self.ks.create_key("revoke-me", "read-only", [])
        self.ks.revoke(created["id"])
        keys = self.ks.list_keys()
        match = [k for k in keys if k["id"] == created["id"]][0]
        assert match["revoked"] == 1

    def test_raises_for_already_revoked(self):
        created = self.ks.create_key("revoke-twice", "read-only", [])
        self.ks.revoke(created["id"])
        with pytest.raises(ValueError, match="already revoked"):
            self.ks.revoke(created["id"])

    def test_raises_for_not_found(self):
        with pytest.raises(ValueError):
            self.ks.revoke("nonexistent-id")


class TestListKeys:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "keys.db")
        self.ks = KeyStore(self.db_path)

    def test_lists_all_including_revoked(self):
        self.ks.create_key("a", "read-only", [])
        created_b = self.ks.create_key("b", "read-write", [])
        self.ks.revoke(created_b["id"])
        keys = self.ks.list_keys()
        assert len(keys) == 2
        names = {k["name"] for k in keys}
        assert names == {"a", "b"}

    def test_does_not_expose_raw_key(self):
        self.ks.create_key("secret", "admin", [])
        keys = self.ks.list_keys()
        assert len(keys) == 1
        assert "key" not in keys[0]
        assert "key_hash" not in keys[0]
