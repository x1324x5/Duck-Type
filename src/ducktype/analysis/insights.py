"""Higher-level board insights added in 0.3.0:

* ``vocab_growth``   -- lifetime cumulative distinct words/characters (your
  vocabulary visibly growing over time).
* ``app_efficiency`` -- per-application typing speed, so you can see where you
  actually write fastest.
* ``weekday_rhythm`` -- weekday-vs-weekend output rhythm.

These are observation layers over the same ``char_events`` / word rollups the
rest of ``stats`` uses; none of them change the headline numbers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from . import metrics
from . import segment
from .statutil import _where, pretty_app
from .char_stats import per_app
from .word_stats import _tail_word_pos


def vocab_growth(db, run_gap: float, max_points: int = 360) -> Dict:
    """Lifetime cumulative count of distinct multi-char words and distinct
    characters, by the day each was first written. This is intentionally
    independent of the dashboard range -- it tells the long story of a growing
    vocabulary, like the contribution calendar tells the yearly rhythm.

    Points are downsampled to at most ``max_points`` so an account with years of
    history still returns a compact series (the cumulative curve is monotone, so
    sampling never hides growth)."""
    segment.build_words(db, run_gap)
    con = db.connect()
    try:
        word_first = con.execute(
            "SELECT word, MIN(day) FROM word_freq_daily "
            "WHERE length(word)>=2 GROUP BY word").fetchall()
        char_first = con.execute(
            "SELECT ch, MIN(date(ts,'unixepoch','localtime')) FROM char_events "
            "GROUP BY ch").fetchall()
    finally:
        con.close()

    # First-appearance day per word, folding in the still-open trailing run so a
    # word coined today is not missing from the curve.
    word_day: Dict[str, str] = {w: d for w, d in word_first if d}
    tail_wc, _pc, _pw = _tail_word_pos(db, run_gap)
    for day, dwc in tail_wc.items():
        for w in dwc:
            if len(w) >= 2 and (w not in word_day or day < word_day[w]):
                word_day[w] = day

    new_words: Dict[str, int] = {}
    for d in word_day.values():
        new_words[d] = new_words.get(d, 0) + 1
    new_chars: Dict[str, int] = {}
    for _ch, d in char_first:
        if d:
            new_chars[d] = new_chars.get(d, 0) + 1

    days = sorted(set(new_words) | set(new_chars))
    if not days:
        return {"points": [], "total_words": 0, "total_chars": 0,
                "first_day": None, "span_days": 0}

    points: List[Dict] = []
    cum_w = cum_c = 0
    for d in days:
        cum_w += new_words.get(d, 0)
        cum_c += new_chars.get(d, 0)
        points.append({"date": d, "words": cum_w, "chars": cum_c})

    # Downsample (keep first + last; sample the middle) when the series is long.
    if len(points) > max_points:
        step = len(points) / max_points
        idx = sorted({int(i * step) for i in range(max_points)} | {len(points) - 1})
        points = [points[i] for i in idx]

    first = datetime.strptime(days[0], "%Y-%m-%d")
    last = datetime.strptime(days[-1], "%Y-%m-%d")
    return {
        "points": points,
        "total_words": cum_w,
        "total_chars": cum_c,
        "first_day": days[0],
        "span_days": (last - first).days + 1,
    }


def app_efficiency(db, since: Optional[float], until: Optional[float],
                   session_gap: float = 60.0, n: int = 8,
                   min_chars: int = 60) -> List[Dict]:
    """Per-application typing speed within the window.

    For each of the busiest apps we recompute the same session/active-minute
    model ``efficiency`` uses (so the cpm is comparable to the headline speed),
    restricted to that app's commits. Apps with too few characters to give a
    meaningful rate are dropped (``min_chars``)."""
    apps = per_app(db, since, max(n * 2, 12), until)
    w, p = _where(since, until)
    out: List[Dict] = []
    con = db.connect()
    try:
        for app, total in apps:
            if total < min_chars:
                continue
            if app == "(unknown)":
                clause, params = "app IS NULL", []
            else:
                clause, params = "app=?", [app]
            extra = (" AND " + w[len(" WHERE "):]) if w else ""
            ts = [r[0] for r in con.execute(
                f"SELECT ts FROM char_events WHERE {clause}{extra} ORDER BY ts",
                (*params, *p)).fetchall()]
            if len(ts) < min_chars:
                continue
            m = metrics._metrics_from_ts(ts, 0, 0, session_gap)
            active_min = m["active_sec"] / 60.0
            cpm = (m["chars"] / active_min) if active_min > 0 else 0.0
            out.append({
                "app": pretty_app(app),
                "chars": m["chars"],
                "cpm": round(cpm, 1),
                "active_minutes": round(active_min, 1),
                "sessions": m["sessions"],
            })
    finally:
        con.close()
    out.sort(key=lambda r: r["cpm"], reverse=True)
    return out[:n]


_WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def weekday_rhythm(db, since: Optional[float], until: Optional[float] = None) -> Dict:
    """Output rhythm by weekday, plus a weekday-vs-weekend per-active-day average.

    Averaging over *active* days (not calendar days) keeps the comparison fair
    when, say, weekends are simply skipped more often than they are low-output."""
    w, p = _where(since, until)
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT date(ts,'unixepoch','localtime') d, COUNT(*) c "
            f"FROM char_events{w} GROUP BY d", p).fetchall()
    finally:
        con.close()

    totals = [0] * 7
    active = [0] * 7
    for d, c in rows:
        try:
            wd = datetime.strptime(d, "%Y-%m-%d").weekday()
        except (ValueError, TypeError):
            continue
        totals[wd] += int(c)
        if c:
            active[wd] += 1

    by_weekday = [{
        "weekday": _WD[i],
        "total": totals[i],
        "active_days": active[i],
        "avg": round(totals[i] / active[i], 1) if active[i] else 0.0,
    } for i in range(7)]

    wk_total = sum(totals[0:5]); wk_days = sum(active[0:5])
    we_total = sum(totals[5:7]); we_days = sum(active[5:7])
    weekday_avg = round(wk_total / wk_days, 1) if wk_days else 0.0
    weekend_avg = round(we_total / we_days, 1) if we_days else 0.0
    ratio = round(weekend_avg / weekday_avg, 2) if weekday_avg else None
    return {
        "by_weekday": by_weekday,
        "weekday_avg": weekday_avg,
        "weekend_avg": weekend_avg,
        "weekday_total": wk_total,
        "weekend_total": we_total,
        "ratio": ratio,
        "has_data": bool(sum(totals)),
    }
