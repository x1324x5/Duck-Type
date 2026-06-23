"""Tests for the 0.3.0 board insights (vocab growth, app efficiency, rhythm)."""
import time
from datetime import datetime, timedelta

from ducktype.analysis import insights, stats


def _day_ts(days_ago, hour=10):
    d = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    return d.timestamp()


def test_vocab_growth_is_monotone_and_reexported(db, insert_chars):
    # Two words on an older day, a new one later -> cumulative must not decrease.
    rows = []
    base = _day_ts(10)
    for i, ch in enumerate("海阔天空"):       # 海阔 / 天空
        rows.append((base + i, ch, "Code"))
    base2 = _day_ts(3)
    for i, ch in enumerate("脚踏实地"):
        rows.append((base2 + i, ch, "Code"))
    insert_chars(db, rows)

    assert stats.vocab_growth is insights.vocab_growth   # facade re-export
    out = stats.vocab_growth(db, 2.5)
    pts = out["points"]
    assert pts, "expected a growth curve"
    cums = [p["words"] for p in pts]
    assert cums == sorted(cums)                  # monotone non-decreasing
    assert out["total_chars"] >= 4
    assert out["total_words"] >= 1


def test_app_efficiency_per_app_speed(db, insert_chars):
    base = _day_ts(1)
    rows = [(base + i * 2, "字", "Code") for i in range(80)]      # steady, slower
    rows += [(base + 5000 + i * 0.5, "字", "WeChat") for i in range(80)]  # bursty, faster
    insert_chars(db, rows)
    out = stats.app_efficiency(db, base - 86400, None, 60.0, n=8, min_chars=10)
    apps = {r["app"]: r for r in out}
    assert "Code" in apps and "WeChat" in apps
    assert apps["WeChat"]["cpm"] >= apps["Code"]["cpm"]
    # Sorted fastest-first.
    assert out[0]["cpm"] == max(r["cpm"] for r in out)


def test_weekday_rhythm_buckets(db, insert_chars):
    # Put everything on a known weekday so weekday/weekend split is determinate.
    monday = datetime.now() - timedelta(days=datetime.now().weekday())
    monday = monday.replace(hour=9, minute=0, second=0, microsecond=0)
    rows = [(monday.timestamp() + i, "字", "Code") for i in range(50)]
    insert_chars(db, rows)
    out = stats.weekday_rhythm(db, monday.timestamp() - 86400, None)
    assert out["has_data"]
    assert len(out["by_weekday"]) == 7
    assert out["by_weekday"][0]["total"] == 50      # index 0 == 周一 (Monday)
    assert out["weekday_avg"] > 0
