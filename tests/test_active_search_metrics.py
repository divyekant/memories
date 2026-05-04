"""Tests for active-search monitoring log summaries."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.active_search_metrics import load_events, summarize_events


def test_summarize_active_search_followup_rate_and_prefix_quality(tmp_path: Path) -> None:
    log = tmp_path / "active-search.jsonl"
    events = [
        {
            "ts": "2026-05-04T15:00:00Z",
            "event": "prompt_evaluated",
            "client": "codex",
            "session_id": "s1",
            "project": "memories",
            "prompt_hash": "a" * 64,
            "active_search_required": True,
            "candidate_count": 2,
        },
        {
            "ts": "2026-05-04T15:00:20Z",
            "event": "tool_call",
            "client": "codex",
            "session_id": "s1",
            "project": "memories",
            "tool_name": "mcp__memories__memory_search",
            "source_prefix": "codex/memories",
            "source_prefix_quality": "exact_project",
        },
        {
            "ts": "2026-05-04T15:02:00Z",
            "event": "prompt_evaluated",
            "client": "claude-code",
            "session_id": "s2",
            "project": "memories",
            "prompt_hash": "b" * 64,
            "active_search_required": True,
            "candidate_count": 1,
        },
        {
            "ts": "2026-05-04T15:02:10Z",
            "event": "tool_call",
            "client": "claude-code",
            "session_id": "s2",
            "project": "memories",
            "tool_name": "mcp__memories__memory_search",
            "source_prefix": "",
            "source_prefix_quality": "broad_or_unscoped",
        },
        {
            "ts": "2026-05-04T15:04:00Z",
            "event": "prompt_evaluated",
            "client": "codex",
            "session_id": "s3",
            "project": "memories",
            "prompt_hash": "c" * 64,
            "active_search_required": True,
            "candidate_count": 1,
        },
    ]
    log.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    summary = summarize_events(load_events(log), followup_window_seconds=60)

    assert summary["required_prompts"] == 3
    assert summary["required_prompts_with_memory_search"] == 2
    assert summary["active_search_followup_rate"] == 2 / 3
    assert summary["passive_risk_prompts"] == 1
    assert summary["memory_search_calls"] == 2
    assert summary["exact_project_searches"] == 1
    assert summary["broad_or_unscoped_searches"] == 1
    assert summary["by_client"]["codex"]["required_prompts"] == 2
    assert summary["by_client"]["codex"]["passive_risk_prompts"] == 1
    assert summary["by_client"]["claude-code"]["broad_or_unscoped_searches"] == 1


def test_summarize_active_search_matches_one_search_to_one_recent_prompt() -> None:
    events = [
        {
            "ts": "2026-05-04T15:00:00Z",
            "event": "prompt_evaluated",
            "client": "codex",
            "session_id": "s1",
            "active_search_required": True,
        },
        {
            "ts": "2026-05-04T15:03:20Z",
            "event": "prompt_evaluated",
            "client": "codex",
            "session_id": "s1",
            "active_search_required": True,
        },
        {
            "ts": "2026-05-04T15:04:10Z",
            "event": "tool_call",
            "client": "codex",
            "session_id": "s1",
            "tool_name": "mcp__memories__memory_search",
            "source_prefix_quality": "exact_project",
        },
    ]

    summary = summarize_events(events, followup_window_seconds=300)

    assert summary["required_prompts"] == 2
    assert summary["required_prompts_with_memory_search"] == 1
    assert summary["active_search_followup_rate"] == 0.5
    assert summary["passive_risk_prompts"] == 1


def test_load_events_skips_invalid_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "active-search.jsonl"
    log.write_text('{"event":"prompt_evaluated"}\nnot-json\n{"event":"tool_call"}\n', encoding="utf-8")

    assert [event["event"] for event in load_events(log)] == ["prompt_evaluated", "tool_call"]
