"""Tests for active-search monitoring log summaries."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.active_search_metrics import (
    derive_candidate_usage,
    load_events,
    prune_candidates,
    summarize_events,
    tally_candidate_usage,
)


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


def _usage_fixture_events() -> list[dict]:
    """Surfaced-vs-used scenario shared by derivation and applier tests.

    - 101: surfaced twice; used once via follow-up memory_search in s1,
      ignored in s4 (memory_get arrives after the window).
    - 202: surfaced twice (s1, s2), never touched -> ignored twice.
    - 303: surfaced once (s2), ignored once.
    - 404: surfaced once (s3); only a memory_delete touches it -> still ignored.
    - 505: surfaced at 10:59; window is still open at now=11:00 -> not judged.
    """
    return [
        {
            "ts": "2026-06-01T10:00:00Z",
            "event": "prompt_evaluated",
            "client": "claude-code",
            "session_id": "s1",
            "project": "memories",
            "prompt_hash": "a" * 64,
            "active_search_required": True,
            "candidate_count": 2,
            "candidate_ids": [101, 202],
        },
        {
            "ts": "2026-06-01T10:00:20Z",
            "event": "tool_call",
            "client": "claude-code",
            "session_id": "s1",
            "project": "memories",
            "tool_name": "mcp__memories__memory_search",
            "source_prefix": "claude-code/memories",
            "source_prefix_quality": "exact_project",
            "memory_ids": [101, 999],
        },
        {
            "ts": "2026-06-01T10:10:00Z",
            "event": "prompt_evaluated",
            "client": "claude-code",
            "session_id": "s2",
            "project": "memories",
            "prompt_hash": "b" * 64,
            "active_search_required": False,
            "candidate_count": 2,
            "candidate_ids": [202, 303],
        },
        {
            "ts": "2026-06-01T10:20:00Z",
            "event": "prompt_evaluated",
            "client": "codex",
            "session_id": "s3",
            "project": "memories",
            "prompt_hash": "c" * 64,
            "active_search_required": False,
            "candidate_count": 1,
            "candidate_ids": [404],
        },
        {
            "ts": "2026-06-01T10:20:10Z",
            "event": "tool_call",
            "client": "codex",
            "session_id": "s3",
            "project": "memories",
            "tool_name": "mcp__memories__memory_delete",
            "source_prefix": "",
            "source_prefix_quality": "broad_or_unscoped",
            "memory_ids": [404],
        },
        {
            "ts": "2026-06-01T10:30:00Z",
            "event": "prompt_evaluated",
            "client": "claude-code",
            "session_id": "s4",
            "project": "memories",
            "prompt_hash": "d" * 64,
            "active_search_required": False,
            "candidate_count": 1,
            "candidate_ids": [101],
        },
        {
            "ts": "2026-06-01T10:36:41Z",
            "event": "tool_call",
            "client": "claude-code",
            "session_id": "s4",
            "project": "memories",
            "tool_name": "mcp__memories__memory_get",
            "source_prefix": "",
            "source_prefix_quality": "broad_or_unscoped",
            "memory_ids": [101],
        },
        {
            "ts": "2026-06-01T10:59:00Z",
            "event": "prompt_evaluated",
            "client": "claude-code",
            "session_id": "s5",
            "project": "memories",
            "prompt_hash": "e" * 64,
            "active_search_required": False,
            "candidate_count": 1,
            "candidate_ids": [505],
        },
    ]


def test_derive_candidate_usage_pairs_surfaced_and_used() -> None:
    surfacings = derive_candidate_usage(
        _usage_fixture_events(),
        window_seconds=300,
        now="2026-06-01T11:00:00Z",
    )

    by_key = {(s["memory_id"], s["session_id"]): s for s in surfacings}

    used_101_s1 = by_key[(101, "s1")]
    assert used_101_s1["used"] is True
    assert used_101_s1["used_by"] == "memory_search"
    assert used_101_s1["window_closed"] is True

    # memory_get arrived 401s after the s4 prompt: outside the 300s window
    assert by_key[(101, "s4")]["used"] is False

    assert by_key[(202, "s1")]["used"] is False
    assert by_key[(202, "s2")]["used"] is False
    assert by_key[(303, "s2")]["used"] is False

    # delete tools never count as usage
    assert by_key[(404, "s3")]["used"] is False

    # window still open at now -> not judged yet
    open_window = by_key[(505, "s5")]
    assert open_window["window_closed"] is False

    # ids never surfaced in a prompt never appear
    assert all(s["memory_id"] != 999 for s in surfacings)


def test_tally_candidate_usage_counts_closed_windows_only() -> None:
    surfacings = derive_candidate_usage(
        _usage_fixture_events(),
        window_seconds=300,
        now="2026-06-01T11:00:00Z",
    )
    tallies = tally_candidate_usage(surfacings)

    assert tallies[101] == {"surfaced": 2, "used": 1, "ignored": 1}
    assert tallies[202] == {"surfaced": 2, "used": 0, "ignored": 2}
    assert tallies[303] == {"surfaced": 1, "used": 0, "ignored": 1}
    assert tallies[404] == {"surfaced": 1, "used": 0, "ignored": 1}
    # open window -> not tallied at all
    assert 505 not in tallies


def test_prune_candidates_lists_chronically_surfaced_never_used() -> None:
    events = _usage_fixture_events()
    # Make 202 chronic: surfaced a third time, again ignored.
    events.append(
        {
            "ts": "2026-06-01T10:40:00Z",
            "event": "prompt_evaluated",
            "client": "claude-code",
            "session_id": "s6",
            "project": "memories",
            "prompt_hash": "f" * 64,
            "active_search_required": False,
            "candidate_count": 1,
            "candidate_ids": [202],
        }
    )

    candidates = prune_candidates(
        events,
        window_seconds=300,
        min_surfaced=3,
        limit=20,
        now="2026-06-01T11:00:00Z",
    )

    assert [c["memory_id"] for c in candidates] == [202]
    entry = candidates[0]
    assert entry["surfaced"] == 3
    assert entry["used"] == 0
    assert entry["ignored"] == 3
    assert entry["last_surfaced_ts"] == "2026-06-01T10:40:00Z"
    assert entry["projects"] == ["memories"]

    # 101 was used once -> never a prune candidate even with min_surfaced=1
    relaxed = prune_candidates(
        events,
        window_seconds=300,
        min_surfaced=1,
        limit=20,
        now="2026-06-01T11:00:00Z",
    )
    assert all(c["memory_id"] != 101 for c in relaxed)
    # relaxed threshold surfaces the once-ignored ids too, most-surfaced first
    assert [c["memory_id"] for c in relaxed] == [202, 303, 404]

    limited = prune_candidates(
        events,
        window_seconds=300,
        min_surfaced=1,
        limit=1,
        now="2026-06-01T11:00:00Z",
    )
    assert [c["memory_id"] for c in limited] == [202]


def test_summarize_events_reports_candidate_usage_counters() -> None:
    summary = summarize_events(_usage_fixture_events(), followup_window_seconds=300)

    # all 7 surfacings count as closed: wall-clock now is far past the
    # fixture timestamps, so even 505's window has elapsed
    assert summary["candidate_surfacings"] == 7
    assert summary["candidates_used"] == 1
    assert summary["candidates_ignored"] == 6


def test_summarize_attributes_opencode_tool_calls_with_ts_field(tmp_path: Path) -> None:
    """OpenCode plugin telemetry must use the same `ts` field name as Claude/Codex hooks.

    This guards against a regression where the plugin emitted `timestamp` instead of `ts`,
    causing matched-prompt analytics to silently drop OpenCode tool_call events.
    """
    log = tmp_path / "active-search.jsonl"
    events = [
        {
            "ts": "2026-05-06T12:00:00Z",
            "event": "prompt_evaluated",
            "client": "opencode",
            "session_id": "oc-1",
            "project": "memories",
            "prompt_hash": "d" * 64,
            "active_search_required": True,
            "candidate_count": 1,
        },
        {
            "ts": "2026-05-06T12:00:15Z",
            "event": "tool_call",
            "client": "opencode",
            "session_id": "oc-1",
            "project": "memories",
            "tool_name": "mcp__memories__memory_search",
            "source_prefix": "opencode/memories",
            "source_prefix_quality": "exact_project",
        },
    ]
    log.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    summary = summarize_events(load_events(log), followup_window_seconds=60)

    assert summary["memory_search_calls"] == 1
    assert summary["exact_project_searches"] == 1
    assert summary["by_client"]["opencode"]["memory_search_calls"] == 1
    assert summary["by_client"]["opencode"]["exact_project_searches"] == 1
    assert summary["by_client"]["opencode"]["required_prompts"] == 1
    assert summary["by_client"]["opencode"]["required_prompts_with_memory_search"] == 1
    assert summary["by_client"]["opencode"]["passive_risk_prompts"] == 0
    assert summary["active_search_followup_rate"] == 1.0
