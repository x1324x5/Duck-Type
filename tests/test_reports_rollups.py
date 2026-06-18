"""Tests for 0.2.3: per-day word rollups, achievement timestamps, and the
enriched report fields."""
import time

import pytest

from ducktype.analysis import segment, stats


def _seed(db, insert_chars, now):
    # Two distinct same-day runs separated by a gap (so build_words closes the
    # first run and materializes it into the rollup tables).
    run1 = "今天我们去公园散步看花"
    run2 = "明天继续写代码学习编程"
    rows = [(now - 200 + i * 0.1, ch, "note.exe") for i, ch in enumerate(run1)]
    rows += [(now - 50 + i * 0.1, ch, "code.exe") for i, ch in enumerate(run2)]
    insert_chars(db, rows)


def test_rollup_matches_live_segmentation(db, insert_chars, now):
    if not segment.HAS_JIEBA:
        pytest.skip("jieba is optional")
    _seed(db, insert_chars, now)
    segment.build_words(db, 3.0)
    # Ground truth: a live pass over the whole window (rollup of closed runs +
    # the live tail must reconstruct exactly this). Multi-char words only.
    wc, _wp, _pc = segment.segment_range(db, now - 86400, 3.0, None)
    live = {w: c for w, c in wc.items() if len(w) >= 2}
    roll = dict(stats.top_words_daily(db, now - 86400, None, 1000, 3.0))
    assert roll == live


def test_pretty_app_strips_exe():
    assert stats.pretty_app("Obsidian.exe") == "Obsidian"
    assert stats.pretty_app("CODE.EXE") == "CODE"
    assert stats.pretty_app("微信") == "微信"
    assert stats.pretty_app(None) is None


def test_record_achievements_idempotent_and_persists(db):
    first = db.record_achievements(["k1", "k5"])
    assert set(first) == {"k1", "k5"}
    ts = first["k1"]
    time.sleep(0.01)
    second = db.record_achievements(["k1", "k5"])  # same ids again
    assert second["k1"] == ts  # original timestamp preserved
    third = db.record_achievements(["streak3"])
    assert set(third) == {"k1", "k5", "streak3"}


def test_gamify_reports_unlocked_at_and_category(db, insert_chars, now):
    insert_chars(db, [(now, "字", "a")])
    g = stats.gamify(db, 100)
    first = next(a for a in g["achievements"] if a["id"] == "first_word")
    assert first["unlocked"] and first["unlocked_at"] is not None
    assert first["category"] == "字数"


def test_report_fast_has_narrative_and_pretty_app(db, insert_chars, now):
    _seed(db, insert_chars, now)
    r = stats.report_fast(db, "today", 3.0, 60.0)
    assert r["narrative"]
    assert r["top_app"] and not r["top_app"].lower().endswith(".exe")
    assert r["distinct_chars"] >= 1


def test_report_words_classifies_new_words(db, insert_chars, now):
    if not segment.HAS_JIEBA:
        pytest.skip("jieba is optional")
    _seed(db, insert_chars, now)
    segment.build_words(db, 3.0)
    rw = stats.report_words(db, "today", 3.0)
    assert rw["distinct_words"] >= 1
    # Everything is brand new on the first day, so new_words is non-empty.
    assert rw["new_words"]
