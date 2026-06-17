"""Validate the curated DuckType ticker quote bank.

History: this script used to *scrape* whole works off Wikisource/Gutenberg and
slice them into sentences. That produced raw narrative filler from a handful of
authors rather than memorable lines, so as of 0.1.5 the bank is hand-curated in
``src/ducktype/analysis/quote_bank.py`` and this tool only checks it.

Checks: no duplicates, every entry has an attribution separator, sane length,
and reports the Chinese/English split plus author spread. Exit code is non-zero
on any hard error so it can gate a release.

Run: ``python tools/build_quote_bank.py``
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "src" / "ducktype" / "analysis" / "quote_bank.py"

# Lower English share is fine; this is just an upper sanity bound (~15% target).
EN_RATIO_MAX = 0.25
MIN_LEN, MAX_LEN = 4, 160


def load_quotes() -> tuple[str, ...]:
    ns: dict = {}
    exec(compile(BANK.read_text(encoding="utf-8"), str(BANK), "exec"), ns)
    return ns["QUOTES"]


def is_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


def attribution(quote: str) -> str | None:
    if "——" in quote:
        return quote.rsplit("——", 1)[1]
    if " — " in quote:
        return quote.rsplit(" — ", 1)[1]
    return None


def main() -> int:
    quotes = load_quotes()
    errors: list[str] = []

    seen: dict[str, int] = {}
    for q in quotes:
        seen[q] = seen.get(q, 0) + 1
    for q, n in seen.items():
        if n > 1:
            errors.append(f"duplicate ({n}x): {q!r}")

    for q in quotes:
        attr = attribution(q)
        if attr is None:
            errors.append(f"missing author separator: {q!r}")
        elif not attr.strip():
            errors.append(f"empty attribution: {q!r}")
        if not (MIN_LEN <= len(q) <= MAX_LEN):
            errors.append(f"suspicious length ({len(q)}): {q!r}")

    zh = [q for q in quotes if is_chinese(q)]
    en = [q for q in quotes if not is_chinese(q)]
    en_ratio = len(en) / len(quotes) if quotes else 0.0
    if en_ratio > EN_RATIO_MAX:
        errors.append(f"English share {en_ratio:.0%} exceeds {EN_RATIO_MAX:.0%}")

    authors = set()
    for q in quotes:
        attr = attribution(q) or ""
        authors.add(re.split(r"[《（,]", attr)[0].strip())

    print(f"quotes : {len(quotes)}")
    print(f"chinese: {len(zh)}")
    print(f"english: {len(en)} ({en_ratio:.1%})")
    print(f"authors: ~{len(authors)} distinct")

    if errors:
        print(f"\nFAILED with {len(errors)} problem(s):", file=sys.stderr)
        for e in errors[:50]:
            print("  -", e, file=sys.stderr)
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
