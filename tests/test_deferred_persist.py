"""Grouped saves and BM25 rebuilds (MEMORIES_PERSIST_DELAY_SEC > 0)."""

import json
import time

import pytest

from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORIES_PERSIST_DELAY_SEC", "0.2")
    eng = MemoryEngine(data_dir=str(tmp_path))
    yield eng
    eng.close()


def _wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _saved_texts(engine):
    if not engine.metadata_path.exists():
        return []
    return [m["text"] for m in json.loads(engine.metadata_path.read_text())]


def test_many_writes_make_few_saves(engine, monkeypatch):
    calls = []
    real = engine._save_now
    monkeypatch.setattr(engine, "_save_now", lambda: (calls.append(1), real())[1])
    builds = []
    real_bm25 = engine._rebuild_bm25_now
    monkeypatch.setattr(engine, "_rebuild_bm25_now", lambda: (builds.append(1), real_bm25())[1])

    for i in range(10):
        engine.add_memories(texts=[f"fact number {i} about grouped saves"], sources=["t.md"])

    assert _wait_for(lambda: len(_saved_texts(engine)) == 10)
    assert 1 <= len(calls) < 10
    assert 1 <= len(builds) < 10
    assert any(h[0] for h in engine._bm25_hits(["grouped"]))


def test_close_writes_pending_work(engine):
    engine.add_memories(texts=["written at shutdown"], sources=["t.md"])
    engine.close()
    assert _saved_texts(engine) == ["written at shutdown"]
    assert engine._bm25_snapshot is not None
    assert engine._flusher is None or not engine._flusher.is_alive()


def test_backup_includes_pending_writes(engine):
    engine.add_memories(texts=["first"], sources=["t.md"])
    engine.flush()
    engine.add_memories(texts=["second"], sources=["t.md"])
    backup = engine._backup(prefix="test")
    texts = [m["text"] for m in json.loads((backup / "metadata.json").read_text())]
    assert texts == ["first", "second"]


def test_search_finds_new_memory_before_bm25_rebuild(engine):
    engine.add_memories(texts=["the deploy uses rsync to the droplet"], sources=["t.md"])
    results = engine.hybrid_search("rsync droplet", k=3)
    assert results and results[0]["text"].startswith("the deploy")


def test_explain_after_delete_before_rebuild(engine):
    ids = engine.add_memories(texts=["alpha keyword one", "beta keyword two"], sources=["t.md", "t.md"])
    engine.flush()
    engine.delete_memory(ids[0])
    out = engine.hybrid_search_explain("keyword", k=5)
    assert [r["id"] for r in out["results"]] == [ids[1]]


def test_flusher_exits_when_idle(engine, monkeypatch):
    monkeypatch.setattr(MemoryEngine, "_FLUSHER_IDLE_SEC", 0.1)
    engine.add_memories(texts=["idle"], sources=["t.md"])
    assert _wait_for(lambda: engine._flusher is None)
    engine.add_memories(texts=["wakes again"], sources=["t.md"])
    assert _wait_for(lambda: len(_saved_texts(engine)) == 2)
