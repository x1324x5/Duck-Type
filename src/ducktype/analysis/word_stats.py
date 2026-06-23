"""Word / POS / topic statistics (these use jieba segmentation).

Both the live-range variants and the rollup-backed day-aligned variants (which
the board uses to avoid running jieba on every range switch) live here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import segment
from ..perf import timed
from .statutil import _day_bounds


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
