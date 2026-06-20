"""Per-day rollup of core character metrics (C1 performance optimization).

The dashboard's headline numbers (字数 / 活跃时长 / 会话 / 速度) and the per-day
character series previously scanned the whole ``char_events`` table on every
range change -- fine for a few weeks of data, painful for the "全部" range once a
user has months of history. This module materializes the metrics of each *closed*
local day into ``daily_metrics`` (incrementally, like ``segment.build_words``), so
a range aggregation becomes "sum a handful of rows + compute only the open day".

Day-boundary semantics (intentional, see CHANGELOG): a session that spans local
midnight is split into two days, and ``peak60`` is the busiest 60s window *within*
a day. Both differences are negligible in practice and make per-day numbers add up
cleanly. The exactly-additive metrics (chars / backspace / delete) are unaffected.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Mirror efficiency()'s commit-timing guard so materialized and live numbers match.
_MIN_SEC_PER_CHAR = 0.12
_PEAK_WINDOW = 60.0


def _metrics_from_ts(ts_list: List[float], back: int, dele: int,
                     session_gap: float) -> Dict:
    """Compute one window's metrics from its sorted commit timestamps. Mirrors
    the session/active/peak logic in :func:`stats.efficiency` exactly."""
    n = len(ts_list)
    if not n:
        return {"chars": 0, "backspace": back, "delete": dele,
                "active_sec": 0.0, "sessions": 0, "peak60": 0}
    sessions = 1
    active = 0.0
    seg_start = ts_list[0]
    seg_count = 1
    prev = ts_list[0]
    for t in ts_list[1:]:
        if t - prev > session_gap:
            active += max(prev - seg_start, seg_count * _MIN_SEC_PER_CHAR)
            sessions += 1
            seg_start, seg_count = t, 1
        else:
            seg_count += 1
        prev = t
    active += max(prev - seg_start, seg_count * _MIN_SEC_PER_CHAR)
    peak = 0
    left = 0
    for right in range(n):
        while ts_list[right] - ts_list[left] > _PEAK_WINDOW:
            left += 1
        peak = max(peak, right - left + 1)
    return {"chars": n, "backspace": back, "delete": dele,
            "active_sec": active, "sessions": sessions, "peak60": peak}


def _window_live(con, lo: float, hi: float, session_gap: float) -> Dict:
    """Compute metrics directly from the events in [lo, hi) (the open day and any
    partial range edges that the rollup cannot cover)."""
    ts = [r[0] for r in con.execute(
        "SELECT ts FROM char_events WHERE ts>=? AND ts<? ORDER BY ts", (lo, hi)
    ).fetchall()]
    kinds = dict(con.execute(
        "SELECT kind, COUNT(*) FROM key_events WHERE ts>=? AND ts<? GROUP BY kind",
        (lo, hi),
    ).fetchall())
    return _metrics_from_ts(ts, kinds.get("backspace", 0), kinds.get("delete", 0),
                            session_gap)


# ---- day-boundary helpers (local time) ------------------------------------
def _floor_midnight(ts: float) -> float:
    dt = datetime.fromtimestamp(ts)
    return datetime(dt.year, dt.month, dt.day).timestamp()


def _ceil_midnight(ts: float) -> float:
    m = _floor_midnight(ts)
    return m if m == ts else (datetime.fromtimestamp(m) + timedelta(days=1)).timestamp()


def _day_bounds(day_iso: str) -> Tuple[float, float]:
    d0 = datetime.strptime(day_iso, "%Y-%m-%d")
    return d0.timestamp(), (d0 + timedelta(days=1)).timestamp()


# ---- incremental materialization ------------------------------------------
def build_daily_metrics(db, session_gap: float = 60.0) -> None:
    """Materialize metrics for every closed local day not yet rolled up. Cheap
    no-op once the watermark reaches yesterday (a single meta read)."""
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    done = db.get_meta("metrics_done_through")
    if done is not None and done >= yesterday:
        return
    today_iso = today.isoformat()
    con = db.connect()
    try:
        if done:
            rows = con.execute(
                "SELECT DISTINCT date(ts,'unixepoch','localtime') d FROM char_events "
                "WHERE date(ts,'unixepoch','localtime') > ? "
                "AND date(ts,'unixepoch','localtime') < ? ORDER BY d",
                (done, today_iso),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT DISTINCT date(ts,'unixepoch','localtime') d FROM char_events "
                "WHERE date(ts,'unixepoch','localtime') < ? ORDER BY d", (today_iso,),
            ).fetchall()
        for (d,) in rows:
            d0, d1 = _day_bounds(d)
            m = _window_live(con, d0, d1, session_gap)
            con.execute(
                "INSERT INTO daily_metrics"
                "(day,chars,backspace,delete_n,active_sec,sessions,peak60) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET "
                "chars=excluded.chars,backspace=excluded.backspace,"
                "delete_n=excluded.delete_n,active_sec=excluded.active_sec,"
                "sessions=excluded.sessions,peak60=excluded.peak60",
                (d, m["chars"], m["backspace"], m["delete"],
                 m["active_sec"], m["sessions"], m["peak60"]),
            )
        con.commit()
        db.set_meta("metrics_done_through", yesterday)
    finally:
        con.close()


# ---- fast range aggregation -----------------------------------------------
def _zero() -> Dict:
    return {"chars": 0, "backspace": 0, "delete": 0,
            "active_sec": 0.0, "sessions": 0, "peak60": 0}


def _merge(a: Dict, b: Dict) -> Dict:
    return {
        "chars": a["chars"] + b["chars"],
        "backspace": a["backspace"] + b["backspace"],
        "delete": a["delete"] + b["delete"],
        "active_sec": a["active_sec"] + b["active_sec"],
        "sessions": a["sessions"] + b["sessions"],
        "peak60": max(a["peak60"], b["peak60"]),
    }


def _plan(db, con, since: Optional[float], until: Optional[float]):
    """Split [since, until) into (materialized full-day span, live edge windows).

    Returns (day_lo_iso, day_hi_iso, live_windows). The day span is inclusive of
    both ends; either may be None when no whole closed day fits."""
    now = time.time()
    if since is None or until is None:
        row = con.execute("SELECT MIN(ts), MAX(ts) FROM char_events").fetchone()
        first, last = (row or (None, None))
    else:
        first = last = None
    # An open upper bound means "everything", including any event dated in the
    # future relative to wall-clock now (the old WHERE clause had no upper bound).
    hi = until if until is not None else max(now, (last + 1.0) if last is not None else now)
    if since is None:
        if first is None:
            return None, None, []
        eff_lo = first
    else:
        eff_lo = since
    if hi <= eff_lo:
        return None, None, []
    today_mid = _floor_midnight(now)
    a = _ceil_midnight(eff_lo)
    b = min(_floor_midnight(hi), today_mid)      # exclusive end of full-day span
    if b <= a:
        return None, None, [(eff_lo, hi)]
    live: List[Tuple[float, float]] = []
    if eff_lo < a:
        live.append((eff_lo, a))
    if hi > b:
        live.append((b, hi))
    day_lo = date.fromtimestamp(a).isoformat()
    day_hi = date.fromtimestamp(b - 1).isoformat()   # last full day = b - 1 day
    return day_lo, day_hi, live


def aggregate(db, since: Optional[float], until: Optional[float],
              session_gap: float = 60.0) -> Dict:
    """Fast equivalent of efficiency() + the char/edit counts, via the rollup."""
    build_daily_metrics(db, session_gap)
    con = db.connect()
    try:
        day_lo, day_hi, live = _plan(db, con, since, until)
        agg = _zero()
        if day_lo is not None:
            row = con.execute(
                "SELECT COALESCE(SUM(chars),0),COALESCE(SUM(backspace),0),"
                "COALESCE(SUM(delete_n),0),COALESCE(SUM(active_sec),0),"
                "COALESCE(SUM(sessions),0),COALESCE(MAX(peak60),0) "
                "FROM daily_metrics WHERE day>=? AND day<=?", (day_lo, day_hi),
            ).fetchone()
            agg = {"chars": row[0], "backspace": row[1], "delete": row[2],
                   "active_sec": row[3] or 0.0, "sessions": row[4], "peak60": row[5]}
        for wlo, whi in live:
            if whi > wlo:
                agg = _merge(agg, _window_live(con, wlo, whi, session_gap))
    finally:
        con.close()
    active_minutes = agg["active_sec"] / 60.0
    cpm = (agg["chars"] / active_minutes) if active_minutes > 0 else 0.0
    peak_cpm = max(agg["peak60"] * (60.0 / _PEAK_WINDOW), cpm)
    return {
        "chars": agg["chars"],
        "backspace": agg["backspace"],
        "delete": agg["delete"],
        "active_minutes": round(active_minutes, 1),
        "sessions": agg["sessions"],
        "cpm": round(cpm, 1),
        "peak_cpm": round(peak_cpm, 1),
    }


def daily_series(db, since: Optional[float],
                 until: Optional[float] = None) -> List[Tuple[str, int]]:
    """Per-day character counts, from the rollup for whole closed days plus a
    live count for partial edges / the open day. Same shape as the old query."""
    build_daily_metrics(db)
    con = db.connect()
    try:
        day_lo, day_hi, live = _plan(db, con, since, until)
        counts: Dict[str, int] = {}
        if day_lo is not None:
            for d, c in con.execute(
                "SELECT day, chars FROM daily_metrics WHERE day>=? AND day<=? AND chars>0",
                (day_lo, day_hi),
            ).fetchall():
                counts[d] = counts.get(d, 0) + c
        for wlo, whi in live:
            if whi <= wlo:
                continue
            for d, c in con.execute(
                "SELECT date(ts,'unixepoch','localtime') d, COUNT(*) FROM char_events "
                "WHERE ts>=? AND ts<? GROUP BY d", (wlo, whi),
            ).fetchall():
                counts[d] = counts.get(d, 0) + c
    finally:
        con.close()
    return sorted(counts.items())
