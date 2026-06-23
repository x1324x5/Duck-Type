"""Periodic reports (today / week / month / year), trend comparison and the
behaviour-insight + narrative generation that turns the numbers into prose.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import idioms as idioms_mod
from . import segment
from ..perf import timed
from .time_ranges import day_start as _day_start
from .time_ranges import resolve_range
from .statutil import (
    _day_bounds, _hour_window_label, _PERIOD_WORD, _PREV_WORD, _WEEKDAYS,
    _weekday_cn, _where, pretty_app,
)
from .char_stats import daily, per_app, top_chars, total_chars
from .edit_stats import edits, efficiency
from .gamify import _daily_map, _streak
from .word_stats import (
    pos_distribution_daily, _tail_word_pos, top_words, topics,
)


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
    longwords = [{"word": w, "count": c} for w, c in counts
                 if len(w) >= 4 and not idioms_mod.is_idiom(w)][:20]

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

    # ---- 0.2.8 report redesign: vocabulary-growth analytics ----
    # Distinct multi-char words bucketed by length (the 词长构成 ring).
    length_dist = {"two": 0, "three": 0, "four_plus": 0}
    for _w, _c in counts:
        L = len(_w)
        length_dist["two" if L == 2 else "three" if L == 3 else "four_plus"] += 1
    # When each *new* word first appeared in the window (a "翻日记" timeline).
    _tl: Dict[str, list] = {}
    for nw in new_words:
        d = earliest.get(nw["word"])
        if d:
            _tl.setdefault(d, []).append(nw["word"])
    new_word_timeline = [{"date": d, "count": len(ws), "words": ws[:10]}
                         for d, ws in sorted(_tl.items())]
    # Cumulative distinct words used over the window (the 词汇增长 curve). Only for
    # bounded windows; "all" is skipped (the running union would be unbounded work).
    vocab_growth = []
    if d0 is not None:
        con2 = db.connect()
        try:
            pairs = con2.execute(
                "SELECT day, word FROM word_freq_daily "
                "WHERE day>=? AND day<=? AND length(word)>=2 ORDER BY day",
                (d0, d1 if d1 is not None else "9999-12-31")).fetchall()
        finally:
            con2.close()
        seen, per_day = set(), {}
        for day, w in pairs:
            seen.add(w)
            per_day[day] = len(seen)
        vocab_growth = [{"date": d, "cumulative": per_day[d]} for d in sorted(per_day)]
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
        "length_dist": length_dist,
        "new_word_timeline": new_word_timeline,
        "vocab_growth": vocab_growth,
    }
