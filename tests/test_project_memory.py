"""Policy and namespace tests for shared project memories."""

from contextlib import contextmanager

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


def test_project_write_gate_keeps_person_and_nonreserved_legacy_sources_unchanged():
    from memory_engine import _validate_project_write

    _validate_project_write(
        "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456",
        "person/alice/fplguru/knowledge",
        None,
    )


@pytest.mark.parametrize(
    "source",
    [
        "project/fplguru/custom",
        "project/fplguru/knowledge/extra",
        "project/FPLGuru/knowledge",
        "person/Alice/fplguru/knowledge",
        "person/alice/fplguru/custom",
    ],
)
def test_project_write_gate_rejects_malformed_reserved_sources(source):
    from memory_engine import _validate_project_write

    with pytest.raises(ProjectMemoryPolicyError, match="sources must be"):
        _validate_project_write("shared fact", source, None)


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


def test_metadata_only_patch_preserves_existing_project_provenance(engine):
    memory_id = engine.add_memories(
        ["Shared project fact"],
        ["project/fplguru/knowledge"],
        trusted_authorship=TrustedAuthorship.system(
            contributors=["alice", "bob"],
            source_memory_ids=[7, 9],
            origin_client="manual",
        ),
    )[0]

    engine.update_memory(
        memory_id,
        metadata_patch={"kept": True},
        trusted_authorship=TrustedAuthorship.principal("carol", "codex"),
    )

    meta = engine._get_meta_by_id(memory_id)
    assert meta["author"] == "system"
    assert meta["origin_client"] == "manual"
    assert meta["contributors"] == ["alice", "bob"]
    assert meta["source_memory_ids"] == [7, 9]
    assert meta["kept"] is True


def test_same_policy_source_move_preserves_project_provenance(engine):
    memory_id = engine.add_memories(
        ["Shared project fact"],
        ["project/fplguru/knowledge"],
        trusted_authorship=TrustedAuthorship.system(
            contributors=["alice"],
            source_memory_ids=[7],
            origin_client="manual",
        ),
    )[0]

    engine.update_memory(
        memory_id,
        source="project/fplguru/state",
        trusted_authorship=TrustedAuthorship.principal("carol", "codex"),
    )

    meta = engine._get_meta_by_id(memory_id)
    assert meta["source"] == "project/fplguru/state"
    assert meta["author"] == "system"
    assert meta["origin_client"] == "manual"
    assert meta["contributors"] == ["alice"]
    assert meta["source_memory_ids"] == [7]


@pytest.mark.parametrize(
    ("old_source", "new_source"),
    [
        ("person/alice/fplguru/knowledge", "person/bob/fplguru/knowledge"),
        ("person/alice/fplguru/knowledge", "person/alice/other/knowledge"),
        ("project/fplguru/knowledge", "project/other/knowledge"),
    ],
)
def test_owner_or_project_source_move_is_an_authorship_boundary(engine, old_source, new_source):
    memory_id = engine.add_memories(
        ["Original fact"],
        [old_source],
        trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
    )[0]

    engine.update_memory(
        memory_id,
        source=new_source,
        trusted_authorship=TrustedAuthorship.principal("bob", "claude-code"),
    )

    meta = engine._get_meta_by_id(memory_id)
    assert meta["source"] == new_source
    assert meta["author"] == "bob"
    assert meta["origin_client"] == "claude-code"


def test_namespace_crossing_source_move_requires_trusted_authorship(engine):
    memory_id = engine.add_memories(["Private fact"], ["legacy/fplguru"])[0]

    with pytest.raises(ProjectMemoryPolicyError, match="trusted"):
        engine.update_memory(
            memory_id,
            source="person/alice/fplguru/knowledge",
        )

    assert engine._get_meta_by_id(memory_id)["source"] == "legacy/fplguru"


def test_legacy_text_and_source_update_remains_unmanaged_compatible(engine):
    memory_id = engine.add_memories(["Old fact"], ["legacy/fplguru"])[0]

    engine.update_memory(
        memory_id,
        text="New fact",
        source="codex/fplguru",
    )

    meta = engine._get_meta_by_id(memory_id)
    assert meta["text"] == "New fact"
    assert meta["source"] == "codex/fplguru"


def test_source_move_into_project_requires_and_applies_trusted_authorship(engine):
    memory_id = engine.add_memories(["fact"], ["legacy/source"])[0]
    # Simulate a pre-upgrade legacy point that predates reserved-field
    # sanitization. Both in-memory state and Qdrant contain stale provenance.
    legacy = engine._get_meta_by_id(memory_id)
    legacy["contributors"] = ["mallory"]
    legacy["source_memory_ids"] = [999]
    engine.qdrant_store.set_payload(
        memory_id,
        {"contributors": ["mallory"], "source_memory_ids": [999]},
    )

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
    assert "contributors" not in meta
    assert "source_memory_ids" not in meta

    # Qdrant payload updates merge by default. A snapshot restore must not
    # resurrect reserved provenance removed during the promotion.
    engine.reload_from_qdrant()
    reloaded = engine._get_meta_by_id(memory_id)
    assert reloaded["author"] == "alice"
    assert "contributors" not in reloaded
    assert "source_memory_ids" not in reloaded


def test_source_only_move_rejects_existing_credential_before_mutation(engine):
    secret = "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456"
    memory_id = engine.add_memories([secret], ["legacy/source"])[0]
    before = dict(engine._get_meta_by_id(memory_id))
    before_count = engine.qdrant_store.count()

    with pytest.raises(ProjectMemoryPolicyError, match="credential-shaped"):
        engine.update_memory(
            memory_id,
            source="project/fplguru/knowledge",
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
        )

    assert engine._get_meta_by_id(memory_id) == before
    assert engine.qdrant_store.count() == before_count
    assert engine._get_meta_by_id(memory_id)["source"] == "legacy/source"
    assert "author" not in engine._get_meta_by_id(memory_id)


def test_project_transition_revalidates_locked_text_before_mutation(engine, monkeypatch):
    secret = "Production token is ghp_abcdefghijklmnopqrstuvwxyz123456"
    memory_id = engine.add_memories(["safe legacy fact"], ["legacy/source"])[0]
    original_acquire = engine._entity_locks.acquire_many

    @contextmanager
    def racing_acquire(keys):
        with original_acquire(keys):
            engine._get_meta_by_id(memory_id)["text"] = secret
            yield

    monkeypatch.setattr(engine._entity_locks, "acquire_many", racing_acquire)

    with pytest.raises(ProjectMemoryPolicyError, match="credential-shaped"):
        engine.update_memory(
            memory_id,
            source="project/fplguru/knowledge",
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
        )

    assert engine._get_meta_by_id(memory_id)["source"] == "legacy/source"


def test_source_only_move_out_of_project_restamps_current_editor(engine):
    memory_id = engine.add_memories(
        ["Shared fact moving to an authorized legacy source"],
        ["project/fplguru/knowledge"],
        trusted_authorship=TrustedAuthorship.system(
            contributors=["alice"],
            source_memory_ids=[17],
            origin_client="consolidator",
        ),
    )[0]

    result = engine.update_memory(
        memory_id,
        source="codex/fplguru",
        trusted_authorship=TrustedAuthorship.principal("bob", "codex"),
        apply_trusted_authorship=True,
    )

    assert result["updated_fields"] == ["source"]
    meta = engine._get_meta_by_id(memory_id)
    assert meta["source"] == "codex/fplguru"
    assert meta["author"] == "bob"
    assert meta["origin_client"] == "codex"
    assert "contributors" not in meta
    assert "source_memory_ids" not in meta


def test_source_only_move_rejects_malformed_reserved_project_target(engine):
    memory_id = engine.add_memories(["legacy fact"], ["legacy/source"])[0]

    with pytest.raises(ProjectMemoryPolicyError, match="project sources must be"):
        engine.update_memory(
            memory_id,
            source="project/fplguru/custom",
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            apply_trusted_authorship=True,
        )

    assert engine._get_meta_by_id(memory_id)["source"] == "legacy/source"


def test_text_update_rejects_preupgrade_malformed_reserved_project_source(engine):
    memory_id = engine.add_memories(["safe legacy fact"], ["legacy/source"])[0]
    engine._get_meta_by_id(memory_id)["source"] = "project/fplguru/custom"
    engine.qdrant_store.set_payload(memory_id, {"source": "project/fplguru/custom"})

    with pytest.raises(ProjectMemoryPolicyError, match="project sources must be"):
        engine.update_memory(
            memory_id,
            text="Production token is ghp_abcdefghijklmnopqrstuvwxyz123456",
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            apply_trusted_authorship=True,
        )

    assert engine._get_meta_by_id(memory_id)["text"] == "safe legacy fact"


def test_text_update_rejects_preupgrade_malformed_reserved_person_source(engine):
    memory_id = engine.add_memories(["safe legacy fact"], ["legacy/source"])[0]
    engine._get_meta_by_id(memory_id)["source"] = "person/Alice/fplguru/knowledge"
    engine.qdrant_store.set_payload(
        memory_id, {"source": "person/Alice/fplguru/knowledge"}
    )

    with pytest.raises(ProjectMemoryPolicyError, match="person sources must be"):
        engine.update_memory(
            memory_id,
            text="replacement",
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            apply_trusted_authorship=True,
        )

    assert engine._get_meta_by_id(memory_id)["text"] == "safe legacy fact"


def test_preupgrade_malformed_project_source_can_be_explicitly_migrated(engine):
    memory_id = engine.add_memories(["historical fact"], ["legacy/source"])[0]
    engine._get_meta_by_id(memory_id)["source"] = "project/decisions.md"
    engine.qdrant_store.set_payload(memory_id, {"source": "project/decisions.md"})

    engine.update_memory(
        memory_id,
        source="legacy/project-decisions",
        trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
    )

    meta = engine._get_meta_by_id(memory_id)
    assert meta["source"] == "legacy/project-decisions"
    assert meta["author"] == "alice"


def test_text_update_revalidates_locked_malformed_reserved_source(engine, monkeypatch):
    memory_id = engine.add_memories(["safe legacy fact"], ["legacy/source"])[0]
    original_acquire = engine._entity_locks.acquire_many

    @contextmanager
    def racing_acquire(keys):
        with original_acquire(keys):
            engine._get_meta_by_id(memory_id)["source"] = "project/fplguru/custom"
            yield

    monkeypatch.setattr(engine._entity_locks, "acquire_many", racing_acquire)

    with pytest.raises(ProjectMemoryPolicyError, match="project sources must be"):
        engine.update_memory(
            memory_id,
            text="Production token is ghp_abcdefghijklmnopqrstuvwxyz123456",
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            apply_trusted_authorship=True,
        )

    assert engine._get_meta_by_id(memory_id)["text"] == "safe legacy fact"


def test_text_update_revalidates_locked_malformed_person_source(engine, monkeypatch):
    memory_id = engine.add_memories(["safe legacy fact"], ["legacy/source"])[0]
    original_acquire = engine._entity_locks.acquire_many

    @contextmanager
    def racing_acquire(keys):
        with original_acquire(keys):
            engine._get_meta_by_id(memory_id)["source"] = "person/Alice/fplguru/knowledge"
            yield

    monkeypatch.setattr(engine._entity_locks, "acquire_many", racing_acquire)

    with pytest.raises(ProjectMemoryPolicyError, match="person sources must be"):
        engine.update_memory(
            memory_id,
            text="replacement",
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            apply_trusted_authorship=True,
        )

    assert engine._get_meta_by_id(memory_id)["text"] == "safe legacy fact"


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


def test_substantive_project_replacement_restamps_current_editor_and_ignores_spoofed_reserved_metadata(engine):
    alice = TrustedAuthorship.principal("alice", "codex")
    bob = TrustedAuthorship.principal("bob", "hook")
    memory_id = engine.add_memories(
        ["Alice's project decision"],
        ["project/fplguru/decisions"],
        trusted_authorship=alice,
    )[0]

    engine.update_memory(
        memory_id,
        text="Bob's replacement decision",
        metadata_patch={
            "author": "mallory",
            "contributors": ["mallory"],
            "origin_client": "spoofed",
            "source_memory_ids": [999],
            "kept": True,
        },
        trusted_authorship=bob,
        apply_trusted_authorship=True,
    )

    meta = engine._get_meta_by_id(memory_id)
    assert meta["text"] == "Bob's replacement decision"
    assert meta["author"] == "bob"
    assert meta["origin_client"] == "hook"
    assert "contributors" not in meta
    assert "source_memory_ids" not in meta
    assert meta["kept"] is True


def test_project_lifecycle_only_update_needs_no_authorship_and_preserves_author(engine):
    memory_id = engine.add_memories(
        ["Alice's project decision"],
        ["project/fplguru/decisions"],
        trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
    )[0]

    result = engine.update_memory(memory_id, pinned=True, archived=True)

    assert result["updated_fields"] == ["pinned", "archived"]
    meta = engine._get_meta_by_id(memory_id)
    assert meta["pinned"] is True
    assert meta["archived"] is True
    assert meta["author"] == "alice"
    assert meta["origin_client"] == "codex"


def test_project_secret_is_rejected_before_storage(engine):
    with pytest.raises(ProjectMemoryPolicyError, match="credential-shaped"):
        engine.add_memories(
            ["Production token is ghp_abcdefghijklmnopqrstuvwxyz123456"],
            ["project/fplguru/knowledge"],
            trusted_authorship=TrustedAuthorship.principal("alice"),
        )
    assert engine.metadata == []
