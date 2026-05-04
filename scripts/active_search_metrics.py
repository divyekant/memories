#!/usr/bin/env python3
"""Summarize local active-search hook telemetry.

The hook log intentionally stores only metadata: timestamps, client/session,
project, prompt hash, candidate counts, tool names, and source prefixes. It
does not store prompt text or retrieved memory text.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_events(path: str | Path) -> list[dict[str, Any]]:
    """Load JSONL events, skipping malformed lines."""

    log_path = Path(path).expanduser()
    if not log_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def _empty_client_summary() -> dict[str, Any]:
    return {
        "required_prompts": 0,
        "required_prompts_with_memory_search": 0,
        "passive_risk_prompts": 0,
        "memory_search_calls": 0,
        "exact_project_searches": 0,
        "broad_or_unscoped_searches": 0,
    }


def _is_memory_search(event: dict[str, Any]) -> bool:
    tool_name = str(event.get("tool_name", ""))
    return tool_name == "memory_search" or tool_name.endswith("__memory_search")


def summarize_events(
    events: list[dict[str, Any]],
    *,
    followup_window_seconds: int = 300,
) -> dict[str, Any]:
    """Summarize active-search prompt events and following memory_search calls."""

    sorted_events = sorted(events, key=lambda event: str(event.get("ts", "")))
    tool_events = [event for event in sorted_events if event.get("event") == "tool_call"]
    prompt_events = [
        event
        for event in sorted_events
        if event.get("event") == "prompt_evaluated" and bool(event.get("active_search_required"))
    ]

    by_client: dict[str, dict[str, Any]] = {}

    memory_search_calls = 0
    exact_project_searches = 0
    broad_or_unscoped_searches = 0
    for event in tool_events:
        if not _is_memory_search(event):
            continue
        memory_search_calls += 1
        client = str(event.get("client") or "unknown")
        by_client.setdefault(client, _empty_client_summary())
        by_client[client]["memory_search_calls"] += 1
        quality = str(event.get("source_prefix_quality") or "")
        if quality == "exact_project":
            exact_project_searches += 1
            by_client[client]["exact_project_searches"] += 1
        elif quality == "broad_or_unscoped":
            broad_or_unscoped_searches += 1
            by_client[client]["broad_or_unscoped_searches"] += 1

    prompt_records: list[dict[str, Any]] = []
    for prompt in prompt_events:
        client = str(prompt.get("client") or "unknown")
        by_client.setdefault(client, _empty_client_summary())
        by_client[client]["required_prompts"] += 1
        prompt_records.append({
            "event": prompt,
            "ts": _parse_ts(str(prompt.get("ts") or "")),
            "matched": False,
        })

    for tool_event in tool_events:
        if not _is_memory_search(tool_event):
            continue
        tool_ts = _parse_ts(str(tool_event.get("ts") or ""))
        if tool_ts is None:
            continue
        tool_session_id = str(tool_event.get("session_id") or "")
        candidates: list[tuple[datetime, int]] = []
        for index, record in enumerate(prompt_records):
            if record["matched"]:
                continue
            prompt_ts = record["ts"]
            if prompt_ts is None:
                continue
            prompt = record["event"]
            if str(prompt.get("session_id") or "") != tool_session_id:
                continue
            delta = (tool_ts - prompt_ts).total_seconds()
            if 0 <= delta <= followup_window_seconds:
                candidates.append((prompt_ts, index))
        if candidates:
            _, matched_index = max(candidates, key=lambda candidate: candidate[0])
            prompt_records[matched_index]["matched"] = True

    prompts_with_search = 0
    passive_risk_prompts = 0
    for record in prompt_records:
        prompt = record["event"]
        client = str(prompt.get("client") or "unknown")
        if record["matched"]:
            prompts_with_search += 1
            by_client[client]["required_prompts_with_memory_search"] += 1
        else:
            passive_risk_prompts += 1
            by_client[client]["passive_risk_prompts"] += 1

    required_prompts = len(prompt_events)
    followup_rate = prompts_with_search / required_prompts if required_prompts else 1.0

    return {
        "required_prompts": required_prompts,
        "required_prompts_with_memory_search": prompts_with_search,
        "active_search_followup_rate": followup_rate,
        "passive_risk_prompts": passive_risk_prompts,
        "memory_search_calls": memory_search_calls,
        "exact_project_searches": exact_project_searches,
        "broad_or_unscoped_searches": broad_or_unscoped_searches,
        "by_client": by_client,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize active-search hook telemetry")
    parser.add_argument(
        "--log",
        default="~/.config/memories/active-search.jsonl",
        help="Path to active-search JSONL telemetry log",
    )
    parser.add_argument(
        "--followup-window-seconds",
        type=int,
        default=300,
        help="Seconds after a required prompt to count a memory_search as follow-up",
    )
    args = parser.parse_args()

    summary = summarize_events(
        load_events(args.log),
        followup_window_seconds=args.followup_window_seconds,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
