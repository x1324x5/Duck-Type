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
    assert e["raw_edits"] == 3 and e["edits"] == 3
    assert e["edit_ratio"] == 0.3


def test_edits_ratio_ignores_empty_backspace(db, insert_chars, insert_keys, now):
    insert_keys(db, [(now, "backspace", "a.exe")] * 5)
    insert_chars(db, [(now + 1, "字", "a.exe")])
    e = stats.edits(db, None)
    assert e["backspace"] == 5
    assert e["raw_edits"] == 5
    assert e["edits"] == 0
    assert e["edit_ratio"] == 0.0


def test_edits_ratio_resets_across_session_gap(db, insert_chars, insert_keys, now):
    insert_chars(db, [(now, "旧", "a.exe")])
    insert_keys(db, [(now + 120, "backspace", "a.exe")])
    e = stats.edits(db, None, session_gap=60.0)
    assert e["raw_edits"] == 1
    assert e["edits"] == 0


def test_edits_ratio_ignores_settled_char_deletion(db, insert_chars, insert_keys, now):
    # A Han char typed, then a backspace 30s later (same session, but the char
    # is "settled"): the backspace is editing other content, not that char.
    insert_chars(db, [(now, "字", "a.exe")])
    insert_keys(db, [(now + 30, "backspace", "a.exe")])
    e = stats.edits(db, None, session_gap=60.0)
    assert e["raw_edits"] == 1
    assert e["edits"] == 0


def test_efficiency_average_excludes_idle_floor(db, insert_chars, now):
    # 10 chars one second apart: 9s of active typing, not a floored minute.
    insert_chars(db, [(now + i, "字", None) for i in range(10)])
    e = stats.efficiency(db, None, session_gap=60.0)
    assert e["sessions"] == 1
    assert e["active_minutes"] == 0.1          # 9s, no 60s-per-session floor
    assert e["cpm"] == 66.7                     # 10 chars / 0.15 min
    assert e["peak_cpm"] >= e["cpm"]            # peak never below average


def test_efficiency_word_commit_does_not_explode_peak(db, insert_chars, now):
    # A whole 12-char word commits at ONE timestamp (as real IMEs do), preceded
    # by ~3s of pinyin typing. The old instantaneous-rate peak divided by ~0 and
    # blew up to millions; the windowed count must stay finite and sane.
    rows = [(now, "起", None)]                       # earlier char
    rows += [(now + 3, "字", None) for _ in range(12)]  # 12-char word, one ts
    rows += [(now + 4, "好", None)]
    insert_chars(db, rows)
    e = stats.efficiency(db, None, session_gap=60.0)
    assert e["peak_cpm"] < 1000                      # bounded, not millions
    assert e["peak_cpm"] == 210.0                    # 14 chars over 4 active s
    assert e["peak_cpm"] >= e["cpm"]


def test_efficiency_peak_is_best_minute(db, insert_chars, now):
    # A dense minute (120 chars in ~60s) plus a sparse tail far later. Peak is
    # the dense minute; average is dragged down by the sparse part.
    dense = [(now + i * 0.5, "字", None) for i in range(120)]   # span 59.5s
    sparse = [(now + 3000 + i * 50, "字", None) for i in range(5)]
    insert_chars(db, dense + sparse)
    e = stats.efficiency(db, None, session_gap=60.0)
    assert e["sessions"] == 2
    assert e["peak_cpm"] == 120.0
    assert e["cpm"] < e["peak_cpm"]


def test_search_counts_occurrences(db, insert_chars, now):
    insert_chars(db, [
        (now, "从", "a"), (now, "前", "a"), (now + 1, "有", "a"),     # run 1
        (now + 100, "从", "a"), (now + 100, "前", "a"),               # run 2
    ])
    r = stats.search(db, "从前", None, run_gap=3.0)
    assert r["query"] == "从前"
    assert r["total"] == 2
    assert r["first_seen"] == now and r["last_seen"] == now + 100
    assert r["apps"] == [{"app": "a", "count": 2}]


def test_tracked_terms_count_per_term(db, insert_chars, now):
    insert_chars(db, [
        (now, "张", "wechat.exe"), (now, "三", "wechat.exe"), (now + 1, "你", "wechat.exe"),
        (now + 100, "张", "word.exe"), (now + 100, "三", "word.exe"),
        (now + 200, "李", "wechat.exe"), (now + 200, "四", "wechat.exe"),
    ])
    rows = stats.tracked_terms(db, ["张三", "李四", "王五"], None, run_gap=3.0)
    assert [r["term"] for r in rows] == ["张三", "李四", "王五"]
    by = {r["term"]: r for r in rows}
    assert by["张三"]["total"] == 2
    assert by["张三"]["active_days"] >= 1
    assert by["张三"]["top_app"]  # whatever pretty_app renders, it is set
    assert by["李四"]["total"] == 1
    assert by["王五"]["total"] == 0
    assert by["王五"]["last_seen"] is None


def test_tracked_terms_does_not_span_a_pause(db, insert_chars, now):
    insert_chars(db, [(now, "张", "a"), (now + 100, "三", "a")])
    rows = stats.tracked_terms(db, ["张三"], None, run_gap=3.0)
    assert rows[0]["total"] == 0


def test_tracked_terms_empty_list(db, insert_chars, now):
    insert_chars(db, [(now, "字", "a")])
    assert stats.tracked_terms(db, [], None, run_gap=3.0) == []
    assert stats.tracked_terms(db, ["", "  "], None, run_gap=3.0) == []


def test_tracked_terms_returns_daily_series_for_sparkline(db, insert_chars, now):
    day = 86400
    insert_chars(db, [
        (now - 2 * day, "张", "a"), (now - 2 * day, "三", "a"),
        (now, "张", "a"), (now, "三", "a"),
        (now + 1, "张", "a"), (now + 1, "三", "a"),
    ])
    rows = stats.tracked_terms(db, ["张三"], None, run_gap=3.0)
    daily = rows[0]["daily"]
    assert {d["date"] for d in daily}  # has dated buckets
    assert sum(d["count"] for d in daily) == rows[0]["total"] == 3
    # two distinct days -> two buckets, matching active_days
    assert len(daily) == rows[0]["active_days"] == 2


def test_user_terms_make_a_name_segment_as_one_word(db, insert_chars, now):
    """A tracked name jieba would otherwise split should, once registered via
    set_user_terms, appear as a whole word in the word-frequency rollup."""
    from ducktype.analysis import segment
    if not segment.HAS_JIEBA:
        import pytest
        pytest.skip("jieba is optional")
    name = "张小满"
    text = (name + "今天写了很多字") * 3
    insert_chars(db, [(now + i * 0.1, ch, "a.exe") for i, ch in enumerate(text)])
    try:
        segment.set_user_terms([])
        v0 = segment.effective_seg_version()
        segment.set_user_terms([name])
        assert segment.effective_seg_version() != v0  # rollups will rebuild
        after = dict(stats.top_words(db, None, 50, run_gap=3.0))
        assert name in after
    finally:
        segment.set_user_terms([])   # don't leak the global into other tests


def test_search_does_not_match_across_a_pause(db, insert_chars, now):
    # '好' then a long gap then '世' must NOT count as the word '好世'.
    insert_chars(db, [(now, "好", "a"), (now + 100, "世", "a")])
    assert stats.search(db, "好世", None, run_gap=3.0)["total"] == 0


def test_search_single_char_matches_char_count(db, insert_chars, now):
    insert_chars(db, [(now, "鸭", "a"), (now + 1, "鸭", "a"), (now + 2, "子", "a")])
    assert stats.search(db, "鸭", None, run_gap=3.0)["total"] == 2
    assert stats.search(db, "  ", None, run_gap=3.0)["total"] == 0


def test_search_examples_include_full_run_context(db, insert_chars, now):
    text = "这是搜索前面的完整上下文鸭这是搜索后面的完整上下文"
    insert_chars(db, [(now + i, ch, "a") for i, ch in enumerate(text)])
    ex = stats.search(db, "鸭", None, run_gap=3.0)["examples"][0]
    assert ex["pre"] == "这是搜索前面的完整上下文"
    assert ex["post"] == "这是搜索后面的完整上下文"
    assert ex["text"] == text


def test_top_words_filters_single_character_materialized_rows(db):
    # Simulate already-materialized data: stamp the current seg version so
    # build_words treats the cache as fresh instead of rebuilding it.
    from ducktype.analysis import segment
    db.set_meta("seg_version", segment._SEG_VERSION)
    con = db.connect()
    try:
        con.executemany(
            "INSERT INTO word_freq(word, count, pos) VALUES (?,?,?)",
            [("的", 99, "uj"), ("我们", 3, "r"), ("鸭子", 2, "n")],
        )
        con.commit()
    finally:
        con.close()
    assert stats.top_words(db, None, 10, run_gap=3.0) == [("我们", 3), ("鸭子", 2)]


def test_coarse_pos_buckets_fine_tags():
    assert stats.coarse_pos("n") == "n"
    assert stats.coarse_pos("ns") == "n"      # 地名 -> 名词
    assert stats.coarse_pos("vn") == "v"      # 名动词 -> 动词
    assert stats.coarse_pos("an") == "a"      # 名形词 -> 形容词
    assert stats.coarse_pos("zg") == "a"      # 状态语素 -> 形容词/状态
    assert stats.coarse_pos("uj") == "fx"     # 结构助词 -> 虚词
    assert stats.coarse_pos("c") == "fx"      # 连词 -> 虚词
    assert stats.coarse_pos("t") == "t"       # 时间 -> 时间/方位
    assert stats.coarse_pos("i") == "other"   # 成语 -> 其他
    assert stats.coarse_pos("") == "other"


def test_pos_override_corrects_common_jieba_errors():
    from ducktype.analysis import segment
    assert segment._pos_tag("位置", "v") == "n"   # jieba mistags as verb
    assert segment._pos_tag("可以", "c") == "v"   # jieba mistags as conjunction
    assert segment._pos_tag("应该", "v") == "v"   # genuinely a verb -> unchanged


def test_pos_word_distribution_groups_tail_and_filters_single_chars(db, monkeypatch):
    def fake_segment_pos_words_range(_db, _since, _run_gap, _until):
        return {"v": {"喜欢": 5, "使用": 3, "看见": 2, "是": 20, "调整": 1}}

    monkeypatch.setattr(stats.segment, "segment_pos_words_range", fake_segment_pos_words_range)
    r = stats.pos_word_distribution(db, "v", None, run_gap=3.0, n=2)
    assert r["label"] == "动词"
    assert r["total"] == 11
    assert r["items"] == [
        {"word": "喜欢", "count": 5, "pct": 45.45},
        {"word": "使用", "count": 3, "pct": 27.27},
    ]
    assert r["other"] == 3
    assert all(len(x["word"]) >= 2 for x in r["least"])


def test_pos_word_distribution_uses_daily_rollup(db, monkeypatch):
    day = datetime.now().date()
    since = datetime.combine(day, datetime.min.time()).timestamp()
    until = since + 86400
    db.set_meta("seg_version", "3")
    db.set_meta("word_cursor", "0")
    con = db.connect()
    try:
        con.executemany(
            "INSERT INTO pos_word_freq_daily(day, pos, word, count) VALUES (?,?,?,?)",
            [
                (day.isoformat(), "n", "老师", 4),
                (day.isoformat(), "nr", "南京", 2),
                (day.isoformat(), "v", "喜欢", 9),
                (day.isoformat(), "n", "我", 20),
            ],
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(
        stats.segment, "segment_pos_words_range",
        lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("live path used")),
    )
    r = stats.pos_word_distribution(db, "n", since, run_gap=3.0, until=until, n=5)
    assert r["total"] == 6
    assert r["items"] == [
        {"word": "老师", "count": 4, "pct": 66.67},
        {"word": "南京", "count": 2, "pct": 33.33},
    ]


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
    assert g["goal_pct"] == 1.25  # 5/4, now uncapped up to 9999% for the ring
    assert any(a["id"] == "streak3" and a["unlocked"] for a in g["achievements"])


def test_mini_stats_goal_progress_caps_at_9999_percent(db, insert_chars):
    today = datetime.now().replace(hour=1, minute=0, second=0, microsecond=0)
    rows = [(today.timestamp() + i * 0.001, "鸭", None) for i in range(10050)]
    insert_chars(db, rows)
    m = stats.mini_stats(db, daily_goal=1)
    assert m["today_chars"] == 10050
    assert m["goal_pct"] == 99.99


def test_gamify_extra_character_achievements(db, insert_chars, now):
    rare = "龘靐齉麤爨饕餮魑魅魍魉"
    rows = [(now + i * 0.01, "鸭", None) for i in range(100)]
    rows += [(now + 10 + i, ch, None) for i, ch in enumerate(rare[:10])]
    insert_chars(db, rows)
    g = stats.gamify(db, daily_goal=1)
    by_id = {a["id"]: a for a in g["achievements"]}
    assert by_id["char100"]["unlocked"]
    assert by_id["duck100"]["unlocked"]
    assert by_id["rare10"]["unlocked"]


def test_report_fast_includes_calendar_rhythm(db, insert_chars):
    monday = datetime(2026, 1, 5, 9, 0)
    tuesday = datetime(2026, 1, 6, 9, 0)
    rows = [(monday.timestamp() + i, "字", None) for i in range(10)]
    rows += [(tuesday.timestamp() + i, "字", None) for i in range(2)]
    insert_chars(db, rows)
    r = stats.report_fast(
        db, "custom", run_gap=3.0, session_gap=60.0,
        start="2026-01-05", end="2026-01-06",
    )
    assert r["busiest_weekday"] == "周一"
    assert r["busiest_weekday_count"] == 10
    assert r["quietest_weekday"] == "周二"
    assert r["quietest_weekday_count"] == 2
    assert r["busiest_week"] == "2026-W02"


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


def test_fun_rankings_common_supplement_excludes_modern_common_chars(db, insert_chars, now):
    insert_chars(db, [(now, ch, None) for ch in "哦蔡噻㐀"])
    f = stats.fun_rankings(db, None, run_gap=3.0)
    rare = {rc["ch"] for rc in f["rare_chars"]}
    assert {"哦", "蔡", "噻"}.isdisjoint(rare)
    assert "㐀" in rare
