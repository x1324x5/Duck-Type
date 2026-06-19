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

from ..perf import timed
from . import segment
from .time_ranges import day_start as _day_start
from .time_ranges import resolve_range, since_for


# ---- time-window resolution ----------------------------------------------
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


# ---- edit / deletion stats -----------------------------------------------
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


# ---- efficiency -----------------------------------------------------------
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
    # Commit-only timing means a multi-char word lands at one timestamp; charge
    # each character at least this long so a tight burst can't divide by ~0.
    MIN_SEC_PER_CHAR = 0.12          # -> apparent speed capped near 500 cpm
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

    # Walk the characters, accumulating each session's floored active span.
    sessions = 1
    active_seconds = 0.0
    seg_start = ts_list[0]
    seg_count = 1
    prev = ts_list[0]
    for t in ts_list[1:]:
        if t - prev > session_gap:                       # session boundary
            active_seconds += max(prev - seg_start, seg_count * MIN_SEC_PER_CHAR)
            sessions += 1
            seg_start, seg_count = t, 1
        else:
            seg_count += 1
        prev = t
    active_seconds += max(prev - seg_start, seg_count * MIN_SEC_PER_CHAR)
    active_minutes = active_seconds / 60.0
    cpm = (len(ts_list) / active_minutes) if active_minutes > 0 else 0.0

    peak_count = 0
    left = 0
    for right in range(len(ts_list)):
        while ts_list[right] - ts_list[left] > peak_window:
            left += 1
        peak_count = max(peak_count, right - left + 1)
    peak_cpm = max(peak_count * (60.0 / peak_window), cpm)

    return {
        "cpm": round(cpm, 1),
        "active_minutes": round(active_minutes, 1),
        "sessions": sessions,
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
                "SELECT word, count FROM word_freq "
                "WHERE length(word)>=2 ORDER BY count DESC LIMIT ?", (n,)
            ).fetchall()
        finally:
            con.close()
        if rows:
            return rows
        # ... otherwise fall back to a live pass (e.g. data is one open run).
    with timed("stats.top_words.segment_range"):
        wc, _wp, _pc = segment.segment_range(db, since, run_gap, until)
    words = ((w, c) for w, c in wc.items() if len(w) >= 2)
    return sorted(words, key=lambda kv: kv[1], reverse=True)[:n]


# Friendly Chinese labels for jieba's POS tags (ICTCLAS-style, incl. sub-tags).
POS_LABELS = {
    "n": "名词", "nr": "人名", "nrfg": "人名", "nrt": "人名", "ns": "地名",
    "nt": "机构团体", "nz": "其他专名", "ng": "名语素",
    "v": "动词", "vd": "副动词", "vn": "名动词", "vi": "不及物动词",
    "vg": "动语素", "vq": "趋向动词",
    "a": "形容词", "ad": "副形词", "an": "名形词", "ag": "形语素",
    "d": "副词", "df": "副词", "dg": "副语素",
    "m": "数词", "mq": "数量词", "q": "量词",
    "r": "代词", "rr": "人称代词", "rz": "指示代词", "ry": "疑问代词", "rg": "代语素",
    "p": "介词", "pba": "介词把", "pbei": "介词被",
    "c": "连词", "cc": "并列连词",
    "u": "助词", "uj": "结构助词(的)", "ud": "助词(得)", "ul": "时态助词(了)",
    "uv": "结构助词(地)", "uz": "时态助词(着)", "ug": "时态助词(过)", "ui": "助词",
    "t": "时间词", "tg": "时间语素",
    "f": "方位词", "s": "处所词", "b": "区别词",
    "z": "状态词", "zg": "状态语素", "y": "语气词", "e": "叹词", "o": "拟声词",
    "h": "前缀", "k": "后缀", "g": "语素", "l": "习用语", "j": "简称",
    "i": "成语", "x": "字符/其他", "eng": "英文", "w": "标点",
}


# Coarse, reader-friendly top-level word classes. jieba emits ~40 fine ICTCLAS
# tags (名语素, 副形词, 时态助词(着)…) which overwhelm a non-linguist; we roll them
# up into these big buckets for the chart and only show fine words on drill-down.
COARSE_LABELS = {
    "n": "名词", "v": "动词", "a": "形容词 / 状态词", "d": "副词", "r": "代词",
    "mq": "数量词", "t": "时间 / 方位", "fx": "虚词（介 / 连 / 助）", "other": "其他",
}
COARSE_ORDER = ["n", "v", "a", "d", "r", "mq", "t", "fx", "other"]


def coarse_pos(pos: str) -> str:
    """Map a fine jieba POS tag to one of the COARSE_LABELS buckets."""
    if not pos:
        return "other"
    c = pos[0]
    if c == "n":
        return "n"
    if c == "v":
        return "v"
    if c == "a" or pos in ("z", "zg", "b"):
        return "a"            # 形容词 + 状态词 / 区别词
    if c == "d":
        return "d"
    if c == "r":
        return "r"
    if c in ("m", "q"):
        return "mq"
    if c in ("t", "f", "s"):
        return "t"            # 时间 / 方位 / 处所
    if c in ("p", "c", "u") or pos in ("y", "e", "o", "h", "k"):
        return "fx"           # 介词 / 连词 / 助词 / 语气词…
    return "other"            # 成语 i, 习用语 l, 简称 j, 英文 eng, 标点 w…


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
        with timed("stats.pos_distribution.segment_range"):
            _wc, _wp, pc = segment.segment_range(db, since, run_gap, until)
    # Roll the fine tags up into the coarse buckets.
    coarse: Dict[str, int] = {}
    for pos, cnt in pc.items():
        cid = coarse_pos(pos)
        coarse[cid] = coarse.get(cid, 0) + cnt
    out = [(cid, COARSE_LABELS.get(cid, cid), cnt) for cid, cnt in coarse.items()]
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def pos_word_distribution(db, pos: str, since: Optional[float], run_gap: float,
                          until: Optional[float] = None, n: int = 12,
                          min_len: int = 2) -> Dict:
    """Words inside one coarse POS bucket, with the tail folded into "其他".

    ``pos`` is a coarse id (see ``COARSE_LABELS``); words from every fine tag that
    rolls up into it are aggregated.
    """
    cid = (pos or "").strip()
    n = max(1, int(n or 12))
    min_len = max(1, int(min_len or 1))
    empty = {
        "pos": cid, "label": COARSE_LABELS.get(cid, cid), "total": 0,
        "items": [], "other": 0, "least": [],
    }
    if not cid:
        return empty

    agg: Dict[str, int] = {}
    d0, d1 = _day_bounds(since, until)
    if d0 is not None:
        segment.build_words(db, run_gap)
        con = db.connect()
        try:
            rows = con.execute(
                "SELECT pos, word, SUM(count) c FROM pos_word_freq_daily "
                "WHERE day>=? AND day<=? GROUP BY pos, word",
                (d0, d1),
            ).fetchall()
        finally:
            con.close()
        for fine, w, c in rows:
            if coarse_pos(fine) == cid and len(w) >= min_len:
                agg[w] = agg.get(w, 0) + int(c)
        _tail_wc, _tail_pc, tail_pw = _tail_word_pos(db, run_gap)
        for day, by_fine in tail_pw.items():
            if d0 <= day <= d1:
                for fine, words in by_fine.items():
                    if coarse_pos(fine) != cid:
                        continue
                    for w, c in words.items():
                        if len(w) >= min_len:
                            agg[w] = agg.get(w, 0) + c
    else:
        with timed("stats.pos_word_distribution.segment_pos_words_range"):
            by_pos = segment.segment_pos_words_range(db, since, run_gap, until)
        for fine, words in by_pos.items():
            if coarse_pos(fine) != cid:
                continue
            for w, c in words.items():
                if len(w) >= min_len:
                    agg[w] = agg.get(w, 0) + c
    rows = list(agg.items())
    rows.sort(key=lambda kv: kv[1], reverse=True)
    total = sum(c for _w, c in rows)
    if not total:
        return empty

    shown = rows[:n]
    shown_total = sum(c for _w, c in shown)
    least = sorted(rows, key=lambda kv: (kv[1], kv[0]))[:min(5, len(rows))]
    return {
        "pos": cid,
        "label": COARSE_LABELS.get(cid, cid),
        "total": total,
        "items": [
            {"word": w, "count": c, "pct": round(c / total * 100, 2)}
            for w, c in shown
        ],
        "other": total - shown_total,
        "least": [
            {"word": w, "count": c, "pct": round(c / total * 100, 2)}
            for w, c in least
        ],
    }


def topics(db, since: Optional[float], topk: int = 25,
           until: Optional[float] = None):
    with timed("stats.topics"):
        return segment.topics(db, since, topk, until)


# ---- rollup-backed ranged word/POS stats (no live jieba) ------------------
# The board's word panels read these instead of running jieba on every range
# switch. They aggregate the per-day rollups materialized by segment.build_words,
# snapping the window to whole local days (which matches the daily bar chart).
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


def _tail_word_pos(db, run_gap: float):
    """Segment the still-open trailing run(s) -- the rows after build_words'
    materialization cursor -- live, grouped by local day. This keeps the daily
    panels up to the second even when the day is one uninterrupted session (the
    rollup only holds *closed* runs). The tail is bounded by run_gap so it is
    small and cheap to segment."""
    cursor = int(db.get_meta("word_cursor", "0") or "0")
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT ts, ch, app FROM char_events WHERE id>? ORDER BY id", (cursor,)
        ).fetchall()
    finally:
        con.close()
    day_wc: Dict[str, Dict[str, int]] = {}
    day_pc: Dict[str, Dict[str, int]] = {}
    day_pw: Dict[str, Dict[str, Dict[str, int]]] = {}
    for first_ts, run in segment._runs_with_ts_from_rows(rows, run_gap):
        a, b, c = segment._segment_text(run)
        day = datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d")
        dwc = day_wc.setdefault(day, {})
        dpc = day_pc.setdefault(day, {})
        dpw = day_pw.setdefault(day, {})
        for k, v in a.items():
            dwc[k] = dwc.get(k, 0) + v
            fine = b.get(k) or "x"
            bucket = dpw.setdefault(fine, {})
            bucket[k] = bucket.get(k, 0) + v
        for k, v in c.items():
            dpc[k] = dpc.get(k, 0) + v
    return day_wc, day_pc, day_pw


def top_words_daily(db, since: Optional[float], until: Optional[float],
                    n: int, run_gap: float) -> List[Tuple[str, int]]:
    """Top multi-char words over a day-aligned window, from word_freq_daily
    plus the live (un-materialized) trailing run."""
    segment.build_words(db, run_gap)
    d0, d1 = _day_bounds(since, until)
    if d0 is None:
        return top_words(db, None, n, run_gap, None)
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT word, SUM(count) c FROM word_freq_daily "
            "WHERE day>=? AND day<=? AND length(word)>=2 GROUP BY word", (d0, d1)
        ).fetchall()
    finally:
        con.close()
    agg: Dict[str, int] = {w: int(c) for w, c in rows}
    tail_wc, _tail_pc, _tail_pw = _tail_word_pos(db, run_gap)
    for day, dwc in tail_wc.items():
        if d0 <= day <= d1:
            for w, v in dwc.items():
                if len(w) >= 2:
                    agg[w] = agg.get(w, 0) + v
    return sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:n]


def pos_distribution_daily(db, since: Optional[float], until: Optional[float],
                           run_gap: float) -> List[Tuple[str, str, int]]:
    """Coarse POS distribution over a day-aligned window, from pos_freq_daily
    plus the live (un-materialized) trailing run."""
    segment.build_words(db, run_gap)
    d0, d1 = _day_bounds(since, until)
    if d0 is None:
        return pos_distribution(db, None, run_gap, None)
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT pos, SUM(count) c FROM pos_freq_daily "
            "WHERE day>=? AND day<=? GROUP BY pos", (d0, d1)
        ).fetchall()
    finally:
        con.close()
    coarse: Dict[str, int] = {}
    for pos, cnt in rows:
        cid = coarse_pos(pos)
        coarse[cid] = coarse.get(cid, 0) + int(cnt)
    _tail_wc, tail_pc, _tail_pw = _tail_word_pos(db, run_gap)
    for day, dpc in tail_pc.items():
        if d0 <= day <= d1:
            for pos, cnt in dpc.items():
                cid = coarse_pos(pos)
                coarse[cid] = coarse.get(cid, 0) + cnt
    out = [(cid, COARSE_LABELS.get(cid, cid), cnt) for cid, cnt in coarse.items()]
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def topics_daily(db, since: Optional[float], until: Optional[float],
                 topk: int, run_gap: float) -> List[Tuple[str, float]]:
    """Lightweight topic words for the board: the most frequent multi-char words
    in the window (no TF-IDF), with counts as weights. Avoids live jieba."""
    rows = top_words_daily(db, since, until, max(topk, 30), run_gap)
    return [(w, float(c)) for w, c in rows[:topk]]


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
                    until: Optional[float] = None,
                    app_filter: Optional[str] = None) -> List[Dict]:
    """Most recent runs (for the timeline view), each with its start time + app."""
    app_filter = (app_filter or "").strip()
    clauses, params = [], []
    if since is not None:
        clauses.append("ts>=?"); params.append(since)
    if until is not None:
        clauses.append("ts<?"); params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT ts, ch, app FROM char_events{where} ORDER BY ts",
            tuple(params),
        ).fetchall()
    finally:
        con.close()
    runs: List[Dict] = []
    cur: List[str] = []
    start_ts = None
    last_ts = None
    last_app = None
    for ts, ch, app in rows:
        if cur and (last_ts is not None and (ts - last_ts > run_gap or app != last_app)):
            if not app_filter or last_app == app_filter:
                runs.append({"ts": start_ts, "app": last_app, "text": "".join(cur)})
            cur = []
            start_ts = None
        if not cur:
            start_ts = ts
        cur.append(ch)
        last_ts, last_app = ts, app
    if cur:
        if not app_filter or last_app == app_filter:
            runs.append({"ts": start_ts, "app": last_app, "text": "".join(cur)})
    runs.reverse()
    return runs[:limit]


def sequence_apps(db, since: Optional[float], until: Optional[float] = None) -> List[Dict]:
    """Applications present in the sequence window, for the timeline filter."""
    w, p = _where(since, until)
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT COALESCE(app, ''), COUNT(*) c FROM char_events{w} "
            "GROUP BY app ORDER BY c DESC",
            p,
        ).fetchall()
    finally:
        con.close()
    return [{"app": app, "count": count} for app, count in rows]


# ---- keyword / character lookup ------------------------------------------
def search(db, query: str, since: Optional[float], run_gap: float,
           until: Optional[float] = None, max_apps: int = 12) -> Dict:
    """Look up how often a character/word/phrase was typed in the window.

    Counts non-overlapping occurrences of ``query`` within the reconstructed
    typed runs (so a match never spans a pause or an app switch), and reports
    when it was first/last seen, a per-app breakdown and a per-day series.
    """
    q = (query or "").strip()
    empty = {"query": q, "total": 0, "first_seen": None, "last_seen": None,
             "apps": [], "daily": [], "by_hour": [0] * 24, "examples": [],
             "peak_hour": None, "active_days": 0, "share_pct": 0.0, "rank": None}
    if not q:
        return empty

    con = db.connect()
    try:
        rows = segment._bounded_rows(con, "ts, ch, app", since, until)
    finally:
        con.close()

    qlen = len(q)
    occ_ts: List[float] = []
    apps: Dict[str, int] = {}
    days: Dict[str, int] = {}
    by_hour = [0] * 24
    examples: List[Dict] = []

    def _scan(run: List[Tuple[float, str, Optional[str]]]) -> None:
        if len(run) < qlen:
            return
        text = "".join(r[1] for r in run)
        i = 0
        while i + qlen <= len(text):
            if text[i:i + qlen] == q:
                ts0, _ch, app0 = run[i]
                occ_ts.append(ts0)
                a = app0 or "(unknown)"
                apps[a] = apps.get(a, 0) + 1
                dt0 = datetime.fromtimestamp(ts0)
                days[dt0.strftime("%Y-%m-%d")] = days.get(dt0.strftime("%Y-%m-%d"), 0) + 1
                by_hour[dt0.hour] += 1
                if len(examples) < 8:
                    examples.append({
                        "ts": ts0, "app": a,
                        "pre": text[:i], "match": text[i:i + qlen],
                        "post": text[i + qlen:],
                        "text": text, "start": i, "end": i + qlen,
                    })
                i += qlen          # non-overlapping
            else:
                i += 1

    run: List[Tuple[float, str, Optional[str]]] = []
    last_ts: Optional[float] = None
    last_app = None
    for ts, ch, app in rows:
        if run and (last_ts is not None and (ts - last_ts > run_gap or app != last_app)):
            _scan(run); run = []
        run.append((ts, ch, app))
        last_ts, last_app = ts, app
    if run:
        _scan(run)

    total = len(occ_ts)
    # Share of all typed characters this query accounts for; rank only makes
    # unambiguous sense for a single character (against the char-frequency table).
    share_pct, rank = 0.0, None
    if total:
        w, p = _where(since, until)
        con = db.connect()
        try:
            total_chars_all = con.execute(
                f"SELECT COUNT(*) FROM char_events{w}", p).fetchone()[0] or 0
            if qlen == 1:
                higher = con.execute(
                    f"SELECT COUNT(*) FROM (SELECT ch, COUNT(*) c FROM char_events{w} "
                    f"GROUP BY ch HAVING c > ?)", (*p, total)).fetchone()[0]
                rank = higher + 1
        finally:
            con.close()
        if total_chars_all:
            share_pct = round(total * qlen / total_chars_all * 100, 2)

    peak_hour = max(range(24), key=lambda h: by_hour[h]) if total else None
    top_apps = sorted(apps.items(), key=lambda kv: kv[1], reverse=True)[:max_apps]
    return {
        "query": q,
        "total": total,
        "first_seen": min(occ_ts) if occ_ts else None,
        "last_seen": max(occ_ts) if occ_ts else None,
        "apps": [{"app": a, "count": c} for a, c in top_apps],
        "daily": [{"date": d, "count": c} for d, c in sorted(days.items())],
        "by_hour": by_hour,
        "peak_hour": peak_hour,
        "active_days": len(days),
        "share_pct": share_pct,
        "rank": rank,
        "examples": examples,
    }


def tracked_terms(db, terms: List[str], since: Optional[float], run_gap: float,
                  until: Optional[float] = None) -> List[Dict]:
    """Count committed occurrences of each user-tracked term in the window.

    A single pass reconstructs the typed runs (same run-grouping as everything
    else, so a match never spans a pause or app switch), then counts every
    term's non-overlapping occurrences inside each run. This is independent of
    jieba segmentation, which is the whole point: names and codenames that the
    segmenter would split are still counted exactly. Returns one row per term in
    the order given, each with total, first/last-seen, active days and the app
    where it shows up most.
    """
    cleaned: List[str] = []
    seen = set()
    for t in terms or []:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    if not cleaned:
        return []

    # Per-term accumulators, parallel to ``cleaned``.
    totals = [0] * len(cleaned)
    first_ts: List[Optional[float]] = [None] * len(cleaned)
    last_ts: List[Optional[float]] = [None] * len(cleaned)
    apps: List[Dict[str, int]] = [dict() for _ in cleaned]
    days: List[Dict[str, int]] = [dict() for _ in cleaned]
    lengths = [len(t) for t in cleaned]

    con = db.connect()
    try:
        rows = segment._bounded_rows(con, "ts, ch, app", since, until)
    finally:
        con.close()

    def _scan(run: List[Tuple[float, str, Optional[str]]]) -> None:
        text = "".join(r[1] for r in run)
        for idx, term in enumerate(cleaned):
            qlen = lengths[idx]
            if len(text) < qlen:
                continue
            i = text.find(term)
            while i != -1:
                ts0, _ch, app0 = run[i]
                totals[idx] += 1
                if first_ts[idx] is None or ts0 < first_ts[idx]:
                    first_ts[idx] = ts0
                if last_ts[idx] is None or ts0 > last_ts[idx]:
                    last_ts[idx] = ts0
                a = app0 or "(unknown)"
                apps[idx][a] = apps[idx].get(a, 0) + 1
                dkey = datetime.fromtimestamp(ts0).strftime("%Y-%m-%d")
                days[idx][dkey] = days[idx].get(dkey, 0) + 1
                i = text.find(term, i + qlen)   # non-overlapping

    run: List[Tuple[float, str, Optional[str]]] = []
    prev_ts: Optional[float] = None
    prev_app = None
    for ts, ch, app in rows:
        if run and (prev_ts is not None and (ts - prev_ts > run_gap or app != prev_app)):
            _scan(run); run = []
        run.append((ts, ch, app))
        prev_ts, prev_app = ts, app
    if run:
        _scan(run)

    out: List[Dict] = []
    for idx, term in enumerate(cleaned):
        top_app = max(apps[idx].items(), key=lambda kv: kv[1])[0] if apps[idx] else None
        out.append({
            "term": term,
            "total": totals[idx],
            "first_seen": first_ts[idx],
            "last_seen": last_ts[idx],
            "active_days": len(days[idx]),
            "top_app": pretty_app(top_app) if top_app else None,
            # daily counts (sorted) drive the per-card sparkline in the dashboard.
            "daily": [{"date": d, "count": c} for d, c in sorted(days[idx].items())],
        })
    return out


# ---- fun rankings ---------------------------------------------------------
from .common_chars import COMMON_CHARS

# A small modern/common supplement on top of the 3,500-character reference. The
# reference table is intentionally conservative; these characters are common in
# names, modal particles, transliterations, food/internet writing, or everyday
# proper nouns and should not make the "生僻字" panel feel noisy.
_COMMON_SUPPLEMENT = frozenset(
    "哦噢嗯欸诶哎唉呀呃哇喔呗嘛哟啦咯啰"
    "蔡噻甄邱邵彭蒋韩萧阎廖薛冯覃翟邹贾袁"
    "咖啡巧克力披萨薯堡酱橙柠檬莓椰"
    "粤闽沪渝圳澳台港甬蓉穗杭"
    "梗梳槽怼囧萌酷飒"
)


def _is_uncommon(ch: str) -> bool:
    """A typed character counts as 生僻/uncommon when it is a Han ideograph that
    is *not* among the 3,500 standard 常用字. This is an intrinsic measure -- it
    never looks at how often the user typed it -- and is far looser than the old
    "only CJK extension blocks" rule (which virtually nobody ever triggers) while
    still excluding every everyday character."""
    return segment._HAN(ch) and ch not in COMMON_CHARS and ch not in _COMMON_SUPPLEMENT


def fun_rankings(db, since: Optional[float], run_gap: float,
                 until: Optional[float] = None) -> Dict:
    """Playful leaderboards: favourite long words, idioms, hapax & rare chars."""
    char_counts = dict(top_chars(db, since, 1_000_000, until))
    hapax = [c for c, n in char_counts.items() if n == 1]
    rare = sorted(
        ((c, n) for c, n in char_counts.items() if _is_uncommon(c)),
        key=lambda kv: kv[1], reverse=True,
    )[:30]

    with timed("stats.fun_rankings.segment_range"):
        wc, wp, _pc = segment.segment_range(db, since, run_gap, until)

    def _is_idiom(w: str) -> bool:
        # jieba's 成语 tag, or a 4-character all-Han word (the classic shape).
        return wp.get(w) == "i" or (len(w) == 4 and all(segment._HAN(c) for c in w))

    fav_words = sorted(
        ((w, n) for w, n in wc.items() if len(w) >= 2),
        key=lambda kv: kv[1], reverse=True,
    )[:30]
    idioms = sorted(
        ((w, n) for w, n in wc.items() if _is_idiom(w)),
        key=lambda kv: kv[1], reverse=True,
    )[:30]
    long_words = sorted(
        ((w, n) for w, n in wc.items() if len(w) >= 3 and not _is_idiom(w)),
        key=lambda kv: kv[1], reverse=True,
    )[:30]
    return {
        "favorite_words": [{"word": w, "count": n} for w, n in fav_words],
        "idioms": [{"word": w, "count": n} for w, n in idioms],
        "long_words": [{"word": w, "count": n} for w, n in long_words],
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


# (id, 名称, 描述, 指标键, 阈值, 分类)
_ACHIEVEMENTS = [
    # 累计字数 total
    ("first_word", "破壳而出", "记录下第一个字", "total", 1, "字数"),
    ("k1", "牛刀小试", "累计 1,000 字", "total", 1_000, "字数"),
    ("k5", "初露锋芒", "累计 5,000 字", "total", 5_000, "字数"),
    ("k10", "出口成章", "累计 10,000 字", "total", 10_000, "字数"),
    ("k50", "笔耕不辍", "累计 50,000 字", "total", 50_000, "字数"),
    ("k100", "著作等身", "累计 100,000 字", "total", 100_000, "字数"),
    ("k300", "学富五车", "累计 300,000 字", "total", 300_000, "字数"),
    ("k1m", "百万雄师", "累计 1,000,000 字", "total", 1_000_000, "字数"),
    # 不同汉字 distinct
    ("distinct100", "初识百字", "用过 100 个不同的字", "distinct", 100, "汉字"),
    ("distinct500", "博览群字", "用过 500 个不同的字", "distinct", 500, "汉字"),
    ("distinct1500", "胸有千壑", "用过 1,500 个不同的字", "distinct", 1_500, "汉字"),
    ("distinct3000", "万象包罗", "用过 3,000 个不同的字", "distinct", 3_000, "汉字"),
    ("hapax100", "偶遇百字", "有 100 个字只出现过一次", "hapax_count", 100, "汉字"),
    ("hapax500", "字海拾贝", "有 500 个字只出现过一次", "hapax_count", 500, "汉字"),
    # 生僻字 rare chars
    ("rare10", "识字冷门派", "用过 10 个生僻字", "rare_distinct", 10, "生僻"),
    ("rare30", "冷字收藏家", "用过 30 个生僻字", "rare_distinct", 30, "生僻"),
    ("rare100", "异体寻踪", "用过 100 个生僻字", "rare_distinct", 100, "生僻"),
    ("rare_total100", "冷门常客", "累计输入生僻字 100 次", "rare_total", 100, "生僻"),
    ("rare_total500", "字库探险", "累计输入生僻字 500 次", "rare_total", 500, "生僻"),
    # 单字重复 single character
    ("char100", "一字百遍", "同一个字累计输入 100 次", "char_max", 100, "单字"),
    ("char500", "念念不忘", "同一个字累计输入 500 次", "char_max", 500, "单字"),
    ("char1000", "千锤百炼", "同一个字累计输入 1,000 次", "char_max", 1_000, "单字"),
    # 趣味彩蛋
    ("duck10", "鸭鸭报到", "「鸭」字累计出现 10 次", "duck_count", 10, "趣味"),
    ("duck100", "鸭力全开", "「鸭」字累计出现 100 次", "duck_count", 100, "趣味"),
    ("duck500", "鸭王之王", "「鸭」字累计出现 500 次", "duck_count", 500, "趣味"),
    # 连续天数 streak
    ("streak3", "小有恒心", "连续 3 天码字", "streak", 3, "连续"),
    ("streak7", "持之以恒", "连续 7 天码字", "streak", 7, "连续"),
    ("streak14", "习惯成形", "连续 14 天码字", "streak", 14, "连续"),
    ("streak30", "铁杵成针", "连续 30 天码字", "streak", 30, "连续"),
    ("streak100", "百日筑基", "连续 100 天码字", "streak", 100, "连续"),
    # 累计活跃天数 active_days
    ("days7", "崭露头角", "累计 7 天有记录", "active_days", 7, "活跃"),
    ("days30", "月度常客", "累计 30 天有记录", "active_days", 30, "活跃"),
    ("days100", "百炼成钢", "累计 100 天有记录", "active_days", 100, "活跃"),
    ("days365", "周年陪伴", "累计 365 天有记录", "active_days", 365, "活跃"),
    # 单日字数 day_max
    ("day1k", "文思泉涌", "单日码字过千", "day_max", 1_000, "单日"),
    ("day5k", "倚马可待", "单日码字过五千", "day_max", 5_000, "单日"),
    ("day10k", "日破万言", "单日码字过万", "day_max", 10_000, "单日"),
    # 看板语录 quote views (distinct / total / egg)
    ("quote_d50", "初拾珠玑", "读过 50 条不同的语录", "quotes_distinct", 50, "语录"),
    ("quote_d200", "渐入佳境", "读过 200 条不同的语录", "quotes_distinct", 200, "语录"),
    ("quote_d500", "博览群句", "读过 500 条不同的语录", "quotes_distinct", 500, "语录"),
    ("quote_v200", "日积月累", "累计看过 200 次语录", "quotes_total", 200, "语录"),
    ("quote_v1000", "手不释卷", "累计看过 1,000 次语录", "quotes_total", 1_000, "语录"),
    ("quote_egg", "一片留白", "在滚动语录里遇见一片空白", "quotes_egg", 1, "语录"),
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
        char_counts = dict(
            con.execute(
                "SELECT ch, COUNT(*) c FROM char_events GROUP BY ch"
            ).fetchall()
        )
    finally:
        con.close()

    from ..storage.db import quote_hash
    q_distinct, q_total, q_egg = db.quote_stats(quote_hash(EASTER_EGG_QUOTE))

    rare_total = sum(n for ch, n in char_counts.items() if _is_uncommon(ch))
    rare_distinct = sum(1 for ch in char_counts if _is_uncommon(ch))
    char_max = max(char_counts.values()) if char_counts else 0
    hapax_count = sum(1 for n in char_counts.values() if n == 1)

    metrics = {"total": total, "distinct": distinct, "streak": best,
               "day_max": day_max, "active_days": len(daymap),
               "rare_total": rare_total, "rare_distinct": rare_distinct,
               "char_max": char_max, "duck_count": char_counts.get("鸭", 0),
               "hapax_count": hapax_count,
               "quotes_distinct": q_distinct, "quotes_total": q_total,
               "quotes_egg": 1 if q_egg else 0}
    achievements = []
    unlocked_ids = []
    for aid, name, desc, key, threshold, category in _ACHIEVEMENTS:
        value = metrics.get(key, 0)
        is_unlocked = value >= threshold
        if is_unlocked:
            unlocked_ids.append(aid)
        achievements.append({
            "id": aid, "name": name, "desc": desc, "category": category,
            "unlocked": is_unlocked,
            "progress": min(1.0, round(value / threshold, 4)) if threshold else 1.0,
        })
    # Stamp (and persist) first-unlock times so the page can show them and the
    # frontend can detect freshly-earned achievements for the toast.
    try:
        stamps = db.record_achievements(unlocked_ids)
    except Exception:
        stamps = {}
    for a in achievements:
        a["unlocked_at"] = stamps.get(a["id"])

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
    e = edits(db, since, until, session_gap)
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


# ---- periodic reports (today / week / month / year) ----------------------
def period_bounds(period: str):
    """Return (since, until, prev_since, prev_until, label) for a named period,
    using calendar boundaries in local time."""
    now = datetime.now()
    if period == "today":
        s = _day_start(now)
        return s, None, s - 86400, s, "今日小结"
    if period == "week":
        monday = (now - timedelta(days=now.weekday())).date()
        s = datetime(monday.year, monday.month, monday.day).timestamp()
        return s, None, s - 7 * 86400, s, "本周周报"
    if period == "month":
        s = datetime(now.year, now.month, 1).timestamp()
        prev = (datetime(now.year - 1, 12, 1) if now.month == 1
                else datetime(now.year, now.month - 1, 1))
        return s, None, prev.timestamp(), s, "本月月报"
    if period == "year":
        s = datetime(now.year, 1, 1).timestamp()
        return s, None, datetime(now.year - 1, 1, 1).timestamp(), s, "年度报告"
    raise ValueError(f"unknown period {period!r}")


def _peak_hour(db, since, until):
    w, p = _where(since, until)
    con = db.connect()
    try:
        row = con.execute(
            f"SELECT CAST(strftime('%H', ts,'unixepoch','localtime') AS INT) h, "
            f"COUNT(*) c FROM char_events{w} GROUP BY h ORDER BY c DESC LIMIT 1", p
        ).fetchone()
    finally:
        con.close()
    return (int(row[0]), row[1]) if row else (None, 0)


def _longest_session(db, since, until, gap):
    w, p = _where(since, until)
    con = db.connect()
    try:
        ts = [r[0] for r in con.execute(
            f"SELECT ts FROM char_events{w} ORDER BY ts", p).fetchall()]
    finally:
        con.close()
    if not ts:
        return 0.0, None
    best_dur, best_start = 0.0, ts[0]
    s_start, s_prev = ts[0], ts[0]
    for t in ts[1:]:
        if t - s_prev > gap:
            if s_prev - s_start > best_dur:
                best_dur, best_start = s_prev - s_start, s_start
            s_start = t
        s_prev = t
    if s_prev - s_start > best_dur:
        best_dur, best_start = s_prev - s_start, s_start
    return round(best_dur / 60.0, 1), best_start


def _top_multichar_word(db, since, until, run_gap):
    for w, _c in top_words(db, since, 80, run_gap, until):
        if len(w) >= 2:
            return w
    return None


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


def _activity_rhythm(day_rows: List[Tuple[str, int]]) -> Dict[str, Optional[object]]:
    """Small calendar rhythm summary for report rows/cards.

    "Quietest" intentionally ignores completely empty weekdays/weeks, otherwise
    a partial month would always claim a future weekday was the quietest.
    """
    weekdays = [0] * 7
    weeks: Dict[str, int] = {}
    for d, count in day_rows:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except Exception:
            continue
        count = int(count or 0)
        weekdays[dt.weekday()] += count
        iso_year, iso_week, _iso_day = dt.isocalendar()
        weeks[f"{iso_year}-W{iso_week:02d}"] = weeks.get(f"{iso_year}-W{iso_week:02d}", 0) + count

    active_weekdays = [(i, c) for i, c in enumerate(weekdays) if c > 0]
    active_weeks = [(w, c) for w, c in weeks.items() if c > 0]
    busy_wd = max(active_weekdays, key=lambda x: x[1]) if active_weekdays else None
    quiet_wd = min(active_weekdays, key=lambda x: x[1]) if active_weekdays else None
    busy_week = max(active_weeks, key=lambda x: x[1]) if active_weeks else None
    quiet_week = min(active_weeks, key=lambda x: x[1]) if active_weeks else None
    return {
        "busiest_weekday": _WEEKDAYS[busy_wd[0]] if busy_wd else None,
        "busiest_weekday_count": busy_wd[1] if busy_wd else 0,
        "quietest_weekday": _WEEKDAYS[quiet_wd[0]] if quiet_wd else None,
        "quietest_weekday_count": quiet_wd[1] if quiet_wd else 0,
        "busiest_week": busy_week[0] if busy_week else None,
        "busiest_week_count": busy_week[1] if busy_week else 0,
        "quietest_week": quiet_week[0] if quiet_week else None,
        "quietest_week_count": quiet_week[1] if quiet_week else 0,
    }


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


def _peak_window(db, since, until, span: int = 3):
    """Busiest contiguous ``span``-hour block. Returns
    (start_h, end_h, count, label) or None."""
    w, p = _where(since, until)
    con = db.connect()
    try:
        rows = con.execute(
            f"SELECT CAST(strftime('%H', ts,'unixepoch','localtime') AS INT) h, "
            f"COUNT(*) c FROM char_events{w} GROUP BY h", p
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    hist = [0] * 24
    for h, c in rows:
        hist[int(h)] = c
    best_sum, best_start = -1, 0
    for s in range(24):
        block = sum(hist[s:min(24, s + span)])
        if block > best_sum:
            best_sum, best_start = block, s
    end = min(24, best_start + span)
    return best_start, end, best_sum, _hour_window_label(best_start)


def report_bounds(period: str, start: Optional[str] = None,
                  end: Optional[str] = None):
    """(since, until, prev_since, prev_until, label) for a named period or, when
    period == 'custom', for the given YYYY-MM-DD range (prev = preceding window
    of equal length)."""
    if period == "custom":
        since, until = resolve_range("custom", start, end)
        import time as _time
        hi = until if until is not None else _time.time()
        lo = since if since is not None else hi
        length = max(hi - lo, 0)
        return since, until, (lo - length if since is not None else None), since, "自定义区间"
    return period_bounds(period)


def _build_narrative(period, label, chars, delta, best_day, best_day_count,
                     top_app, peak_window, active_days) -> str:
    pw = _PERIOD_WORD.get(period, "这段时间")
    parts = [f"{pw}你输入了 {chars:,} 个汉字"]
    if delta is not None and delta != 0:
        prev = _PREV_WORD.get(period, "上一周期")
        parts[0] += f"，比{prev}{'多' if delta > 0 else '少'} {abs(delta)}%"
    elif delta == 0:
        prev = _PREV_WORD.get(period, "上一周期")
        parts[0] += f"，与{prev}基本持平"
    sent = "。".join([parts[0]]) + "。"
    extra = []
    if best_day and best_day_count and period != "today":
        wd = _weekday_cn(best_day)
        extra.append(f"最高产的一天是{wd or best_day}（{best_day_count:,} 字）")
    if top_app:
        extra.append(f"主要输入场景是 {pretty_app(top_app)}")
    if peak_window:
        s, e, _c, lab = peak_window
        extra.append(f"{lab} {s:02d}:00–{e:02d}:00 是你的高产时段")
    if extra:
        sent += "，".join(extra) + "。"
    if chars == 0:
        return f"{pw}还没有记录到输入，先去敲几个字吧。"
    return sent


def report_fast(db, period: str, run_gap: float, session_gap: float,
                start: Optional[str] = None, end: Optional[str] = None) -> Dict:
    """Fast report fields that avoid word segmentation/topic extraction."""
    since, until, ps, pe, label = report_bounds(period, start, end)
    chars = total_chars(db, since, until)
    prev_chars = total_chars(db, ps, pe)
    delta = None if not prev_chars else round((chars - prev_chars) / prev_chars * 100, 1)

    peak_hr, peak_cnt = _peak_hour(db, since, until)
    peak_window = _peak_window(db, since, until)
    day_rows = daily(db, since, until)
    rhythm = _activity_rhythm(day_rows)
    best_day = max(day_rows, key=lambda r: r[1]) if day_rows else (None, 0)
    apps = per_app(db, since, 50, until)
    app_total = sum(c for _a, c in apps) or 1
    top_app = apps[0][0] if apps else None
    top_app_share = round(apps[0][1] / app_total * 100, 1) if apps else 0.0
    top_chars_list = top_chars(db, since, 1, until)
    fav_char = top_chars_list[0][0] if top_chars_list else None
    longest_min, _start = _longest_session(db, since, until, session_gap)
    edit_ratio = edits(db, since, until, session_gap).get("edit_ratio", 0.0)
    _cur, streak_best = _streak(_daily_map(db))
    con = db.connect()
    try:
        w, pr = _where(since, until)
        distinct_chars = con.execute(
            f"SELECT COUNT(DISTINCT ch) FROM char_events{w}", pr).fetchone()[0]
    finally:
        con.close()

    narrative = _build_narrative(period, label, chars, delta, best_day[0],
                                 best_day[1], top_app, peak_window, len(day_rows))

    return {
        "period": period,
        "label": label,
        "chars": chars,
        "delta_pct": delta,
        "active_days": len(day_rows),
        "distinct_chars": distinct_chars,
        "peak_hour": peak_hr,
        "peak_hour_count": peak_cnt,
        "peak_window": ([peak_window[0], peak_window[1], peak_window[3]]
                        if peak_window else None),
        "best_day": best_day[0],
        "best_day_count": best_day[1],
        "best_day_weekday": _weekday_cn(best_day[0]),
        **rhythm,
        "top_app": pretty_app(top_app),
        "top_app_share": top_app_share,
        "fav_char": fav_char,
        "fav_word": None,
        "longest_session_min": longest_min,
        "streak_best": streak_best,
        "narrative": narrative,
        "insights": _behavior_insights(
            label, chars, delta, top_app, top_app_share, peak_window,
            longest_min, len(day_rows), distinct_chars, edit_ratio,
        ),
        "keywords": [],
        "heavy_ready": False,
    }


def report_heavy(db, period: str, run_gap: float) -> Dict:
    """Deferred report fields backed by segmentation and TF-IDF."""
    since, until, ps, pe, _label = period_bounds(period)
    with timed(f"stats.report_heavy.{period}"):
        fav_word = _top_multichar_word(db, since, until, run_gap)
        kw = topics(db, since, 8, until)
        new_word_count, top_bigram = _card_word_extras(db, since, until, ps, pe, run_gap)
    return {
        "period": period,
        "fav_word": fav_word,
        "keywords": [w for w, _wt in kw],
        "new_word_count": new_word_count,
        "top_bigram": top_bigram,
        "heavy_ready": True,
    }


def _card_word_extras(db, since, until, ps, pe, run_gap):
    """Two punchy, share-worthy numbers for the PNG card: how many words appeared
    for the first time this period, and the period's top 双字词. Rollup-backed."""
    segment.build_words(db, run_gap)
    d0, d1 = _day_bounds(since, until)
    if d0 is None:
        return 0, None
    con = db.connect()
    try:
        rows = _range_word_rows(con, d0, d1)
        earliest = {w: dd for w, dd in con.execute(
            "SELECT word, MIN(day) FROM word_freq_daily GROUP BY word").fetchall()}
    finally:
        con.close()
    new_count = sum(1 for w, _c in rows if earliest.get(w, "9999") >= d0)
    top_bigram = next((w for w, _c in rows if len(w) == 2), None)
    return new_count, top_bigram


def _behavior_insights(label: str, chars: int, delta, top_app: Optional[str],
                       top_app_share: float, peak_window, longest_min: int,
                       active_days: int, distinct_chars: int,
                       edit_ratio: float) -> List[Dict]:
    if chars <= 0:
        return [{
            "title": "还没有形成节奏",
            "body": f"{label}暂时没有输入记录。等有几段真实输入后，这里会总结你的时间、场景和修改习惯。",
            "tone": "neutral",
        }]
    out: List[Dict] = []
    if peak_window:
        s, e, _count, bucket = peak_window
        out.append({
            "title": f"{bucket}是你的高产窗口",
            "metric": f"{s:02d}:00–{e:02d}:00",
            "body": f"{s:02d}:00–{e:02d}:00 这一段最集中。以后想复现状态，可以把重要写作任务放到这个时段附近。",
            "tone": "focus",
        })
    if top_app and top_app_share >= 55:
        out.append({
            "title": "输入场景很集中",
            "metric": pretty_app(top_app),
            "body": f"{pretty_app(top_app)} 占了这段时间 {top_app_share:.1f}% 的输入量，说明这份报告主要反映那个应用里的工作流。",
            "tone": "app",
        })
    elif top_app:
        out.append({
            "title": "输入分布比较分散",
            "metric": f"{top_app_share:.1f}%",
            "body": f"主力应用是 {pretty_app(top_app)}，但占比只有 {top_app_share:.1f}%，这段时间更像是在多个场景之间切换。",
            "tone": "app",
        })
    if delta is not None:
        if delta >= 25:
            body = f"比上一周期多 {abs(delta):.1f}%，产出有明显抬升。可以顺手看一下高频词，判断增长来自哪类内容。"
            tone = "up"
        elif delta <= -25:
            body = f"比上一周期少 {abs(delta):.1f}%。如果这是主动休息，就不用焦虑；如果不是，可以检查高产时段是否被打断。"
            tone = "down"
        else:
            body = f"和上一周期差异不大（{delta:+.1f}%），节奏相对稳定。"
            tone = "steady"
        out.append({"title": "产出节奏", "body": body, "tone": tone})
        out[-1]["metric"] = f"{delta:+.1f}%"
    if edit_ratio >= 25:
        out.append({
            "title": "修改动作偏多",
            "metric": f"{edit_ratio:.1f}%",
            "body": f"修改率约 {edit_ratio:.1f}%。这通常意味着你在边想边改，适合把草稿和整理拆成两个阶段。",
            "tone": "edit",
        })
    elif chars >= 200 and edit_ratio <= 8:
        out.append({
            "title": "输入很顺",
            "metric": f"{edit_ratio:.1f}%",
            "body": f"修改率只有 {edit_ratio:.1f}%，这段时间的内容大概率比较连贯。",
            "tone": "edit",
        })
    if longest_min >= 20:
        out.append({
            "title": "有一段沉浸输入",
            "metric": f"{longest_min} 分钟",
            "body": f"最长连续输入约 {longest_min} 分钟。这个长度已经接近一次完整专注块。",
            "tone": "focus",
        })
    if active_days >= 3:
        out.append({
            "title": "持续性不错",
            "metric": f"{active_days} 天",
            "body": f"这段时间有 {active_days} 天留下输入记录，比单日爆发更能说明习惯在稳定发生。",
            "tone": "streak",
        })
    if distinct_chars and chars >= 100:
        ratio = distinct_chars / max(chars, 1) * 100
        out.append({
            "title": "表达覆盖面",
            "metric": f"{ratio:.1f}%",
            "body": f"不同汉字占比约 {ratio:.1f}%。这个值越高，通常说明内容主题更分散或表达变化更多。",
            "tone": "language",
        })
    return out[:5]


def report(db, period: str, run_gap: float, session_gap: float) -> Dict:
    """Full report payload, preserving the historical report/card behavior."""
    data = report_fast(db, period, run_gap, session_gap)
    data.update(report_heavy(db, period, run_gap))
    return data


def _range_word_rows(con, d0, d1):
    """[(word, count)] for multi-char words in a day-aligned window."""
    return con.execute(
        "SELECT word, SUM(count) c FROM word_freq_daily "
        "WHERE day>=? AND day<=? AND length(word)>=2 GROUP BY word ORDER BY c DESC",
        (d0, d1),
    ).fetchall()


def report_words(db, period: str, run_gap: float,
                 start: Optional[str] = None, end: Optional[str] = None,
                 progress=None) -> Dict:
    """Heavy, word-level report analytics. Backed by the per-day rollups
    (fast) for word frequency / new / returning words, plus one live TF-IDF
    pass for topic keywords. ``progress(pct, phase)`` is an optional callback."""
    def _p(pct, phase):
        if progress:
            try:
                progress(pct, phase)
            except Exception:
                pass

    since, until, ps, pe, label = report_bounds(period, start, end)
    _p(8, "准备数据")
    segment.build_words(db, run_gap)          # ensure rollups are materialized
    _p(35, "汇总词频")

    d0, d1 = _day_bounds(since, until)
    pd0, pd1 = _day_bounds(ps, pe)
    con = db.connect()
    try:
        if d0 is None:                         # whole history
            rows = con.execute(
                "SELECT word, SUM(count) c FROM word_freq_daily "
                "WHERE length(word)>=2 GROUP BY word ORDER BY c DESC").fetchall()
        else:
            rows = _range_word_rows(con, d0, d1)
        earliest = {w: dd for w, dd in con.execute(
            "SELECT word, MIN(day) FROM word_freq_daily GROUP BY word").fetchall()}
        prev_words = set()
        if pd0 is not None:
            prev_words = {w for (w,) in con.execute(
                "SELECT DISTINCT word FROM word_freq_daily WHERE day>=? AND day<=?",
                (pd0, pd1)).fetchall()}
    finally:
        con.close()
    _p(55, "对比历史")

    # Fold in the still-open trailing run so the freshest words count too.
    cnt_map: Dict[str, int] = {w: int(c) for w, c in rows}
    tail_wc, _tail_pc, _tail_pw = _tail_word_pos(db, run_gap)
    lo = d0 if d0 is not None else "0000-01-01"
    hi = d1 if d1 is not None else "9999-12-31"
    for day, dwc in tail_wc.items():
        if lo <= day <= hi:
            for w, v in dwc.items():
                if len(w) >= 2:
                    cnt_map[w] = cnt_map.get(w, 0) + v
                    earliest[w] = min(earliest.get(w, day), day)
    counts = sorted(cnt_map.items(), key=lambda kv: kv[1], reverse=True)
    distinct_words = len(counts)
    bigrams = [{"word": w, "count": c} for w, c in counts if len(w) == 2][:20]
    trigrams = [{"word": w, "count": c} for w, c in counts if len(w) == 3][:20]
    longwords = [{"word": w, "count": c} for w, c in counts if len(w) >= 4][:20]

    lo_day = d0 if d0 is not None else "0000-01-01"
    new_words, returning_words = [], []
    for w, c in counts:
        first = earliest.get(w)
        if first is None:
            continue
        if first >= lo_day:                    # first ever seen inside the window
            new_words.append({"word": w, "count": c})
        elif pd0 is not None and first < pd0 and w not in prev_words:
            returning_words.append({"word": w, "count": c})
    new_words = new_words[:24]
    returning_words = returning_words[:24]
    _p(72, "提取主题")

    with timed(f"stats.report_words.topics.{period}"):
        kw = topics(db, since, 14, until)
    keywords = [{"word": w, "weight": round(float(wt), 4)} for w, wt in kw]
    pos_rows = pos_distribution_daily(db, since, until, run_gap)
    pos = [{"pos": ps2, "label": lbl, "count": c} for ps2, lbl, c in pos_rows]
    _p(100, "完成")

    return {
        "period": period,
        "label": label,
        "distinct_words": distinct_words,
        "new_words": new_words,
        "returning_words": returning_words,
        "bigrams": bigrams,
        "trigrams": trigrams,
        "long_words": longwords,
        "pos": pos,
        "keywords": keywords,
    }


# ---- board ticker (rotating facts + user phrases) -------------------------
# A hidden "blank" line. It is a zero-width space, so str.strip() keeps it (it
# is not ASCII/Unicode whitespace) and load_phrases() won't drop it, yet the
# banner renders empty — a quiet little easter egg. Landing on it unlocks an
# achievement (see gamify + the frontend ticker).
EASTER_EGG_QUOTE = chr(0x200b)  # U+200B zero-width space

# Seed lines written to phrases.txt on first run. A mix of literary,
# philosophical, romantic, cute and trivia/tips.
_DEFAULT_PHRASES = [
    "# DuckType 看板滚动文字 · 每行一句，以 # 开头的行会被忽略。",
    "# DuckType 会把这些句子和自动生成的数据事实一起轮播。",
    "",
    "# —— 文学 / 哲思 ——",
    "每一个字，都是思想落在纸上的脚印。",
    "笔落惊风雨，键响动心弦。",
    "今天写下的每一句，都是明天的回忆。",
    "字句汇成河，日久见汪洋。",
    "慢慢写，认真写，字会记得你的用心。",
    "一个人真正走远的时候，常常不是脚步先动，而是心先安静下来。",
    "人总要在某个清晨，原谅昨夜那个想太多的自己。",
    "答案有时不是被找到的，而是在一次次追问里慢慢长出来的。",
    "所谓成熟，大概是把许多话咽下去以后，仍然愿意温柔地开口。",
    "时间不回答问题，它只是把问题变成经历。",
    "很多事当时像山，后来回头看，不过是一段上坡路。",
    "真正重要的东西，往往不急着证明自己。",
    "生活不是把日子过成结论，而是在细节里慢慢练习理解。",
    "人会被一句话点亮，也会被长久的沉默照见自己。",
    "有些风景必须走过一段孤独，才看得出它的辽阔。",
    "别急着成为谁，先认真听见自己。",
    "世界很吵，能把心安放好，本身就是一种本事。",
    "念念不忘，不一定会有回响，但一定会改变回望的人。",
    "真正的告别，不是删掉名字，而是想起时不再慌张。",
    "许多遗憾后来都变成了方向，提醒我们下一次怎样珍惜。",
    "最深的理解，常常不是赞同，而是愿意多停留一会儿。",
    "命运有时像一条河，你不能命令它转弯，但可以学会划船。",
    "人这一生，总要学会在无解处继续生活。",
    "把平凡的一天过认真，就是在替未来保存证据。",
    "热爱不是永远沸腾，而是冷下来以后仍愿意靠近。",
    "",
    "# —— 爱情 / 时间 ——",
    "爱情这东西，时间很关键，认识得太早或太晚，都不行。——《2046》",
    "世上最遥远的距离，不是生与死，而是我就站在你面前，你却不知道我爱你。——《荷包里的单人床》张小娴",
    "我是天空里的一片云，偶尔投影在你的波心。——《偶然》徐志摩",
    "你我相逢在黑夜的海上，你有你的，我有我的，方向。——《偶然》徐志摩",
    "你记得也好，最好你忘掉，在这交会时互放的光亮。——《偶然》徐志摩",
    "有些人渐渐不联系了，不是淡了远了，而是没有合适的身份陪伴，没有合适的理由联系，没有合适的机会见面。",
    "有些人只能放在心里，偶尔回忆，经常想念。",
    "爱不是把一个人留在身边，而是在想起时仍愿意祝他天晴。",
    "错过有时不是惩罚，只是时间用另一种方式保存了温柔。",
    "相遇是两条河短暂并行，告别是各自奔向更宽阔的海。",
    "最好的喜欢，不是急着占有，而是愿意让对方成为自己。",
    "有人教会你爱，也有人教会你把爱放回人海。",
    "爱一个人最难的部分，可能是承认他不必按照你的期待生活。",
    "心动是一瞬间的光，长久相处才知道那束光能不能照路。",
    "有些名字不再提起，不是忘了，而是终于学会轻轻放好。",
    "时间会筛掉很多热闹，留下真正愿意并肩的人。",
    "爱若只剩执念，就该让风替它松一松手。",
    "相爱的人未必总能抵达，但真诚的片刻不会白白发生。",
    "所有来不及说出口的话，后来都在某个夜里变成了月光。",
    "人和人的缘分，常常是深一脚浅一脚地走到某个路口。",
    "爱不是答案，它更像一道题，让人一次次重新认识自己。",
    "愿你遇见的人，既懂你的沉默，也珍惜你的开口。",
    "",
    "# —— 写作 / 思考 ——",
    "写作不是把心事说尽，而是给混乱留出秩序。",
    "每一次敲键，都是把无形的念头请到人间坐一会儿。",
    "语言有边界，但沉默太辽阔，所以我们才需要写字。",
    "一个词被反复使用，可能是生活正在反复叩门。",
    "把想法写下来，是给未来的自己留一盏灯。",
    "如果今天没有答案，就先把问题写清楚。",
    "文字不一定能改变世界，但能让一个人不被世界轻易带走。",
    "思考不是为了赢过别人，而是为了少误会一点自己。",
    "好句子像窗，推开以后，心里有风。",
    "慢一点也没关系，重要的是别把自己的声音弄丢。",
    "真正的表达，是把复杂的心事交给清楚的句子。",
    "日子会过去，写下来的东西会替你留下来。",
    "",
    "# —— 冷知识 / 小贴士 ——",
    "「的」是现代汉语里使用频率最高的字。",
    "小贴士：点击高频字 / 词的条形图，可直接查看它的详情。",
    "小贴士：拖动高频面板上的滑条，可以看到更多名次。",
    "小贴士：「按小时」图可单独切换今天 / 近 24 小时 / 近 7 天。",
    "小贴士：每张图右上角的 ⬇ 可把图表存成图片。",
    "成语「一目十行」形容读得快——那你打字有多快呢？",
    "汉字数量逾八万，但日常常用的不过三千余个。",
    EASTER_EGG_QUOTE,
]


def _phrase_lines(lines: List[str]) -> List[str]:
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def load_phrases() -> List[str]:
    """Read local rotating phrases and merge in built-in defaults.

    Blank lines and ``#`` comments are ignored. Any read/write error degrades to
    the built-in defaults so the ticker never breaks the dashboard.
    """
    from ..paths import phrases_path
    p = phrases_path()
    try:
        from .quote_bank import QUOTES as quote_bank
    except Exception:
        quote_bank = ()
    defaults = _phrase_lines(_DEFAULT_PHRASES) + list(quote_bank)
    try:
        if not p.exists():
            p.write_text("\n".join(_DEFAULT_PHRASES) + "\n", encoding="utf-8")
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return defaults

    out: List[str] = []
    seen = set()
    for phrase in _phrase_lines(lines) + defaults:
        if phrase not in seen:
            out.append(phrase)
            seen.add(phrase)
    return out


def _since_last_word(db, run_gap: float) -> Optional[str]:
    """A "remember when" stat: when you last typed a notable word, and how many
    characters you've committed since. Returns None if there's nothing to show."""
    import random
    import time as _time
    words = [w for w, _c in top_words(db, None, 60, run_gap) if len(w) >= 2]
    if not words:
        return None
    word = random.choice(words[:40])
    r = search(db, word, None, run_gap)
    if not r["total"] or not r["last_seen"]:
        return None
    hours = (_time.time() - r["last_seen"]) / 3600.0
    after = total_chars(db, r["last_seen"])
    when = "不到 1 小时前" if hours < 1 else f"约 {hours:.0f} 小时前"
    return f"你上次打出「{word}」是在{when}，之后又码了 {after:,} 字。"


def ticker(db, run_gap: float, session_gap: float, daily_goal: int) -> Dict:
    """Content for the board ticker: code-generated data facts + user phrases."""
    facts: List[str] = []
    try:
        g = gamify(db, daily_goal)
        today, goal = g["today_chars"], g["daily_goal"]
        if today > 0:
            facts.append(
                f"今天已输入 {today:,} 字，已经达成今日目标。" if today >= goal
                else f"今天已输入 {today:,} 字，距离今日目标还差 {goal - today:,} 字。")
        if g["streak_current"] > 0:
            facts.append(f"已连续码字 {g['streak_current']} 天，最长纪录 {g['streak_best']} 天。")
        if g["total_chars"] > 0:
            facts.append(f"到目前为止，你一共码了 {g['total_chars']:,} 个汉字。")
        nxt = next((a for a in g["achievements"] if not a["unlocked"]), None)
        if nxt:
            facts.append(f"继续积累，就能解锁成就「{nxt['name']}」：{nxt['desc']}。")
        since, until, _ps, _pe, _lbl = period_bounds("today")
        ph, _cnt = _peak_hour(db, since, until)
        if ph is not None:
            facts.append(f"今天 {ph:02d}:00 时段你最高产。")
        fw = _top_multichar_word(db, since, until, run_gap)
        if fw:
            facts.append(f"今天你最常用的词是「{fw}」。")
        sl = _since_last_word(db, run_gap)
        if sl:
            facts.append(sl)
    except Exception:
        pass
    return {"facts": facts, "phrases": load_phrases()}


# ---- one-shot overview ----------------------------------------------------
def overview(db, since: Optional[float], run_gap: float, session_gap: float,
             until: Optional[float] = None) -> Dict:
    e = edits(db, since, until, session_gap)
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
