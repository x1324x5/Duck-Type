"""Aggregate statistics consumed by the dashboard and CLI.

Every query is bounded by an optional half-open time window ``[since, until)``
(both in epoch seconds; ``None`` means unbounded on that side). The dashboard
maps its range buttons -- and a custom date picker -- onto these two numbers, so
"today", "last 7 days", "this month" and an arbitrary date range are all just
different bounds over the same functions.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import segment

# Map a range key to the number of days back, or None for "all".
_RANGE_DAYS = {"today": 0, "7d": 7, "30d": 30, "all": None}


# ---- time-window resolution ----------------------------------------------
def _day_start(dt: datetime) -> float:
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
    the end of that day.
    """
    now = datetime.now()
    if range_key == "custom":
        since = _day_start(datetime.strptime(start, "%Y-%m-%d")) if start else None
        until = None
        if end:
            until = _day_start(datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1))
        return since, until
    if range_key == "all":
        return None, None
    if range_key == "today":
        return _day_start(now), None
    days = _RANGE_DAYS.get(range_key, 7) or 7
    return (now - timedelta(days=days)).timestamp(), None


def _where(since: Optional[float], until: Optional[float] = None):
    clauses, params = [], []
    if since is not None:
        clauses.append("ts>=?"); params.append(since)
    if until is not None:
        clauses.append("ts<?"); params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


# ---- character-level (range-aware, fast) ---------------------------------
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
    w, p = _where(since, until)
    con = db.connect()
    try:
        return con.execute(
            f"SELECT date(ts,'unixepoch','localtime') d, COUNT(*) c "
            f"FROM char_events{w} GROUP BY d ORDER BY d",
            p,
        ).fetchall()
    finally:
        con.close()


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


# ---- edit / deletion stats -----------------------------------------------
def edits(db, since: Optional[float], until: Optional[float] = None) -> Dict[str, float]:
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
    edits_total = back + dele
    ratio = (edits_total / chars) if chars else 0.0
    return {
        "chars": chars,
        "backspace": back,
        "delete": dele,
        "enter": enter,
        "edits": edits_total,
        "edit_ratio": round(ratio, 4),
    }


# ---- efficiency -----------------------------------------------------------
def efficiency(db, since: Optional[float], session_gap: float = 60.0,
               until: Optional[float] = None) -> Dict[str, float]:
    w, p = _where(since, until)
    con = db.connect()
    try:
        ts_rows = con.execute(
            f"SELECT ts FROM char_events{w} ORDER BY ts", p
        ).fetchall()
    finally:
        con.close()
    ts_list = [r[0] for r in ts_rows]
    if not ts_list:
        return {"cpm": 0.0, "active_minutes": 0.0, "sessions": 0, "peak_cpm": 0.0}

    sessions: List[List[float]] = [[ts_list[0]]]
    for t in ts_list[1:]:
        if t - sessions[-1][-1] > session_gap:
            sessions.append([t])
        else:
            sessions[-1].append(t)

    active_seconds = 0.0
    peak_cpm = 0.0
    for s in sessions:
        dur = s[-1] - s[0]
        active_seconds += dur
        if dur >= 1.0:
            cpm = len(s) / (dur / 60.0)
            peak_cpm = max(peak_cpm, cpm)
    active_minutes = active_seconds / 60.0
    cpm = (len(ts_list) / active_minutes) if active_minutes > 0 else 0.0
    return {
        "cpm": round(cpm, 1),
        "active_minutes": round(active_minutes, 1),
        "sessions": len(sessions),
        "peak_cpm": round(peak_cpm, 1),
    }


# ---- word / POS / topics (use segmentation) -------------------------------
def top_words(db, since: Optional[float], n: int, run_gap: float,
              until: Optional[float] = None) -> List[Tuple[str, int]]:
    if since is None and until is None:
        # Use the fast materialized all-time table when it has data ...
        segment.build_words(db, run_gap)
        con = db.connect()
        try:
            rows = con.execute(
                "SELECT word, count FROM word_freq ORDER BY count DESC LIMIT ?", (n,)
            ).fetchall()
        finally:
            con.close()
        if rows:
            return rows
        # ... otherwise fall back to a live pass (e.g. data is one open run).
    wc, _wp, _pc = segment.segment_range(db, since, run_gap, until)
    return sorted(wc.items(), key=lambda kv: kv[1], reverse=True)[:n]


# Friendly Chinese labels for the common jieba POS tags.
POS_LABELS = {
    "n": "名词", "nr": "人名", "ns": "地名", "nt": "机构", "nz": "专名",
    "v": "动词", "vn": "动名词", "a": "形容词", "ad": "副形词", "d": "副词",
    "m": "数词", "q": "量词", "r": "代词", "p": "介词", "c": "连词",
    "u": "助词", "t": "时间词", "f": "方位词", "s": "处所词", "x": "其他",
    "i": "成语", "l": "习用语", "j": "简称", "e": "叹词", "o": "拟声词",
}


def pos_distribution(db, since: Optional[float], run_gap: float,
                     until: Optional[float] = None) -> List[Tuple[str, str, int]]:
    pc = {}
    if since is None and until is None:
        segment.build_words(db, run_gap)
        con = db.connect()
        try:
            rows = con.execute(
                "SELECT pos, count FROM pos_freq ORDER BY count DESC", ()
            ).fetchall()
        finally:
            con.close()
        pc = {r[0]: r[1] for r in rows}
    if not pc:
        _wc, _wp, pc = segment.segment_range(db, since, run_gap, until)
    out = [(pos, POS_LABELS.get(pos, pos), cnt) for pos, cnt in pc.items()]
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def topics(db, since: Optional[float], topk: int = 25,
           until: Optional[float] = None):
    return segment.topics(db, since, topk, until)


# ---- committed-character sequence ----------------------------------------
def sequence_runs(db, since: Optional[float], run_gap: float,
                  until: Optional[float] = None) -> List[str]:
    """The typed sequence reconstructed into run strings (newest pauses split)."""
    con = db.connect()
    try:
        rows = segment._bounded_rows(con, "ts, ch, app", since, until)
    finally:
        con.close()
    return list(segment._runs_from_rows(rows, run_gap))


def sequence_recent(db, since: Optional[float], run_gap: float, limit: int = 200,
                    until: Optional[float] = None) -> List[Dict]:
    """Most recent runs (for the timeline view), each with its start time + app."""
    con = db.connect()
    try:
        rows = segment._bounded_rows(con, "ts, ch, app", since, until)
    finally:
        con.close()
    runs: List[Dict] = []
    cur: List[str] = []
    start_ts = None
    last_ts = None
    last_app = None
    for ts, ch, app in rows:
        if cur and (last_ts is not None and (ts - last_ts > run_gap or app != last_app)):
            runs.append({"ts": start_ts, "app": last_app, "text": "".join(cur)})
            cur = []
            start_ts = None
        if not cur:
            start_ts = ts
        cur.append(ch)
        last_ts, last_app = ts, app
    if cur:
        runs.append({"ts": start_ts, "app": last_app, "text": "".join(cur)})
    runs.reverse()
    return runs[:limit]


# ---- fun rankings ---------------------------------------------------------
def _in_rare_block(ch: str) -> bool:
    cp = ord(ch)
    return (0x3400 <= cp <= 0x4DBF      # CJK Extension A (uncommon)
            or 0xF900 <= cp <= 0xFAFF   # Compatibility Ideographs
            or cp >= 0x20000)           # Extensions B+ (astral, rare)


def fun_rankings(db, since: Optional[float], run_gap: float,
                 until: Optional[float] = None) -> Dict:
    """Playful leaderboards: favourite long words, idioms, hapax & rare chars."""
    char_counts = dict(top_chars(db, since, 1_000_000, until))
    hapax = [c for c, n in char_counts.items() if n == 1]
    rare = sorted(
        ((c, n) for c, n in char_counts.items() if _in_rare_block(c)),
        key=lambda kv: kv[1], reverse=True,
    )[:30]

    wc, wp, _pc = segment.segment_range(db, since, run_gap, until)
    fav_words = sorted(
        ((w, n) for w, n in wc.items() if len(w) >= 2),
        key=lambda kv: kv[1], reverse=True,
    )[:30]
    idioms = sorted(
        ((w, n) for w, n in wc.items() if wp.get(w) == "i" or len(w) >= 4),
        key=lambda kv: kv[1], reverse=True,
    )[:30]
    return {
        "favorite_words": [{"word": w, "count": n} for w, n in fav_words],
        "idioms": [{"word": w, "count": n} for w, n in idioms],
        "hapax": hapax[:60],
        "rare_chars": [{"ch": c, "count": n} for c, n in rare],
        "distinct": len(char_counts),
        "hapax_count": len(hapax),
    }


# ---- streak / goal / achievements ----------------------------------------
def _daily_map(db) -> Dict[str, int]:
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT date(ts,'unixepoch','localtime') d, COUNT(*) c "
            "FROM char_events GROUP BY d"
        ).fetchall()
    finally:
        con.close()
    return {d: c for d, c in rows}


def _streak(daymap: Dict[str, int]) -> Tuple[int, int]:
    """(current, best) run of consecutive active days. Current counts back from
    today, tolerating that today itself may not have activity yet."""
    if not daymap:
        return 0, 0
    days = sorted(daymap)
    best = run = 1
    prev = datetime.strptime(days[0], "%Y-%m-%d")
    for d in days[1:]:
        cur = datetime.strptime(d, "%Y-%m-%d")
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
        prev = cur

    today = datetime.now().date()
    current = 0
    cursor = today
    while cursor.strftime("%Y-%m-%d") in daymap:
        current += 1
        cursor = cursor - timedelta(days=1)
    if current == 0:  # nothing today -> maybe the streak ended yesterday
        cursor = today - timedelta(days=1)
        while cursor.strftime("%Y-%m-%d") in daymap:
            current += 1
            cursor = cursor - timedelta(days=1)
    return current, best


# (id, 名称, 描述, 指标键, 阈值)
_ACHIEVEMENTS = [
    ("first_word", "破壳而出", "记录下第一个字", "total", 1),
    ("k1", "牛刀小试", "累计 1,000 字", "total", 1_000),
    ("k10", "出口成章", "累计 10,000 字", "total", 10_000),
    ("k100", "著作等身", "累计 100,000 字", "total", 100_000),
    ("k1m", "百万雄师", "累计 1,000,000 字", "total", 1_000_000),
    ("distinct500", "博览群字", "用过 500 个不同的字", "distinct", 500),
    ("distinct1500", "学富五车", "用过 1,500 个不同的字", "distinct", 1_500),
    ("streak3", "小有恒心", "连续 3 天码字", "streak", 3),
    ("streak7", "持之以恒", "连续 7 天码字", "streak", 7),
    ("streak30", "铁杵成针", "连续 30 天码字", "streak", 30),
    ("day1k", "文思泉涌", "单日码字过千", "day_max", 1_000),
    ("day5k", "倚马可待", "单日码字过五千", "day_max", 5_000),
]


def gamify(db, daily_goal: int) -> Dict:
    """Goal progress + streak + achievement list for the gamification panel."""
    daymap = _daily_map(db)
    total = sum(daymap.values())
    today_key = datetime.now().strftime("%Y-%m-%d")
    today_chars = daymap.get(today_key, 0)
    day_max = max(daymap.values()) if daymap else 0
    current, best = _streak(daymap)

    con = db.connect()
    try:
        distinct = con.execute("SELECT COUNT(DISTINCT ch) FROM char_events").fetchone()[0]
    finally:
        con.close()

    metrics = {"total": total, "distinct": distinct, "streak": best, "day_max": day_max}
    achievements = []
    for aid, name, desc, key, threshold in _ACHIEVEMENTS:
        value = metrics.get(key, 0)
        achievements.append({
            "id": aid, "name": name, "desc": desc,
            "unlocked": value >= threshold,
            "progress": min(1.0, round(value / threshold, 4)) if threshold else 1.0,
        })

    goal = max(1, int(daily_goal or 1))
    return {
        "today_chars": today_chars,
        "daily_goal": goal,
        "goal_pct": min(1.0, round(today_chars / goal, 4)),
        "streak_current": current,
        "streak_best": best,
        "total_chars": total,
        "unlocked": sum(1 for a in achievements if a["unlocked"]),
        "achievements": achievements,
    }


# ---- trend comparison (this period vs the preceding one) ------------------
def _window_metrics(db, since, until, run_gap, session_gap) -> Dict:
    e = edits(db, since, until)
    eff = efficiency(db, since, session_gap, until)
    return {
        "chars": e["chars"],
        "edit_ratio": e["edit_ratio"],
        "active_minutes": eff["active_minutes"],
        "cpm": eff["cpm"],
    }


def trend(db, since, until, run_gap, session_gap) -> Optional[Dict]:
    """Compare the current window against the immediately preceding window of
    equal length. Returns None when the window is unbounded ('all')."""
    if since is None:
        return None
    import time as _time
    end = until if until is not None else _time.time()
    length = end - since
    if length <= 0:
        return None
    prev_since, prev_until = since - length, since
    cur = _window_metrics(db, since, until, run_gap, session_gap)
    prev = _window_metrics(db, prev_since, prev_until, run_gap, session_gap)

    def _delta(key):
        a, b = cur[key], prev[key]
        if not b:
            return None  # no baseline -> show as "new"
        return round((a - b) / b * 100, 1)

    return {
        "current": cur,
        "previous": prev,
        "delta_pct": {k: _delta(k) for k in cur},
    }


# ---- one-shot overview ----------------------------------------------------
def overview(db, since: Optional[float], run_gap: float, session_gap: float,
             until: Optional[float] = None) -> Dict:
    e = edits(db, since, until)
    eff = efficiency(db, since, session_gap, until)
    w, p = _where(since, until)
    con = db.connect()
    try:
        distinct = con.execute(
            f"SELECT COUNT(DISTINCT ch) FROM char_events{w}", p
        ).fetchone()[0]
        first_ts = con.execute("SELECT MIN(ts) FROM char_events").fetchone()[0]
    finally:
        con.close()
    return {
        "total_chars": e["chars"],
        "distinct_chars": distinct,
        "edits": e["edits"],
        "edit_ratio": e["edit_ratio"],
        "backspace": e["backspace"],
        "delete": e["delete"],
        "cpm": eff["cpm"],
        "peak_cpm": eff["peak_cpm"],
        "active_minutes": eff["active_minutes"],
        "sessions": eff["sessions"],
        "tracking_since": (
            datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d") if first_ts else None
        ),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
