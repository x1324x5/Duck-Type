"""成语 detection over the committed-character stream.

Membership against a real idiom dictionary (see ``idiom_data``) replaces the old
heuristic that flagged *every* 4-character all-Han word as an idiom. Idioms are
grouped by length so the scanner only slides windows of lengths that exist, and
``scan`` does greedy longest-match so a long idiom is counted once rather than
also matching shorter idioms nested inside it.
"""
from __future__ import annotations

from typing import Dict, Iterator

from . import idiom_data

# length -> frozenset of idioms of that length. The 4-char set (by far the
# largest) is reconstructed from the concatenated ``_FOUR`` blob in 4-char slices.
BY_LEN: Dict[int, frozenset] = {
    4: frozenset(idiom_data._FOUR[i:i + 4]
                 for i in range(0, len(idiom_data._FOUR), 4))
}
for _w in idiom_data._OTHER:
    BY_LEN.setdefault(len(_w), set()).add(_w)  # type: ignore[union-attr]
BY_LEN = {k: frozenset(v) for k, v in BY_LEN.items()}

# Window sizes to try, longest first (greedy longest-match).
_LENGTHS = sorted(BY_LEN, reverse=True)
_MINLEN = min(BY_LEN) if BY_LEN else 0


def is_idiom(word: str) -> bool:
    """True if ``word`` is a known idiom (used to keep idioms out of 长词)."""
    s = BY_LEN.get(len(word))
    return s is not None and word in s


def scan(text: str) -> Iterator[str]:
    """Yield idioms found in ``text``, scanning left-to-right with greedy
    longest-match. Overlapping/nested matches are not double-counted: once an
    idiom is matched the cursor advances past it."""
    n = len(text)
    i = 0
    while i <= n - _MINLEN:
        for length in _LENGTHS:
            if i + length <= n and text[i:i + length] in BY_LEN[length]:
                yield text[i:i + length]
                i += length
                break
        else:
            i += 1


def count_text(text: str, counts: Dict[str, int]) -> None:
    """Accumulate idiom occurrences from one run string into ``counts``."""
    for w in scan(text):
        counts[w] = counts.get(w, 0) + 1
