"""Dashboard/CLI time-window resolution."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

# Map a range key to the number of days back, or None for "all".
_RANGE_DAYS = {"today": 0, "7d": 7, "30d": 30, "all": None}


def day_start(dt: datetime) -> float:
    return datetime(dt.year, dt.month, dt.day).timestamp()


def since_for(range_key: str) -> Optional[float]:
    """Back-compat helper: lower bound only (used by the CLI)."""
    return resolve_range(range_key)[0]


def resolve_range(
    range_key: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """Resolve a range key (+ optional custom YYYY-MM-DD bounds) to (since, until).

    Recognised keys: today, 7d, 30d, all, custom. For ``custom`` the inclusive
    ``start``/``end`` dates are interpreted in local time; ``end`` is expanded to
    the start of the following day, making the returned window half-open.
    """
    now = datetime.now()
    if range_key == "custom":
        since = day_start(datetime.strptime(start, "%Y-%m-%d")) if start else None
        until = None
        if end:
            until = day_start(datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1))
        return since, until
    if range_key == "all":
        return None, None
    if range_key == "today":
        return day_start(now), None
    days = _RANGE_DAYS.get(range_key, 7) or 7
    return (now - timedelta(days=days)).timestamp(), None
