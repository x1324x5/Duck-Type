"""Comparison reports and dashboard-usage analytics (0.2.8).

Split out of ``stats.py`` to keep the new "compare two periods" and
"usage history" responsibilities in one focused place instead of growing the
already large core stats module. Everything here is read-only and delegates the
heavy lifting to ``stats`` (imported lazily to avoid an import cycle, since
``stats`` re-exports nothing from here).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


# ---- comparison reports ---------------------------------------------------
# A "period spec" is a small dict the dashboard sends:
#   {"kind": "week"}                      a named relative period
#   {"kind": "day", "day": "2026-06-01"}  one calendar day
#   {"kind": "custom", "start": ..., "end": ...}
_NAMED_LABEL = {
    "today": "今天", "yesterday": "昨天",
    "week": "本周", "last_week": "上周",
    "month": "本月", "last_month": "上月",
    "year": "今年", "last_year": "去年",
}


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1)


def compare_bounds(spec: dict) -> Tuple[Optional[float], Optional[float], str]:
    """Resolve a period spec to (since, until, label). ``until`` is half-open."""
    spec = spec or {}
    kind = spec.get("kind") or spec.get("period") or "today"
    now = datetime.now()
    if kind == "day":
        day = (spec.get("day") or "").strip()
        if not day:
            return None, None, "某一天"
        try:
            d = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return None, None, day
        s = datetime(d.year, d.month, d.day)
        return s.timestamp(), (s + timedelta(days=1)).timestamp(), day
    if kind == "custom":
        from .time_ranges import resolve_range
        since, until = resolve_range("custom", spec.get("start"), spec.get("end"))
        return since, until, "自定义区间"
    if kind in ("today", "yesterday"):
        s = datetime(now.year, now.month, now.day)
        if kind == "yesterday":
            s -= timedelta(days=1)
        return s.timestamp(), (s + timedelta(days=1)).timestamp(), _NAMED_LABEL[kind]
    if kind in ("week", "last_week"):
        monday = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())
        if kind == "last_week":
            monday -= timedelta(days=7)
        return monday.timestamp(), (monday + timedelta(days=7)).timestamp(), _NAMED_LABEL[kind]
    if kind in ("month", "last_month"):
        s = _month_start(now.year, now.month)
        if kind == "last_month":
            s = _month_start(now.year - 1, 12) if now.month == 1 else _month_start(now.year, now.month - 1)
        nxt = _month_start(s.year + 1, 1) if s.month == 12 else _month_start(s.year, s.month + 1)
        return s.timestamp(), nxt.timestamp(), _NAMED_LABEL[kind]
    if kind in ("year", "last_year"):
        y = now.year - (1 if kind == "last_year" else 0)
        return datetime(y, 1, 1).timestamp(), datetime(y + 1, 1, 1).timestamp(), _NAMED_LABEL[kind]
    # fall back to today
    s = datetime(now.year, now.month, now.day)
    return s.timestamp(), (s + timedelta(days=1)).timestamp(), "今天"


def _side_metrics(db, since, until, run_gap, session_gap) -> Dict:
    from . import stats
    chars = stats.total_chars(db, since, until)
    day_rows = stats.daily(db, since, until)
    eff = stats.efficiency(db, since, session_gap, until)
    e = stats.edits(db, since, until, session_gap)
    peak_hr, _c = stats._peak_hour(db, since, until)
    apps = stats.per_app(db, since, 50, until)
    app_total = sum(c for _a, c in apps) or 1
    top_app = stats.pretty_app(apps[0][0]) if apps else None
    top_app_share = round(apps[0][1] / app_total * 100, 1) if apps else 0.0
    best = max(day_rows, key=lambda r: r[1]) if day_rows else (None, 0)
    words = stats.top_words_daily(db, since, until, 12, run_gap)
    con = db.connect()
    try:
        w, pr = stats._where(since, until)
        distinct = con.execute(
            f"SELECT COUNT(DISTINCT ch) FROM char_events{w}", pr).fetchone()[0]
    finally:
        con.close()
    return {
        "chars": chars,
        "distinct_chars": distinct,
        "active_days": len(day_rows),
        "cpm": eff["cpm"],
        "active_minutes": eff["active_minutes"],
        "sessions": eff["sessions"],
        "edit_ratio": e["edit_ratio"],
        "peak_hour": peak_hr,
        "top_app": top_app,
        "top_app_share": top_app_share,
        "best_day": best[0],
        "best_day_count": best[1],
        "top_words": [{"word": w, "count": c} for w, c in words],
    }


def _pct_delta(a, b) -> Optional[float]:
    """Percent change of ``a`` relative to baseline ``b``."""
    if not b:
        return None
    return round((a - b) / b * 100, 1)


def report_compare(db, a_spec: dict, b_spec: dict,
                   run_gap: float, session_gap: float) -> Dict:
    """Compare two periods A (focus) vs B (baseline). Each side gets the same
    core metrics; deltas express A relative to B."""
    a_since, a_until, a_label = compare_bounds(a_spec)
    b_since, b_until, b_label = compare_bounds(b_spec)
    a = _side_metrics(db, a_since, a_until, run_gap, session_gap)
    b = _side_metrics(db, b_since, b_until, run_gap, session_gap)
    a["label"], b["label"] = a_label, b_label
    deltas = {
        "chars": _pct_delta(a["chars"], b["chars"]),
        "distinct_chars": _pct_delta(a["distinct_chars"], b["distinct_chars"]),
        "active_minutes": _pct_delta(a["active_minutes"], b["active_minutes"]),
        "cpm": _pct_delta(a["cpm"], b["cpm"]),
        # edit_ratio is a "lower is better" metric; expose the raw point change.
        "edit_ratio_pts": round((a["edit_ratio"] - b["edit_ratio"]) * 100, 1),
    }
    # Words that A wrote a lot relative to B (and vice-versa) make the comparison
    # feel alive: surface the biggest movers in either direction.
    movers = _word_movers(a["top_words"], b["top_words"])
    return {"a": a, "b": b, "deltas": deltas, "movers": movers,
            "narrative": _compare_narrative(a, b, deltas)}


def _word_movers(a_words: List[dict], b_words: List[dict]) -> Dict:
    bmap = {w["word"]: w["count"] for w in b_words}
    amap = {w["word"]: w["count"] for w in a_words}
    only_a = [w for w in a_words if w["word"] not in bmap][:6]
    only_b = [w for w in b_words if w["word"] not in amap][:6]
    return {"only_a": only_a, "only_b": only_b}


def _compare_narrative(a, b, deltas) -> str:
    da = deltas["chars"]
    head = f"「{a['label']}」你输入了 {a['chars']:,} 字，「{b['label']}」是 {b['chars']:,} 字"
    if da is None:
        head += "。"
    elif da == 0:
        head += "，两者基本持平。"
    else:
        head += f"，{'多' if da > 0 else '少'}了 {abs(da)}%。"
    bits = []
    if a["active_minutes"] and b["active_minutes"]:
        dm = deltas["active_minutes"]
        if dm:
            bits.append(f"活跃时长{'增加' if dm > 0 else '减少'} {abs(dm)}%")
    if a["top_app"]:
        bits.append(f"主力应用是 {a['top_app']}")
    if bits:
        head += "，".join(bits) + "。"
    return head


# ---- dashboard usage history ----------------------------------------------
def _local_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def dashboard_usage(db, days: int = 30) -> Dict:
    """Aggregate the dashboard/mini open log into the usage-history panel payload.

    All-time totals + a per-day series for the last ``days`` days + hour/weekday
    distributions + the most recent opens."""
    try:
        days = max(7, min(180, int(days)))
    except (TypeError, ValueError):
        days = 30
    opens = db.dashboard_opens(None)
    total = len(opens)
    mini = sum(1 for _ts, k in opens if k == "mini")
    dash = total - mini
    by_hour = [0] * 24
    by_weekday = [0] * 7
    per_date: Dict[str, Dict[str, int]] = {}
    active_dates = set()
    for ts, kind in opens:
        dt = datetime.fromtimestamp(ts)
        by_hour[dt.hour] += 1
        by_weekday[dt.weekday()] += 1
        d = dt.strftime("%Y-%m-%d")
        active_dates.add(d)
        slot = per_date.setdefault(d, {"total": 0, "mini": 0})
        slot["total"] += 1
        if kind == "mini":
            slot["mini"] += 1
    # dense per-day window (fill gaps with zeros so the chart has a steady x-axis)
    today = datetime.now().date()
    per_day = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        slot = per_date.get(d, {"total": 0, "mini": 0})
        per_day.append({"date": d, "total": slot["total"], "mini": slot["mini"]})
    busiest = max(per_date.items(), key=lambda kv: kv[1]["total"], default=None)
    recent = [{"ts": ts, "kind": k} for ts, k in opens[-14:]][::-1]
    return {
        "total": total,
        "dashboard": dash,
        "mini": mini,
        "active_days": len(active_dates),
        "first_ts": opens[0][0] if opens else None,
        "last_ts": opens[-1][0] if opens else None,
        "per_day": per_day,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "busiest_day": ({"date": busiest[0], "count": busiest[1]["total"]}
                        if busiest else None),
        "recent": recent,
        "window_days": days,
    }


# ---- personal records ("collection" page) ---------------------------------
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _weekday_cn(date_str: Optional[str]) -> str:
    if not date_str:
        return ""
    try:
        return _WEEKDAY_CN[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except (ValueError, TypeError):
        return ""


def records(db, run_gap: float, session_gap: float) -> Dict:
    """A curated set of all-time personal bests for the 记录珍藏 page.

    Read-only; reuses the same all-time helpers the gamification panel uses so
    the numbers always agree with the rest of the dashboard."""
    from . import stats
    from .fun import _is_uncommon

    daymap = stats._daily_map(db)
    total = sum(daymap.values())
    active_days = len(daymap)
    current_streak, best_streak = stats._streak(daymap)
    best_day = max(daymap.items(), key=lambda kv: kv[1]) if daymap else (None, 0)

    con = db.connect()
    try:
        distinct = con.execute("SELECT COUNT(DISTINCT ch) FROM char_events").fetchone()[0]
        first_ts = con.execute("SELECT MIN(ts) FROM char_events").fetchone()[0]
        last_ts = con.execute("SELECT MAX(ts) FROM char_events").fetchone()[0]
        key_total = con.execute("SELECT COUNT(*) FROM key_events").fetchone()[0]
        top_char_row = con.execute(
            "SELECT ch, COUNT(*) c FROM char_events GROUP BY ch ORDER BY c DESC LIMIT 1"
        ).fetchone()
        char_counts = dict(con.execute(
            "SELECT ch, COUNT(*) FROM char_events GROUP BY ch").fetchall())
        hour_row = con.execute(
            "SELECT CAST(strftime('%H', ts,'unixepoch','localtime') AS INT) h, "
            "COUNT(*) c FROM char_events GROUP BY h ORDER BY c DESC LIMIT 1"
        ).fetchone()
        app_rows = con.execute(
            "SELECT app, COUNT(*) c FROM char_events WHERE app IS NOT NULL "
            "GROUP BY app ORDER BY c DESC LIMIT 1").fetchone()
    finally:
        con.close()

    eff = stats.efficiency(db, None, session_gap)
    longest_min, longest_start = stats._longest_session(db, None, None, session_gap)
    top_word = stats._top_multichar_word(db, None, None, run_gap)
    top_word_count = 0
    if top_word:
        for w, c in stats.top_words(db, None, 80, run_gap):
            if w == top_word:
                top_word_count = c
                break
    rare_distinct = sum(1 for ch in char_counts if _is_uncommon(ch))
    rare_total = sum(n for ch, n in char_counts.items() if _is_uncommon(ch))

    # span (calendar days from first record to today) + how full it has been
    span_days = 0
    if first_ts:
        span_days = (datetime.now().date()
                     - datetime.fromtimestamp(first_ts).date()).days + 1

    return {
        "total_chars": total,
        "distinct_chars": distinct,
        "key_total": key_total,
        "active_days": active_days,
        "span_days": span_days,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "avg_per_active_day": round(total / active_days) if active_days else 0,
        "best_day": {"date": best_day[0], "count": best_day[1],
                     "weekday": _weekday_cn(best_day[0])} if best_day[0] else None,
        "best_streak": best_streak,
        "current_streak": current_streak,
        "peak_cpm": eff.get("peak_cpm", 0),
        "avg_cpm": eff.get("cpm", 0),
        "longest_session_min": longest_min,
        "longest_session_ts": longest_start,
        "top_char": ({"ch": top_char_row[0], "count": top_char_row[1]}
                     if top_char_row else None),
        "top_word": ({"word": top_word, "count": top_word_count}
                     if top_word else None),
        "peak_hour": (int(hour_row[0]), hour_row[1]) if hour_row else None,
        "top_app": ({"app": stats.pretty_app(app_rows[0]), "count": app_rows[1]}
                    if app_rows else None),
        "rare_distinct": rare_distinct,
        "rare_total": rare_total,
    }


# ---- single-day review ("day view") ---------------------------------------
def day_detail(db, day: str, run_gap: float, session_gap: float) -> Dict:
    """Everything about one calendar day for the 回顾 (day view) page."""
    from . import stats

    day = (day or "").strip()
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        d = datetime.now()
        day = d.strftime("%Y-%m-%d")
    since = datetime(d.year, d.month, d.day).timestamp()
    until = since + 86400

    chars = stats.total_chars(db, since, until)
    eff = stats.efficiency(db, since, session_gap, until)
    e = stats.edits(db, since, until, session_gap)
    peak_hr, peak_hr_count = stats._peak_hour(db, since, until)
    top_chars = stats.top_chars(db, since, 20, until)
    top_words = stats.top_words(db, since, 20, run_gap, until)
    apps = stats.per_app(db, since, 12, until)
    app_total = sum(c for _a, c in apps) or 1

    w, p = stats._where(since, until)
    con = db.connect()
    try:
        distinct = con.execute(
            f"SELECT COUNT(DISTINCT ch) FROM char_events{w}", p).fetchone()[0]
        hour_rows = con.execute(
            f"SELECT CAST(strftime('%H', ts,'unixepoch','localtime') AS INT) h, "
            f"COUNT(*) c FROM char_events{w} GROUP BY h", p).fetchall()
    finally:
        con.close()
    by_hour = [0] * 24
    for h, c in hour_rows:
        if h is not None:
            by_hour[int(h)] = c

    # how this day stacks up against the user's active-day average + its rank
    daymap = stats._daily_map(db)
    active_days = len(daymap)
    grand_total = sum(daymap.values())
    avg = round(grand_total / active_days) if active_days else 0
    ranked = sorted(daymap.values(), reverse=True)
    rank = (ranked.index(chars) + 1) if chars in ranked else None
    # a few representative runs from that day (most recent first)
    runs = stats.sequence_recent(db, since, run_gap, 6, until, "", "")

    return {
        "day": day,
        "weekday": _weekday_cn(day),
        "chars": chars,
        "distinct_chars": distinct,
        "active_minutes": eff["active_minutes"],
        "cpm": eff["cpm"],
        "peak_cpm": eff.get("peak_cpm", 0),
        "sessions": eff["sessions"],
        "edit_ratio": e["edit_ratio"],
        "peak_hour": peak_hr,
        "peak_hour_count": peak_hr_count,
        "by_hour": by_hour,
        "top_chars": [{"ch": c, "count": k} for c, k in top_chars],
        "top_words": [{"word": ww, "count": cc} for ww, cc in top_words],
        "apps": [{"app": stats.pretty_app(a), "count": c,
                  "share": round(c / app_total * 100, 1)} for a, c in apps],
        "avg_per_active_day": avg,
        "vs_avg_pct": _pct_delta(chars, avg),
        "rank": rank,
        "active_days": active_days,
        "runs": runs,
    }
