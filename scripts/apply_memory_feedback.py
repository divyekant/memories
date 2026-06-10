#!/usr/bin/env python3
"""Batch recall-feedback applier — closes the surfaced-vs-used loop.

Reads the local active-search JSONL telemetry (see
docs/active-search-monitoring.md), tallies which hook-surfaced candidate
memories were subsequently used vs ignored, and applies relevance feedback
through the existing ``POST /search/feedback`` backend mechanism (the same
endpoint the ``memory_is_useful`` MCP tool hits). Search ranking already
consumes this signal via ``feedback_weight``.

Safety properties:
- Dry-run by default; ``--execute`` is required to write anything.
- Idempotent: an event cursor file records the last judged prompt event, so
  re-running over the same log never double-applies feedback.
- Only judges prompts whose follow-up window has fully elapsed.
- Never deletes memories — it only records useful/not_useful feedback.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # imported as scripts.apply_memory_feedback (tests)
    from scripts.active_search_metrics import (
        _parse_ts,
        _resolve_now,
        derive_candidate_usage,
        load_events,
        tally_candidate_usage,
    )
except ImportError:  # executed directly: python scripts/apply_memory_feedback.py
    from active_search_metrics import (  # type: ignore[no-redef]
        _parse_ts,
        _resolve_now,
        derive_candidate_usage,
        load_events,
        tally_candidate_usage,
    )

DEFAULT_LOG = "~/.config/memories/active-search.jsonl"
DEFAULT_CURSOR = "~/.config/memories/feedback-cursor.json"

Poster = Callable[[int, str, str], int]


def load_cursor(path: str | Path) -> dict[str, Any]:
    cursor_path = Path(path).expanduser()
    if not cursor_path.exists():
        return {"last_ts": "", "applied_keys": []}
    try:
        raw = json.loads(cursor_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_ts": "", "applied_keys": []}
    if not isinstance(raw, dict):
        return {"last_ts": "", "applied_keys": []}
    return {
        "last_ts": str(raw.get("last_ts") or ""),
        "applied_keys": [str(k) for k in raw.get("applied_keys") or []],
    }


def save_cursor(path: str | Path, cursor: dict[str, Any]) -> None:
    cursor_path = Path(path).expanduser()
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps(cursor, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _select_new_surfacings(
    surfacings: list[dict[str, Any]],
    cursor: dict[str, Any],
) -> list[dict[str, Any]]:
    """Closed-window surfacings from prompt events not yet covered by the cursor."""

    last_ts = _parse_ts(str(cursor.get("last_ts") or ""))
    applied_keys = set(cursor.get("applied_keys") or [])

    selected: list[dict[str, Any]] = []
    for surfacing in surfacings:
        if not surfacing.get("window_closed"):
            continue
        prompt_ts = _parse_ts(str(surfacing.get("prompt_ts") or ""))
        if prompt_ts is None:
            continue
        if last_ts is not None:
            if prompt_ts < last_ts:
                continue
            if prompt_ts == last_ts and surfacing["event_key"] in applied_keys:
                continue
        selected.append(surfacing)
    return selected


def compute_decisions(
    tallies: dict[int, dict[str, int]],
    *,
    min_ignored: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate one feedback decision per memory for this window.

    - any use in the window  -> one ``useful`` signal
    - ignored >= min_ignored -> one ``not_useful`` signal
    - otherwise              -> no action (reported for transparency)
    """

    decisions: list[dict[str, Any]] = []
    no_action: list[dict[str, Any]] = []
    for memory_id in sorted(tallies):
        tally = tallies[memory_id]
        entry = {"memory_id": memory_id, **tally}
        if tally["used"] > 0:
            decisions.append({"memory_id": memory_id, "signal": "useful", **tally})
        elif tally["ignored"] >= min_ignored:
            decisions.append({"memory_id": memory_id, "signal": "not_useful", **tally})
        else:
            no_action.append(entry)
    return decisions, no_action


def _advance_cursor(
    cursor: dict[str, Any],
    judged: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    if not judged:
        return cursor, False

    max_ts_raw = max(judged, key=lambda s: _parse_ts(s["prompt_ts"]) or datetime.min.replace(tzinfo=timezone.utc))["prompt_ts"]
    max_ts = _parse_ts(max_ts_raw)
    keys_at_max = sorted(
        {s["event_key"] for s in judged if _parse_ts(s["prompt_ts"]) == max_ts}
    )
    if _parse_ts(str(cursor.get("last_ts") or "")) == max_ts:
        keys_at_max = sorted(set(keys_at_max) | set(cursor.get("applied_keys") or []))
    return {"last_ts": max_ts_raw, "applied_keys": keys_at_max}, True


def run_feedback(
    events: list[dict[str, Any]],
    *,
    cursor: dict[str, Any],
    window_seconds: int = 300,
    min_ignored: int = 2,
    now: str | datetime | None = None,
    execute: bool = False,
    poster: Poster | None = None,
    max_actions: int = 200,
) -> dict[str, Any]:
    """Judge new closed-window surfacings and (optionally) apply feedback."""

    surfacings = derive_candidate_usage(events, window_seconds=window_seconds, now=now)
    judged = _select_new_surfacings(surfacings, cursor)
    tallies = tally_candidate_usage(judged)
    decisions, no_action = compute_decisions(tallies, min_ignored=min_ignored)

    judged_prompts = len({s["event_key"] for s in judged})
    new_cursor, advanced = _advance_cursor(cursor, judged)

    report: dict[str, Any] = {
        "mode": "execute" if execute else "dry-run",
        "window_seconds": window_seconds,
        "min_ignored": min_ignored,
        "judged_prompts": judged_prompts,
        "decisions": decisions,
        "no_action": no_action,
        "applied": 0,
        "skipped_missing": [],
        "failures": [],
        "cursor": new_cursor,
        "cursor_advanced": advanced,
    }

    if len(decisions) > max_actions:
        report["aborted"] = "max_actions_exceeded"
        report["cursor"] = cursor
        report["cursor_advanced"] = False
        return report

    if not execute:
        return report

    run_ts = _resolve_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")
    search_id = f"feedback-loop:{run_ts}"
    if poster is None:
        raise ValueError("execute=True requires a poster")

    for decision in decisions:
        try:
            status = poster(decision["memory_id"], decision["signal"], search_id)
        except Exception as exc:  # transport-level failure
            report["failures"].append(
                {"memory_id": decision["memory_id"], "signal": decision["signal"], "error": str(exc)}
            )
            continue
        if 200 <= status < 300:
            report["applied"] += 1
        elif status == 404:
            report["skipped_missing"].append(decision["memory_id"])
        else:
            report["failures"].append(
                {"memory_id": decision["memory_id"], "signal": decision["signal"], "status": status}
            )

    if report["failures"]:
        # keep the cursor so the failed window is retried next run
        report["cursor"] = cursor
        report["cursor_advanced"] = False

    return report


def http_poster(url: str, api_key: str, *, timeout: float = 10.0) -> Poster:
    """Poster that hits the existing /search/feedback backend endpoint."""

    endpoint = url.rstrip("/") + "/search/feedback"

    def post(memory_id: int, signal: str, search_id: str) -> int:
        body = json.dumps(
            {"memory_id": memory_id, "query": "", "signal": signal, "search_id": search_id}
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status)
        except urllib.error.HTTPError as error:
            return int(error.code)

    return post


def main(argv: list[str] | None = None, poster: Poster | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply recall feedback derived from active-search telemetry (dry-run by default)"
    )
    parser.add_argument("--log", default=DEFAULT_LOG, help="Active-search JSONL telemetry log")
    parser.add_argument("--cursor", default=DEFAULT_CURSOR, help="Event cursor file for idempotent runs")
    parser.add_argument("--window-seconds", type=int, default=300, help="Follow-up window per surfaced prompt")
    parser.add_argument(
        "--min-ignored",
        type=int,
        default=2,
        help="Closed-window ignores required (per run) before a not_useful signal",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=200,
        help="Abort without posting if more decisions than this accumulate",
    )
    parser.add_argument("--url", default=os.environ.get("MEMORIES_URL", "http://localhost:8900"))
    parser.add_argument("--api-key", default=os.environ.get("MEMORIES_API_KEY", ""))
    parser.add_argument("--now", default=None, help="Override wall clock (ISO timestamp, mainly for tests)")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply feedback to the backend and advance the cursor (default: dry-run)",
    )
    args = parser.parse_args(argv)

    events = load_events(args.log)
    cursor = load_cursor(args.cursor)
    if args.execute and poster is None:
        poster = http_poster(args.url, args.api_key)

    report = run_feedback(
        events,
        cursor=cursor,
        window_seconds=args.window_seconds,
        min_ignored=args.min_ignored,
        now=args.now,
        execute=args.execute,
        poster=poster,
        max_actions=args.max_actions,
    )
    print(json.dumps(report, indent=2, sort_keys=True))

    if report.get("aborted"):
        return 3
    if report["failures"]:
        return 2
    if args.execute and report["cursor_advanced"]:
        save_cursor(args.cursor, report["cursor"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
