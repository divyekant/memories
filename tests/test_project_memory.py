"""Policy and namespace tests for shared project memories."""

import pytest

from memory_engine import MemoryEngine
from project_memory import (
    ALLOWED_ORIGIN_CLIENTS,
    PROJECT_KINDS,
    ProjectMemoryPolicyError,
    TrustedAuthorship,
    is_project_source,
    normalize_origin_client,
    parse_memory_source,
)


@pytest.fixture
def engine(tmp_path):
    return MemoryEngine(data_dir=str(tmp_path))


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


def test_trusted_authorship_has_stable_principal_identity():
    trusted = TrustedAuthorship.principal("alice", " Codex ")
    assert trusted.author == "alice"


def test_project_write_gate_rejects_detected_credentials_before_storage():
    from memory_engine import _validate_project_write

    with pytest.raises(ProjectMemoryPolicyError, match="credential-shaped"):
        _validate_project_write(
            "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456",
            "project/fplguru/knowledge",
            TrustedAuthorship.principal("alice"),
        )


def test_project_write_gate_keeps_person_and_legacy_sources_unchanged():
    from memory_engine import _validate_project_write

    _validate_project_write(
        "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456",
        "person/alice/fplguru/knowledge",
        None,
    )
    _validate_project_write(
        "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456",
        "project/fplguru/custom",
        None,
    )


def test_metadata_patch_cannot_spoof_or_replace_existing_author(engine):
    memory_id = engine.add_memories(
        ["legacy fact"],
        ["legacy/source"],
        trusted_authorship=TrustedAuthorship.principal("alice"),
    )[0]

    engine.update_memory(
        memory_id,
        metadata_patch={
            "author": "mallory",
            "contributors": ["mallory"],
            "origin_client": "spoofed",
            "source_memory_ids": [999],
            "kept": True,
        },
        trusted_authorship=TrustedAuthorship.principal("alice"),
    )

    meta = engine._get_meta_by_id(memory_id)
    assert meta["author"] == "alice"
    assert meta["origin_client"] == "other"
    assert "contributors" not in meta
    assert "source_memory_ids" not in meta
    assert meta["kept"] is True


def test_source_move_into_project_requires_and_applies_trusted_authorship(engine):
    memory_id = engine.add_memories(["fact"], ["legacy/source"])[0]

    with pytest.raises(ProjectMemoryPolicyError):
        engine.update_memory(memory_id, source="project/fplguru/knowledge")
    assert engine._get_meta_by_id(memory_id)["source"] == "legacy/source"

    engine.update_memory(
        memory_id,
        source="project/fplguru/knowledge",
        trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
    )
    meta = engine._get_meta_by_id(memory_id)
    assert meta["source"] == "project/fplguru/knowledge"
    assert meta["author"] == "alice"


def test_upsert_replacement_applies_current_trusted_authorship(engine):
    created = engine.upsert_memory(
        text="first",
        source="project/fplguru/state",
        key="status",
        metadata={"author": "mallory"},
        trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
    )
    updated = engine.upsert_memory(
        text="second",
        source="project/fplguru/state",
        key="status",
        metadata={"origin_client": "spoofed"},
        trusted_authorship=TrustedAuthorship.principal("bob", "hook"),
    )

    assert updated["id"] == created["id"]
    meta = engine._get_meta_by_id(created["id"])
    assert meta["text"] == "second"
    assert meta["author"] == "bob"
    assert meta["origin_client"] == "hook"


def test_project_secret_is_rejected_before_storage(engine):
    with pytest.raises(ProjectMemoryPolicyError, match="credential-shaped"):
        engine.add_memories(
            ["Production token is ghp_abcdefghijklmnopqrstuvwxyz123456"],
            ["project/fplguru/knowledge"],
            trusted_authorship=TrustedAuthorship.principal("alice"),
        )
    assert engine.metadata == []
