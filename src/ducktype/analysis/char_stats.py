"""Character-level board statistics (range-aware, fast).

Counts, top characters, the daily/hourly series, heatmap, contribution
calendar, richness trend and the per-application breakdown. None of these need
jieba; word/POS analytics live in ``word_stats``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import segment
from .statutil import _where


def total_chars(db, since: Optional[float], until: Optional[float] = None) -> int:
    w, p = _where(since, until)
    con = db.connect()
    try:
        return con.execute(f"SELECT COUNT(*) FROM char_events{w}", p).fetchone()[0]
    finally:
        con.close()


def top_chars(db, since: Optional[float], n: int = 50,
              until: Optional[float] = None) -> List[Tuple[str, int]]:
    w, p = _where(since, until)
    con = db.connect()
    try:
        return con.execute(
            f"SELECT ch, COUNT(*) c FROM char_events{w} GROUP BY ch ORDER BY c DESC LIMIT ?",
            (*p, n),
        ).fetchall()
    finally:
        con.close()


def daily(db, since: Optional[float], until: Optional[float] = None) -> List[Tuple[str, int]]:
    # Served from the daily_metrics rollup (closed days) + a live count for the
    # open day / partial edges. See analysis.metrics (C1).
    from . import metrics
    return metrics.daily_series(db, since, until)


def heatmap(db, since: Optional[float], until: Optional[float] = None) -> List[List[int]]:
    """7x24 matrix; row 0 = Sunday (SQLite %w), col 0 = hour 0."""
    w, p = _where(since, until)
    grid = [[0] * 24 for _ in range(7)]
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT CAST(strftime('%w', ts,'unixepoch','localtime') AS INT) dow, "
            f"CAST(strftime('%H', ts,'unixepoch','localtime') AS INT) hr, COUNT(*) c "
            f"FROM char_events{w} GROUP BY dow, hr",
            p,
        ).fetchall()
    finally:
        con.close()
    for dow, hr, c in rows:
        if dow is not None and hr is not None:
            grid[int(dow)][int(hr)] = c
    return grid


def richness_trend(db, since: Optional[float],
                   until: Optional[float] = None) -> List[Dict]:
    """Per-day vocabulary richness: distinct characters / total characters.

    A higher ratio means more varied writing that day; a low ratio means heavy
    repetition. Days with no input are simply absent (the chart skips gaps)."""
    w, p = _where(since, until)
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT date(ts,'unixepoch','localtime') d, COUNT(*) total, "
            f"COUNT(DISTINCT ch) distinct_ FROM char_events{w} GROUP BY d ORDER BY d",
            p,
        ).fetchall()
    finally:
        con.close()
    return [{"date": d, "total": t, "distinct": dd,
             "ratio": round(dd / t, 4) if t else 0.0} for d, t, dd in rows]


def contrib_calendar(db, days: int = 364) -> Dict:
    """GitHub-style contribution calendar: per-day character counts over roughly
    the trailing year, aligned to whole weeks (columns start on Sunday). This is
    intentionally independent of the dashboard's selected range -- it always
    shows the last ~52 weeks so the yearly rhythm is visible at a glance."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days)
    start -= timedelta(days=int(start.strftime("%w")))   # back up to Sunday
    w, p = _where(start.timestamp(), None)
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT date(ts,'unixepoch','localtime') d, COUNT(*) c "
            f"FROM char_events{w} GROUP BY d", p,
        ).fetchall()
    finally:
        con.close()
    counts = {d: c for d, c in rows}
    cells: List[Dict] = []
    cur = start
    while cur <= today:
        ds = cur.strftime("%Y-%m-%d")
        cells.append({"date": ds, "count": counts.get(ds, 0)})
        cur += timedelta(days=1)
    vals = [c["count"] for c in cells if c["count"] > 0]
    return {
        "start": start.strftime("%Y-%m-%d"),
        "end": today.strftime("%Y-%m-%d"),
        "cells": cells,
        "weeks": (len(cells) + 6) // 7,
        "max": max(vals) if vals else 0,
        "total": sum(c["count"] for c in cells),
        "active_days": len(vals),
    }


def timeseries(db, since: Optional[float], until: Optional[float] = None,
               bucket: str = "hour", cap: int = 2000) -> Dict:
    """Dense, zero-filled character counts bucketed by hour or day.

    The series is filled across the whole window (or, when a side is unbounded,
    across the data's own min/max) so the chart never collapses gaps -- an idle
    hour shows up as a real zero, not a missing point. ``cap`` guards against an
    unbounded all-time hourly request producing tens of thousands of points.
    """
    w, p = _where(since, until)
    key = ("date(ts,'unixepoch','localtime')" if bucket == "day"
           else "strftime('%Y-%m-%d %H', ts,'unixepoch','localtime')")
    con = db.connect()
    try:
        bounds = con.execute(
            f"SELECT MIN(ts), MAX(ts) FROM char_events{w}", p).fetchone()
        rows = con.execute(
            f"SELECT {key} k, COUNT(*) c FROM char_events{w} GROUP BY k", p
        ).fetchall()
    finally:
        con.close()
    counts = {k: c for k, c in rows}
    lo = since if since is not None else bounds[0]
    hi = until if until is not None else bounds[1]
    if lo is None or hi is None:
        return {"bucket": bucket, "points": []}

    cur = datetime.fromtimestamp(lo)
    end = datetime.fromtimestamp(hi)
    if bucket == "day":
        cur = datetime(cur.year, cur.month, cur.day)
        step, keyfmt = timedelta(days=1), "%Y-%m-%d"
    else:
        cur = cur.replace(minute=0, second=0, microsecond=0)
        step, keyfmt = timedelta(hours=1), "%Y-%m-%d %H"

    points = []
    while cur <= end and len(points) < cap:
        points.append({"ts": cur.timestamp(),
                       "count": counts.get(cur.strftime(keyfmt), 0)})
        cur += step
    return {"bucket": bucket, "points": points}


def per_app(db, since: Optional[float], n: int = 20,
            until: Optional[float] = None) -> List[Tuple[str, int]]:
    w, p = _where(since, until)
    con = db.connect()
    try:
        return con.execute(
            f"SELECT COALESCE(app,'(unknown)') a, COUNT(*) c "
            f"FROM char_events{w} GROUP BY a ORDER BY c DESC LIMIT ?",
            (*p, n),
        ).fetchall()
    finally:
        con.close()


def app_detail(db, app: str, since: Optional[float], run_gap: float,
               until: Optional[float] = None, n: int = 20) -> Dict:
    """Per-application breakdown: that app's top characters and top (2+ char)
    words within the window. Powers the drill-down when an app bar is clicked."""
    app = (app or "").strip()
    empty = {"app": app, "total": 0, "chars": [], "words": []}
    if not app:
        return empty
    # per_app reports NULL apps as "(unknown)"; map that back to an IS NULL match.
    clauses = ["app IS NULL"] if app == "(unknown)" else ["app=?"]
    params: List = [] if app == "(unknown)" else [app]
    if since is not None:
        clauses.append("ts>=?"); params.append(since)
    if until is not None:
        clauses.append("ts<?"); params.append(until)
    where = " WHERE " + " AND ".join(clauses)
    con = db.connect()
    try:
        total = con.execute(
            f"SELECT COUNT(*) FROM char_events{where}", params).fetchone()[0]
        crows = con.execute(
            f"SELECT ch, COUNT(*) c FROM char_events{where} "
            f"GROUP BY ch ORDER BY c DESC LIMIT ?", (*params, n)).fetchall()
        wrows = con.execute(
            f"SELECT ts, ch, app FROM char_events{where} ORDER BY ts", params
        ).fetchall()
    finally:
        con.close()
    wc: Dict[str, int] = {}
    for run in segment._runs_from_rows(wrows, run_gap):
        a, _b, _c = segment._segment_text(run)
        for k, v in a.items():
            wc[k] = wc.get(k, 0) + v
    words = sorted(((k, v) for k, v in wc.items() if len(k) >= 2),
                   key=lambda kv: kv[1], reverse=True)[:n]
    return {
        "app": app, "total": total,
        "chars": [{"ch": c, "count": k} for c, k in crows],
        "words": [{"word": w, "count": k} for w, k in words],
    }
