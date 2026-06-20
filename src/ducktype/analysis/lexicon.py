"""Pluggable word libraries (词库) scanned over the committed-character stream.

A *lexicon* is just a named set of words. DuckType ships one built-in lexicon --
the 成语 idiom dictionary (:mod:`analysis.idioms`) -- and lets the user add their
own: uploaded dictionary files, pasted space-separated text, or words entered one
at a time. Each lexicon can be enabled or disabled independently.

Lexicon statistics are a *separate, additive* layer on top of the dashboard --
they never change the core character / word / POS counts. The 词库 tab shows, for
each enabled lexicon, how the words it recognises are distributed across the
committed stream (a pie chart of per-word share), so the design is deliberately
open to more lexicon-driven views later.

Storage layout, under ``<root>/lexicons/``::

    index.json        ordered metadata for every lexicon (id/name/enabled/...)
    <id>.txt          one word per line, for user lexicons only

The built-in idiom lexicon is code-backed and has no ``.txt`` file. Matching uses
greedy longest-match against the words grouped by length, so a longer phrase is
counted once instead of also matching the shorter entries nested inside it (the
same approach as :mod:`analysis.idioms`, generalised to any word set).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Iterator, List, Optional

from . import idioms, segment

# The built-in idiom lexicon's fixed id. Reserved -- a user lexicon can never
# take it, and it can never be deleted.
IDIOM_ID = "idiom"
IDIOM_NAME = "成语"

# Sanity caps so a pathological upload can't blow up memory or the scan cost.
_MAX_WORDS = 200_000
_MAX_WORD_LEN = 32

# Token separators for free-form pasted text and dictionary-file first columns.
_PASTE_SEP = re.compile(r"[\s,，、;；/|]+")
_COLUMN_SEP = re.compile(r"[\s,，、\t]+")


class Matcher:
    """Greedy longest-match scanner over a fixed set of words.

    Words are bucketed by length; the scanner only slides windows of lengths
    that actually exist and tries the longest first, so an N-character entry is
    matched as one unit rather than also matching shorter entries inside it.
    """

    def __init__(self, by_len: Dict[int, frozenset]):
        self.by_len = by_len
        self.lengths = sorted(by_len, reverse=True)
        self.minlen = min(by_len) if by_len else 0
        self.size = sum(len(s) for s in by_len.values())

    @classmethod
    def from_words(cls, words) -> "Matcher":
        grouped: Dict[int, set] = {}
        for w in words:
            if w:
                grouped.setdefault(len(w), set()).add(w)
        return cls({k: frozenset(v) for k, v in grouped.items()})

    def __contains__(self, word: str) -> bool:
        s = self.by_len.get(len(word))
        return s is not None and word in s

    def scan(self, text: str) -> Iterator[str]:
        """Yield the lexicon words found in ``text`` (left-to-right, greedy)."""
        minlen = self.minlen
        if not minlen:
            return
        by_len, lengths = self.by_len, self.lengths
        n = len(text)
        i = 0
        while i <= n - minlen:
            for length in lengths:
                if i + length <= n and text[i:i + length] in by_len[length]:
                    yield text[i:i + length]
                    i += length
                    break
            else:
                i += 1

    def count_text(self, text: str, counts: Dict[str, int]) -> None:
        for w in self.scan(text):
            counts[w] = counts.get(w, 0) + 1


def idiom_matcher() -> Matcher:
    """Matcher backed by the built-in idiom dictionary (reuses its frozensets)."""
    return Matcher(idioms.BY_LEN)


# ---- parsing user-supplied word sources ----------------------------------
def _dedupe_cap(words) -> List[str]:
    out: List[str] = []
    seen = set()
    for w in words:
        w = (w or "").strip()
        if not w or len(w) > _MAX_WORD_LEN:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= _MAX_WORDS:
            break
    return out


def parse_words(text: str) -> List[str]:
    """Words from free-form pasted text: split on whitespace and punctuation, so
    每个 token 就是一个词。Used by the "paste space-separated text" entry path."""
    return _dedupe_cap(_PASTE_SEP.split(text or ""))


def parse_items(items) -> List[str]:
    """Words entered one-per-item (a list, or newline-separated text). Each item
    is taken verbatim (after trimming), supporting the per-item entry path."""
    if isinstance(items, str):
        items = items.splitlines()
    try:
        seq = list(items)
    except TypeError:
        seq = []
    return _dedupe_cap(seq)


def parse_file_lines(content: str) -> List[str]:
    """Words from an uploaded dictionary file. Reads one entry per line and keeps
    only the first column, which adapts the common formats (plain one-word-per-
    line lists, and ``word freq``/``word\\tcode``/``word,weight`` dictionaries
    used by jieba / Rime / Sogou exports). ``#`` and ``//`` comment lines are
    skipped."""
    out: List[str] = []
    seen = set()
    for line in (content or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        tok = _COLUMN_SEP.split(line, 1)[0].strip()
        if not tok or len(tok) > _MAX_WORD_LEN:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
        if len(out) >= _MAX_WORDS:
            break
    return out


# ---- scanning the committed stream ---------------------------------------
def scan_counts(db, matcher: Matcher, since: Optional[float], run_gap: float,
                until: Optional[float] = None) -> Dict[str, int]:
    """Count occurrences of each lexicon word across the committed runs in the
    window. Commas/pauses between clauses are already gone (runs are contiguous
    Han) so multi-clause entries match naturally."""
    counts: Dict[str, int] = {}
    for run in segment.iter_run_texts(db, since, run_gap, until):
        matcher.count_text(run, counts)
    return counts


# ---- persistent store -----------------------------------------------------
class LexiconStore:
    """Filesystem-backed registry of lexicons living under ``base_dir``.

    The built-in idiom lexicon is always present (and always first) even before
    anything is written to disk. Matchers are cached per id and invalidated when
    a lexicon is created/deleted; built-in words and a user lexicon's file are
    immutable for a given id, so the cache stays correct between writes.
    """

    def __init__(self, base_dir):
        self.dir = str(base_dir)
        self._matchers: Dict[str, Matcher] = {}
        # Derived built-in lexicons whose word set is supplied live by a callback
        # (e.g. the user's 关注词, or 生僻字 from their data) -- id -> (name, fn).
        # They behave like the idiom lexicon (built-in, cannot be deleted, can be
        # toggled), but their matcher is rebuilt on demand since the source is
        # dynamic. This is how the 关注词 / 生僻字 systems "plug into" 词库.
        self._providers: Dict[str, tuple] = {}

    def register_provider(self, lex_id: str, name: str, fn,
                          default_enabled: bool = True) -> None:
        self._providers[lex_id] = (name, fn, default_enabled)

    def _provider_words(self, lex_id: str):
        name, fn, _en = self._providers[lex_id]
        try:
            return list(fn() or [])
        except Exception:
            return []

    # paths -----------------------------------------------------------------
    def _index_path(self) -> str:
        return os.path.join(self.dir, "index.json")

    def _words_path(self, lex_id: str) -> str:
        return os.path.join(self.dir, lex_id + ".txt")

    # index -----------------------------------------------------------------
    def _load_index(self) -> dict:
        try:
            with open(self._index_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = []
        items = [it for it in items if isinstance(it, dict) and it.get("id")]
        if not any(it.get("id") == IDIOM_ID for it in items):
            items.insert(0, {"id": IDIOM_ID, "name": IDIOM_NAME,
                             "builtin": True, "enabled": True})
        # Make sure each registered derived lexicon has a metadata entry (so its
        # enabled state persists), inserted after idiom but before user lexicons.
        pos = 1
        for pid, (pname, _fn, _en) in self._providers.items():
            if not any(it.get("id") == pid for it in items):
                items.insert(pos, {"id": pid, "name": pname, "builtin": True,
                                   "enabled": bool(_en), "derived": True})
            pos += 1
        return {"format": 1, "items": items}

    def _save_index(self, data: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._index_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._index_path())

    # words -----------------------------------------------------------------
    def _read_words(self, lex_id: str) -> List[str]:
        try:
            with open(self._words_path(lex_id), encoding="utf-8") as f:
                return [ln.strip() for ln in f if ln.strip()]
        except OSError:
            return []

    def matcher(self, lex_id: str, builtin: bool) -> Matcher:
        if lex_id in self._providers:
            # dynamic source -> rebuild each call (the sets are small)
            return Matcher.from_words(self._provider_words(lex_id))
        m = self._matchers.get(lex_id)
        if m is not None:
            return m
        if builtin and lex_id == IDIOM_ID:
            m = idiom_matcher()
        else:
            m = Matcher.from_words(self._read_words(lex_id))
        self._matchers[lex_id] = m
        return m

    # public API ------------------------------------------------------------
    def list(self) -> List[dict]:
        """Metadata for every lexicon, with a live word count."""
        out = []
        for it in self._load_index()["items"]:
            lex_id = it["id"]
            builtin = bool(it.get("builtin"))
            derived = lex_id in self._providers
            count = (len(set(self._provider_words(lex_id))) if derived
                     else self.matcher(lex_id, builtin).size)
            out.append({
                "id": lex_id,
                "name": it.get("name") or lex_id,
                "builtin": builtin,
                "derived": derived,
                "enabled": bool(it.get("enabled", True)),
                "count": count,
            })
        return out

    def meta(self, lex_id: str) -> Optional[dict]:
        for m in self.list():
            if m["id"] == lex_id:
                return m
        return None

    def is_editable(self, lex_id: str) -> bool:
        """Only user-created lexicons can be edited (built-in / derived are read-only)."""
        return lex_id != IDIOM_ID and lex_id not in self._providers

    def words(self, lex_id: str) -> List[str]:
        """The full ordered word list for any lexicon (for the 查看/编辑 modal).
        Derived lexicons come from their live provider; the idiom lexicon is
        flattened from its length-bucketed frozensets; user lexicons from disk."""
        if lex_id in self._providers:
            return [str(w) for w in self._provider_words(lex_id)]
        if lex_id == IDIOM_ID:
            out: List[str] = []
            for length in sorted(idioms.BY_LEN):
                out.extend(sorted(idioms.BY_LEN[length]))
            return out
        return self._read_words(lex_id)

    def _new_id(self) -> str:
        base = "u_" + format(int(time.time() * 1000), "x")
        lex_id = base
        n = 1
        existing = {it["id"] for it in self._load_index()["items"]}
        while lex_id in existing or os.path.exists(self._words_path(lex_id)):
            lex_id = f"{base}_{n}"
            n += 1
        return lex_id

    def create(self, name: Optional[str], words) -> str:
        words = _dedupe_cap(words)
        if not words:
            raise ValueError("词库为空，没有找到可用的词。")
        lex_id = self._new_id()
        os.makedirs(self.dir, exist_ok=True)
        with open(self._words_path(lex_id), "w", encoding="utf-8") as f:
            f.write("\n".join(words))
        idx = self._load_index()
        idx["items"].append({
            "id": lex_id,
            "name": (str(name).strip()[:40] if name else "") or "我的词库",
            "builtin": False,
            "enabled": True,
        })
        self._save_index(idx)
        self._matchers.pop(lex_id, None)
        return lex_id

    def update(self, lex_id: str, name: Optional[str] = None,
               enabled: Optional[bool] = None) -> bool:
        idx = self._load_index()
        for it in idx["items"]:
            if it["id"] == lex_id:
                if name is not None:
                    it["name"] = str(name).strip()[:40] or it.get("name") or lex_id
                if enabled is not None:
                    it["enabled"] = bool(enabled)
                self._save_index(idx)
                return True
        return False

    def set_words(self, lex_id: str, words) -> int:
        """Overwrite a *user* lexicon's word list (the 查看/编辑 modal). Built-in
        and derived lexicons are read-only. Returns the resulting word count."""
        if lex_id == IDIOM_ID or lex_id in self._providers:
            raise ValueError("内置词库不可编辑。")
        if not any(it["id"] == lex_id for it in self._load_index()["items"]):
            raise ValueError("找不到这个词库。")
        words = _dedupe_cap(words)
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._words_path(lex_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(words))
        os.replace(tmp, self._words_path(lex_id))
        self._matchers.pop(lex_id, None)
        return len(words)

    def delete(self, lex_id: str) -> None:
        if lex_id == IDIOM_ID or lex_id in self._providers:
            raise ValueError("内置词库不可删除。")
        idx = self._load_index()
        before = len(idx["items"])
        idx["items"] = [it for it in idx["items"] if it["id"] != lex_id]
        if len(idx["items"]) == before:
            raise ValueError("找不到这个词库。")
        self._save_index(idx)
        try:
            os.remove(self._words_path(lex_id))
        except OSError:
            pass
        self._matchers.pop(lex_id, None)
