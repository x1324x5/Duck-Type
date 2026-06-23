"""Committed-character sequence reconstruction, keyword search and the
user-tracked-term counter. All three rebuild the typed runs the same way (a
match never spans a pause or app switch) and are independent of jieba.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import segment
from .statutil import _where, pretty_app


def sequence_runs(db, since: Optional[float], run_gap: float,
                  until: Optional[float] = None) -> List[str]:
    """The typed sequence reconstructed into run strings (newest pauses split)."""
    con = db.connect()
    try:
        rows = segment._bounded_rows(con, "ts, ch, app", since, until)
    finally:
        con.close()
    return list(segment._runs_from_rows(rows, run_gap))


def _app_filter_set(app_filter) -> Optional[set]:
    """Normalise the sequence app filter into a set of app names, or None for
    "all apps". Accepts a single string (possibly comma-separated) or a list."""
    if app_filter is None:
        return None
    if isinstance(app_filter, str):
        names = [a.strip() for a in app_filter.split(",")]
    else:
        names = [str(a).strip() for a in app_filter]
    names = [a for a in names if a]
    return set(names) if names else None


def sequence_recent(db, since: Optional[float], run_gap: float, limit: int = 200,
                    until: Optional[float] = None,
                    app_filter=None, keyword: Optional[str] = None) -> List[Dict]:
    """Most recent runs (for the timeline view), each with its start time + app.

    ``app_filter`` keeps only runs from the given app(s) -- a single name, a
    comma-separated string, or a list (None/empty = all). ``keyword`` further
    keeps only runs whose text contains that substring."""
    apps = _app_filter_set(app_filter)
    kw = (keyword or "").strip()
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

    def _emit():
        if apps is not None and (last_app or "") not in apps:
            return
        text = "".join(cur)
        if kw and kw not in text:
            return
        runs.append({"ts": start_ts, "app": last_app, "text": text})

    for ts, ch, app in rows:
        if cur and (last_ts is not None and (ts - last_ts > run_gap or app != last_app)):
            _emit()
            cur = []
            start_ts = None
        if not cur:
            start_ts = ts
        cur.append(ch)
        last_ts, last_app = ts, app
    if cur:
        _emit()
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
