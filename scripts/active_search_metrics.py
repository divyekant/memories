#!/usr/bin/env python3
"""Summarize local active-search hook telemetry.

The hook log intentionally stores only metadata: timestamps, client/session,
project, prompt hash, candidate counts, tool names, and source prefixes. It
does not store prompt text or retrieved memory text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Tools that touch memory ids without meaning "this memory was useful".
USAGE_EXCLUDED_TOOLS = {
    "memory_delete",
    "memory_delete_batch",
    "memory_delete_by_source",
}

_TOOL_BASE_NAME_RE = re.compile(r"(memory_[a-z_]+)$")


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
        "prompt_evaluations": 0,
        "session_recall_events": 0,
        "automatic_searches": 0,
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


def tool_base_name(tool_name: str) -> str:
    """Normalize MCP tool names (mcp__memories__memory_get -> memory_get)."""

    match = _TOOL_BASE_NAME_RE.search(str(tool_name or ""))
    return match.group(1) if match else str(tool_name or "")


def event_key(event: dict[str, Any]) -> str:
    """Stable identity for a telemetry event, used by the feedback cursor."""

    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_now(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc)
    parsed = _parse_ts(str(now))
    if parsed is None:
        raise ValueError(f"Unparseable now value: {now!r}")
    return parsed


def _coerce_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [i for i in value if isinstance(i, int) and not isinstance(i, bool)]


def derive_candidate_usage(
    events: list[dict[str, Any]],
    *,
    window_seconds: int = 300,
    now: str | datetime | None = None,
) -> list[dict[str, Any]]:
    """Pair surfaced candidate ids with follow-up memory tool calls.

    Returns one surfacing record per (prompt_evaluated event, candidate id):
    ``{memory_id, prompt_ts, session_id, client, project, event_key, used,
    used_by, window_closed}``. A candidate counts as used when a later
    ``tool_call`` event in the same session, within ``window_seconds``, has the
    id in its ``memory_ids`` (delete tools excluded). ``window_closed`` is
    False while the follow-up window is still open at ``now`` — such
    surfacings must not be judged as ignored yet.
    """

    now_dt = _resolve_now(now)
    window = timedelta(seconds=window_seconds)

    tool_records: list[tuple[datetime, str, str, set[int]]] = []
    for event in events:
        if event.get("event") != "tool_call":
            continue
        if tool_base_name(str(event.get("tool_name", ""))) in USAGE_EXCLUDED_TOOLS:
            continue
        memory_ids = set(_coerce_ids(event.get("memory_ids")))
        if not memory_ids:
            continue
        ts = _parse_ts(str(event.get("ts") or ""))
        if ts is None:
            continue
        session_id = str(event.get("session_id") or "")
        tool_records.append((ts, session_id, tool_base_name(str(event.get("tool_name", ""))), memory_ids))
    tool_records.sort(key=lambda record: record[0])

    surfacings: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda e: str(e.get("ts", ""))):
        if event.get("event") != "prompt_evaluated":
            continue
        candidate_ids = _coerce_ids(event.get("candidate_ids"))
        if not candidate_ids:
            continue
        prompt_ts = _parse_ts(str(event.get("ts") or ""))
        if prompt_ts is None:
            continue
        session_id = str(event.get("session_id") or "")
        key = event_key(event)
        window_closed = prompt_ts + window <= now_dt
        for memory_id in candidate_ids:
            used_by = None
            for tool_ts, tool_session, tool_name, memory_ids in tool_records:
                if tool_session != session_id:
                    continue
                delta = tool_ts - prompt_ts
                if delta < timedelta(0):
                    continue
                if delta > window:
                    break  # tool_records are sorted; nothing later can match
                if memory_id in memory_ids:
                    used_by = tool_name
                    break
            surfacings.append(
                {
                    "memory_id": memory_id,
                    "prompt_ts": str(event.get("ts") or ""),
                    "session_id": session_id,
                    "client": str(event.get("client") or "unknown"),
                    "project": str(event.get("project") or "unknown"),
                    "event_key": key,
                    "used": used_by is not None,
                    "used_by": used_by,
                    "window_closed": window_closed,
                }
            )
    return surfacings


def tally_candidate_usage(surfacings: list[dict[str, Any]]) -> dict[int, dict[str, int]]:
    """Per-memory used/ignored tallies over closed-window surfacings only."""

    tallies: dict[int, dict[str, int]] = {}
    for surfacing in surfacings:
        if not surfacing.get("window_closed"):
            continue
        tally = tallies.setdefault(
            int(surfacing["memory_id"]), {"surfaced": 0, "used": 0, "ignored": 0}
        )
        tally["surfaced"] += 1
        if surfacing.get("used"):
            tally["used"] += 1
        else:
            tally["ignored"] += 1
    return tallies


def prune_candidates(
    events: list[dict[str, Any]],
    *,
    window_seconds: int = 300,
    min_surfaced: int = 3,
    limit: int = 20,
    now: str | datetime | None = None,
) -> list[dict[str, Any]]:
    """List chronically surfaced-but-never-used memories as REVIEW candidates.

    Report only — nothing here deletes or archives memories. Review each id
    (``memory_get``) before acting on it.
    """

    surfacings = derive_candidate_usage(events, window_seconds=window_seconds, now=now)
    tallies = tally_candidate_usage(surfacings)

    last_surfaced: dict[int, str] = {}
    projects: dict[int, set[str]] = {}
    for surfacing in surfacings:
        if not surfacing.get("window_closed"):
            continue
        memory_id = int(surfacing["memory_id"])
        ts = str(surfacing.get("prompt_ts") or "")
        if ts > last_surfaced.get(memory_id, ""):
            last_surfaced[memory_id] = ts
        projects.setdefault(memory_id, set()).add(str(surfacing.get("project") or "unknown"))

    candidates = [
        {
            "memory_id": memory_id,
            "surfaced": tally["surfaced"],
            "used": tally["used"],
            "ignored": tally["ignored"],
            "last_surfaced_ts": last_surfaced.get(memory_id, ""),
            "projects": sorted(projects.get(memory_id, set())),
        }
        for memory_id, tally in tallies.items()
        if tally["used"] == 0 and tally["surfaced"] >= min_surfaced
    ]
    candidates.sort(key=lambda c: (-c["surfaced"], c["memory_id"]))
    return candidates[:limit]


def summarize_events(
    events: list[dict[str, Any]],
    *,
    followup_window_seconds: int = 300,
) -> dict[str, Any]:
    """Summarize active-search prompt events and following memory_search calls."""

    sorted_events = sorted(events, key=lambda event: str(event.get("ts", "")))
    tool_events = [event for event in sorted_events if event.get("event") == "tool_call"]
    all_prompt_events = [
        event for event in sorted_events if event.get("event") == "prompt_evaluated"
    ]
    prompt_events = [
        event
        for event in all_prompt_events
        if bool(event.get("active_search_required"))
    ]
    session_recall_events = [
        event for event in sorted_events if event.get("event") == "session_recall"
    ]

    by_client: dict[str, dict[str, Any]] = {}

    automatic_searches = 0
    for event in [*all_prompt_events, *session_recall_events]:
        client = str(event.get("client") or "unknown")
        by_client.setdefault(client, _empty_client_summary())
        if event.get("event") == "prompt_evaluated":
            by_client[client]["prompt_evaluations"] += 1
        else:
            by_client[client]["session_recall_events"] += 1
        search_count = event.get("search_count", 0)
        if isinstance(search_count, int) and not isinstance(search_count, bool) and search_count > 0:
            automatic_searches += search_count
            by_client[client]["automatic_searches"] += search_count

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

    usage = tally_candidate_usage(
        derive_candidate_usage(sorted_events, window_seconds=followup_window_seconds)
    )
    candidate_surfacings = sum(t["surfaced"] for t in usage.values())
    candidates_used = sum(t["used"] for t in usage.values())
    candidates_ignored = sum(t["ignored"] for t in usage.values())

    return {
        "prompt_evaluations": len(all_prompt_events),
        "session_recall_events": len(session_recall_events),
        "automatic_searches": automatic_searches,
        "required_prompts": required_prompts,
        "required_prompts_with_memory_search": prompts_with_search,
        "active_search_followup_rate": followup_rate,
        "passive_risk_prompts": passive_risk_prompts,
        "memory_search_calls": memory_search_calls,
        "exact_project_searches": exact_project_searches,
        "broad_or_unscoped_searches": broad_or_unscoped_searches,
        "candidate_surfacings": candidate_surfacings,
        "candidates_used": candidates_used,
        "candidates_ignored": candidates_ignored,
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
    parser.add_argument(
        "--prune-report",
        action="store_true",
        help="List chronically surfaced-but-never-used memories as REVIEW candidates (report only)",
    )
    parser.add_argument(
        "--prune-min-surfaced",
        type=int,
        default=3,
        help="Minimum closed-window surfacings before a never-used memory is reported",
    )
    parser.add_argument(
        "--prune-limit",
        type=int,
        default=20,
        help="Maximum prune candidates to report",
    )
    args = parser.parse_args()

    events = load_events(args.log)
    if args.prune_report:
        report = {
            "note": (
                "REVIEW candidates only — never auto-delete. Inspect each id with "
                "memory_get before archiving or deleting."
            ),
            "criteria": {
                "window_seconds": args.followup_window_seconds,
                "min_surfaced": args.prune_min_surfaced,
                "used": 0,
            },
            "prune_candidates": prune_candidates(
                events,
                window_seconds=args.followup_window_seconds,
                min_surfaced=args.prune_min_surfaced,
                limit=args.prune_limit,
            ),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    summary = summarize_events(
        events,
        followup_window_seconds=args.followup_window_seconds,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
