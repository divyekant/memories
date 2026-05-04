"""Tests for agent-facing evidence packets."""

from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from evidence_packet import build_evidence_packet


def test_evidence_packet_selects_latest_answer_and_keeps_older_evidence() -> None:
    results = [
        {
            "id": 1,
            "text": "The deploy target is hvt-v1.",
            "source": "codex/project",
            "document_at": "2026-02-01T00:00:00+00:00",
            "rrf_score": 0.012,
        },
        {
            "id": 2,
            "text": "The deploy target is hvt-v2.",
            "source": "codex/project",
            "document_at": "2026-04-01T00:00:00+00:00",
            "rrf_score": 0.011,
            "is_latest": True,
        },
    ]

    packet = build_evidence_packet("what is the latest deploy target?", results)

    assert packet["current_answer"]["id"] == 2
    assert packet["current_answer"]["date"] == "2026-04-01T00:00:00+00:00"
    assert [item["id"] for item in packet["older_evidence"]] == [1]
    assert [item["id"] for item in packet["older_conflicting_memories"]] == [1]
    assert [item["id"] for item in packet["source_date_trail"]] == [2, 1]
    assert packet["confidence"]["level"] == "medium"
    assert any("older evidence" in reason for reason in packet["confidence"]["reasons"])
    assert packet["follow_up_queries"]


def test_evidence_packet_recency_beats_stale_latest_flag() -> None:
    results = [
        {
            "id": 1,
            "text": "The deploy target is hvt-v1.",
            "source": "codex/project",
            "document_at": "2026-02-01T00:00:00+00:00",
            "rrf_score": 0.02,
            "is_latest": True,
        },
        {
            "id": 2,
            "text": "The deploy target is hvt-v2.",
            "source": "codex/project",
            "document_at": "2026-04-01T00:00:00+00:00",
            "rrf_score": 0.01,
        },
    ]

    packet = build_evidence_packet("latest deploy target", results)

    assert packet["current_answer"]["id"] == 2
    assert packet["older_evidence"][0]["id"] == 1


def test_evidence_packet_separates_dated_evidence_when_current_is_undated() -> None:
    results = [
        {
            "id": 1,
            "text": "The deploy target is hvt-v2.",
            "source": "codex/project",
            "rrf_score": 0.02,
        },
        {
            "id": 2,
            "text": "The deploy target used to be hvt-v1.",
            "source": "codex/project",
            "document_at": "2026-02-01T00:00:00+00:00",
            "rrf_score": 0.01,
        },
    ]

    packet = build_evidence_packet("deploy target", results)

    assert packet["current_answer"]["id"] == 1
    assert packet["supporting_memories"] == []
    assert packet["older_evidence"][0]["id"] == 2
    assert packet["older_evidence"][0]["relation"] == "dated_unranked"
    assert packet["confidence"]["level"] == "low"


def test_evidence_packet_followups_do_not_duplicate_temporal_words() -> None:
    packet = build_evidence_packet("latest deploy target", [])

    assert "latest latest deploy target" not in packet["follow_up_queries"]
    assert len(packet["follow_up_queries"]) == len(set(packet["follow_up_queries"]))


def test_evidence_packet_marks_missing_when_no_results() -> None:
    packet = build_evidence_packet("what changed yesterday?", [])

    assert packet["current_answer"] is None
    assert packet["confidence"]["level"] == "missing"
    assert packet["older_conflicting_memories"] == []
    assert packet["follow_up_queries"]


def test_search_evidence_endpoint_returns_packet() -> None:
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {
            "total_memories": 2,
            "dimension": 384,
            "model": "all-MiniLM-L6-v2",
        }
        mock_engine.hybrid_search.return_value = [
            {
                "id": 10,
                "text": "Current answer",
                "source": "codex/project",
                "document_at": "2026-04-01T00:00:00+00:00",
                "rrf_score": 0.02,
            },
            {
                "id": 9,
                "text": "Older answer",
                "source": "codex/project",
                "document_at": "2026-01-01T00:00:00+00:00",
                "rrf_score": 0.019,
            },
        ]
        app_module.memory = mock_engine
        client = TestClient(app_module.app)

        response = client.post(
            "/search/evidence",
            json={"query": "latest answer", "k": 5},
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "latest answer"
    assert body["evidence_packet"]["current_answer"]["id"] == 10
    assert body["evidence_packet"]["older_conflicting_memories"][0]["id"] == 9


def test_search_evidence_endpoint_applies_temporal_auto_intent() -> None:
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import app as app_module

        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {
            "total_memories": 0,
            "dimension": 384,
            "model": "all-MiniLM-L6-v2",
        }
        mock_engine.hybrid_search.return_value = []
        app_module.memory = mock_engine
        client = TestClient(app_module.app)

        response = client.post(
            "/search/evidence",
            json={
                "query": "what changed yesterday?",
                "reference_date": "2026-05-04T12:00:00Z",
            },
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    call = mock_engine.hybrid_search.call_args.kwargs
    assert call["since"] == "2026-05-03T00:00:00Z"
    assert call["until"] == "2026-05-03T23:59:59Z"
    assert call["graph_weight"] == 0.0
