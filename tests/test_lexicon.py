"""Tests for the pluggable 词库 (lexicon) subsystem: the generic Matcher, the
on-disk LexiconStore, the input parsers, and the dashboard endpoints/stats."""
import pytest

from ducktype.analysis import lexicon as lx
from ducktype.config import Config
from ducktype.dashboard.api import Api


def _api(db):
    return Api(db, Config(), status_fn=lambda: {"ok": True})


# ---- Matcher --------------------------------------------------------------
def test_matcher_greedy_longest_match():
    m = lx.Matcher.from_words(["苹果", "苹果手机", "手机"])
    assert "苹果手机" in m and "手机" in m
    # longest-match: 苹果手机 counted once, not also 苹果 + 手机
    assert list(m.scan("我爱苹果手机啊")) == ["苹果手机"]
    assert list(m.scan("手机和苹果")) == ["手机", "苹果"]


def test_idiom_matcher_reuses_dictionary():
    m = lx.idiom_matcher()
    assert m.size > 20000
    assert list(m.scan("画蛇添足今天天气")) == ["画蛇添足"]


# ---- parsers --------------------------------------------------------------
def test_parse_words_splits_and_dedupes():
    assert lx.parse_words("苹果 香蕉,西瓜、香蕉") == ["苹果", "香蕉", "西瓜"]


def test_parse_items_one_per_line():
    assert lx.parse_items("海阔天空\n  脚踏实地  \n\n海阔天空") == ["海阔天空", "脚踏实地"]


def test_parse_file_lines_keeps_first_column():
    # jieba/rime/sogou-style "word freq pos" or "word,weight" -> just the word
    content = "﻿苹果 100 n\n# comment\n香蕉\t50\n西瓜,3\n//skip\n"
    assert lx.parse_file_lines(content) == ["苹果", "香蕉", "西瓜"]


# ---- store ----------------------------------------------------------------
def test_store_seeds_builtin_idiom(tmp_path):
    store = lx.LexiconStore(tmp_path)
    items = store.list()
    assert items[0]["id"] == lx.IDIOM_ID
    assert items[0]["builtin"] is True and items[0]["enabled"] is True
    assert items[0]["count"] > 20000


def test_store_create_toggle_delete(tmp_path):
    store = lx.LexiconStore(tmp_path)
    lex_id = store.create("水果", ["苹果", "香蕉", "香蕉"])
    by_id = {it["id"]: it for it in store.list()}
    assert by_id[lex_id]["count"] == 2 and by_id[lex_id]["name"] == "水果"

    store.update(lex_id, enabled=False)
    assert store.meta(lex_id)["enabled"] is False

    store.delete(lex_id)
    assert store.meta(lex_id) is None


def test_store_rejects_deleting_builtin(tmp_path):
    store = lx.LexiconStore(tmp_path)
    with pytest.raises(ValueError):
        store.delete(lx.IDIOM_ID)


def test_store_create_rejects_empty(tmp_path):
    store = lx.LexiconStore(tmp_path)
    with pytest.raises(ValueError):
        store.create("空", [])


def test_store_survives_reload(tmp_path):
    lx.LexiconStore(tmp_path).create("水果", ["苹果", "香蕉"])
    again = lx.LexiconStore(tmp_path)
    names = {it["name"] for it in again.list()}
    assert "水果" in names and lx.IDIOM_NAME in names


# ---- scan over the committed stream --------------------------------------
def test_scan_counts_over_runs(db, insert_chars, now):
    text = "苹果香蕉苹果"
    insert_chars(db, [(now + i, ch, "Code.exe") for i, ch in enumerate(text)])
    m = lx.Matcher.from_words(["苹果", "香蕉"])
    counts = lx.scan_counts(db, m, None, 3.0, None)
    assert counts == {"苹果": 2, "香蕉": 1}


# ---- dashboard endpoints --------------------------------------------------
def test_api_lexicon_crud_and_stats(db, insert_chars, now, tmp_path, monkeypatch):
    # Point the lexicon store at a throwaway dir so we don't touch the real root.
    import ducktype.paths as paths
    monkeypatch.setattr(paths, "root_dir", lambda: tmp_path)

    insert_chars(db, [(now + i, ch, "Code.exe")
                      for i, ch in enumerate("画蛇添足画蛇添足一目十行")])
    api = _api(db)

    listed = api.lexicon_list()["items"]
    assert listed[0]["id"] == lx.IDIOM_ID

    # built-in idiom stats: 画蛇添足 x2, 一目十行 x1
    stats = api.get("lexicon_stats", {"id": lx.IDIOM_ID, "range": "all"})
    assert stats["found"] and stats["total"] == 3
    words = {w["word"]: w["count"] for w in stats["words"]}
    assert words.get("画蛇添足") == 2 and words.get("一目十行") == 1

    # create a user lexicon and check its stats + that it cannot collide with idiom
    res = api.lexicon_create(name="成语自选", words="画蛇添足")
    assert res["ok"]
    uid = res["id"]
    ustats = api.get("lexicon_stats", {"id": uid, "range": "all"})
    assert ustats["total"] == 2 and ustats["distinct"] == 1

    assert api.lexicon_delete(lx.IDIOM_ID)["ok"] is False   # protected
    assert api.lexicon_delete(uid)["ok"] is True


def test_api_derived_lexicons_and_report(db, insert_chars, now, tmp_path, monkeypatch):
    import ducktype.paths as paths
    monkeypatch.setattr(paths, "root_dir", lambda: tmp_path)

    insert_chars(db, [(now + i, ch, "Code.exe")
                      for i, ch in enumerate("画蛇添足画蛇添足张三张三魑魅魍魉")])
    api = Api(db, Config(tracked_terms=["张三"]), status_fn=lambda: {"ok": True})

    by_id = {it["id"]: it for it in api.lexicon_list()["items"]}
    # 关注词 / 生僻字 are plugged in as derived built-in lexicons
    assert by_id["tracked"]["derived"] and by_id["tracked"]["builtin"]
    assert by_id["rare"]["derived"] and by_id["rare"]["count"] == 4  # 魑魅魍魉

    tracked = api.get("lexicon_stats", {"id": "tracked", "range": "all"})
    assert tracked["total"] == 2 and tracked["words"][0]["word"] == "张三"

    # derived built-ins cannot be deleted
    assert api.lexicon_delete("tracked")["ok"] is False
    assert api.lexicon_delete("rare")["ok"] is False

    rep = api.get("lexicon_report", {"range": "all"})["lexicons"]
    names = {r["name"]: r for r in rep}
    assert {"成语", "关注词", "生僻字"} <= set(names)
    assert names["成语"]["top_word"] == "画蛇添足"
    # sorted by total usage, descending
    totals = [r["total"] for r in rep]
    assert totals == sorted(totals, reverse=True)
