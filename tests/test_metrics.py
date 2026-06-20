"""C1 daily_metrics rollup: it must agree with a full live computation and only
materialize *closed* days (the current day stays live)."""
from datetime import datetime, timedelta

from ducktype.analysis import metrics, stats


def _at(days_ago, hour, minute=0):
    base = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (base - timedelta(days=days_ago)).timestamp()


def _live_ref(db, session_gap=60.0):
    con = db.connect()
    try:
        ts = [r[0] for r in con.execute(
            "SELECT ts FROM char_events ORDER BY ts").fetchall()]
        kinds = dict(con.execute(
            "SELECT kind, COUNT(*) FROM key_events GROUP BY kind").fetchall())
    finally:
        con.close()
    return metrics._metrics_from_ts(
        ts, kinds.get("backspace", 0), kinds.get("delete", 0), session_gap)


def test_rollup_matches_live_and_keeps_today_open(db, insert_chars, insert_keys):
    rows = []
    rows += [(_at(2, 10) + i, "字", "a.exe") for i in range(30)]   # closed day, sess 1
    rows += [(_at(2, 15) + i, "好", "a.exe") for i in range(20)]   # closed day, sess 2
    rows += [(_at(1, 9) + i, "写", "a.exe") for i in range(40)]    # closed day
    rows += [(_at(0, 8) + i, "码", "a.exe") for i in range(15)]    # today (open)
    insert_chars(db, rows)
    insert_keys(db, [(_at(1, 9, 1), "backspace", "a.exe"),
                     (_at(2, 10, 1), "delete", "a.exe")])

    agg = metrics.aggregate(db, None, None, 60.0)
    ref = _live_ref(db)

    # No session crosses midnight here, so rollup == live exactly.
    assert agg["chars"] == ref["chars"] == 105
    assert agg["backspace"] == ref["backspace"] == 1
    assert agg["delete"] == ref["delete"] == 1
    assert agg["sessions"] == ref["sessions"]
    assert abs(agg["active_minutes"] - ref["active_sec"] / 60.0) < 0.2

    # Only the two fully-closed past days are materialized; today stays live.
    con = db.connect()
    try:
        days = [r[0] for r in con.execute(
            "SELECT day FROM daily_metrics ORDER BY day").fetchall()]
    finally:
        con.close()
    today = datetime.now().strftime("%Y-%m-%d")
    assert today not in days
    assert len(days) == 2

    # daily() (now rollup-backed) still sums to the true total.
    assert sum(c for _, c in stats.daily(db, None, None)) == 105


def test_rollup_empty_db(db):
    agg = metrics.aggregate(db, None, None, 60.0)
    assert agg == {"chars": 0, "backspace": 0, "delete": 0,
                   "active_minutes": 0.0, "sessions": 0, "cpm": 0.0, "peak_cpm": 0.0}
    assert stats.daily(db, None, None) == []
