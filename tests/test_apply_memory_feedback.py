"""Tests for the batch recall-feedback applier."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.apply_memory_feedback import (
    compute_decisions,
    load_cursor,
    run_feedback,
    save_cursor,
)
from tests.test_active_search_metrics import _usage_fixture_events


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "apply_memory_feedback.py"
NOW = "2026-06-01T11:00:00Z"


class RecordingPoster:
    def __init__(self, status: int = 200, statuses: dict[int, int] | None = None):
        self.status = status
        self.statuses = statuses or {}
        self.calls: list[dict] = []

    def __call__(self, memory_id: int, signal: str, search_id: str) -> int:
        self.calls.append({"memory_id": memory_id, "signal": signal, "search_id": search_id})
        return self.statuses.get(memory_id, self.status)


def _write_log(tmp_path: Path, events: list[dict]) -> Path:
    log = tmp_path / "active-search.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return log


def test_compute_decisions_applies_thresholds() -> None:
    tallies = {
        101: {"surfaced": 2, "used": 1, "ignored": 1},
        202: {"surfaced": 2, "used": 0, "ignored": 2},
        303: {"surfaced": 1, "used": 0, "ignored": 1},
    }

    decisions, no_action = compute_decisions(tallies, min_ignored=2)

    assert decisions == [
        {"memory_id": 101, "signal": "useful", "surfaced": 2, "used": 1, "ignored": 1},
        {"memory_id": 202, "signal": "not_useful", "surfaced": 2, "used": 0, "ignored": 2},
    ]
    assert no_action == [{"memory_id": 303, "surfaced": 1, "used": 0, "ignored": 1}]

    relaxed, leftover = compute_decisions(tallies, min_ignored=1)
    assert {d["memory_id"]: d["signal"] for d in relaxed} == {
        101: "useful",
        202: "not_useful",
        303: "not_useful",
    }
    assert leftover == []


def test_dry_run_reports_without_posting_or_cursor_movement() -> None:
    poster = RecordingPoster()
    cursor = {"last_ts": "", "applied_keys": []}

    report = run_feedback(
        _usage_fixture_events(),
        cursor=cursor,
        window_seconds=300,
        min_ignored=2,
        now=NOW,
        execute=False,
        poster=poster,
    )

    assert report["mode"] == "dry-run"
    assert report["judged_prompts"] == 4
    assert {d["memory_id"]: d["signal"] for d in report["decisions"]} == {
        101: "useful",
        202: "not_useful",
    }
    # 303 and 404 stay below the min-ignored threshold
    assert {entry["memory_id"] for entry in report["no_action"]} == {303, 404}
    assert poster.calls == []
    # dry-run still previews the cursor it WOULD write
    assert report["cursor"]["last_ts"] == "2026-06-01T10:30:00Z"
    assert report["cursor_advanced"] is True
    assert report["applied"] == 0


def test_execute_posts_decisions_and_run_is_idempotent(tmp_path: Path) -> None:
    events = _usage_fixture_events()
    poster = RecordingPoster()
    cursor = {"last_ts": "", "applied_keys": []}

    report = run_feedback(
        events,
        cursor=cursor,
        window_seconds=300,
        min_ignored=2,
        now=NOW,
        execute=True,
        poster=poster,
    )

    assert report["mode"] == "execute"
    assert report["applied"] == 2
    assert report["failures"] == []
    assert {(c["memory_id"], c["signal"]) for c in poster.calls} == {
        (101, "useful"),
        (202, "not_useful"),
    }
    assert all(c["search_id"].startswith("feedback-loop:") for c in poster.calls)

    # second run from the advanced cursor judges nothing new
    second_poster = RecordingPoster()
    second = run_feedback(
        events,
        cursor=report["cursor"],
        window_seconds=300,
        min_ignored=2,
        now=NOW,
        execute=True,
        poster=second_poster,
    )

    assert second["judged_prompts"] == 0
    assert second["decisions"] == []
    assert second_poster.calls == []
    assert second["cursor_advanced"] is False
    assert second["cursor"] == report["cursor"]


def test_open_window_prompt_is_judged_once_window_closes() -> None:
    events = _usage_fixture_events()
    poster = RecordingPoster()

    first = run_feedback(
        events,
        cursor={"last_ts": "", "applied_keys": []},
        window_seconds=300,
        min_ignored=1,
        now=NOW,
        execute=True,
        poster=poster,
    )
    # 505's window is open at 11:00 -> cursor stops at the s4 prompt
    assert first["cursor"]["last_ts"] == "2026-06-01T10:30:00Z"
    assert all(c["memory_id"] != 505 for c in poster.calls)

    later_poster = RecordingPoster()
    later = run_feedback(
        events,
        cursor=first["cursor"],
        window_seconds=300,
        min_ignored=1,
        now="2026-06-01T12:00:00Z",
        execute=True,
        poster=later_poster,
    )

    assert later["judged_prompts"] == 1
    assert [c["memory_id"] for c in later_poster.calls] == [505]
    assert later_poster.calls[0]["signal"] == "not_useful"
    assert later["cursor"]["last_ts"] == "2026-06-01T10:59:00Z"


def test_execute_does_not_advance_cursor_on_hard_failure() -> None:
    poster = RecordingPoster(statuses={202: 500})

    report = run_feedback(
        _usage_fixture_events(),
        cursor={"last_ts": "", "applied_keys": []},
        window_seconds=300,
        min_ignored=2,
        now=NOW,
        execute=True,
        poster=poster,
    )

    assert report["failures"] == [{"memory_id": 202, "signal": "not_useful", "status": 500}]
    assert report["cursor_advanced"] is False
    # cursor unchanged so the failed window is retried next run
    assert report["cursor"] == {"last_ts": "", "applied_keys": []}


def test_execute_treats_missing_memory_as_skipped() -> None:
    poster = RecordingPoster(statuses={202: 404})

    report = run_feedback(
        _usage_fixture_events(),
        cursor={"last_ts": "", "applied_keys": []},
        window_seconds=300,
        min_ignored=2,
        now=NOW,
        execute=True,
        poster=poster,
    )

    assert report["skipped_missing"] == [202]
    assert report["failures"] == []
    assert report["applied"] == 1
    assert report["cursor_advanced"] is True


def test_max_actions_guardrail_blocks_run_without_posting() -> None:
    poster = RecordingPoster()

    report = run_feedback(
        _usage_fixture_events(),
        cursor={"last_ts": "", "applied_keys": []},
        window_seconds=300,
        min_ignored=2,
        now=NOW,
        execute=True,
        poster=poster,
        max_actions=1,
    )

    assert report["aborted"] == "max_actions_exceeded"
    assert poster.calls == []
    assert report["cursor_advanced"] is False


def test_cursor_roundtrip_and_default_shape(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    assert load_cursor(path) == {"last_ts": "", "applied_keys": []}

    save_cursor(path, {"last_ts": "2026-06-01T10:30:00Z", "applied_keys": ["k1"]})
    assert load_cursor(path) == {"last_ts": "2026-06-01T10:30:00Z", "applied_keys": ["k1"]}


def test_cli_dry_run_prints_report_and_writes_no_cursor(tmp_path: Path) -> None:
    log = _write_log(tmp_path, _usage_fixture_events())
    cursor_path = tmp_path / "cursor.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--log",
            str(log),
            "--cursor",
            str(cursor_path),
            "--window-seconds",
            "300",
            "--min-ignored",
            "2",
            "--now",
            NOW,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == "dry-run"
    assert {d["memory_id"]: d["signal"] for d in report["decisions"]} == {
        101: "useful",
        202: "not_useful",
    }
    assert not cursor_path.exists()
