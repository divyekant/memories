"""Policy and namespace tests for shared project memories."""

import pytest

from project_memory import (
    ALLOWED_ORIGIN_CLIENTS,
    PROJECT_KINDS,
    ProjectMemoryPolicyError,
    TrustedAuthorship,
    is_project_source,
    normalize_origin_client,
    parse_memory_source,
)


@pytest.mark.parametrize("kind", sorted(PROJECT_KINDS))
@pytest.mark.parametrize("namespace", ["person", "project"])
def test_parse_memory_source_accepts_strict_project_namespaces(
    namespace, kind
):
    project_id = "fplguru"
    principal_id = "alice" if namespace == "person" else None
    source = (
        f"person/{principal_id}/fplguru/{kind}"
        if namespace == "person"
        else f"project/fplguru/{kind}"
    )
    parsed = parse_memory_source(source)

    assert parsed is not None
    assert parsed.namespace == namespace
    assert parsed.principal_id == principal_id
    assert parsed.project_id == project_id
    assert parsed.kind == kind


@pytest.mark.parametrize(
    "source",
    [
        "person/Alice/fplguru/knowledge",
        "person/alice/fpl_guru/knowledge",
        "person/alice/fplguru/unknown",
        "person/alice/fplguru/knowledge/extra",
        "project/FPLGuru/knowledge",
        "project/fpl_guru/knowledge",
        "project/fplguru/unknown",
        "project/fplguru/knowledge/extra",
    ],
)
def test_parse_memory_source_leaves_invalid_similar_sources_legacy(source):
    assert parse_memory_source(source) is None
    assert not is_project_source(source)


def test_project_kinds_are_exactly_the_declared_four():
    assert PROJECT_KINDS == frozenset({"decisions", "knowledge", "state", "operations"})


def test_origin_client_is_allowlisted_and_normalized():
    assert ALLOWED_ORIGIN_CLIENTS == frozenset(
        {"codex", "claude-code", "hook", "manual", "other"}
    )
    assert normalize_origin_client("  Claude-Code ") == "claude-code"
    assert normalize_origin_client("not-a-client") == "other"


def test_trusted_principal_authorship_stamps_server_identity():
    trusted = TrustedAuthorship.principal("alice", "  Codex ")

    assert trusted.author == "alice"
    assert trusted.origin_client == "codex"
    assert trusted.as_metadata() == {"author": "alice", "origin_client": "codex"}


def test_trusted_system_authorship_normalizes_contributors_and_source_ids():
    trusted = TrustedAuthorship.system(
        contributors=[" alice ", "alice", "bob"],
        source_memory_ids=[7, 7, 9],
        origin_client="unknown-client",
    )

    assert trusted.author == "system"
    assert trusted.origin_client == "other"
    assert trusted.as_metadata() == {
        "author": "system",
        "contributors": ["alice", "bob"],
        "source_memory_ids": [7, 9],
        "origin_client": "other",
    }


def test_policy_error_is_a_value_error():
    assert issubclass(ProjectMemoryPolicyError, ValueError)
