"""Shared helpers for the stat submodules.

``stats.py`` was split into cohesive sibling modules (0.3.0). This module holds
the small primitives every group needs -- time-window SQL, day-key bounds, app
name prettifying and the calendar/word label tables -- so the other modules
don't have to import each other just for a helper. See ``stats.py`` for the
facade that re-exports the whole public surface.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple


# ---- time-window resolution ----------------------------------------------
def _where(since: Optional[float], until: Optional[float] = None):
    clauses, params = [], []
    if since is not None:
        clauses.append("ts>=?"); params.append(since)
    if until is not None:
        clauses.append("ts<?"); params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


def _day_bounds(since: Optional[float], until: Optional[float]):
    """Return inclusive ('YYYY-MM-DD', 'YYYY-MM-DD') day keys covering
    [since, until), or (None, None) for an unbounded window."""
    if since is None and until is None:
        return None, None
    start = (datetime.fromtimestamp(since).strftime("%Y-%m-%d")
             if since is not None else "0000-01-01")
    if until is not None:
        end = datetime.fromtimestamp(until - 1).strftime("%Y-%m-%d")
    else:
        end = datetime.now().strftime("%Y-%m-%d")
    return start, end


def pretty_app(name: Optional[str]) -> Optional[str]:
    """Display name for an app/process: drop a trailing ``.exe`` (case-insensitive)
    so reports read '主要输入场景是 Obsidian' rather than 'Obsidian.exe'. The raw
    name is kept in the DB / drill-down lookups; this is display-only."""
    if not name:
        return name
    return name[:-4] if name.lower().endswith(".exe") else name


_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_PERIOD_WORD = {"today": "今天", "week": "本周", "month": "本月",
                "year": "今年", "custom": "这段时间"}
_PREV_WORD = {"today": "昨天", "week": "上周", "month": "上月",
              "year": "去年", "custom": "上一周期"}


def _weekday_cn(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    try:
        return _WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except Exception:
        return None


def _hour_window_label(start_h: int) -> str:
    if start_h < 5:
        return "凌晨"
    if start_h < 8:
        return "清晨"
    if start_h < 11:
        return "上午"
    if start_h < 13:
        return "中午"
    if start_h < 17:
        return "下午"
    if start_h < 19:
        return "傍晚"
    if start_h < 23:
        return "晚上"
    return "深夜"


# A hidden "blank" ticker line. It is a zero-width space, so str.strip() keeps
# it (it is not ASCII/Unicode whitespace) and load_phrases() won't drop it, yet
# the banner renders empty -- a quiet little easter egg. Landing on it unlocks an
# achievement (see gamify + the frontend ticker). Lives here so both gamify and
# ticker can reference it without importing each other.
EASTER_EGG_QUOTE = chr(0x200b)  # U+200B zero-width space
