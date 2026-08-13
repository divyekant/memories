"""Tests for key_store module — API key generation, hashing, and CRUD."""
import hashlib
import json
import os
import sqlite3
import tempfile
import time

import pytest

from key_store import KeyStore


class TestPrincipalIdMigration:
    def test_backfills_existing_rows_from_pre_feature_schema(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "keys.db")
        raw_key = "mem_" + "a" * 32
        now = "2026-08-11T00:00:00Z"

        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                role TEXT NOT NULL,
                prefixes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                usage_count INTEGER DEFAULT 0,
                revoked INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            """INSERT INTO api_keys
               (id, name, key_hash, key_prefix, role, prefixes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-id",
                "legacy-key",
                KeyStore.hash_key(raw_key),
                raw_key[:8],
                "admin",
                json.dumps([]),
                now,
            ),
        )
        conn.commit()
        conn.close()

        store = KeyStore(db_path)
        listed = store.list_keys()
        assert listed[0]["principal_id"] == "legacy-key"
        looked_up = store.lookup(raw_key)
        assert looked_up["principal_id"] == "legacy-key"

        # A later startup must preserve an explicitly changed principal.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE api_keys SET principal_id = ? WHERE id = ?",
            ("stable-principal", "legacy-id"),
        )
        conn.commit()
        conn.close()
        restarted = KeyStore(db_path)
        assert restarted.list_keys()[0]["principal_id"] == "stable-principal"

    def test_invalid_legacy_display_name_backfills_to_null(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "keys.db")
        raw_key = "mem_" + "b" * 32
        now = "2026-08-11T00:00:00Z"

        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                role TEXT NOT NULL,
                prefixes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                usage_count INTEGER DEFAULT 0,
                revoked INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            """INSERT INTO api_keys
               (id, name, key_hash, key_prefix, role, prefixes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-display-id",
                "Legacy Display Name",
                KeyStore.hash_key(raw_key),
                raw_key[:8],
                "read-write",
                json.dumps(["legacy/*"]),
                now,
            ),
        )
        conn.commit()
        conn.close()

        store = KeyStore(db_path)

        assert store.list_keys()[0]["principal_id"] is None
        assert store.lookup(raw_key)["principal_id"] is None

    def test_startup_normalizes_invalid_backfill_but_preserves_valid_principal(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "keys.db")
        store = KeyStore(db_path)
        invalid = store.create_key("Legacy Display Name", "read-write", ["legacy/*"])
        valid = store.create_key(
            "Another Display Name",
            "read-write",
            ["legacy/*"],
            principal_id="stable-principal",
        )

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE api_keys SET principal_id = ? WHERE id = ?",
            ("Legacy Display Name", invalid["id"]),
        )
        conn.commit()
        conn.close()

        restarted = KeyStore(db_path)
        principals = {item["id"]: item["principal_id"] for item in restarted.list_keys()}
        assert principals[invalid["id"]] is None
        assert principals[valid["id"]] == "stable-principal"


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
        assert result["principal_id"] == "test-key"
        assert "created_at" in result

    def test_accepts_explicit_principal_id(self):
        result = self.ks.create_key(
            "Display Name", "read-only", ["proj/"], principal_id="person-a"
        )
        assert result["principal_id"] == "person-a"
        assert self.ks.list_keys()[0]["principal_id"] == "person-a"

    def test_omitted_principal_does_not_use_invalid_display_name(self):
        result = self.ks.create_key("Legacy Display Name", "read-only", ["proj/"])
        assert result["principal_id"] is None

    def test_omitted_principal_uses_name_when_name_is_valid_slug(self):
        result = self.ks.create_key("legacy-key", "read-only", ["proj/"])
        assert result["principal_id"] == "legacy-key"

    def test_rejects_invalid_explicit_principal_id(self):
        with pytest.raises(ValueError, match="principal_id"):
            self.ks.create_key(
                "Display Name", "read-only", ["proj/"], principal_id="Person A"
            )

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
        created = self.ks.create_key(
            "lookup-test", "read-write", ["a/"], principal_id="lookup-principal"
        )
        raw_key = created["key"]
        found = self.ks.lookup(raw_key)
        assert found is not None
        assert found["id"] == created["id"]
        assert found["name"] == "lookup-test"
        assert found["principal_id"] == "lookup-principal"
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
        assert match["principal_id"] == "old-name"

    def test_updates_principal_id_explicitly(self):
        created = self.ks.create_key("display-name", "read-only", [])
        self.ks.update_key(created["id"], principal_id="stable-id")
        match = [k for k in self.ks.list_keys() if k["id"] == created["id"]][0]
        assert match["principal_id"] == "stable-id"

    def test_rejects_invalid_principal_id_update(self):
        created = self.ks.create_key("display-name", "read-only", [])
        with pytest.raises(ValueError, match="principal_id"):
            self.ks.update_key(created["id"], principal_id="Not A Slug")

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
