"""Focused coverage for Jev lifecycle hooks at production boundaries."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from auth_context import AuthContext
from llm_provider import CompletionResult
from project_promotion import PromotionProposal, ReviewDecision


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("test", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )


class _Hooks:
    def __init__(self):
        self.observed = []
        self.started = []
        self.finished = []

    def observe(self, flow, state, baseline, *, source="", **kwargs):
        self.observed.append((flow, state, baseline, source, kwargs))

    def start(self, flow, state, *, source="", primary_model=None, **kwargs):
        ticket = SimpleNamespace(flow=flow, state=state, source=source, primary_model=primary_model)
        self.started.append(ticket)
        return ticket

    def finish(self, ticket, baseline, **kwargs):
        self.finished.append((ticket, baseline, kwargs))


@pytest.fixture
def hooks(monkeypatch):
    import shadow_runner

    recorder = _Hooks()
    monkeypatch.setattr(shadow_runner, "observe_jev", recorder.observe, raising=False)
    monkeypatch.setattr(shadow_runner, "start_jev", recorder.start, raising=False)
    monkeypatch.setattr(shadow_runner, "finish_jev", recorder.finish, raising=False)
    return recorder


@pytest.fixture
def app_module(monkeypatch):
    import app

    monkeypatch.setattr(app, "_get_auth", lambda request: AuthContext.unrestricted())
    monkeypatch.setattr(app, "_log_usage_event", lambda *args, **kwargs: None)
    return app


def _retrieval_results(count=25):
    return [
        {
            "id": index,
            "text": f"fact {index}",
            "source": "allowed/source",
            "category": "detail",
            "created_at": "2026-01-01T00:00:00+00:00",
            "metadata": {"event_at": "2026-01-01T00:00:00+00:00", "secret": "hide"},
            "similarity": 1.0 - index / 100,
            "secret": "hide",
        }
        for index in range(count)
    ]


def test_retrieval_hooks_receive_filtered_bounded_results_without_reordering(
    app_module, hooks, monkeypatch
):
    raw = _retrieval_results() + [{"id": 999, "text": "private", "source": "denied/source"}]
    engine = MagicMock()
    engine.search.return_value = raw
    monkeypatch.setattr(app_module, "memory", engine)
    monkeypatch.setattr(
        app_module,
        "_get_auth",
        lambda request: AuthContext(role="read-only", prefixes=["allowed/"], key_type="managed"),
    )

    body = app_module.SearchRequest(query="facts", hybrid=False, source="client")
    response = asyncio.run(app_module.search(body, _request("/search")))

    assert [item["id"] for item in response["results"]] == list(range(25))
    assert len(hooks.observed) == 1
    flow, state, baseline, source, _ = hooks.observed[0]
    assert flow == "retrieval"
    assert source == "client"
    assert [item["id"] for item in state["candidates"]] == list(range(20))
    assert state["total_candidates"] == 25
    assert baseline == {"ranking": list(range(20))}
    assert all("secret" not in item for item in state["candidates"])
    assert state["candidates"][0]["metadata"] == {
        "event_at": "2026-01-01T00:00:00+00:00"
    }


@pytest.mark.parametrize(
    "route", ["/search", "/search/explain", "/search/evidence", "/search/batch"]
)
def test_each_retrieval_surface_emits_a_hook_after_auth_filter(app_module, hooks, monkeypatch, route):
    allowed = [{"id": 2, "text": "visible", "source": "allowed/source", "similarity": 0.9}]
    denied = [{"id": 3, "text": "hidden", "source": "denied/source", "similarity": 0.8}]
    engine = MagicMock()
    engine.search.return_value = allowed + denied
    engine.hybrid_search.return_value = allowed + denied
    engine.hybrid_search_explain.return_value = {"results": allowed + denied, "explain": {}}
    monkeypatch.setattr(app_module, "memory", engine)
    monkeypatch.setattr(
        app_module,
        "_get_auth",
        lambda request: AuthContext(role="read-only", prefixes=["allowed/"], key_type="managed"),
    )
    if route == "/search/explain":
        monkeypatch.setattr(app_module, "_require_admin", lambda auth: None)
    body = app_module.SearchRequest(query="facts", source="client", auto_intent=False)

    if route == "/search":
        response = asyncio.run(app_module.search(body, _request(route)))
        result_ids = [item["id"] for item in response["results"]]
    elif route == "/search/explain":
        response = asyncio.run(app_module.search_explain(body, _request(route)))
        result_ids = [item["id"] for item in response["results"]]
    elif route == "/search/evidence":
        response = asyncio.run(app_module.search_evidence(body, _request(route)))
        result_ids = [item["id"] for item in response["results"]]
    else:
        response = asyncio.run(
            app_module.search_batch(
                app_module.SearchBatchRequest(queries=[body]), _request(route)
            )
        )
        result_ids = [item["id"] for item in response["results"][0]["results"]]

    assert result_ids == [2]
    assert hooks.observed[-1][0] == "retrieval"
    assert [item["id"] for item in hooks.observed[-1][1]["candidates"]] == [2]


def test_retrieval_hook_failure_does_not_break_api(app_module, monkeypatch):
    import shadow_runner

    engine = MagicMock()
    engine.search.return_value = [{"id": 1, "text": "visible", "source": "source"}]
    monkeypatch.setattr(app_module, "memory", engine)

    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(shadow_runner, "observe_jev", fail, raising=False)
    body = app_module.SearchRequest(query="facts", hybrid=False, auto_intent=False)

    response = asyncio.run(app_module.search(body, _request("/search")))

    assert response["results"][0]["id"] == 1


def _memory(memory_id, text, *, source="codex/source", **extra):
    now = datetime.now(timezone.utc).isoformat()
    return {"id": memory_id, "text": text, "source": source, "created_at": now, **extra}


def test_consolidation_hooks_bracket_provider_and_skip_protected_clusters(hooks):
    from consolidator import consolidate_cluster

    provider = MagicMock(model="primary-model")
    provider.complete.return_value = CompletionResult(text=json.dumps(["merged fact"]))
    engine = MagicMock()
    cluster = [_memory(1, "a"), _memory(2, "b")]

    result = consolidate_cluster(provider, engine, cluster, dry_run=True)

    assert result["new_texts"] == ["merged fact"]
    assert [item.flow for item in hooks.started] == ["consolidation"]
    assert hooks.started[0].primary_model == "primary-model"
    assert hooks.finished[0][1]["status"] == "ok"
    assert hooks.observed[-1][0] == "consolidation"
    assert hooks.observed[-1][1]["phase"] == "after_merge"

    hooks.started.clear()
    hooks.finished.clear()
    hooks.observed.clear()
    protected = [_memory(1, "a", pinned=True), _memory(2, "b")]
    consolidate_cluster(provider, engine, protected, dry_run=True)
    assert hooks.started == []
    assert hooks.finished == []
    assert hooks.observed == []
    provider.complete.assert_called_once()


def test_pruning_hook_sees_only_eligible_candidates_and_does_not_delete(hooks):
    from consolidator import find_prune_candidates

    old = datetime.now(timezone.utc) - timedelta(days=61)
    candidates = [
        {
            "id": index,
            "text": f"old {index}",
            "source": "codex/source",
            "category": "detail",
            "created_at": old.isoformat(),
        }
        for index in range(25)
    ]
    protected = _memory(100, "pinned", created_at=old.isoformat(), pinned=True)
    recent = _memory(101, "recent", created_at=datetime.now(timezone.utc).isoformat())
    memories = candidates + [protected, recent]

    result = find_prune_candidates(memories, list(range(25)) + [100, 101])

    assert [item["id"] for item in result] == list(range(25))
    assert len(hooks.observed) == 1
    flow, state, baseline, source, _ = hooks.observed[0]
    assert flow == "pruning"
    assert source == "maintenance/pruning"
    assert [item["id"] for item in state["candidates"]] == list(range(20))
    assert state["total_candidates"] == 25
    assert state["evidence"] == []
    assert baseline == {"candidate_ids": list(range(20)), "selector": "age_and_nonuse"}
    assert [item["id"] for item in memories] == list(range(25)) + [100, 101]


def test_promotion_hook_finishes_with_guarded_defer_when_provider_fails(hooks):
    from promotion_service import PromotionReviewer

    provider = MagicMock(provider_name="provider", model="review-model")
    provider.complete.side_effect = RuntimeError("provider down")
    reviewer = PromotionReviewer(provider=provider)
    candidate = {
        "id": 1,
        "text": "The project uses Postgres.",
        "source": "person/alice/project/knowledge",
        "author": "alice",
    }
    proposal = PromotionProposal(
        project_relevance=0.9,
        visibility="project",
        assertion_status="confirmed",
        project_kind="knowledge",
        confidence=0.9,
        reason="confirmed",
        classifier_version="classifier-v1",
    )

    review = reviewer.review(candidate, proposal, "The project uses Postgres.", [])

    assert review.decision is ReviewDecision.DEFER
    assert len(hooks.started) == 1
    assert hooks.started[0].primary_model == "review-model"
    assert hooks.started[0].source == candidate["source"]
    assert len(hooks.finished) == 1
    baseline = hooks.finished[0][1]
    assert baseline["status"] == "error"
    assert baseline["decision"] == ReviewDecision.DEFER.value
    assert baseline["reason"] == "review provider unavailable"
