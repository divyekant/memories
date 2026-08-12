"""Conflict-queue drain: newest wins, loser archived with a supersedes link.

Extraction flags contradictions with ``conflicts_with`` markers and — until
now — nothing ever resolved them (223 unresolved in production at audit time).
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import memory_engine as memory_engine_module
from memory_engine import MemoryEngine

DIM = 8


class HashEmbedder:
    def get_sentence_embedding_dimension(self):
        return DIM

    def encode(self, sentences, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]
        out = np.zeros((len(sentences), DIM), dtype=np.float32)
        for i, text in enumerate(sentences):
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            vec = rng.standard_normal(DIM).astype(np.float32)
            out[i] = vec / max(np.linalg.norm(vec), 1e-9)
        return out

    def close(self):
        pass


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    for var in (
        "EMBED_PROVIDER", "EMBED_MODEL", "EMBED_DIMENSION", "EMBED_COLLECTION",
        "QDRANT_COLLECTION", "MODEL_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        memory_engine_module.MemoryEngine, "_make_embedder", lambda self: HashEmbedder()
    )
    return MemoryEngine(data_dir=str(tmp_path / "data"))


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _add(engine, text, days_ago, source="learning/x", **extra):
    mid = engine.add_memories([text], [source])[0]
    meta = engine._get_meta_by_id(mid)
    meta["created_at"] = _iso(days_ago)
    meta.update(extra)
    return mid


def _conflict_pair(engine, *, old_days=30, new_days=1, old_extra=None, new_extra=None):
    old_id = _add(engine, "relay identity is dk-fplguru", old_days, **(old_extra or {}))
    new_id = _add(engine, "relay identity is dk-local-llm", new_days, **(new_extra or {}))
    engine._get_meta_by_id(new_id)["conflicts_with"] = old_id
    return old_id, new_id


def test_dry_run_reports_without_mutating(engine):
    old_id, new_id = _conflict_pair(engine)

    out = engine.resolve_conflicts(dry_run=True)

    assert out["resolved_count"] == 1
    assert out["resolved"][0] == {"kept": new_id, "archived": old_id, "flagger": new_id}
    assert not engine._get_meta_by_id(old_id).get("archived")
    assert engine._get_meta_by_id(new_id).get("conflicts_with") == old_id


def test_newest_wins_archives_older_side(engine):
    old_id, new_id = _conflict_pair(engine)

    out = engine.resolve_conflicts(dry_run=False)

    assert out["resolved_count"] == 1
    old_meta = engine._get_meta_by_id(old_id)
    new_meta = engine._get_meta_by_id(new_id)
    assert old_meta["archived"] is True
    assert old_meta["superseded_by"] == new_id
    assert "conflicts_with" not in new_meta
    assert new_meta["conflict_resolution"]["outcome"] == "won"
    # second run is a no-op: the queue actually drains
    again = engine.resolve_conflicts(dry_run=False)
    assert again["resolved_count"] == 0


@pytest.mark.parametrize(
    "flagger_source, other_source",
    [
        ("project/private", "project/shared"),
        ("person/alice/shared-demo/knowledge", "person/bob/shared-demo/knowledge"),
    ],
)
def test_cross_source_conflict_requires_review_without_archiving_or_linking(
    engine, flagger_source, other_source
):
    old_id = _add(
        engine,
        "relay identity is dk-fplguru",
        30,
        source=other_source,
    )
    new_id = _add(
        engine,
        "relay identity is dk-local-llm",
        1,
        source=flagger_source,
    )
    engine._get_meta_by_id(new_id)["conflicts_with"] = old_id

    out = engine.resolve_conflicts(dry_run=False)

    assert out["resolved_count"] == 0
    assert out["needs_review"] == [
        {"id": new_id, "conflicts_with": old_id, "reason": "cross_source"}
    ]
    assert not engine._get_meta_by_id(old_id).get("archived")
    flagger = engine._get_meta_by_id(new_id)
    assert not flagger.get("archived")
    assert flagger["conflicts_with"] == old_id
    assert flagger["conflict_review"] == "cross_source"
    assert not any(
        link.get("to_id") == old_id and link.get("type") == "supersedes"
        for link in flagger.get("links", [])
    )


def test_older_flagger_loses_to_newer_existing_memory(engine):
    """If the flagging memory is somehow OLDER, the existing newer memory wins."""
    old_id, new_id = _conflict_pair(engine, old_days=1, new_days=30)

    out = engine.resolve_conflicts(dry_run=False)

    assert out["resolved"][0]["kept"] == old_id
    flagger = engine._get_meta_by_id(new_id)
    assert flagger["archived"] is True
    assert flagger["superseded_by"] == old_id


def test_pinned_loser_goes_to_review_not_archive(engine):
    old_id, new_id = _conflict_pair(engine, old_extra={"pinned": True})

    out = engine.resolve_conflicts(dry_run=False)

    assert out["resolved_count"] == 0
    assert out["needs_review"][0]["reason"] == "pinned_loser"
    assert not engine._get_meta_by_id(old_id).get("archived")
    assert engine._get_meta_by_id(new_id)["conflict_review"] == "pinned_loser"
    assert engine._get_meta_by_id(new_id)["conflicts_with"] == old_id


def test_undated_pair_goes_to_review(engine):
    old_id, new_id = _conflict_pair(engine)
    engine._get_meta_by_id(old_id).pop("created_at", None)
    engine._get_meta_by_id(old_id).pop("timestamp", None)

    out = engine.resolve_conflicts(dry_run=False)

    assert out["needs_review"][0]["reason"] == "undated"
    assert not engine._get_meta_by_id(old_id).get("archived")


def test_orphaned_marker_is_cleared(engine):
    _, new_id = _conflict_pair(engine)
    engine._get_meta_by_id(new_id)["conflicts_with"] = 99999  # other side gone

    out = engine.resolve_conflicts(dry_run=False)

    assert out["orphaned_count"] == 1
    meta = engine._get_meta_by_id(new_id)
    assert "conflicts_with" not in meta
    assert meta["conflict_resolution"]["outcome"] == "orphaned_missing"


def test_already_archived_other_side_clears_marker(engine):
    old_id, new_id = _conflict_pair(engine)
    engine._get_meta_by_id(old_id)["archived"] = True

    out = engine.resolve_conflicts(dry_run=False)

    assert out["orphaned_count"] == 1
    assert "conflicts_with" not in engine._get_meta_by_id(new_id)


def test_max_resolutions_caps_a_run(engine):
    for _ in range(3):
        _conflict_pair(engine)

    out = engine.resolve_conflicts(dry_run=False, max_resolutions=2)

    assert out["resolved_count"] == 2
    remaining = engine.resolve_conflicts(dry_run=True)
    assert remaining["resolved_count"] == 1


def test_unknown_policy_rejected(engine):
    with pytest.raises(ValueError):
        engine.resolve_conflicts(policy="oldest_wins")
