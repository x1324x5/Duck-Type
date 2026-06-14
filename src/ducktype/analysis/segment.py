"""Word segmentation + POS tagging, built on jieba.

The committed-character stream is reconstructed into "runs" -- maximal sequences
of characters typed in the same app without a long pause -- and each run is
segmented into words. We expose both an incremental, all-time materializer
(``build_words`` -> word_freq / pos_freq tables, fast for the "all" range) and a
live range segmenter (``segment_range``) used for time-bounded views.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:
    import jieba  # noqa: F401
    import jieba.posseg as _pseg
    import jieba.analyse as _analyse
    HAS_JIEBA = True
except Exception:  # pragma: no cover - jieba optional
    HAS_JIEBA = False

_HAN = lambda ch: "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"


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
        word_counts[w] = word_counts.get(w, 0) + 1
        word_pos[w] = flag
        pos_counts[flag] = pos_counts.get(flag, 0) + 1
    return word_counts, word_pos, pos_counts


# ---- incremental, all-time materialization -------------------------------
def build_words(db, run_gap: float = 3.0) -> None:
    """Segment any newly *closed* runs and fold them into word_freq/pos_freq."""
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
        for run in _runs_from_rows([(r[1], r[2], r[3]) for r in closed], run_gap):
            a, b, c = _segment_text(run)
            for k, v in a.items():
                total_wc[k] = total_wc.get(k, 0) + v
            total_wp.update(b)
            for k, v in c.items():
                total_pc[k] = total_pc.get(k, 0) + v

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
