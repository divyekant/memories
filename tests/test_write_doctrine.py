"""Write doctrine: every write is an explicit ADD / SUPERSEDE / SKIP decision.

Pins the fix for the staleness root cause: a corrected fact ("weight is now
79kg") used to be silently eaten by dedup-skip while the stale fact kept
ranking. Under the doctrine the correction supersedes the original, which is
archived (never deleted) with a supersedes link.
"""

import numpy as np
import pytest

import memory_engine as memory_engine_module
from memory_engine import MemoryEngine
from project_memory import ProjectMemoryPolicyError, TrustedAuthorship

DIM = 8

_BASE = np.zeros(DIM, dtype=np.float32); _BASE[0] = 1.0
_ORTH = np.zeros(DIM, dtype=np.float32); _ORTH[1] = 1.0
_OTHER = np.zeros(DIM, dtype=np.float32); _OTHER[2] = 1.0


def _blend(a, b, amount):
    v = a + amount * b
    return (v / np.linalg.norm(v)).astype(np.float32)


T78 = "User weight is 78kg"
T79 = "User weight is 79kg"          # cos ~0.944 vs T78 -> supersede band
T78_NEAR = "User weight is 78 kg"    # cos ~0.995 vs T78 -> identical band
UNRELATED = "Deploys use a blue-green strategy"

_VECTORS = {
    T78: _BASE,                          # vs T79: cos ~0.912 (supersede band)
    T79: _blend(_BASE, _ORTH, 0.45),
    T78_NEAR: _blend(_BASE, _ORTH, 0.10),  # vs T78: cos ~0.995 (identical); vs T79: ~0.948 (supersede)
    UNRELATED: _OTHER,
}


class MappedEmbedder:
    def get_sentence_embedding_dimension(self):
        return DIM

    def encode(self, sentences, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]
        out = np.zeros((len(sentences), DIM), dtype=np.float32)
        for i, text in enumerate(sentences):
            if text in _VECTORS:
                out[i] = _VECTORS[text]
            else:
                seed = abs(hash(text)) % (2**32)
                rng = np.random.default_rng(seed)
                vec = rng.standard_normal(DIM).astype(np.float32)
                out[i] = vec / max(np.linalg.norm(vec), 1e-9)
        return out

    def close(self):
        pass


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    for var in (
        "EMBED_PROVIDER", "EMBED_MODEL", "EMBED_BASE_URL", "EMBED_API_KEY",
        "EMBED_DIMENSION", "EMBED_COLLECTION", "EMBED_QUERY_PREFIX",
        "EMBED_DOC_PREFIX", "EMBED_ALLOW_SPACE_REBIND", "QDRANT_COLLECTION",
        "MODEL_NAME", "OPENAI_API_KEY", "DOCTRINE_IDENTICAL_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        memory_engine_module.MemoryEngine, "_make_embedder", lambda self: MappedEmbedder()
    )
    return MemoryEngine(data_dir=str(tmp_path / "data"))


def _texts(results):
    return [r["text"] for r in results]


class TestSupersedeArchivesNotDeletes:
    def test_supersede_archives_old_and_links_new(self, engine):
        old_id = engine.add_memories([T78], ["learning/health"])[0]

        out = engine.supersede(old_id, T79)

        new_id = out["new_id"]
        assert out["archived_old"] is True
        old_meta = engine._get_meta_by_id(old_id)
        assert old_meta["archived"] is True
        assert old_meta["superseded_by"] == new_id
        new_meta = engine._get_meta_by_id(new_id)
        assert new_meta["supersedes"] == old_id
        assert new_meta["previous_text"] == T78
        # source inherited from the original when not given
        assert new_meta["source"] == "learning/health"

        default = engine.search(T79, k=5)
        assert T78 not in _texts(default), "archived original must not surface in default search"
        assert T79 in _texts(default)

        with_archived = engine.search(T78, k=5, include_archived=True)
        assert T78 in _texts(with_archived), "history must remain reachable via include_archived"

    def test_supersede_keeps_original_when_add_fails(self, engine, monkeypatch):
        old_id = engine.add_memories([T78], ["learning/health"])[0]
        monkeypatch.setattr(engine, "add_memories", lambda *a, **k: [])

        with pytest.raises(RuntimeError):
            engine.supersede(old_id, T79)

        old_meta = engine._get_meta_by_id(old_id)
        assert not old_meta.get("archived"), "original must be untouched when the add fails"


class TestAddWithDoctrine:
    def test_correction_supersedes_original(self, engine):
        first = engine.add_with_doctrine(T78, "learning/health")
        assert first["action"] == "added"

        second = engine.add_with_doctrine(T79, "learning/health")

        assert second["action"] == "superseded"
        assert second["superseded"] == first["id"]
        assert 0.90 <= second["similarity"] < 0.97
        top = engine.search("User weight is 79kg", k=3)
        assert _texts(top)[0] == T79
        assert T78 not in _texts(top)
        assert engine._get_meta_by_id(first["id"])["archived"] is True

    def test_identical_text_is_skipped_with_blocker(self, engine):
        first = engine.add_with_doctrine(T78, "learning/health")

        out = engine.add_with_doctrine(T78_NEAR, "learning/health")

        assert out["action"] == "skipped"
        assert out["reason"] == "identical"
        assert out["blocked_by"] == first["id"]
        assert "supersede" in out["hint"]

    def test_skip_mode_keeps_legacy_behavior_but_surfaces_blocker(self, engine):
        first = engine.add_with_doctrine(T78, "learning/health")

        out = engine.add_with_doctrine(T79, "learning/health", on_duplicate="skip")

        assert out["action"] == "skipped"
        assert out["reason"] == "duplicate"
        assert out["blocked_by"] == first["id"]
        assert not engine._get_meta_by_id(first["id"]).get("archived")

    def test_pinned_blocker_is_never_superseded(self, engine):
        first = engine.add_with_doctrine(T78, "learning/health")
        engine.update_memory(first["id"], pinned=True)

        out = engine.add_with_doctrine(T79, "learning/health")

        assert out["action"] == "skipped"
        assert out["reason"] == "pinned_blocker"
        assert not engine._get_meta_by_id(first["id"]).get("archived")

    def test_add_mode_bypasses_collision_check(self, engine):
        engine.add_with_doctrine(T78, "learning/health")
        out = engine.add_with_doctrine(T79, "learning/health", on_duplicate="add")
        assert out["action"] == "added"
        both = engine.search(T79, k=5)
        assert T78 in _texts(both) and T79 in _texts(both)

    def test_unrelated_text_adds_normally(self, engine):
        engine.add_with_doctrine(T78, "learning/health")
        out = engine.add_with_doctrine(UNRELATED, "learning/deploys")
        assert out["action"] == "added"

    def test_trusted_collision_is_scoped_to_exact_destination_source(self, engine):
        alice = TrustedAuthorship.principal("alice")
        bob = TrustedAuthorship.principal("bob")
        first = engine.add_with_doctrine(
            T78,
            "project/acme/decisions",
            trusted_authorship=alice,
        )

        out = engine.add_with_doctrine(
            T79,
            "project/other/decisions",
            trusted_authorship=bob,
        )

        assert out["action"] == "added"
        assert out.get("blocked_by") is None
        assert not engine._get_meta_by_id(first["id"]).get("archived")

    def test_trusted_supersede_cannot_cross_destination_source(self, engine):
        old_id = engine.add_memories(
            [T78],
            ["project/acme/decisions"],
            trusted_authorship=TrustedAuthorship.principal("alice"),
        )[0]

        with pytest.raises(ProjectMemoryPolicyError, match="source"):
            engine.supersede(
                old_id,
                T79,
                source="project/other/decisions",
                trusted_authorship=TrustedAuthorship.principal("bob"),
            )

        assert not engine._get_meta_by_id(old_id).get("archived")

    def test_trusted_supersede_can_move_between_authorized_legacy_sources(self, engine):
        trusted = TrustedAuthorship.principal("alice")
        old_id = engine.add_memories(
            [T78],
            ["codex/acme"],
            trusted_authorship=trusted,
        )[0]

        result = engine.supersede(
            old_id,
            T79,
            source="claude-code/acme",
            trusted_authorship=trusted,
        )

        assert engine._get_meta_by_id(old_id)["archived"] is True
        replacement = engine._get_meta_by_id(result["new_id"])
        assert replacement["source"] == "claude-code/acme"
        assert replacement["author"] == "alice"

    def test_invalid_mode_rejected(self, engine):
        with pytest.raises(ValueError):
            engine.add_with_doctrine(T78, "learning/health", on_duplicate="merge")

    def test_superseded_old_does_not_block_future_writes(self, engine):
        """The archived original must not act as a dedup blocker later."""
        engine.add_with_doctrine(T78, "learning/health")
        second = engine.add_with_doctrine(T79, "learning/health")
        assert second["action"] == "superseded"

        third = engine.add_with_doctrine(T78_NEAR, "learning/health")
        # T78_NEAR collides with archived T78 (0.995) but archived memories are
        # excluded from search; against live T79 it sits in the supersede band.
        assert third["action"] == "superseded"
        assert third["superseded"] == second["id"]
