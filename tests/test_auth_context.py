"""Tests for AuthContext — role checks and prefix enforcement."""

import pytest
from auth_context import AuthContext


# ---------------------------------------------------------------------------
# unrestricted() classmethod
# ---------------------------------------------------------------------------

class TestUnrestricted:
    def test_unrestricted_is_admin(self):
        ctx = AuthContext.unrestricted()
        assert ctx.role == "admin"

    def test_unrestricted_has_no_prefixes(self):
        ctx = AuthContext.unrestricted()
        assert ctx.prefixes is None

    def test_unrestricted_key_type_is_none(self):
        ctx = AuthContext.unrestricted()
        assert ctx.key_type == "none"

    def test_unrestricted_can_read_anything(self):
        ctx = AuthContext.unrestricted()
        assert ctx.can_read("claude-code/foo") is True
        assert ctx.can_read("myapp/bar") is True
        assert ctx.can_read("") is True

    def test_unrestricted_can_write_anything(self):
        ctx = AuthContext.unrestricted()
        assert ctx.can_write("claude-code/foo") is True
        assert ctx.can_write("anything/at/all") is True

    def test_unrestricted_can_manage_keys(self):
        ctx = AuthContext.unrestricted()
        assert ctx.can_manage_keys is True


# ---------------------------------------------------------------------------
# Prefix matching
# ---------------------------------------------------------------------------

class TestPrefixMatching:
    def test_glob_star_matches_child(self):
        ctx = AuthContext(role="read-write", prefixes=["claude-code/*"], key_type="managed")
        assert ctx.can_read("claude-code/foo") is True

    def test_glob_star_matches_deep_nested(self):
        ctx = AuthContext(role="read-write", prefixes=["claude-code/*"], key_type="managed")
        assert ctx.can_read("claude-code/deep/nested") is True

    def test_glob_star_does_not_match_similar_prefix(self):
        """claude-code/* must NOT match claude-codex/other."""
        ctx = AuthContext(role="read-write", prefixes=["claude-code/*"], key_type="managed")
        assert ctx.can_read("claude-codex/other") is False

    def test_plain_prefix_matches_child(self):
        ctx = AuthContext(role="read-write", prefixes=["myapp/"], key_type="managed")
        assert ctx.can_read("myapp/something") is True

    def test_plain_prefix_no_trailing_slash_matches(self):
        ctx = AuthContext(role="read-write", prefixes=["myapp"], key_type="managed")
        assert ctx.can_read("myapp/something") is True

    def test_no_match_returns_false(self):
        ctx = AuthContext(role="read-write", prefixes=["myapp/*"], key_type="managed")
        assert ctx.can_read("other/something") is False

    def test_multiple_prefixes_any_match(self):
        ctx = AuthContext(role="read-write", prefixes=["alpha/*", "beta/*"], key_type="managed")
        assert ctx.can_read("alpha/one") is True
        assert ctx.can_read("beta/two") is True
        assert ctx.can_read("gamma/three") is False

    def test_empty_source_does_not_match(self):
        ctx = AuthContext(role="read-write", prefixes=["myapp/*"], key_type="managed")
        assert ctx.can_read("") is False

    def test_none_prefixes_matches_everything(self):
        ctx = AuthContext(role="read-write", prefixes=None, key_type="env")
        assert ctx.can_read("anything/at/all") is True
        assert ctx.can_read("") is True

    def test_path_traversal_blocked(self):
        ctx = AuthContext(role="read-write", prefixes=["myapp/*"], key_type="managed")
        assert not ctx.can_read("myapp/../kai/secret")
        assert not ctx.can_write("myapp/../kai/secret")

    def test_exact_source_match(self):
        ctx = AuthContext(role="read-write", prefixes=["myapp/*"], key_type="managed")
        assert ctx.can_read("myapp")
        assert ctx.can_write("myapp")


# ---------------------------------------------------------------------------
# Role-based access: read-only
# ---------------------------------------------------------------------------

class TestReadOnly:
    def test_can_read_allowed_prefix(self):
        ctx = AuthContext(role="read-only", prefixes=["myapp/*"], key_type="managed")
        assert ctx.can_read("myapp/data") is True

    def test_cannot_read_disallowed_prefix(self):
        ctx = AuthContext(role="read-only", prefixes=["myapp/*"], key_type="managed")
        assert ctx.can_read("other/data") is False

    def test_cannot_write_even_allowed_prefix(self):
        ctx = AuthContext(role="read-only", prefixes=["myapp/*"], key_type="managed")
        assert ctx.can_write("myapp/data") is False

    def test_cannot_write_any_prefix(self):
        ctx = AuthContext(role="read-only", prefixes=None, key_type="env")
        assert ctx.can_write("anything") is False

    def test_cannot_manage_keys(self):
        ctx = AuthContext(role="read-only", prefixes=["myapp/*"], key_type="managed")
        assert ctx.can_manage_keys is False


# ---------------------------------------------------------------------------
# Role-based access: read-write
# ---------------------------------------------------------------------------

class TestReadWrite:
    def test_can_read_allowed(self):
        ctx = AuthContext(role="read-write", prefixes=["proj/*"], key_type="managed")
        assert ctx.can_read("proj/file") is True

    def test_can_write_allowed(self):
        ctx = AuthContext(role="read-write", prefixes=["proj/*"], key_type="managed")
        assert ctx.can_write("proj/file") is True

    def test_cannot_read_disallowed(self):
        ctx = AuthContext(role="read-write", prefixes=["proj/*"], key_type="managed")
        assert ctx.can_read("other/file") is False

    def test_cannot_write_disallowed(self):
        ctx = AuthContext(role="read-write", prefixes=["proj/*"], key_type="managed")
        assert ctx.can_write("other/file") is False

    def test_cannot_manage_keys(self):
        ctx = AuthContext(role="read-write", prefixes=["proj/*"], key_type="managed")
        assert ctx.can_manage_keys is False


# ---------------------------------------------------------------------------
# Admin role
# ---------------------------------------------------------------------------

class TestAdmin:
    def test_admin_can_read_any(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        assert ctx.can_read("anything/here") is True

    def test_admin_can_write_any(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        assert ctx.can_write("anything/here") is True

    def test_admin_can_manage_keys(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        assert ctx.can_manage_keys is True


# ---------------------------------------------------------------------------
# filter_results
# ---------------------------------------------------------------------------

class TestFilterResults:
    def test_admin_returns_all(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        results = [
            {"source": "a/one", "text": "x"},
            {"source": "b/two", "text": "y"},
        ]
        assert ctx.filter_results(results) == results

    def test_filters_to_allowed_prefix(self):
        ctx = AuthContext(role="read-write", prefixes=["a/*"], key_type="managed")
        results = [
            {"source": "a/one", "text": "x"},
            {"source": "b/two", "text": "y"},
            {"source": "a/three", "text": "z"},
        ]
        filtered = ctx.filter_results(results)
        assert len(filtered) == 2
        assert all(r["source"].startswith("a/") for r in filtered)

    def test_filters_empty_source(self):
        ctx = AuthContext(role="read-write", prefixes=["a/*"], key_type="managed")
        results = [{"text": "no source"}, {"source": "", "text": "empty"}]
        assert ctx.filter_results(results) == []

    def test_empty_results_returns_empty(self):
        ctx = AuthContext(role="read-write", prefixes=["a/*"], key_type="managed")
        assert ctx.filter_results([]) == []

    def test_none_prefixes_returns_all(self):
        ctx = AuthContext(role="read-write", prefixes=None, key_type="env")
        results = [{"source": "x/1"}, {"source": "y/2"}]
        assert ctx.filter_results(results) == results


# ---------------------------------------------------------------------------
# to_me_response
# ---------------------------------------------------------------------------

class TestToMeResponse:
    def test_basic_response(self):
        ctx = AuthContext(role="read-write", prefixes=["proj/*"], key_type="managed")
        resp = ctx.to_me_response()
        assert resp["type"] == "managed"
        assert resp["role"] == "read-write"
        assert resp["prefixes"] == ["proj/*"]
        assert "id" not in resp
        assert "name" not in resp

    def test_admin_no_prefixes_key(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        resp = ctx.to_me_response()
        assert resp["type"] == "env"
        assert resp["role"] == "admin"
        assert "prefixes" not in resp

    def test_includes_id_and_name_when_set(self):
        ctx = AuthContext(
            role="read-write",
            prefixes=["proj/*"],
            key_type="managed",
            key_id="abc-123",
            key_name="My Key",
        )
        resp = ctx.to_me_response()
        assert resp["id"] == "abc-123"
        assert resp["name"] == "My Key"

    def test_no_id_or_name_when_not_set(self):
        ctx = AuthContext(role="admin", prefixes=None, key_type="env")
        resp = ctx.to_me_response()
        assert "id" not in resp
        assert "name" not in resp
