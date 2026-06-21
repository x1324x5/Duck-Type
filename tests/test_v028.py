"""Tests for the 0.2.8 additions: hotkey parsing/normalisation, dashboard usage
log + analytics, and the comparison report."""
import time
from datetime import datetime, timedelta

from ducktype import hotkeys
from ducktype.config import _normalise_hotkey
from ducktype.analysis import reporting


# ---- hotkey spec normalisation + parsing ----
def test_normalise_hotkey_canonicalises_and_orders():
    assert _normalise_hotkey("alt+ctrl+d") == "Ctrl+Alt+D"
    assert _normalise_hotkey("WIN+shift+F5") == "Shift+Win+F5"
    assert _normalise_hotkey("ctrl+space") == "Ctrl+Space"


def test_normalise_hotkey_rejects_invalid():
    assert _normalise_hotkey("d") == ""            # no modifier -> would hijack typing
    assert _normalise_hotkey("ctrl") == ""         # modifier only
    assert _normalise_hotkey("ctrl+alt+x+y") == ""  # two main keys
    assert _normalise_hotkey("") == ""
    assert _normalise_hotkey("ctrl+nope") == ""


def test_parse_spec_modifiers_and_vk():
    mods, vk = hotkeys.parse_spec("Ctrl+Alt+D")
    assert mods & hotkeys.MOD_CONTROL and mods & hotkeys.MOD_ALT
    assert mods & hotkeys.MOD_NOREPEAT
    assert vk == ord("D")
    assert hotkeys.parse_spec("") is None
    assert hotkeys.parse_spec("D") is None        # no modifier
    f5 = hotkeys.parse_spec("Shift+F5")
    assert f5 is not None and f5[1] == 0x74        # VK_F5


# ---- dashboard usage log ----
def test_dashboard_opens_recorded_and_queried(db):
    db.record_dashboard_open("dashboard")
    db.record_dashboard_open("mini")
    db.record_dashboard_open("dashboard")
    rows = db.dashboard_opens(None)
    assert len(rows) == 3
    kinds = [k for _ts, k in rows]
    assert kinds.count("mini") == 1 and kinds.count("dashboard") == 2


def test_dashboard_usage_summary(db):
    now = time.time()
    # two opens today, one mini "yesterday"
    con = db.connect()
    con.executemany(
        "INSERT INTO dashboard_sessions(ts, kind) VALUES (?,?)",
        [(now, "dashboard"), (now, "dashboard"), (now - 86400, "mini")],
    )
    con.commit()
    con.close()
    u = reporting.dashboard_usage(db, days=30)
    assert u["total"] == 3
    assert u["dashboard"] == 2 and u["mini"] == 1
    assert u["active_days"] == 2
    assert len(u["per_day"]) == 30
    assert len(u["by_hour"]) == 24 and len(u["by_weekday"]) == 7
    assert u["recent"][0]["ts"] >= u["recent"][-1]["ts"]   # newest first


def test_dashboard_usage_empty(db):
    u = reporting.dashboard_usage(db)
    assert u["total"] == 0 and u["first_ts"] is None and u["busiest_day"] is None


# ---- comparison report ----
def test_compare_bounds_named_and_day():
    s, u, label = reporting.compare_bounds({"kind": "yesterday"})
    assert s is not None and u is not None and u - s == 86400
    assert label == "昨天"
    s2, u2, label2 = reporting.compare_bounds({"kind": "day", "day": "2026-01-15"})
    assert label2 == "2026-01-15" and u2 - s2 == 86400


def test_report_compare_metrics_and_deltas(db, insert_chars):
    today0 = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    yest0 = today0 - timedelta(days=1)
    # today: 4 chars, yesterday: 2 chars -> +100%
    insert_chars(db, [(today0.timestamp() + i, c, "app.exe")
                      for i, c in enumerate("写字记录")])
    insert_chars(db, [(yest0.timestamp() + i, c, "app.exe")
                      for i, c in enumerate("写字")])
    r = reporting.report_compare(db, {"kind": "today"}, {"kind": "yesterday"},
                                 run_gap=3.0, session_gap=60.0)
    assert r["a"]["chars"] == 4 and r["b"]["chars"] == 2
    assert r["deltas"]["chars"] == 100.0
    assert r["a"]["label"] == "今天" and r["b"]["label"] == "昨天"
    assert isinstance(r["narrative"], str) and r["narrative"]
