"""Temporal intent detection for search queries.

Detects time-related language in queries and returns structured date ranges
to narrow search results. Pure regex + stdlib datetime — no external deps.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

_WORD_TO_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "few": 3, "couple": 2, "several": 4,
}

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_MONTH_PATTERN = "|".join(_MONTH_NAMES.keys())
_WEEKDAY_PATTERN = "|".join(_WEEKDAY_NAMES.keys())
_NUM_PATTERN = r"(?:\d+|" + "|".join(_WORD_TO_NUM.keys()) + ")"

# Regex patterns (compiled once)
_MONTH_RANGE = re.compile(
    rf"(?:from\s+|between\s+)({_MONTH_PATTERN})\s+(?:to|and)\s+({_MONTH_PATTERN})",
    re.IGNORECASE,
)
_IN_MONTH = re.compile(
    rf"(?:in|from|during|month\s+of)\s+({_MONTH_PATTERN})\b",
    re.IGNORECASE,
)
_YESTERDAY = re.compile(r"\byesterday\b", re.IGNORECASE)
_LAST_PERIOD = re.compile(r"\blast\s+(week|month|year)\b", re.IGNORECASE)
_LAST_WEEKDAY = re.compile(
    rf"\blast\s+({_WEEKDAY_PATTERN})\b", re.IGNORECASE,
)
_N_AGO = re.compile(
    rf"\b({_NUM_PATTERN})\s+(day|week|month|year)s?\s+ago\b", re.IGNORECASE,
)
_PAST_N = re.compile(
    rf"\b(?:past|last)\s+({_NUM_PATTERN})\s+(day|week|month|year)s?\b", re.IGNORECASE,
)
_THIS_PERIOD = re.compile(r"\bthis\s+(week|month|year)\b", re.IGNORECASE)
_RECENCY = re.compile(r"\b(?:recently|latest|most\s+recent|newest)\b", re.IGNORECASE)


@dataclass
class TemporalIntent:
    since: Optional[str] = None
    until: Optional[str] = None
    recency_boost: bool = False
    suppress_graph: bool = False


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _iso_end(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT23:59:59")


def _parse_number(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return _WORD_TO_NUM.get(s.lower(), 1)


def _start_of_week(dt: datetime) -> datetime:
    """Monday of the week containing dt."""
    return dt - timedelta(days=dt.weekday())


def _end_of_week(dt: datetime) -> datetime:
    """Sunday of the week containing dt."""
    return _start_of_week(dt) + timedelta(days=6)


def _start_of_month(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _end_of_month(year: int, month: int) -> datetime:
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day, tzinfo=timezone.utc)


def detect_temporal_intent(
    query: str,
    reference_date: Optional[datetime] = None,
) -> Optional[TemporalIntent]:
    now = reference_date or datetime.now(timezone.utc)

    # 1. Month range (most specific)
    m = _MONTH_RANGE.search(query)
    if m:
        m1 = _MONTH_NAMES[m.group(1).lower()]
        m2 = _MONTH_NAMES[m.group(2).lower()]
        year = now.year
        # If end month is past current month, use previous year
        if m2 > now.month:
            year -= 1
        return TemporalIntent(
            since=_iso(_start_of_month(year, m1)),
            until=_iso(_end_of_month(year, m2)),
            suppress_graph=True,
        )

    # 2. Specific month
    m = _IN_MONTH.search(query)
    if m:
        month_num = _MONTH_NAMES[m.group(1).lower()]
        year = now.year if month_num <= now.month else now.year - 1
        return TemporalIntent(
            since=_iso(_start_of_month(year, month_num)),
            until=_iso(_end_of_month(year, month_num)),
            suppress_graph=True,
        )

    # 3. Yesterday
    if _YESTERDAY.search(query):
        yesterday = now - timedelta(days=1)
        return TemporalIntent(
            since=_iso(yesterday),
            until=_iso(yesterday),
            suppress_graph=True,
        )

    # 4. Last period (last week/month/year)
    m = _LAST_PERIOD.search(query)
    if m:
        # Check it's not "last N <unit>" (handled by _PAST_N)
        period = m.group(1).lower()
        # Make sure the word before "last" isn't a number context
        if period == "week":
            mon = _start_of_week(now) - timedelta(weeks=1)
            return TemporalIntent(
                since=_iso(mon),
                until=_iso(_end_of_week(mon)),
                suppress_graph=True,
            )
        elif period == "month":
            if now.month == 1:
                s = _start_of_month(now.year - 1, 12)
                e = _end_of_month(now.year - 1, 12)
            else:
                s = _start_of_month(now.year, now.month - 1)
                e = _end_of_month(now.year, now.month - 1)
            return TemporalIntent(since=_iso(s), until=_iso(e), suppress_graph=True)
        elif period == "year":
            return TemporalIntent(
                since=_iso(datetime(now.year - 1, 1, 1, tzinfo=timezone.utc)),
                until=_iso(datetime(now.year - 1, 12, 31, tzinfo=timezone.utc)),
                suppress_graph=True,
            )

    # 5. Last weekday
    m = _LAST_WEEKDAY.search(query)
    if m:
        target_wd = _WEEKDAY_NAMES[m.group(1).lower()]
        days_back = (now.weekday() - target_wd) % 7
        if days_back == 0:
            days_back = 7
        target = now - timedelta(days=days_back)
        return TemporalIntent(
            since=_iso(target), until=_iso(target), suppress_graph=True,
        )

    # 6. N ago
    m = _N_AGO.search(query)
    if m:
        n = _parse_number(m.group(1))
        unit = m.group(2).lower()
        if unit == "day":
            target = now - timedelta(days=n)
            return TemporalIntent(
                since=_iso(target), until=_iso(target), suppress_graph=True,
            )
        elif unit == "week":
            target_week = now - timedelta(weeks=n)
            mon = _start_of_week(target_week)
            return TemporalIntent(
                since=_iso(mon), until=_iso(_end_of_week(mon)), suppress_graph=True,
            )
        elif unit == "month":
            target = now - timedelta(days=n * 30)
            return TemporalIntent(
                since=_iso(target), until=_iso(target), suppress_graph=True,
            )
        elif unit == "year":
            target = datetime(now.year - n, now.month, now.day, tzinfo=timezone.utc)
            return TemporalIntent(
                since=_iso(target), until=_iso(target), suppress_graph=True,
            )

    # 7. Past N
    m = _PAST_N.search(query)
    if m:
        n = _parse_number(m.group(1))
        unit = m.group(2).lower()
        if unit == "day":
            since = now - timedelta(days=n)
        elif unit == "week":
            since = now - timedelta(weeks=n)
        elif unit == "month":
            since = now - timedelta(days=n * 30)
        elif unit == "year":
            since = datetime(now.year - n, now.month, now.day, tzinfo=timezone.utc)
        else:
            return None
        return TemporalIntent(since=_iso(since), suppress_graph=True)

    # 8. This period
    m = _THIS_PERIOD.search(query)
    if m:
        period = m.group(1).lower()
        if period == "week":
            return TemporalIntent(
                since=_iso(_start_of_week(now)), suppress_graph=True,
            )
        elif period == "month":
            return TemporalIntent(
                since=_iso(_start_of_month(now.year, now.month)),
                suppress_graph=True,
            )
        elif period == "year":
            return TemporalIntent(
                since=_iso(datetime(now.year, 1, 1, tzinfo=timezone.utc)),
                suppress_graph=True,
            )

    # 9. Recency signals (no date range, just flag)
    if _RECENCY.search(query):
        return TemporalIntent(recency_boost=True)

    return None
