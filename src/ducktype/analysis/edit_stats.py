"""Edit / deletion accounting and typing-speed (efficiency) metrics."""
from __future__ import annotations

from typing import Dict, List, Optional

from .statutil import _where


def _effective_deletions(
    db,
    since: Optional[float],
    until: Optional[float] = None,
    session_gap: float = 60.0,
    del_window: float = 10.0,
) -> int:
    """Count backspace/delete keys that plausibly removed a just-typed Han char.

    Each committed Han character pushes onto a per-app stack; a deletion pops
    one. Two guards keep this honest:
      * a session gap (``session_gap``) clears the stack -- deletions long after
        the last character aren't fixing it;
      * a freshness window (``del_window``) drops characters that have been
        sitting committed for a while: a backspace now is editing other content
        (e.g. trailing English, which we never capture), not deleting that Han
        char. Without this, holding backspace over non-Han text would still be
        charged against earlier Han characters and inflate the edit ratio.
    """
    cw, cp = _where(since, until)
    kw, kp = _where(since, until)
    con = db.connect()
    try:
        chars = [
            (ts, 0, rowid, ch, app)
            for rowid, ts, ch, app in con.execute(
                f"SELECT id, ts, ch, app FROM char_events{cw}", cp
            ).fetchall()
        ]
        keys = [
            (ts, 1, rowid, kind, app)
            for rowid, ts, kind, app in con.execute(
                f"SELECT id, ts, kind, app FROM key_events{kw}", kp
            ).fetchall()
            if kind in ("backspace", "delete")
        ]
    finally:
        con.close()

    stacks: Dict[str, List[float]] = {}
    last_ts: Dict[str, float] = {}
    effective = 0
    for ts, order, _rowid, value, app in sorted(chars + keys):
        key = app or ""
        if key in last_ts and ts - last_ts[key] > session_gap:
            stacks[key] = []
        last_ts[key] = ts

        if order == 0:
            stacks.setdefault(key, []).append(ts)
        else:
            stack = stacks.setdefault(key, [])
            # Forget Han chars old enough to be "settled" (top is newest).
            while stack and ts - stack[-1] > del_window:
                stack.pop()
            if stack:
                stack.pop()
                effective += 1
    return effective


def edits(
    db,
    since: Optional[float],
    until: Optional[float] = None,
    session_gap: float = 60.0,
) -> Dict[str, float]:
    w, p = _where(since, until)
    con = db.connect()
    try:
        kinds = dict(
            con.execute(
                f"SELECT kind, COUNT(*) FROM key_events{w} GROUP BY kind", p
            ).fetchall()
        )
        chars = con.execute(f"SELECT COUNT(*) FROM char_events{w}", p).fetchone()[0]
    finally:
        con.close()
    back = kinds.get("backspace", 0)
    dele = kinds.get("delete", 0)
    enter = kinds.get("enter", 0)
    raw_edits = back + dele
    edits_total = _effective_deletions(db, since, until, session_gap)
    ratio = (edits_total / chars) if chars else 0.0
    return {
        "chars": chars,
        "backspace": back,
        "delete": dele,
        "enter": enter,
        "raw_edits": raw_edits,
        "edits": edits_total,
        "edit_ratio": round(ratio, 4),
    }


def efficiency(db, since: Optional[float], session_gap: float = 60.0,
               until: Optional[float] = None,
               peak_window: float = 60.0) -> Dict[str, float]:
    """Typing-speed metrics.

    Important: the IME commits a whole word at once, so every character of a
    multi-character word shares one timestamp -- we have *commit* timing, not
    per-keystroke timing. Metrics are therefore built to be robust to that:

    * ``cpm`` (average speed) = characters / active minutes, where a session's
      active time is its time *span* (last-first) but never less than a tiny
      per-character minimum. The floor matters because an IME often commits a
      whole word -- or several characters -- at virtually one instant: the raw
      span of such a burst is ~0, which would divide a real character count by
      ~0 and explode cpm to absurd values (seen as e.g. 120,000+ cpm). Charging
      each committed character at least ``MIN_SEC_PER_CHAR`` caps the apparent
      speed at a human-plausible ceiling (~60/MIN_SEC_PER_CHAR cpm) while leaving
      any normally-paced session completely unaffected (its span dominates).
    * ``peak_cpm`` = the most characters committed in any ``peak_window``-second
      sliding window, expressed per minute (the "best minute"). A count over a
      real time window cannot explode the way an instantaneous rate does when
      many characters land on the same timestamp.
    """
    # Served from the daily_metrics rollup (closed days summed) + a live compute
    # for the open day / partial edges, instead of scanning every timestamp in
    # the range. Sessions are per-day-independent and peak is the busiest 60s
    # window within a day (see analysis.metrics / CHANGELOG). cpm/active_minutes
    # use the same per-session floored-span logic as before.
    from . import metrics
    agg = metrics.aggregate(db, since, until, session_gap)
    return {
        "cpm": agg["cpm"],
        "active_minutes": agg["active_minutes"],
        "sessions": agg["sessions"],
        "peak_cpm": agg["peak_cpm"],
    }
