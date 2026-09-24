"""Tests for the reranker shadow: bounded, read-only, never changes responses."""

import importlib
import json
import os
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

import rerank_shadow


class FakeScorer:
    def score(self, query, docs):
        return np.array([float(len(d)) for d in docs])  # longer text ranks first


def _shadow(tmp_path, **kw):
    kw.setdefault("sample_rate", 1.0)
    return rerank_shadow.RerankShadow(
        scorer_factory=FakeScorer, log_dir=str(tmp_path), model="m/x", top_n=3, **kw
    )


def _records(tmp_path):
    files = list(tmp_path.glob("rerank-shadow-*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text().splitlines()]


def test_disabled_by_default():
    with patch.dict(os.environ, {}, clear=True):
        assert rerank_shadow.from_env() is None


def test_enabled_from_env(tmp_path):
    env = {"RERANK_SHADOW_ENABLED": "true", "SHADOW_LOG_DIR": str(tmp_path), "RERANK_SHADOW_SAMPLE_RATE": "0.5"}
    with patch.dict(os.environ, env, clear=True):
        shadow = rerank_shadow.from_env()
    assert shadow is not None and shadow.sample_rate == 0.5 and shadow.top_n == 20
    shadow.close()


def test_observe_logs_reranked_order_without_touching_primary(tmp_path):
    shadow = _shadow(tmp_path)
    primary = [{"id": 1, "text": "a"}, {"id": 2, "text": "ccc"}]
    candidates = [{"id": 1, "text": "a"}, {"id": 2, "text": "ccc"}, {"id": 3, "text": "bb"}, {"id": 4, "text": "dddd"}]
    assert shadow.observe("/search", "why redis", 2, [r["id"] for r in primary], lambda: candidates)
    shadow.close()
    assert primary == [{"id": 1, "text": "a"}, {"id": 2, "text": "ccc"}]
    (rec,) = _records(tmp_path)
    assert rec["candidate_ids"] == [1, 2, 3]  # top_n=3
    assert rec["reranked_ids"] == [2, 3, 1]
    assert rec["primary_ids"] == [1, 2] and rec["k"] == 2
    assert rec["scores"] == [1.0, 3.0, 2.0]
    assert rec["text_chars"] == [1, 3, 2]
    assert rec["query_sha256"] == rerank_shadow.query_hash("why redis")
    assert "why redis" not in json.dumps(rec)
    assert rec["error"] is None and rec["retrieve_ms"] >= 0 and rec["rerank_ms"] >= 0


def test_saturation_drops_instead_of_waiting(tmp_path):
    shadow = _shadow(tmp_path)
    gate = threading.Event()

    def slow_fetch():
        gate.wait(5)
        return [{"id": 1, "text": "x"}]

    assert shadow.observe("/search", "q1", 5, [1], slow_fetch)
    assert not shadow.observe("/search", "q2", 5, [1], slow_fetch)
    gate.set()
    shadow.close()
    assert shadow.dropped == 1
    (rec,) = _records(tmp_path)
    assert rec["dropped_total"] == 1


def test_sample_rate_zero_skips(tmp_path):
    shadow = _shadow(tmp_path, sample_rate=0.0)
    fetch = MagicMock()
    assert not shadow.observe("/search", "q", 5, [], fetch)
    shadow.close()
    fetch.assert_not_called()
    assert _records(tmp_path) == []


def test_errors_are_recorded_not_raised(tmp_path):
    shadow = _shadow(tmp_path)

    def broken():
        raise RuntimeError("secret detail")

    assert shadow.observe("/search", "q", 5, [], broken)
    shadow.close()
    (rec,) = _records(tmp_path)
    assert rec["error"] == "RuntimeError" and shadow.errors == 1
    assert "secret detail" not in json.dumps(rec)


def test_load_failure_disables_shadow_before_fetch(tmp_path):
    def broken_factory():
        raise OSError("no network")

    shadow = rerank_shadow.RerankShadow(scorer_factory=broken_factory, log_dir=str(tmp_path), model="m/x", sample_rate=1.0)
    fetch = MagicMock(return_value=[{"id": 1, "text": "x"}])
    assert shadow.observe("/search", "q", 5, [], fetch)
    shadow.close()
    fetch.assert_not_called()
    assert shadow.disabled and not shadow.observe("/search", "q", 5, [], fetch)


def test_hybrid_search_without_reinforce_writes_nothing(tmp_path):
    from memory_engine import MemoryEngine

    engine = MemoryEngine(data_dir=str(tmp_path))
    engine.add_memories(["Redis was removed in September", "Use pnpm, not npm"], ["t/a", "t/b"])
    before = {m["id"]: m.get("last_reinforced_at") for m in engine.metadata}
    assert engine.hybrid_search("why remove redis", k=2, reinforce=False)
    assert {m["id"]: m.get("last_reinforced_at") for m in engine.metadata} == before
    engine.hybrid_search("why remove redis", k=2)
    assert {m["id"]: m.get("last_reinforced_at") for m in engine.metadata} != before


@pytest.fixture
def client():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        engine = MagicMock()
        engine.hybrid_search.return_value = [{"id": 1, "text": "one", "source": "t/p", "rrf_score": 0.02}]
        engine.hybrid_search_explain.return_value = {
            "results": [{"id": 1, "text": "one", "source": "t/p"}, {"id": 2, "text": "two", "source": "t/p"}],
            "explain": {},
        }
        app_module.memory = engine
        yield app_module, TestClient(app_module.app), engine


def test_search_route_feeds_shadow_read_only(client):
    app_module, tc, engine = client
    shadow = MagicMock(top_n=20)
    app_module.rerank_shadow_instance = shadow
    resp = tc.post("/search", json={"query": "q", "k": 1, "hybrid": True}, headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200 and [r["id"] for r in resp.json()["results"]] == [1]
    route, query, k, primary_ids, fetch = shadow.observe.call_args.args
    assert (route, query, k, primary_ids) == ("/search", "q", 1, [1])
    engine.hybrid_search.return_value = [{"id": 1, "text": "one", "source": "t/p"}, {"id": 2, "text": "two", "source": "t/p"}]
    assert [r["id"] for r in fetch()] == [1, 2]
    kwargs = engine.hybrid_search.call_args.kwargs
    assert kwargs["k"] == 20 and kwargs["query"] == "q" and kwargs["reinforce"] is False


def test_search_route_survives_shadow_failure(client):
    app_module, tc, _ = client
    shadow = MagicMock(top_n=20)
    shadow.observe.side_effect = RuntimeError("boom")
    app_module.rerank_shadow_instance = shadow
    resp = tc.post("/search", json={"query": "q", "k": 1, "hybrid": True}, headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200 and resp.json()["count"] == 1
