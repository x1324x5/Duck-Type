"""Playful leaderboards and the 生僻字 (uncommon-character) classifier.

Extracted from ``stats.py`` (0.2.8 refactor) so the "fun" panel and the
common-character filter live in one small module. ``stats`` re-exports the
public names (``fun_rankings``, ``set_user_common``, ``_is_uncommon``,
``_COMMON_SUPPLEMENT``) so existing callers and tests are unaffected.
"""
from __future__ import annotations

from typing import Dict, Optional

from ..perf import timed
from . import idioms as idioms_mod
from . import segment
from .common_chars import COMMON_CHARS

# A small modern/common supplement on top of the 3,500-character reference. The
# reference table is intentionally conservative; these characters are common in
# names, modal particles, transliterations, food/internet writing, or everyday
# proper nouns and should not make the "生僻字" panel feel noisy.
_COMMON_SUPPLEMENT = frozenset(
    "哦噢嗯欸诶哎唉呀呃哇喔呗嘛哟啦咯啰"
    "蔡噻甄邱邵彭蒋韩萧阎廖薛冯覃翟邹贾袁"
    "咖啡巧克力披萨薯堡酱橙柠檬莓椰"
    "粤闽沪渝圳澳台港甬蓉穗杭"
    "梗梳槽怼囧萌酷飒"
)

# Characters the user has marked as common (config.common_chars_extra). Injected
# by the dashboard via set_user_common(); empty by default so the pure-logic
# tests stay deterministic. This is the user-tunable half of the "生僻字 = 常用字
# 表的补集" rule -- the built-in table is the filter, this is the personal escape
# hatch for names/jargon the user does not consider 生僻.
_USER_COMMON: frozenset = frozenset()


def set_user_common(chars) -> None:
    """Replace the user's extra-common character set used by _is_uncommon."""
    global _USER_COMMON
    _USER_COMMON = frozenset(c for c in (chars or []) if c)


def _is_uncommon(ch: str) -> bool:
    """A typed character counts as 生僻/uncommon when it is a Han ideograph that
    is *not* in the common-character filter: the 3,500 standard 常用字 table, the
    modern supplement, or the user's own "extra common" list. The classification
    is the complement of that filter -- it never looks at how often the user
    typed the character, only at whether the character is intrinsically common."""
    return (segment._HAN(ch) and ch not in COMMON_CHARS
            and ch not in _COMMON_SUPPLEMENT and ch not in _USER_COMMON)


def fun_rankings(db, since: Optional[float], run_gap: float,
                 until: Optional[float] = None) -> Dict:
    """Playful leaderboards: favourite long words, idioms, hapax & rare chars."""
    from . import stats   # lazy: avoids any import-order cycle with stats
    char_counts = dict(stats.top_chars(db, since, 1_000_000, until))
    hapax = [c for c, n in char_counts.items() if n == 1]
    rare = sorted(
        ((c, n) for c, n in char_counts.items() if _is_uncommon(c)),
        key=lambda kv: kv[1], reverse=True,
    )[:30]

    with timed("stats.fun_rankings.segment_range"):
        wc, wp, _pc = segment.segment_range(db, since, run_gap, until)

    fav_words = sorted(
        ((w, n) for w, n in wc.items() if len(w) >= 2),
        key=lambda kv: kv[1], reverse=True,
    )[:30]
    # Idioms now live in their own 词库 (lexicon) tab, scanned against the idiom
    # dictionary. They are still kept out of the 长词 (long-word) list here so the
    # two views don't overlap.
    long_words = sorted(
        ((w, n) for w, n in wc.items()
         if len(w) >= 3 and not idioms_mod.is_idiom(w)),
        key=lambda kv: kv[1], reverse=True,
    )[:30]
    return {
        "favorite_words": [{"word": w, "count": n} for w, n in fav_words],
        "long_words": [{"word": w, "count": n} for w, n in long_words],
        "hapax": hapax[:60],
        "rare_chars": [{"ch": c, "count": n} for c, n in rare],
        "distinct": len(char_counts),
        "hapax_count": len(hapax),
    }
