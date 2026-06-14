"""Analysis-layer tests. These run without jieba (segmentation degrades to
per-character) and without flask, so they are CI-friendly on any OS."""
import time
from datetime import datetime, timedelta

from ducktype.analysis import stats


def test_resolve_range_presets():
    assert stats.resolve_range("all") == (None, None)
    since, until = stats.resolve_range("today")
    assert since is not None and until is None
    since, until = stats.resolve_range("7d")
    assert since is not None and until is None


def test_resolve_range_custom():
    since, until = stats.resolve_range("custom", "2026-01-01", "2026-01-31")
    assert since == datetime(2026, 1, 1).timestamp()
    # 'until' is exclusive end-of-day -> start of Feb 1.
    assert until == datetime(2026, 2, 1).timestamp()


def test_total_and_top_chars(db, insert_chars, now):
    insert_chars(db, [(now, "鸭", "a.exe"), (now, "鸭", "a.exe"), (now, "子", "a.exe")])
    assert stats.total_chars(db, None) == 3
    top = dict(stats.top_chars(db, None, 10))
    assert top["鸭"] == 2 and top["子"] == 1


def test_until_bound_excludes_future(db, insert_chars, now):
    insert_chars(db, [(now - 100, "早", None), (now + 100, "晚", None)])
    # window ending "now" should only see the earlier char.
    assert stats.total_chars(db, now - 200, now) == 1


def test_sequence_runs_split_on_gap(db, insert_chars, now):
    insert_chars(db, [
        (now, "你", "a"), (now + 1, "好", "a"),       # one run
        (now + 100, "世", "a"), (now + 101, "界", "a"),  # gap -> new run
    ])
    runs = stats.sequence_runs(db, None, run_gap=3.0)
    assert runs == ["你好", "世界"]


def test_sequence_recent_newest_first(db, insert_chars, now):
    insert_chars(db, [(now, "甲", "a"), (now + 100, "乙", "a")])
    recent = stats.sequence_recent(db, None, run_gap=3.0)
    assert recent[0]["text"] == "乙"  # newest first
    assert recent[1]["text"] == "甲"


def test_edits_ratio(db, insert_chars, insert_keys, now):
    insert_chars(db, [(now, "字", None)] * 10)
    insert_keys(db, [(now, "backspace", None)] * 2 + [(now, "delete", None)])
    e = stats.edits(db, None)
    assert e["chars"] == 10 and e["backspace"] == 2 and e["delete"] == 1
    assert e["edit_ratio"] == 0.3


def test_gamify_streak_and_goal(db, insert_chars):
    today = datetime.now().date()
    rows = []
    for d in range(3):  # today, yesterday, day before -> streak 3
        ts = datetime.combine(today - timedelta(days=d), datetime.min.time()).timestamp() + 3600
        rows += [(ts, "鸭", None)] * 5
    insert_chars(db, rows)
    g = stats.gamify(db, daily_goal=4)
    assert g["streak_current"] == 3
    assert g["today_chars"] == 5
    assert g["goal_pct"] == 1.0  # 5/4 capped at 1.0
    assert any(a["id"] == "streak3" and a["unlocked"] for a in g["achievements"])


def test_trend_compares_previous_window(db, insert_chars, now):
    # current window [now-10, now): 3 chars; previous [now-20, now-10): 1 char
    insert_chars(db, [(now - 5, "a", None), (now - 4, "b", None), (now - 3, "c", None)])
    insert_chars(db, [(now - 15, "x", None)])
    t = stats.trend(db, now - 10, now, run_gap=3.0, session_gap=60.0)
    assert t["current"]["chars"] == 3
    assert t["previous"]["chars"] == 1
    assert t["delta_pct"]["chars"] == 200.0


def test_trend_none_for_all(db):
    assert stats.trend(db, None, None, 3.0, 60.0) is None


def test_fun_rankings_hapax_and_rare(db, insert_chars, now):
    # 㐀 (U+3400) is in CJK Extension A -> counts as rare.
    insert_chars(db, [(now, "鸭", None), (now, "鸭", None), (now, "孤", None), (now, "㐀", None)])
    f = stats.fun_rankings(db, None, run_gap=3.0)
    assert "孤" in f["hapax"]
    assert "鸭" not in f["hapax"]
    assert any(rc["ch"] == "㐀" for rc in f["rare_chars"])
