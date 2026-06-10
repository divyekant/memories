"""Durability batch: pruner guards, atomic save, backup rotation, consolidator safety.

These pin the fixes for the June 2026 incident class: the weekly pruner
hard-deleted archived version history, backup rotation evicted the backups it
had just created, and a truncate-write save could corrupt the canonical store.
"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from consolidator import consolidate_cluster, find_clusters, find_prune_candidates


# ---------------------------------------------------------------------------
# find_prune_candidates guards
# ---------------------------------------------------------------------------

def _mem(mid, *, days_old=400, category="detail", pinned=False, archived=False):
    from datetime import datetime, timedelta, timezone

    created = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    m = {"id": mid, "text": f"m{mid}", "category": category, "created_at": created}
    if pinned:
        m["pinned"] = True
    if archived:
        m["archived"] = True
    return m


def test_prune_excludes_pinned():
    mems = [_mem(1), _mem(2, pinned=True)]
    out = find_prune_candidates(mems, unretrieved_ids=[1, 2])
    assert [m["id"] for m in out] == [1]


def test_prune_excludes_archived_version_history():
    mems = [_mem(1), _mem(2, archived=True)]
    out = find_prune_candidates(mems, unretrieved_ids=[1, 2])
    assert [m["id"] for m in out] == [1]


def test_prune_still_finds_stale_unretrieved():
    mems = [_mem(1, days_old=400), _mem(2, days_old=1)]
    out = find_prune_candidates(mems, unretrieved_ids=[1, 2])
    assert [m["id"] for m in out] == [1]


# ---------------------------------------------------------------------------
# find_clusters: cosine threshold + protected exclusion
# ---------------------------------------------------------------------------

class _StubEngine:
    def __init__(self, metadata, hits):
        self.metadata = metadata
        self._hits = hits
        self.search_calls = 0

    def search(self, query, k=10, source_prefix=None, **kw):
        self.search_calls += 1
        return self._hits


def test_find_clusters_uses_cosine_scale_similarity():
    """RRF-scale scores (<=0.017) must not cluster; cosine 0.8 must."""
    meta = [{"id": i, "text": f"t{i}"} for i in range(1, 5)]
    cosine_hits = [{"id": i, "text": f"t{i}", "similarity": 0.8} for i in range(1, 5)]
    eng = _StubEngine(meta, cosine_hits)
    clusters = find_clusters(eng, min_cluster_size=3)
    assert clusters, "cosine-similarity hits at 0.8 should form a cluster"

    rrf_hits = [{"id": i, "text": f"t{i}", "rrf_score": 0.016} for i in range(1, 5)]
    eng2 = _StubEngine(meta, rrf_hits)
    assert find_clusters(eng2, min_cluster_size=3) == []


def test_find_clusters_skips_pinned_and_archived():
    meta = [
        {"id": 1, "text": "a"},
        {"id": 2, "text": "b", "pinned": True},
        {"id": 3, "text": "c", "archived": True},
        {"id": 4, "text": "d"},
    ]
    hits = [
        {"id": 1, "text": "a", "similarity": 0.9},
        {"id": 2, "text": "b", "similarity": 0.9, "pinned": True},
        {"id": 3, "text": "c", "similarity": 0.9, "archived": True},
        {"id": 4, "text": "d", "similarity": 0.9},
    ]
    eng = _StubEngine(meta, hits)
    clusters = find_clusters(eng, min_cluster_size=2)
    flat = [m["id"] for c in clusters for m in c]
    assert 2 not in flat and 3 not in flat


# ---------------------------------------------------------------------------
# consolidate_cluster: add-before-delete, strict parse, protected screen
# ---------------------------------------------------------------------------

class _RecordingEngine:
    def __init__(self, add_result=None):
        self.calls = []
        self._add_result = add_result if add_result is not None else [101]

    def add_memories(self, texts, sources, metadata_list=None, **kw):
        self.calls.append(("add", list(texts)))
        return self._add_result

    def delete_memories(self, ids):
        self.calls.append(("delete", list(ids)))
        return {"deleted_count": len(ids)}


def _provider(text):
    return SimpleNamespace(complete=lambda system, user: SimpleNamespace(text=text))


_CLUSTER = [
    {"id": 1, "text": "fact one", "source": "learning/x", "category": "detail"},
    {"id": 2, "text": "fact one again", "source": "learning/x", "category": "detail"},
    {"id": 3, "text": "fact one redux", "source": "learning/x", "category": "detail"},
]


def test_consolidate_adds_before_deleting():
    eng = _RecordingEngine()
    out = consolidate_cluster(_provider('["merged fact"]'), eng, _CLUSTER, dry_run=False)
    assert out.get("error") is None
    ops = [c[0] for c in eng.calls]
    assert ops == ["add", "delete"], f"expected add then delete, got {ops}"


def test_consolidate_rejects_unparseable_response_without_mutation():
    eng = _RecordingEngine()
    out = consolidate_cluster(_provider("Sure! Here are the merged memories..."), eng, _CLUSTER, dry_run=False)
    assert "error" in out
    assert eng.calls == [], "no mutation on unparseable LLM output"


def test_consolidate_keeps_originals_when_add_fails():
    eng = _RecordingEngine(add_result=[])
    out = consolidate_cluster(_provider('["merged"]'), eng, _CLUSTER, dry_run=False)
    assert "error" in out
    assert ("delete", [1, 2, 3]) not in eng.calls


def test_consolidate_skips_cluster_containing_pinned():
    eng = _RecordingEngine()
    cluster = [dict(_CLUSTER[0]), dict(_CLUSTER[1]), {**_CLUSTER[2], "pinned": True}]
    out = consolidate_cluster(_provider('["merged"]'), eng, cluster, dry_run=False)
    assert "skipped_reason" in out
    assert eng.calls == []


# ---------------------------------------------------------------------------
# Backup rotation by mtime with per-prefix retention
# ---------------------------------------------------------------------------

def _make_backup(backup_dir: Path, name: str, age_seconds: int) -> Path:
    p = backup_dir / name
    p.mkdir()
    (p / "metadata.json").write_text("{}")
    ts = time.time() - age_seconds
    os.utime(p, (ts, ts))
    return p


def test_backup_rotation_keeps_fresh_pre_delete_over_old_alphabetical_winners(tmp_path):
    """Name-descending sort evicted a just-created pre_delete backup while
    alphabetically-later stale backups survived."""
    from memory_engine import MemoryEngine

    eng = MemoryEngine.__new__(MemoryEngine)
    eng.backup_dir = tmp_path
    eng._max_backups = 3

    # 'weekly_*' sorts after 'pre_delete_*' alphabetically.
    old1 = _make_backup(tmp_path, "weekly_20260101_000000", age_seconds=9000)
    old2 = _make_backup(tmp_path, "weekly_20260102_000000", age_seconds=8000)
    old3 = _make_backup(tmp_path, "weekly_20260103_000000", age_seconds=7000)
    old4 = _make_backup(tmp_path, "weekly_20260104_000000", age_seconds=6000)
    fresh = _make_backup(tmp_path, "pre_delete_20260601_120000", age_seconds=1)

    eng._cleanup_old_backups(keep=3)

    assert fresh.exists(), "freshest backup must always survive rotation"
    assert not old1.exists(), "oldest backup beyond keep+quota should be evicted"


def test_backup_rotation_retains_two_most_recent_per_prefix(tmp_path):
    from memory_engine import MemoryEngine

    eng = MemoryEngine.__new__(MemoryEngine)
    eng.backup_dir = tmp_path
    eng._max_backups = 2

    auto = [_make_backup(tmp_path, f"auto_2026010{i}_000000", age_seconds=100 - i) for i in range(1, 6)]
    pre_old = _make_backup(tmp_path, "pre_delete_20250101_000000", age_seconds=50000)
    pre_older = _make_backup(tmp_path, "pre_delete_20240101_000000", age_seconds=90000)
    pre_oldest = _make_backup(tmp_path, "pre_delete_20230101_000000", age_seconds=99000)

    eng._cleanup_old_backups(keep=2)

    survivors = {p.name for p in tmp_path.glob("*_*")}
    assert "pre_delete_20250101_000000" in survivors
    assert "pre_delete_20240101_000000" in survivors
    assert "pre_delete_20230101_000000" not in survivors


# ---------------------------------------------------------------------------
# Atomic save / corrupt-store fallback
# ---------------------------------------------------------------------------

def _bare_engine(tmp_path):
    from memory_engine import MemoryEngine

    eng = MemoryEngine.__new__(MemoryEngine)
    eng.metadata_path = tmp_path / "metadata.json"
    eng.config_path = tmp_path / "config.json"
    eng.metadata = [{"id": 1, "text": "x", "source": "s"}]
    eng.config = {"model": "m"}
    return eng


def test_save_is_atomic_and_keeps_bak(tmp_path):
    eng = _bare_engine(tmp_path)
    eng.save()
    eng.metadata = [{"id": 2, "text": "y", "source": "s"}]
    eng.save()

    assert json.loads((tmp_path / "metadata.json").read_text())[0]["id"] == 2
    bak = tmp_path / "metadata.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text())[0]["id"] == 1
    assert not (tmp_path / "metadata.json.tmp").exists()


def test_save_survives_simulated_crash_before_replace(tmp_path, monkeypatch):
    """If the process dies mid-write, the canonical file is untouched."""
    eng = _bare_engine(tmp_path)
    eng.save()
    original = (tmp_path / "metadata.json").read_text()

    def boom(src, dst):
        raise OSError("simulated crash before atomic replace")

    monkeypatch.setattr(os, "replace", boom)
    eng.metadata = [{"id": 99, "text": "z", "source": "s"}]
    with pytest.raises(OSError):
        eng.save()
    assert (tmp_path / "metadata.json").read_text() == original
