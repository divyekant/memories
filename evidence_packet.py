"""Agent-facing evidence packet construction for search results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _memory_date(memory: dict[str, Any]) -> str:
    return str(
        memory.get("document_at")
        or memory.get("updated_at")
        or memory.get("created_at")
        or memory.get("timestamp")
        or ""
    )


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _score(memory: dict[str, Any]) -> float:
    value = memory.get("similarity", memory.get("rrf_score", 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compact_memory(memory: dict[str, Any], *, relation: str) -> dict[str, Any]:
    return {
        "id": memory.get("id"),
        "source": memory.get("source", ""),
        "date": _memory_date(memory),
        "text": memory.get("text", ""),
        "relation": relation,
        "score": _score(memory),
        "is_latest": bool(memory.get("is_latest", False)),
        "archived": bool(memory.get("archived", False)),
    }


def _rank_key(memory: dict[str, Any]) -> tuple[int, datetime, float]:
    parsed = _parse_date(_memory_date(memory)) or datetime.min.replace(tzinfo=timezone.utc)
    return (1 if memory.get("is_latest") else 0, parsed, _score(memory))


def build_evidence_packet(query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic evidence packet over search results.

    The packet does not invent a natural-language answer. It highlights the
    latest candidate and preserves older evidence so the agent can reason about
    currency, supersession, and missing evidence explicitly.
    """
    if not results:
        return {
            "current_answer": None,
            "supporting_memories": [],
            "older_conflicting_memories": [],
            "source_date_trail": [],
            "confidence": {
                "level": "missing",
                "reasons": ["No memories were retrieved for this query."],
            },
            "follow_up_queries": [
                query,
                f"latest {query}",
                f"current decision {query}",
            ],
        }

    ranked = sorted(results, key=_rank_key, reverse=True)
    current = ranked[0]
    current_date = _parse_date(_memory_date(current))

    supporting = []
    older = []
    for memory in ranked[1:]:
        memory_date = _parse_date(_memory_date(memory))
        if current_date and memory_date and memory_date < current_date:
            older.append(_compact_memory(memory, relation="older"))
        elif memory.get("archived"):
            older.append(_compact_memory(memory, relation="archived"))
        else:
            supporting.append(_compact_memory(memory, relation="supporting"))

    reasons = []
    if _memory_date(current):
        reasons.append("Current candidate has a source date.")
    else:
        reasons.append("Current candidate has no source date.")
    if older:
        reasons.append("Packet includes older evidence that may conflict or be superseded.")
    if current.get("is_latest"):
        reasons.append("Current candidate is explicitly marked is_latest.")

    if not _memory_date(current):
        level = "low"
    elif older:
        level = "medium"
    else:
        level = "high"

    trail = [_compact_memory(current, relation="current")]
    trail.extend(supporting)
    trail.extend(older)

    return {
        "current_answer": _compact_memory(current, relation="current"),
        "supporting_memories": supporting[:5],
        "older_conflicting_memories": older[:5],
        "source_date_trail": trail[:10],
        "confidence": {
            "level": level,
            "reasons": reasons,
        },
        "follow_up_queries": [
            f"latest {query}",
            f"current {query}",
            f"what changed about {query}",
        ],
    }
