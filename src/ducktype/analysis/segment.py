"""Word segmentation + POS tagging, built on jieba.

The committed-character stream is reconstructed into "runs" -- maximal sequences
of characters typed in the same app without a long pause -- and each run is
segmented into words. We expose both an incremental, all-time materializer
(``build_words`` -> word_freq / pos_freq tables, fast for the "all" range) and a
live range segmenter (``segment_range``) used for time-bounded views.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import jieba  # noqa: F401
    import jieba.posseg as _pseg
    import jieba.analyse as _analyse
    HAS_JIEBA = True
except Exception:  # pragma: no cover - jieba optional
    HAS_JIEBA = False

_HAN = lambda ch: "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"

# Bump when segmentation/POS logic changes so the materialized word_freq/pos_freq
# caches rebuild instead of keeping stale tags (see build_words).
_SEG_VERSION = "3"

# jieba's bundled dictionary mistags a handful of very common words (its tags come
# from one corpus and have known per-word errors). Correct only clear, high-
# frequency mistakes so the POS breakdown isn't misleading -- e.g. it tags 位置 /
# 东西 as verb / place-name, 可以 as conjunction. Keep this conservative.
_POS_OVERRIDE = {
    "位置": "n", "东西": "n", "想法": "n", "城市": "n", "地方": "n",
    "可以": "v", "好看": "a", "开心": "a", "难看": "a", "大家": "r",
}


def _pos_tag(word: str, flag: str) -> str:
    """jieba's POS for ``word`` with our small correction table applied."""
    return _POS_OVERRIDE.get(word, flag)


def _runs_from_rows(rows: List[Tuple[float, str, Optional[str]]], run_gap: float):
    """Yield run strings from (ts, ch, app) rows ordered by time."""
    cur: List[str] = []
    last_ts: Optional[float] = None
    last_app = None
    for ts, ch, app in rows:
        if cur and (last_ts is not None and (ts - last_ts > run_gap or app != last_app)):
            yield "".join(cur)
            cur = []
        cur.append(ch)
        last_ts, last_app = ts, app
    if cur:
        yield "".join(cur)


def _runs_with_ts_from_rows(rows, run_gap: float):
    """Like ``_runs_from_rows`` but yields ``(first_ts, run_string)`` so callers
    can attribute a run to a day. ``rows`` are (ts, ch, app) ordered by time."""
    cur = []
    first_ts = None
    last_ts = None
    last_app = None
    for ts, ch, app in rows:
        if cur and (last_ts is not None and (ts - last_ts > run_gap or app != last_app)):
            yield first_ts, "".join(cur)
            cur = []
            first_ts = None
        if not cur:
            first_ts = ts
        cur.append(ch)
        last_ts, last_app = ts, app
    if cur:
        yield first_ts, "".join(cur)


def _segment_text(text: str):
    """Return (word_counts, word_pos, pos_counts) for one run string."""
    word_counts: Dict[str, int] = {}
    word_pos: Dict[str, str] = {}
    pos_counts: Dict[str, int] = {}
    if not HAS_JIEBA:
        # Degrade gracefully: treat every Han char as its own "word".
        for ch in text:
            if _HAN(ch):
                word_counts[ch] = word_counts.get(ch, 0) + 1
                word_pos[ch] = "x"
                pos_counts["x"] = pos_counts.get("x", 0) + 1
        return word_counts, word_pos, pos_counts
    for w, flag in _pseg.cut(text):
        w = w.strip()
        if not w or not any(_HAN(c) for c in w):
            continue
        flag = _pos_tag(w, flag)
        word_counts[w] = word_counts.get(w, 0) + 1
        word_pos[w] = flag
        pos_counts[flag] = pos_counts.get(flag, 0) + 1
    return word_counts, word_pos, pos_counts


def _segment_text_pos_words(text: str):
    """Return {pos: {word: count}} for one run string."""
    out: Dict[str, Dict[str, int]] = {}
    if not HAS_JIEBA:
        for ch in text:
            if _HAN(ch):
                bucket = out.setdefault("x", {})
                bucket[ch] = bucket.get(ch, 0) + 1
        return out
    for w, flag in _pseg.cut(text):
        w = w.strip()
        if not w or not any(_HAN(c) for c in w):
            continue
        bucket = out.setdefault(_pos_tag(w, flag), {})
        bucket[w] = bucket.get(w, 0) + 1
    return out


# ---- incremental, all-time materialization -------------------------------
def build_words(db, run_gap: float = 3.0) -> None:
    """Segment any newly *closed* runs and fold them into word_freq/pos_freq."""
    # If the segmentation/POS logic changed, throw away the materialized caches and
    # rebuild from scratch so corrected tags apply to historical data too.
    if db.get_meta("seg_version") != _SEG_VERSION:
        con = db.connect()
        try:
            con.execute("DELETE FROM word_freq")
            con.execute("DELETE FROM pos_freq")
            con.execute("DELETE FROM word_freq_daily")
            con.execute("DELETE FROM pos_freq_daily")
            con.execute("DELETE FROM pos_word_freq_daily")
            con.commit()
        finally:
            con.close()
        db.set_meta("word_cursor", "0")
        db.set_meta("seg_version", _SEG_VERSION)
    cursor = int(db.get_meta("word_cursor", "0") or "0")
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT id, ts, ch, app FROM char_events WHERE id>? ORDER BY id",
            (cursor,),
        ).fetchall()
        if len(rows) < 2:
            return

        # Find the last definite run boundary; only process complete runs so we
        # never split a word across two incremental builds.
        last_boundary = None  # index of first row of the still-open trailing run
        for i in range(1, len(rows)):
            _, ts, _, app = rows[i]
            _, pts, _, papp = rows[i - 1]
            if ts - pts > run_gap or app != papp:
                last_boundary = i
        if last_boundary is None:
            return  # everything so far is one open run; wait for more

        closed = rows[:last_boundary]
        new_cursor = closed[-1][0]
        total_wc: Dict[str, int] = {}
        total_wp: Dict[str, str] = {}
        total_pc: Dict[str, int] = {}
        # Per-day rollups, attributing each run to the local day of its first char.
        day_wc: Dict[str, Dict[str, int]] = {}
        day_pc: Dict[str, Dict[str, int]] = {}
        day_pw: Dict[str, Dict[Tuple[str, str], int]] = {}
        for first_ts, run in _runs_with_ts_from_rows(
                [(r[1], r[2], r[3]) for r in closed], run_gap):
            a, b, c = _segment_text(run)
            day = datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d")
            dwc = day_wc.setdefault(day, {})
            dpc = day_pc.setdefault(day, {})
            dpw = day_pw.setdefault(day, {})
            for k, v in a.items():
                total_wc[k] = total_wc.get(k, 0) + v
                dwc[k] = dwc.get(k, 0) + v
                pos = b.get(k) or "x"
                key = (pos, k)
                dpw[key] = dpw.get(key, 0) + v
            total_wp.update(b)
            for k, v in c.items():
                total_pc[k] = total_pc.get(k, 0) + v
                dpc[k] = dpc.get(k, 0) + v

        for word, cnt in total_wc.items():
            con.execute(
                "INSERT INTO word_freq(word, count, pos) VALUES (?,?,?) "
                "ON CONFLICT(word) DO UPDATE SET count=count+excluded.count, pos=excluded.pos",
                (word, cnt, total_wp.get(word)),
            )
        for pos, cnt in total_pc.items():
            con.execute(
                "INSERT INTO pos_freq(pos, count) VALUES (?,?) "
                "ON CONFLICT(pos) DO UPDATE SET count=count+excluded.count",
                (pos, cnt),
            )
        for day, dwc in day_wc.items():
            for word, cnt in dwc.items():
                con.execute(
                    "INSERT INTO word_freq_daily(day, word, pos, count) VALUES (?,?,?,?) "
                    "ON CONFLICT(day, word) DO UPDATE SET "
                    "count=count+excluded.count, pos=excluded.pos",
                    (day, word, total_wp.get(word), cnt),
                )
        for day, dpc in day_pc.items():
            for pos, cnt in dpc.items():
                con.execute(
                    "INSERT INTO pos_freq_daily(day, pos, count) VALUES (?,?,?) "
                    "ON CONFLICT(day, pos) DO UPDATE SET count=count+excluded.count",
                    (day, pos, cnt),
                )
        for day, dpw in day_pw.items():
            for (pos, word), cnt in dpw.items():
                con.execute(
                    "INSERT INTO pos_word_freq_daily(day, pos, word, count) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(day, pos, word) DO UPDATE SET "
                    "count=count+excluded.count",
                    (day, pos, word, cnt),
                )
        con.commit()
        db.set_meta("word_cursor", str(new_cursor))
    finally:
        con.close()


def _bounded_rows(con, cols: str, since: Optional[float], until: Optional[float]):
    clauses, params = [], []
    if since is not None:
        clauses.append("ts>=?"); params.append(since)
    if until is not None:
        clauses.append("ts<?"); params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return con.execute(
        f"SELECT {cols} FROM char_events{where} ORDER BY ts", tuple(params)
    ).fetchall()


# ---- live range segmentation ---------------------------------------------
def segment_range(db, since: Optional[float], run_gap: float = 3.0,
                  until: Optional[float] = None):
    """Segment all characters in [since, until) live. Returns (word_counts, word_pos, pos_counts)."""
    con = db.connect()
    try:
        rows = _bounded_rows(con, "ts, ch, app", since, until)
    finally:
        con.close()
    total_wc: Dict[str, int] = {}
    total_wp: Dict[str, str] = {}
    total_pc: Dict[str, int] = {}
    for run in _runs_from_rows(rows, run_gap):
        a, b, c = _segment_text(run)
        for k, v in a.items():
            total_wc[k] = total_wc.get(k, 0) + v
        total_wp.update(b)
        for k, v in c.items():
            total_pc[k] = total_pc.get(k, 0) + v
    return total_wc, total_wp, total_pc


def segment_pos_words_range(db, since: Optional[float], run_gap: float = 3.0,
                            until: Optional[float] = None):
    """Segment all characters in [since, until) into {pos: {word: count}}."""
    con = db.connect()
    try:
        rows = _bounded_rows(con, "ts, ch, app", since, until)
    finally:
        con.close()
    out: Dict[str, Dict[str, int]] = {}
    for run in _runs_from_rows(rows, run_gap):
        pw = _segment_text_pos_words(run)
        for pos, words in pw.items():
            bucket = out.setdefault(pos, {})
            for word, count in words.items():
                bucket[word] = bucket.get(word, 0) + count
    return out


def topics(db, since: Optional[float], topk: int = 25,
           until: Optional[float] = None):
    """Return [(keyword, weight)] capturing the dominant topics of the period."""
    con = db.connect()
    try:
        rows = _bounded_rows(con, "ch", since, until)
    finally:
        con.close()
    text = "".join(r[0] for r in rows)
    if not text:
        return []
    if not HAS_JIEBA:
        return []
    return list(_analyse.extract_tags(text, topK=topk, withWeight=True))
