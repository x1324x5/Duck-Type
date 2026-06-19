"""Tests for the in-process dashboard Api (native-window bridge backend)."""
import time

import pytest

from ducktype.analysis import segment
from ducktype.config import Config
from ducktype.dashboard.api import READ_ENDPOINTS, Api


def _api(db):
    return Api(db, Config(), status_fn=lambda: {"ok": True})


def test_board_bundles_all_sections(db, insert_chars, now):
    insert_chars(db, [(now - i * 10, ch, "Code.exe")
                      for i, ch in enumerate("今天天气很好我们去看电影吧")])
    api = _api(db)
    board = api.get("board", {"range": "all", "charN": 5, "wordN": 5})
    # gamify is range-independent and fetched on its own endpoint now (kept off
    # the board hot path so switching scales stays snappy), so it is no longer
    # bundled here.
    for key in ("overview", "trend", "daily", "top_chars", "top_words",
                "pos", "apps", "heatmap", "topics"):
        assert key in board, f"board missing {key}"
    assert "gamify" not in board
    assert "achievements" in api.get("gamify", {})
    assert board["overview"]["total_chars"] >= 1
    assert isinstance(board["top_chars"], list)


def test_board_fast_and_heavy_split_sections(db, insert_chars, now):
    insert_chars(db, [(now - i, ch, "Code.exe") for i, ch in enumerate("今天天气很好我们去看电影吧")])
    api = _api(db)
    fast = api.get("board_fast", {"range": "all", "charN": 5, "wordN": 5})
    heavy = api.get("board_heavy", {"range": "all", "charN": 5, "wordN": 5})
    assert {"overview", "trend", "daily", "top_chars", "apps", "heatmap"} <= set(fast)
    assert "gamify" not in fast  # gamify moved to its own range-independent endpoint
    assert "top_words" not in fast and "topics" not in fast and "pos" not in fast
    assert {"top_words", "topics", "pos"} <= set(heavy)


def test_board_today_includes_word_cloud_source(db, insert_chars, now):
    if not segment.HAS_JIEBA:
        pytest.skip("jieba is optional")
    text = "南京老师朋友作业时间课堂南京老师朋友作业时间课堂"
    insert_chars(db, [(now - len(text) + i, ch, "Code.exe")
                      for i, ch in enumerate(text)])
    board = _api(db).get("board", {"range": "today", "charN": 5, "wordN": 12})
    assert board["daily"] and len(board["daily"]) == 1
    assert board["top_words"]
    assert board["topics"]


def test_read_endpoints_match_shapes(db, insert_chars, now):
    insert_chars(db, [(now - i, ch, "wechat.exe") for i, ch in enumerate("你好世界你好")])
    api = _api(db)
    tc = api.get("top_chars", {"range": "all", "n": 3})
    assert tc and set(tc[0].keys()) == {"ch", "count"}
    assert api.get("overview", {"range": "all"})["total_chars"] >= 1
    assert "grid" in api.get("heatmap", {"range": "all"})


def test_revision_cache_serves_until_write(db, insert_chars, now):
    insert_chars(db, [(now, "字", "a")])
    db.revision += 1                      # simulate a committed write
    api = _api(db)
    first = api.get("overview", {"range": "all"})
    second = api.get("overview", {"range": "all"})
    assert first is second                # same object => served from cache
    db.revision += 1                      # a new write invalidates the cache
    third = api.get("overview", {"range": "all"})
    assert third is not first


def test_config_get_set_roundtrip(db):
    api = _api(db)
    cfg = api.config_get()
    assert "editable" in cfg and "daily_goal" in cfg
    res = api.config_set({"daily_goal": 1234})
    assert res["ok"] is True
    assert api.config_get()["daily_goal"] == 1234


def test_report_fast_defers_word_analytics(db, insert_chars, now):
    insert_chars(db, [(now - i, ch, "Code.exe") for i, ch in enumerate("南京老师朋友作业时间课堂")])
    api = _api(db)
    fast = api.get("report_fast", {"period": "today"})
    heavy = api.get("report_heavy", {"period": "today"})
    assert fast["heavy_ready"] is False
    assert fast["keywords"] == []
    assert fast["insights"] and {"title", "body", "tone"} <= set(fast["insights"][0])
    assert heavy["heavy_ready"] is True
    assert "fav_word" in heavy and "keywords" in heavy


def test_sequence_filters_by_app_and_lists_apps(db, insert_chars, now):
    insert_chars(db, [
        (now - 4, "甲", "Code.exe"),
        (now - 3, "乙", "Code.exe"),
        (now - 2, "丙", "Word.exe"),
        (now - 1, "丁", "Word.exe"),
    ])
    api = _api(db)
    seq = api.get("sequence", {"range": "all", "app": "Word.exe"})
    assert len(seq) == 1
    assert seq[0]["text"] == "丙丁"
    apps = api.get("sequence_apps", {"range": "all"})
    assert {"app": "Code.exe", "count": 2} in apps
    assert {"app": "Word.exe", "count": 2} in apps


def test_sequence_app_filter_keeps_app_boundaries(db, insert_chars, now):
    insert_chars(db, [
        (now - 4, "甲", "Code.exe"),
        (now - 3, "乙", "Word.exe"),
        (now - 2, "丙", "Code.exe"),
    ])
    seq = _api(db).get("sequence", {"range": "all", "app": "Code.exe"})
    assert [r["text"] for r in seq] == ["丙", "甲"]


def test_data_summary_reports_root(db, insert_chars, now):
    insert_chars(db, [(now, "字", "a")])
    s = _api(db).data_summary()
    assert s["char_rows"] >= 1
    assert "db_path" in s and "is_default" in s


def test_status_passthrough(db):
    assert _api(db).status() == {"ok": True}


def test_bridge_keeps_heavy_runtime_objects_private(db):
    api = _api(db)
    public_attrs = {k for k in vars(api) if not k.startswith("_")}
    assert not (public_attrs & {"window", "db", "config", "relocator"})


def test_tracked_endpoint_returns_group_delta_and_series(db, insert_chars, now):
    day = 86400
    insert_chars(db, [
        (now - day, "张", "a"), (now - day, "三", "a"),          # earlier
        (now, "张", "a"), (now, "三", "a"),
        (now + 1, "张", "a"), (now + 1, "三", "a"),
    ])
    api = _api(db)
    api.config_set({"tracked_terms": ["张三"], "tracked_groups": ["同事"]})
    res = api.get("tracked", {"range": "all"})
    row = res["terms"][0]
    assert row["term"] == "张三" and row["group"] == "同事"
    assert "delta_pct" in row            # present (None for the unbounded range)
    assert isinstance(row["daily"], list) and row["daily"]


def test_demo_mode_swaps_database_without_touching_real(db):
    api = _api(db)
    assert api.demo_status() == {"on": False}
    assert api.get("overview", {"range": "all"})["total_chars"] == 0
    assert api.demo_set(True) == {"on": True}
    assert api.get("overview", {"range": "all"})["total_chars"] > 0   # sample data
    assert api.demo_set(False) == {"on": False}
    assert api.get("overview", {"range": "all"})["total_chars"] == 0  # real db intact


def test_card_png_supports_long_template(db, insert_chars, now):
    insert_chars(db, [(now - i, ch, "Code.exe")
                      for i, ch in enumerate("今天天气很好我们去看电影吧")])
    api = _api(db)
    assert api.card_png("today").startswith("data:image/png;base64,")
    assert api.card_png("today", "long").startswith("data:image/png;base64,")


def test_read_endpoint_contract_matches_handlers(db):
    api = _api(db)
    assert "board" in READ_ENDPOINTS
    assert all(hasattr(api, "_r_" + endpoint) for endpoint in READ_ENDPOINTS)
    assert api.get("missing") == {"error": "unknown endpoint missing"}
