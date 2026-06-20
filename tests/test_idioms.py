"""Tests for dictionary-based 成语 detection (analysis.idioms) and its effect on
the fun-rankings idiom / long-word split."""
from ducktype.analysis import idioms, stats


def test_known_idioms_are_recognised():
    assert idioms.is_idiom("画蛇添足")
    assert idioms.is_idiom("一目十行")
    # An ordinary 4-char collocation must NOT be treated as an idiom (the bug the
    # rewrite fixes -- the old code flagged every 4-char all-Han word).
    assert not idioms.is_idiom("今天天气")
    assert not idioms.is_idiom("的的的的")


def test_scan_greedy_longest_match():
    # A multi-character idiom is counted once, not also as nested shorter idioms.
    assert list(idioms.scan("八仙过海各显神通")) == ["八仙过海各显神通"]
    assert list(idioms.scan("画蛇添足一目十行")) == ["画蛇添足", "一目十行"]
    assert list(idioms.scan("今天天气很好")) == []


def test_multi_clause_idiom_matches_without_comma():
    # Dictionary entries written with a comma (胜不骄，败不馁) are stored as the
    # contiguous Han run, matching the comma-free committed stream.
    assert idioms.is_idiom("胜不骄败不馁")
    assert list(idioms.scan("胜不骄败不馁")) == ["胜不骄败不馁"]


def test_dictionary_has_rich_four_char_coverage():
    assert len(idioms.BY_LEN[4]) > 20000        # the rich four-char set
    assert set(idioms.BY_LEN) >= {3, 4, 5, 6, 7, 8}


def test_fun_rankings_keeps_idioms_out_of_long_words(db, insert_chars, now):
    text = "画蛇添足今天天气很好"
    insert_chars(db, [(now + i, ch, "Code.exe") for i, ch in enumerate(text)])
    fun = stats.fun_rankings(db, None, 3.0, None)
    # Idioms moved to their own 词库 tab -- the fun payload no longer lists them.
    assert "idioms" not in fun
    long_words = {r["word"] for r in fun["long_words"]}
    assert "画蛇添足" not in long_words        # idioms stay out of 长词
